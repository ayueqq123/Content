import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch

from license_server import login_service


class _SlowClient:
    async def connect(self):
        await asyncio.sleep(1)

    async def disconnect(self):
        return None


class LoginServiceTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        login_service._proxy_health.clear()

    def test_cooling_proxy_is_skipped_but_direct_remains(self):
        login_service._proxy_health["bad"] = {
            "failures": login_service._PROXY_FAILURE_THRESHOLD,
            "cooldown_until": time.monotonic() + 60,
        }

        candidates = login_service._filter_cooled_candidates(
            login_service._build_candidates("bad", pool=["alternate"]))

        self.assertEqual(candidates, ["alternate", ""])

        login_service._proxy_health["alternate"] = {
            "failures": login_service._PROXY_FAILURE_THRESHOLD,
            "cooldown_until": time.monotonic() + 60,
        }
        self.assertEqual(
            login_service._filter_cooled_candidates(
                login_service._build_candidates("bad", pool=["alternate"])),
            [""],
        )

    def test_send_code_budget_leaves_time_for_direct(self):
        proxy_budget = (
            login_service._SEND_CODE_TOTAL_BUDGET
            - login_service._SEND_CODE_DIRECT_RESERVE
        )

        # At least one proxy can be tried without consuming the direct reserve.
        self.assertGreaterEqual(
            proxy_budget, login_service._CONNECT_TIMEOUT + 2)

    async def test_send_code_budget_returns_failure(self):
        with patch.object(login_service, "_SEND_CODE_TOTAL_BUDGET", 0.05), \
                patch.object(login_service, "_SEND_CODE_DIRECT_RESERVE", 0.02), \
                patch.object(login_service, "_CONNECT_TIMEOUT", 0.01), \
                patch.object(login_service, "_make_client",
                             return_value=_SlowClient()), \
                patch.object(login_service, "cleanup_expired",
                             new_callable=AsyncMock), \
                patch.object(login_service, "_session_path",
                             return_value="/tmp/test-send-code"), \
                patch.object(login_service, "_cleanup_session_files"):
            result = await login_service.send_code(
                phone="+123",
                api_id=1,
                api_hash="hash",
                proxy="bad",
                proxy_pool=["alternate"],
            )

        self.assertFalse(result["success"])
        self.assertIn("代理连接超时", result["message"])
