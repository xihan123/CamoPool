import os
import socket
from dataclasses import dataclass
from typing import Dict, Optional, Union


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, minimum: int = 1) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError("%s must be >= %s" % (name, minimum))
    return value


@dataclass(frozen=True)
class Settings:
    pool_size: int
    warm_pool_size: int
    base_port: int
    worker_host: str
    public_host: str
    public_scheme: str
    ws_host: str
    ws_port: int
    public_ws_port: int
    ws_path_prefix: str
    acquire_timeout_ms: int
    launch_timeout_ms: int
    lease_ttl_seconds: int
    headless: Union[bool, str]
    geoip: bool
    browser_os: str
    camoufox_browser: str
    api_key: Optional[str]
    proxy_server: Optional[str]
    proxy_username: Optional[str]
    proxy_password: Optional[str]
    host: str
    port: int
    instance_id: str

    @property
    def launch_proxy(self) -> Optional[Dict[str, str]]:
        if not self.proxy_server:
            return None
        return {
            key: value
            for key, value in {
                "server": self.proxy_server,
                "username": self.proxy_username,
                "password": self.proxy_password,
            }.items()
            if value
        }

    @classmethod
    def from_env(cls) -> "Settings":
        pool_size = _int("CAMOPOOL_POOL_SIZE", 2)
        warm_pool_size = _int("CAMOPOOL_WARM_POOL_SIZE", 0, 0)
        if warm_pool_size > pool_size:
            raise ValueError("CAMOPOOL_WARM_POOL_SIZE must be <= CAMOPOOL_POOL_SIZE")
        base_port = _int("CAMOPOOL_BASE_PORT", 53_400, 1024)
        if base_port + pool_size - 1 > 65_535:
            raise ValueError("BrowserServer port range exceeds 65535")
        ws_port = _int("CAMOPOOL_WS_PORT", 53_333, 1024)
        if base_port <= ws_port < base_port + pool_size:
            raise ValueError("CAMOPOOL_WS_PORT overlaps BrowserServer port range")
        headless_value = os.getenv("CAMOPOOL_HEADLESS", "true").strip().lower()
        headless: Union[bool, str] = (
            "virtual" if headless_value == "virtual" else headless_value in {"1", "true", "yes", "on"}
        )
        browser_os = os.getenv("CAMOPOOL_BROWSER_OS", "linux").strip().lower()
        if browser_os not in {"windows", "macos", "linux"}:
            raise ValueError("CAMOPOOL_BROWSER_OS must be windows, macos, or linux")
        public_scheme = os.getenv("CAMOPOOL_PUBLIC_SCHEME", "ws").strip().lower()
        if public_scheme not in {"ws", "wss"}:
            raise ValueError("CAMOPOOL_PUBLIC_SCHEME must be ws or wss")
        ws_path_prefix = os.getenv("CAMOPOOL_WS_PATH_PREFIX", "").strip().rstrip("/")
        if ws_path_prefix and not ws_path_prefix.startswith("/"):
            raise ValueError("CAMOPOOL_WS_PATH_PREFIX must start with /")
        return cls(
            pool_size=pool_size,
            warm_pool_size=warm_pool_size,
            base_port=base_port,
            worker_host=os.getenv("CAMOPOOL_WORKER_HOST", "127.0.0.1"),
            public_host=os.getenv("CAMOPOOL_PUBLIC_HOST", "localhost"),
            public_scheme=public_scheme,
            ws_host=os.getenv("CAMOPOOL_WS_HOST", "0.0.0.0"),
            ws_port=ws_port,
            public_ws_port=_int("CAMOPOOL_PUBLIC_WS_PORT", ws_port, 1),
            ws_path_prefix=ws_path_prefix,
            acquire_timeout_ms=_int("CAMOPOOL_ACQUIRE_TIMEOUT_MS", 10_000),
            launch_timeout_ms=_int("CAMOPOOL_LAUNCH_TIMEOUT_MS", 30_000),
            lease_ttl_seconds=_int("CAMOPOOL_LEASE_TTL_SECONDS", 300),
            headless=headless,
            geoip=_bool("CAMOPOOL_GEOIP", True),
            browser_os=browser_os,
            camoufox_browser=os.getenv(
                "CAMOPOOL_CAMOUFOX_BROWSER", "jwriter20/150.0.2-beta.25"
            ),
            api_key=os.getenv("CAMOPOOL_API_KEY") or None,
            proxy_server=os.getenv("CAMOPOOL_PROXY_SERVER") or None,
            proxy_username=os.getenv("CAMOPOOL_PROXY_USERNAME") or None,
            proxy_password=os.getenv("CAMOPOOL_PROXY_PASSWORD") or None,
            host=os.getenv("CAMOPOOL_HOST", "0.0.0.0"),
            port=_int("CAMOPOOL_PORT", 8000),
            instance_id=os.getenv("CAMOPOOL_INSTANCE_ID") or socket.gethostname(),
        )
