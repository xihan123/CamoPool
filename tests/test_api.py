import unittest
import subprocess
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import Response

from camopool.api import app, create_app
import camopool
from camopool.config import Settings
from camopool.pool import BrowserServerPool, BrowserServerUnavailable, ServerSlot
from camopool.profiles import PROFILES, Platform
from playwright._impl._driver import compute_driver_executable


def settings():
    return Settings(
        pool_size=2,
        warm_pool_size=0,
        base_port=53400,
        worker_host="127.0.0.1",
        public_host="browser.example.com",
        public_scheme="wss",
        ws_host="0.0.0.0",
        ws_port=53333,
        public_ws_port=443,
        ws_path_prefix="",
        acquire_timeout_ms=1000,
        launch_timeout_ms=30000,
        lease_ttl_seconds=300,
        headless=True,
        geoip=False,
        browser_os="linux",
        camoufox_browser="jwriter20/150.0.2-beta.25",
        api_key=None,
        proxy_server=None,
        proxy_username=None,
        proxy_password=None,
        host="127.0.0.1",
        port=8000,
        instance_id="test-node",
    )


class ApiTests(unittest.TestCase):
    def test_latest_dependency_versions(self):
        self.assertEqual(version("playwright"), "1.61.0")
        self.assertTrue(version("camoufox"))

    def test_node_compatibility_hook_matches_playwright_bundle(self):
        node, cli = compute_driver_executable()
        hook = Path(camopool.__file__).with_name("playwright_compat.cjs")
        result = subprocess.run(
            [node, "-r", str(hook), "-e", "require('./lib/coreBundle')"],
            cwd=Path(cli).parent,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_settings_reject_port_range_overflow(self):
        with patch.dict(
            "os.environ",
            {"CAMOPOOL_POOL_SIZE": "10", "CAMOPOOL_BASE_PORT": "65530"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "65535"):
                Settings.from_env()

    def test_settings_reject_ws_port_overlap(self):
        with patch.dict(
            "os.environ",
            {"CAMOPOOL_BASE_PORT": "53333", "CAMOPOOL_WS_PORT": "53333"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "overlaps"):
                Settings.from_env()

    def test_settings_reject_warm_pool_larger_than_capacity(self):
        with patch.dict(
            "os.environ",
            {"CAMOPOOL_POOL_SIZE": "2", "CAMOPOOL_WARM_POOL_SIZE": "3"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "WARM_POOL_SIZE"):
                Settings.from_env()

    def test_openapi_documents_lease_lifecycle_and_api_key(self):
        schema = app.openapi()
        self.assertIn("/v1/leases", schema["paths"])
        self.assertIn("/v1/leases/{lease_id}/renew", schema["paths"])
        self.assertEqual(
            schema["components"]["securitySchemes"]["APIKeyHeader"]["name"],
            "X-API-Key",
        )

    def test_public_endpoint_rewrites_host_scheme_and_keeps_secret_path(self):
        pool = BrowserServerPool(settings())
        slot = ServerSlot(index=0, port=53400, endpoint="ws://127.0.0.1:53400/secret-path")
        self.assertEqual(
            pool._public_endpoint(slot),
            "wss://browser.example.com:443/secret-path",
        )

    def test_public_endpoint_adds_configured_ws_path_prefix(self):
        configured = settings()
        object.__setattr__(configured, "ws_path_prefix", "/ws")
        pool = BrowserServerPool(configured)
        slot = ServerSlot(index=0, port=53400, endpoint="ws://127.0.0.1:53400/secret-path")
        self.assertEqual(
            pool._public_endpoint(slot),
            "wss://browser.example.com:443/ws/secret-path",
        )

    def test_settings_normalizes_ws_path_prefix(self):
        with patch.dict("os.environ", {"CAMOPOOL_WS_PATH_PREFIX": "/ws/"}, clear=True):
            self.assertEqual(Settings.from_env().ws_path_prefix, "/ws")

    def test_empty_public_ws_port_omits_port_from_endpoint(self):
        with patch.dict("os.environ", {"CAMOPOOL_PUBLIC_WS_PORT": ""}, clear=True):
            configured = Settings.from_env()
        object.__setattr__(configured, "public_host", "camopool.xihan.website")
        object.__setattr__(configured, "ws_path_prefix", "/ws")
        pool = BrowserServerPool(configured)
        slot = ServerSlot(index=0, port=53400, endpoint="ws://127.0.0.1:53400/secret-path")
        self.assertEqual(
            pool._public_endpoint(slot),
            "ws://camopool.xihan.website/ws/secret-path",
        )

    def test_ws_path_prefix_is_removed_before_upstream_lookup(self):
        configured = settings()
        object.__setattr__(configured, "ws_path_prefix", "/ws")
        pool = BrowserServerPool(configured)
        pool.slots[0].endpoint = "ws://127.0.0.1:53400/secret-path"
        self.assertEqual(
            pool._upstream_endpoint("/ws/secret-path"),
            pool.slots[0].endpoint,
        )
        self.assertIsNone(pool._upstream_endpoint("/secret-path"))


class PoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_ready_stays_healthy_when_warm_slots_are_leased(self):
        configured = settings()
        object.__setattr__(configured, "warm_pool_size", 1)
        with patch("camopool.api.BrowserServerPool") as pool_type:
            pool_type.return_value.stats.return_value = {
                "started": True,
                "warm_servers": 0,
            }
            application = create_app(configured)

        route = next(
            route for route in application.routes if route.path == "/health/ready"
        )
        response = Response()
        await route.endpoint(response)

        self.assertEqual(response.status_code, 200)

    async def test_start_only_launches_configured_warm_slots(self):
        configured = settings()
        object.__setattr__(configured, "warm_pool_size", 1)
        pool = BrowserServerPool(configured)
        pool._replace = AsyncMock()
        pool._terminate = AsyncMock()
        ws_server = unittest.mock.MagicMock()
        ws_server.wait_closed = AsyncMock()

        with patch("camopool.pool.serve", AsyncMock(return_value=ws_server)):
            await pool.start()
            self.assertEqual(pool._replace.await_count, 1)
            self.assertEqual(pool.available.qsize(), configured.pool_size)
            await pool.stop()

    async def test_pool_can_restart_without_stale_capacity(self):
        pool = BrowserServerPool(settings())
        pool._terminate = AsyncMock()
        servers = [unittest.mock.MagicMock(), unittest.mock.MagicMock()]
        for server in servers:
            server.wait_closed = AsyncMock()

        with patch("camopool.pool.serve", AsyncMock(side_effect=servers)):
            await pool.start()
            pool.slots[0].lease_id = "stale"
            pool.slots[0].expires_at = datetime.now(timezone.utc)
            await pool.stop()
            await pool.start()

            self.assertEqual(pool.available.qsize(), pool.settings.pool_size)
            self.assertTrue(all(slot.lease_id is None for slot in pool.slots))
            await pool.stop()

    async def test_release_stops_slot_when_warm_floor_is_satisfied(self):
        pool = BrowserServerPool(settings())
        slot = pool.slots[0]
        slot.lease_id = "lease"
        slot.expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
        pool.leases["lease"] = slot

        async def terminate(target):
            target.process = None
            target.endpoint = None

        pool._terminate = AsyncMock(side_effect=terminate)
        pool._launch = AsyncMock()
        await pool.release("lease")

        pool._launch.assert_not_awaited()
        self.assertEqual(pool.available.qsize(), 1)

    async def test_release_rewarms_slot_to_configured_floor(self):
        configured = settings()
        object.__setattr__(configured, "warm_pool_size", 1)
        pool = BrowserServerPool(configured)
        slot = pool.slots[0]
        slot.lease_id = "lease"
        slot.expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
        pool.leases["lease"] = slot

        async def terminate(target):
            target.process = None
            target.endpoint = None

        pool._terminate = AsyncMock(side_effect=terminate)
        pool._launch = AsyncMock()
        await pool.release("lease")

        pool._launch.assert_awaited_once_with(slot, PROFILES[Platform.GENERIC])

    async def test_browserforge_failure_is_service_unavailable(self):
        pool = BrowserServerPool(settings())
        slot = pool.slots[0]
        error = ValueError("No WebGL data found")
        with patch("camopool.pool.launch_options", side_effect=[error] * 6):
            with self.assertRaisesRegex(
                BrowserServerUnavailable, "compatible browser fingerprint"
            ):
                await pool._launch(slot, PROFILES[Platform.GENERIC])

    async def test_failed_platform_switch_restores_previous_server(self):
        pool = BrowserServerPool(settings())
        slot = pool.slots[0]
        slot.process = object()
        pool._terminate = AsyncMock()
        pool._launch = AsyncMock(
            side_effect=[RuntimeError("launch failed"), None]
        )

        with self.assertRaisesRegex(RuntimeError, "launch failed"):
            await pool._replace(slot, PROFILES[Platform.DOUYIN])

        self.assertEqual(
            [call.args[1].platform for call in pool._launch.await_args_list],
            [Platform.DOUYIN, Platform.GENERIC],
        )


if __name__ == "__main__":
    unittest.main()
