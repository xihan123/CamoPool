import asyncio
import json
import logging
import os
import re
import signal
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from camoufox.server import to_camel_case_dict
from camoufox.utils import launch_options
from playwright._impl._driver import compute_driver_executable
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve

from .config import Settings
from .profiles import PROFILES, Platform, PlatformProfile

logger = logging.getLogger(__name__)
_WS_RE = re.compile(r"(ws://\S+)")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class PoolBusy(RuntimeError):
    pass


class LeaseNotFound(KeyError):
    pass


class BrowserServerUnavailable(RuntimeError):
    pass


@dataclass
class ServerSlot:
    index: int
    port: int
    process: Optional[asyncio.subprocess.Process] = None
    endpoint: Optional[str] = None
    browser_version: Optional[str] = None
    platform: Platform = Platform.GENERIC
    lease_id: Optional[str] = None
    expires_at: Optional[datetime] = None
    restarts: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass(frozen=True)
class Lease:
    lease_id: str
    ws_endpoint: str
    browser_version: str
    platform: Platform
    user_agent: Optional[str]
    expires_at: datetime


class BrowserServerPool:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.slots: List[ServerSlot] = [
            ServerSlot(index=index, port=settings.base_port + index)
            for index in range(settings.pool_size)
        ]
        self.available: asyncio.Queue[int] = asyncio.Queue(maxsize=settings.pool_size)
        self.leases: Dict[str, ServerSlot] = {}
        self.warm_lock = asyncio.Lock()
        self.started = False
        self.reaper: Optional[asyncio.Task] = None
        self.ws_server = None

    async def start(self) -> None:
        if self.started:
            return
        self.started = True
        try:
            self.ws_server = await serve(
                self._proxy_websocket,
                self.settings.ws_host,
                self.settings.ws_port,
                compression=None,
                max_size=None,
            )
            await asyncio.gather(
                *(
                    self._replace(slot, PROFILES[Platform.GENERIC])
                    for slot in self.slots[: self.settings.warm_pool_size]
                )
            )
        except BaseException:
            await self.stop()
            raise
        for slot in self.slots:
            self.available.put_nowait(slot.index)
        self.reaper = asyncio.create_task(self._reap_expired(), name="camopool-lease-reaper")
        logger.info(
            "browser server pool ready: instance=%s capacity=%s warm=%s ports=%s-%s",
            self.settings.instance_id,
            len(self.slots),
            self.settings.warm_pool_size,
            self.slots[0].port,
            self.slots[-1].port,
        )

    async def stop(self) -> None:
        self.started = False
        if self.ws_server:
            self.ws_server.close()
            await self.ws_server.wait_closed()
            self.ws_server = None
        if self.reaper:
            self.reaper.cancel()
            await asyncio.gather(self.reaper, return_exceptions=True)
            self.reaper = None
        await asyncio.gather(*(self._terminate(slot) for slot in self.slots), return_exceptions=True)
        self.leases.clear()
        while not self.available.empty():
            self.available.get_nowait()
        for slot in self.slots:
            slot.lease_id = None
            slot.expires_at = None

    async def _launch(self, slot: ServerSlot, profile: PlatformProfile) -> None:
        config = profile.camoufox_config()
        kwargs: Dict[str, Any] = {
            "headless": self.settings.headless,
            "geoip": self.settings.geoip,
            "proxy": self.settings.launch_proxy,
            "os": "linux" if config else self.settings.browser_os,
            "config": config,
            "host": self.settings.worker_host,
            "port": slot.port,
            "enable_cache": True,
            "fingerprint_preset": True,
            "browser": self.settings.camoufox_browser,
            "i_know_what_im_doing": bool(config),
        }
        for attempt in range(5):
            try:
                options = await asyncio.to_thread(launch_options, **kwargs)
                break
            except ValueError as exc:
                if "No WebGL data found" not in str(exc):
                    raise
                logger.warning("incompatible real fingerprint preset, retrying (%s/5)", attempt + 1)
        else:
            logger.warning("real fingerprint presets exhausted; falling back to BrowserForge")
            kwargs["fingerprint_preset"] = False
            try:
                options = await asyncio.to_thread(launch_options, **kwargs)
            except ValueError as exc:
                raise BrowserServerUnavailable(
                    "unable to generate a compatible browser fingerprint"
                ) from exc
        node, cli = compute_driver_executable()
        fd, config_path = tempfile.mkstemp(prefix="camopool-", suffix=".json")
        os.close(fd)
        Path(config_path).write_text(
            json.dumps(to_camel_case_dict(options)), encoding="utf-8"
        )
        compat_hook = Path(__file__).with_name("playwright_compat.cjs")
        process_kwargs: Dict[str, Any] = {}
        if os.name == "nt":
            process_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            process_kwargs["start_new_session"] = True
        version_data = json.loads(
            (Path(options["executable_path"]).parent / "version.json").read_text(
                encoding="utf-8"
            )
        )
        slot.browser_version = "%s-%s" % (
            version_data["version"],
            version_data["build"],
        )
        command = [node]
        if int(version_data["version"].split(".", 1)[0]) < 152:
            command.extend(["-r", str(compat_hook)])
        command.extend([
            cli,
            "launch-server",
            "--browser",
            "firefox",
            "--config",
            config_path,
        ])
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=Path(cli).parent,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **process_kwargs,
        )
        slot.process = process
        assert process.stdout

        try:
            deadline = asyncio.get_running_loop().time() + self.settings.launch_timeout_ms / 1000
            output: List[str] = []
            while True:
                timeout = deadline - asyncio.get_running_loop().time()
                if timeout <= 0:
                    raise BrowserServerUnavailable("browser server launch timed out")
                line = await asyncio.wait_for(process.stdout.readline(), timeout)
                if not line:
                    raise BrowserServerUnavailable(
                        "browser server exited: %s" % " | ".join(output[-5:])
                    )
                text = _ANSI_RE.sub("", line.decode(errors="replace")).strip()
                if text:
                    output.append(text)
                    logger.debug("slot %s: %s", slot.index, text)
                match = _WS_RE.search(text)
                if match:
                    slot.endpoint = match.group(1)
                    slot.platform = profile.platform
                    logger.info(
                        "browser server slot %s ready on port %s (%s)",
                        slot.index,
                        slot.port,
                        profile.platform.value,
                    )
                    return
        finally:
            Path(config_path).unlink(missing_ok=True)

    async def _terminate(self, slot: ServerSlot) -> None:
        process, slot.process, slot.endpoint = slot.process, None, None
        if not process or process.returncode is not None:
            return
        try:
            if os.name == "nt":
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.wait()
            else:
                os.killpg(process.pid, signal.SIGTERM)
            await asyncio.wait_for(process.wait(), 5)
        except (ProcessLookupError, asyncio.TimeoutError):
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def _replace(self, slot: ServerSlot, profile: PlatformProfile) -> None:
        async with slot.lock:
            had_process = slot.process is not None
            previous_profile = PROFILES[slot.platform]
            await self._terminate(slot)
            try:
                await self._launch(slot, profile)
            except BaseException:
                await self._terminate(slot)
                if had_process and previous_profile.platform is not profile.platform:
                    try:
                        await self._launch(slot, previous_profile)
                    except BaseException:
                        await self._terminate(slot)
                        logger.exception(
                            "failed to restore browser server slot %s", slot.index
                        )
                raise
            if had_process:
                slot.restarts += 1

    def _public_endpoint(self, slot: ServerSlot) -> str:
        if not slot.endpoint:
            raise BrowserServerUnavailable("browser server is not ready")
        parsed = urlsplit(slot.endpoint)
        return parsed._replace(
            scheme=self.settings.public_scheme,
            netloc="%s:%s" % (
                self.settings.public_host,
                self.settings.public_ws_port,
            ),
            query="",
            fragment="",
        ).geturl()

    async def _proxy_websocket(self, client: ServerConnection) -> None:
        path = client.request.path
        upstream_endpoint = next(
            (
                slot.endpoint
                for slot in self.slots
                if slot.endpoint and urlsplit(slot.endpoint).path == path
            ),
            None,
        )
        if not upstream_endpoint:
            await client.close(code=1008, reason="unknown browser endpoint")
            return

        async with connect(
            upstream_endpoint,
            proxy=None,
            compression=None,
            max_size=None,
        ) as upstream:
            async def forward(source, target) -> None:
                async for message in source:
                    await target.send(message)

            tasks = {
                asyncio.create_task(forward(client, upstream)),
                asyncio.create_task(forward(upstream, client)),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)

    def _lease(self, slot: ServerSlot) -> Lease:
        assert slot.lease_id and slot.expires_at
        profile = PROFILES[slot.platform]
        return Lease(
            lease_id=slot.lease_id,
            ws_endpoint=self._public_endpoint(slot),
            browser_version=slot.browser_version or "unknown",
            platform=slot.platform,
            user_agent=profile.user_agent,
            expires_at=slot.expires_at,
        )

    async def acquire(self, profile: PlatformProfile) -> Lease:
        try:
            slot_index = await asyncio.wait_for(
                self.available.get(), self.settings.acquire_timeout_ms / 1000
            )
        except asyncio.TimeoutError as exc:
            raise PoolBusy("browser server pool is at capacity") from exc
        slot = self.slots[slot_index]
        try:
            if (
                not slot.process
                or slot.process.returncode is not None
                or slot.platform is not profile.platform
            ):
                await self._replace(slot, profile)
            slot.lease_id = uuid.uuid4().hex
            slot.expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.settings.lease_ttl_seconds)
            self.leases[slot.lease_id] = slot
            return self._lease(slot)
        except BaseException:
            self.available.put_nowait(slot_index)
            raise

    def get(self, lease_id: str) -> Lease:
        slot = self.leases.get(lease_id)
        if not slot or slot.lease_id != lease_id:
            raise LeaseNotFound(lease_id)
        return self._lease(slot)

    def renew(self, lease_id: str) -> Lease:
        slot = self.leases.get(lease_id)
        if not slot or slot.lease_id != lease_id:
            raise LeaseNotFound(lease_id)
        slot.expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.settings.lease_ttl_seconds)
        return self._lease(slot)

    async def release(self, lease_id: str) -> None:
        slot = self.leases.pop(lease_id, None)
        if not slot or slot.lease_id != lease_id:
            raise LeaseNotFound(lease_id)
        slot.lease_id = None
        slot.expires_at = None
        try:
            profile = PROFILES[slot.platform]
            await self._terminate(slot)
            async with self.warm_lock:
                if self._idle_running_count() < self.settings.warm_pool_size:
                    await self._launch(slot, profile)
                    slot.restarts += 1
        except Exception:
            logger.exception("failed to recycle browser server slot %s", slot.index)
        finally:
            self.available.put_nowait(slot.index)

    async def _reap_expired(self) -> None:
        while self.started:
            await asyncio.sleep(1)
            now = datetime.now(timezone.utc)
            expired = [
                lease_id
                for lease_id, slot in self.leases.items()
                if slot.expires_at and slot.expires_at <= now
            ]
            if expired:
                await asyncio.gather(*(self.release(lease_id) for lease_id in expired), return_exceptions=True)

    def stats(self) -> Dict[str, Any]:
        connected = sum(
            1
            for slot in self.slots
            if slot.process and slot.process.returncode is None and slot.endpoint
        )
        return {
            "started": self.started,
            "instance_id": self.settings.instance_id,
            "servers": len(self.slots),
            "connected_servers": connected,
            "warm_servers": self._idle_running_count(),
            "leased": len(self.leases),
            "available": self.available.qsize(),
            "starting_servers": max(
                0, len(self.slots) - self.available.qsize() - len(self.leases)
            ),
            "warm_pool_size": self.settings.warm_pool_size,
            "base_port": self.settings.base_port,
            "restarts": sum(slot.restarts for slot in self.slots),
        }

    def _idle_running_count(self) -> int:
        return sum(
            1
            for slot in self.slots
            if not slot.lease_id
            and slot.process
            and slot.process.returncode is None
            and slot.endpoint
        )
