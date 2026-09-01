import asyncio
import os
import sys
import tempfile
import types
import time
import unittest
from unittest.mock import AsyncMock, patch

from license_server import login_service


class _SlowClient:
    async def connect(self):
        await asyncio.sleep(1)

    async def disconnect(self):
        return None


class _TdataSlowClient:
    disconnected = False

    def __init__(self, *args, **kwargs):
        self.session = types.SimpleNamespace(
            dc_id=2,
            server_address="127.0.0.1",
            port=443,
        )

    async def connect(self):
        await asyncio.sleep(1)

    async def disconnect(self):
        self.disconnected = True


class _TdataCandidateClient:
    attempts = []
    timeouts = []
    dead = set()

    def __init__(self, *args, **kwargs):
        self.proxy = kwargs["proxy"]
        type(self).timeouts.append(kwargs["timeout"])
        self.session = types.SimpleNamespace(
            dc_id=2,
            server_address="127.0.0.1",
            port=443,
        )
        type(self).attempts.append(self.proxy)

    async def connect(self):
        if self.proxy in type(self).dead:
            raise OSError("proxy unavailable")

    async def disconnect(self):
        return None

    async def ToTDesktop(self, flag):
        class _TDesktop:
            def SaveTData(self, directory):
                with open(f"{directory}/key_data", "wb") as output:
                    output.write(b"tdata")

        return _TDesktop()


class _FailingClient:
    async def connect(self):
        raise OSError("proxy unavailable")

    async def disconnect(self):
        return None


class _SlowFailingClient:
    async def connect(self):
        await asyncio.sleep(0.15)
        raise OSError("proxy unavailable")

    async def disconnect(self):
        return None


class _FakeSession:
    def __init__(self, dc_id, server_address, port=443):
        self.dc_id = dc_id
        self.server_address = server_address
        self.port = port

    def set_dc(self, dc_id, server_address, port):
        self.dc_id = dc_id
        self.server_address = server_address
        self.port = port


class LoginServiceTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        login_service._proxy_health.clear()
        login_service._proxy_last_success.clear()

    def test_cooling_proxy_is_skipped_but_direct_remains(self):
        login_service._proxy_health["bad"] = {
            "failures": login_service._PROXY_FAILURE_THRESHOLD,
            "cooldown_until": time.monotonic() + 60,
            "last_failure_at": time.monotonic(),
        }

        candidates = login_service._filter_cooled_candidates(
            login_service._build_candidates("bad", pool=["alternate"]))

        self.assertEqual(candidates, ["alternate", ""])

        login_service._proxy_health["alternate"] = {
            "failures": login_service._PROXY_FAILURE_THRESHOLD,
            "cooldown_until": time.monotonic() + 60,
            "last_failure_at": time.monotonic(),
        }
        self.assertEqual(
            login_service._filter_cooled_candidates(
                login_service._build_candidates("bad", pool=["alternate"])),
            ["bad", "alternate", ""],
        )

    def test_proxy_configuration_excludes_direct_candidate(self):
        self.assertEqual(
            login_service._build_candidates(
                "bound", pool=["alternate"], include_direct=False),
            ["bound", "alternate"],
        )
        self.assertEqual(
            login_service._build_candidates(
                "", pool=None, include_direct=True),
            [""],
        )

    def test_proxy_failure_cooling_threshold_and_decay(self):
        for _ in range(login_service._PROXY_FAILURE_THRESHOLD - 1):
            login_service._record_proxy_failure("bad")
        self.assertFalse(login_service._proxy_is_cooling("bad"))
        login_service._record_proxy_failure("bad")
        self.assertTrue(login_service._proxy_is_cooling("bad"))

        login_service._proxy_health["bad"]["last_failure_at"] = (
            time.monotonic() - login_service._PROXY_FAILURE_DECAY_WINDOW - 1
        )
        self.assertFalse(login_service._proxy_is_cooling("bad"))
        self.assertNotIn("bad", login_service._proxy_health)

    def test_request_budget_uses_reported_timeout(self):
        self.assertEqual(login_service._request_budget(90), (85, 12))
        self.assertEqual(login_service._request_budget(185), (180, 12))
        self.assertEqual(login_service._request_budget(300), (180, 12))

    def test_build_candidates_pool_cap(self):
        pool = [f"proxy{i}" for i in range(10)]

        self.assertEqual(
            login_service._build_candidates(
                "bound", pool=pool, include_direct=False),
            ["bound"] + pool[:login_service._MAX_POOL_CANDIDATES],
        )
        self.assertEqual(
            login_service._build_candidates(
                "bound", pool=pool, include_direct=False,
                pool_cap=login_service._SEND_CODE_MAX_POOL_CANDIDATES),
            ["bound"] + pool,
        )

    def test_build_candidates_prefers_recently_successful_pool_proxy(self):
        pool = ["a", "b", "c"]
        login_service._clear_proxy_failure("c")

        self.assertEqual(
            login_service._build_candidates(
                pool=pool, include_direct=False, pool_cap=2,
                prefer_healthy=True),
            ["c", "a"],
        )

    def test_build_candidates_default_pool_order_is_unchanged(self):
        pool = ["a", "b", "c"]
        login_service._clear_proxy_failure("c")

        self.assertEqual(
            login_service._build_candidates(
                pool=pool, include_direct=False, pool_cap=2),
            ["a", "b"],
        )

    def test_build_candidates_puts_failed_proxy_after_untested_proxy(self):
        login_service._record_proxy_failure("a")

        self.assertEqual(
            login_service._build_candidates(
                pool=["a", "b"], include_direct=False,
                pool_cap=2, prefer_healthy=True),
            ["b", "a"],
        )

    def test_proxy_health_rank_discards_stale_success(self):
        login_service._proxy_last_success["stale"] = (
            time.monotonic() - login_service._PROXY_SUCCESS_WINDOW - 1
        )

        self.assertEqual(login_service._proxy_health_rank("stale"), 1)
        self.assertNotIn("stale", login_service._proxy_last_success)

    def test_request_budget_respects_small_reported_timeout(self):
        budget, _ = login_service._request_budget(12)

        self.assertGreater(budget, 0)
        self.assertLessEqual(budget, 7)

    def test_request_budget_falls_back_for_missing_or_dirty_timeout(self):
        for client_timeout in (None, 0, -1, "90", 9999):
            with self.subTest(client_timeout=client_timeout):
                self.assertEqual(
                    login_service._request_budget(client_timeout),
                    (22, 6),
                )

    def test_request_budget_leaves_time_for_direct(self):
        for client_timeout in (None, 30, 60, 90, 120):
            with self.subTest(client_timeout=client_timeout):
                budget, direct_reserve = login_service._request_budget(
                    client_timeout)
                proxy_budget = budget - direct_reserve

                # At least one proxy can be tried while preserving direct
                # fallback time.
                self.assertGreaterEqual(
                    proxy_budget, login_service._CONNECT_TIMEOUT + 2)

    async def test_send_code_budget_returns_failure(self):
        with patch.object(login_service, "_request_budget",
                          return_value=(0.05, 0.02)), \
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
        self.assertIn("所有代理均不可用", result["message"])
        self.assertIn("裸 IP", result["message"])
        self.assertTrue(result["proxy_unavailable"])

    async def test_send_code_reports_out_of_range_api_id(self):
        with patch.object(
            login_service,
            "cleanup_expired",
            new_callable=AsyncMock,
        ), patch.object(login_service, "_request_budget") as request_budget:
            result = await login_service.send_code(
                phone="+123",
                api_id=8_991_561_850,
                api_hash="hash",
            )

        self.assertFalse(result["success"])
        self.assertTrue(result["api_id_invalid"])
        self.assertEqual(result["api_id"], 8_991_561_850)
        self.assertIn("8991561850", result["message"])
        self.assertIn(str(login_service._MAX_API_ID), result["message"])
        request_budget.assert_not_called()

    async def test_tdata_conversion_timeout_returns_none(self):
        fake_td = types.ModuleType("opentele.td")
        fake_td.TDesktop = object
        fake_tl = types.ModuleType("opentele.tl")
        fake_tl.TelegramClient = _TdataSlowClient
        fake_api = types.ModuleType("opentele.api")
        fake_api.UseCurrentSession = object()
        fake_root = types.ModuleType("opentele")
        fake_root.__path__ = []

        with patch.dict(
            sys.modules,
            {
                "opentele": fake_root,
                "opentele.td": fake_td,
                "opentele.tl": fake_tl,
                "opentele.api": fake_api,
            },
        ), patch.object(
            login_service,
            "get_session_file_path",
            return_value="/tmp/test-tdata.session",
        ), patch.object(
            login_service,
            "_TDATA_TOTAL_TIMEOUT",
            0.05,
        ):
            result = await login_service.get_tdata_zip_path(
                "session", "+123", "")

        self.assertIsNone(result)

    async def test_tdata_conversion_uses_login_connect_timeout(self):
        fake_td = types.ModuleType("opentele.td")
        fake_td.TDesktop = object
        fake_tl = types.ModuleType("opentele.tl")
        fake_tl.TelegramClient = _TdataCandidateClient
        fake_api = types.ModuleType("opentele.api")
        fake_api.UseCurrentSession = object()
        fake_root = types.ModuleType("opentele")
        fake_root.__path__ = []

        _TdataCandidateClient.attempts = []
        _TdataCandidateClient.timeouts = []
        _TdataCandidateClient.dead = set()
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = f"{temp_dir}/tdata.session"
            with open(session_path, "w", encoding="utf-8"):
                pass
            with patch.dict(
                sys.modules,
                {
                    "opentele": fake_root,
                    "opentele.td": fake_td,
                    "opentele.tl": fake_tl,
                    "opentele.api": fake_api,
                },
            ), patch.object(
                login_service,
                "get_session_file_path",
                return_value=session_path,
            ), patch.object(
                login_service,
                "_SESSIONS_DIR",
                temp_dir,
            ):
                result = await login_service.get_tdata_zip_path(
                    "session", "+123", "")

        self.assertIsNotNone(result)
        self.assertEqual(
            _TdataCandidateClient.timeouts,
            [login_service._LOGIN_CONNECT_TIMEOUT],
        )

    async def test_tdata_conversion_falls_back_to_pool_proxy(self):
        dead = "socks5://dead.example:1080"
        alive = "socks5://alive.example:1080"
        fake_td = types.ModuleType("opentele.td")
        fake_td.TDesktop = object
        fake_tl = types.ModuleType("opentele.tl")
        fake_tl.TelegramClient = _TdataCandidateClient
        fake_api = types.ModuleType("opentele.api")
        fake_api.UseCurrentSession = object()
        fake_root = types.ModuleType("opentele")
        fake_root.__path__ = []

        _TdataCandidateClient.attempts = []
        _TdataCandidateClient.dead = {dead}
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = f"{temp_dir}/tdata.session"
            with open(session_path, "w", encoding="utf-8"):
                pass
            with patch.dict(
                sys.modules,
                {
                    "opentele": fake_root,
                    "opentele.td": fake_td,
                    "opentele.tl": fake_tl,
                    "opentele.api": fake_api,
                },
            ), patch.object(
                login_service,
                "get_session_file_path",
                return_value=session_path,
            ), patch.object(
                login_service,
                "_load_session_proxy",
                return_value=dead,
            ), patch.object(
                login_service,
                "_parse_proxy",
                side_effect=lambda value: value or None,
            ), patch.object(
                login_service,
                "_SESSIONS_DIR",
                temp_dir,
            ):
                result = await login_service.get_tdata_zip_path(
                    "session", "+123", "", proxy_pool=[alive])

            self.assertIsNotNone(result)
            self.assertTrue(result.endswith("_tdata.zip"))
            self.assertTrue(os.path.exists(result))

        self.assertEqual(_TdataCandidateClient.attempts, [dead, alive])

    async def test_tdata_proxy_session_never_falls_back_to_direct(self):
        proxy = "socks5://user-abc:hunter2@dc.oxylabs.io:8823"
        alternate = "socks5://backup.example:1080"
        fake_td = types.ModuleType("opentele.td")
        fake_td.TDesktop = object
        fake_tl = types.ModuleType("opentele.tl")
        fake_tl.TelegramClient = _TdataCandidateClient
        fake_api = types.ModuleType("opentele.api")
        fake_api.UseCurrentSession = object()
        fake_root = types.ModuleType("opentele")
        fake_root.__path__ = []

        _TdataCandidateClient.attempts = []
        _TdataCandidateClient.dead = {proxy, alternate}
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = f"{temp_dir}/tdata.session"
            with open(session_path, "w", encoding="utf-8"):
                pass
            with patch.dict(
                sys.modules,
                {
                    "opentele": fake_root,
                    "opentele.td": fake_td,
                    "opentele.tl": fake_tl,
                    "opentele.api": fake_api,
                },
            ), patch.object(
                login_service,
                "get_session_file_path",
                return_value=session_path,
            ), patch.object(
                login_service,
                "_load_session_proxy",
                return_value=proxy,
            ), patch.object(
                login_service,
                "_parse_proxy",
                side_effect=lambda value: value or None,
            ), patch.object(
                login_service,
                "_SESSIONS_DIR",
                temp_dir,
            ), self.assertLogs(login_service.logger, "WARNING") as logs:
                result = await login_service.get_tdata_zip_path(
                    "session", "+123", "", proxy=proxy,
                    proxy_pool=[alternate])

        self.assertIsNone(result)
        self.assertNotIn("", _TdataCandidateClient.attempts)
        logged = "\n".join(logs.output)
        self.assertIn("+123", logged)
        self.assertIn("dc.oxylabs.io:8823", logged)
        self.assertNotIn("hunter2", logged)

    async def test_tdata_conversion_uses_persisted_pool_for_old_client(self):
        dead = "socks5://dead.example:1080"
        alive = "socks5://alive.example:1080"
        fake_td = types.ModuleType("opentele.td")
        fake_td.TDesktop = object
        fake_tl = types.ModuleType("opentele.tl")
        fake_tl.TelegramClient = _TdataCandidateClient
        fake_api = types.ModuleType("opentele.api")
        fake_api.UseCurrentSession = object()
        fake_root = types.ModuleType("opentele")
        fake_root.__path__ = []

        _TdataCandidateClient.attempts = []
        _TdataCandidateClient.dead = {dead}
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = f"{temp_dir}/tdata.session"
            base_path = session_path[:-8]
            with open(session_path, "w", encoding="utf-8"):
                pass
            with open(base_path + ".proxy", "w", encoding="utf-8") as f:
                f.write(dead)
            with open(base_path + ".pool", "w", encoding="utf-8") as f:
                f.write(f"{dead}\n{alive}\n")
            with patch.dict(
                sys.modules,
                {
                    "opentele": fake_root,
                    "opentele.td": fake_td,
                    "opentele.tl": fake_tl,
                    "opentele.api": fake_api,
                },
            ), patch.object(
                login_service,
                "get_session_file_path",
                return_value=session_path,
            ), patch.object(
                login_service,
                "_parse_proxy",
                side_effect=lambda value: value or None,
            ), patch.object(
                login_service,
                "_SESSIONS_DIR",
                temp_dir,
            ):
                result = await login_service.get_tdata_zip_path(
                    "session", "+123", "", proxy="", proxy_pool=None)

            self.assertIsNotNone(result)
            self.assertTrue(result.endswith("_tdata.zip"))
            self.assertTrue(os.path.exists(result))

        self.assertEqual(_TdataCandidateClient.attempts, [dead, alive])

    async def test_tdata_request_pool_takes_precedence_over_persisted_pool(self):
        bound = "socks5://bound.example:1080"
        file_proxy = "socks5://file.example:1080"
        request_dead = "socks5://request-dead.example:1080"
        request_alive = "socks5://request-alive.example:1080"
        fake_td = types.ModuleType("opentele.td")
        fake_td.TDesktop = object
        fake_tl = types.ModuleType("opentele.tl")
        fake_tl.TelegramClient = _TdataCandidateClient
        fake_api = types.ModuleType("opentele.api")
        fake_api.UseCurrentSession = object()
        fake_root = types.ModuleType("opentele")
        fake_root.__path__ = []

        _TdataCandidateClient.attempts = []
        _TdataCandidateClient.dead = {bound, request_dead}
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = f"{temp_dir}/tdata.session"
            base_path = session_path[:-8]
            with open(session_path, "w", encoding="utf-8"):
                pass
            with open(base_path + ".proxy", "w", encoding="utf-8") as f:
                f.write(bound)
            with open(base_path + ".pool", "w", encoding="utf-8") as f:
                f.write(f"{file_proxy}\n")
            with patch.dict(
                sys.modules,
                {
                    "opentele": fake_root,
                    "opentele.td": fake_td,
                    "opentele.tl": fake_tl,
                    "opentele.api": fake_api,
                },
            ), patch.object(
                login_service,
                "get_session_file_path",
                return_value=session_path,
            ), patch.object(
                login_service,
                "_parse_proxy",
                side_effect=lambda value: value or None,
            ), patch.object(
                login_service,
                "_SESSIONS_DIR",
                temp_dir,
            ):
                result = await login_service.get_tdata_zip_path(
                    "session", "+123", "",
                    proxy_pool=[request_dead, request_alive])

            self.assertIsNotNone(result)
            self.assertTrue(result.endswith("_tdata.zip"))

        self.assertEqual(
            _TdataCandidateClient.attempts,
            [bound, request_dead, request_alive],
        )
        self.assertNotIn(file_proxy, _TdataCandidateClient.attempts)

    def test_save_session_proxy_pool_latest_nonempty_upload_wins(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = f"{temp_dir}/session"
            pool_path = base_path + ".pool"
            login_service._save_session_proxy_pool(
                base_path, [" first ", "", None, 123, "second "])
            with open(pool_path, encoding="utf-8") as f:
                self.assertEqual(f.read(), "first\nsecond")

            login_service._save_session_proxy_pool(base_path, [])
            with open(pool_path, encoding="utf-8") as f:
                self.assertEqual(f.read(), "first\nsecond")
            login_service._save_session_proxy_pool(base_path, None)
            with open(pool_path, encoding="utf-8") as f:
                self.assertEqual(f.read(), "first\nsecond")

            login_service._save_session_proxy_pool(base_path, [" new "])
            with open(pool_path, encoding="utf-8") as f:
                self.assertEqual(f.read(), "new")

    def test_cleanup_session_files_removes_proxy_pool(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = f"{temp_dir}/session"
            pool_path = base_path + ".pool"
            with open(pool_path, "w", encoding="utf-8") as f:
                f.write("proxy")

            login_service._cleanup_session_files(base_path)

            self.assertFalse(os.path.exists(pool_path))

    async def test_tdata_persisted_proxy_pool_never_falls_back_to_direct(self):
        proxy = "socks5://user-abc:hunter2@dc.oxylabs.io:8823"
        alternate = "socks5://backup.example:1080"
        fake_td = types.ModuleType("opentele.td")
        fake_td.TDesktop = object
        fake_tl = types.ModuleType("opentele.tl")
        fake_tl.TelegramClient = _TdataCandidateClient
        fake_api = types.ModuleType("opentele.api")
        fake_api.UseCurrentSession = object()
        fake_root = types.ModuleType("opentele")
        fake_root.__path__ = []

        _TdataCandidateClient.attempts = []
        _TdataCandidateClient.dead = {proxy, alternate}
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = f"{temp_dir}/tdata.session"
            base_path = session_path[:-8]
            with open(session_path, "w", encoding="utf-8"):
                pass
            with open(base_path + ".proxy", "w", encoding="utf-8") as f:
                f.write(proxy)
            with open(base_path + ".pool", "w", encoding="utf-8") as f:
                f.write(f"{proxy}\n{alternate}\n")
            with patch.dict(
                sys.modules,
                {
                    "opentele": fake_root,
                    "opentele.td": fake_td,
                    "opentele.tl": fake_tl,
                    "opentele.api": fake_api,
                },
            ), patch.object(
                login_service,
                "get_session_file_path",
                return_value=session_path,
            ), patch.object(
                login_service,
                "_parse_proxy",
                side_effect=lambda value: value or None,
            ), patch.object(
                login_service,
                "_SESSIONS_DIR",
                temp_dir,
            ), self.assertLogs(login_service.logger, "WARNING") as logs:
                result = await login_service.get_tdata_zip_path(
                    "session", "+123", "", proxy_pool=None)

        self.assertIsNone(result)
        self.assertNotIn("", _TdataCandidateClient.attempts)
        logged = "\n".join(logs.output)
        self.assertIn("+123", logged)
        self.assertIn("dc.oxylabs.io:8823", logged)
        self.assertNotIn("hunter2", logged)

    async def test_check_session_proxy_session_never_tries_direct(self):
        attempted = []

        def make_client(session, api_id, api_hash, candidate,
                        device_model=None):
            attempted.append(candidate)
            return _FailingClient()

        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = f"{temp_dir}/proxy-session_123.session"
            with open(session_path, "w", encoding="utf-8"):
                pass
            with patch.object(
                login_service,
                "_SESSIONS_DIR",
                temp_dir,
            ), patch.object(
                login_service,
                "_load_session_proxy",
                return_value="socks5://proxy.example:1080",
            ), patch.object(
                login_service,
                "_make_client",
                side_effect=make_client,
            ):
                result = await login_service.check_session_valid(
                    phone="+123",
                    api_id=1,
                    api_hash="hash",
                    proxy="socks5://request.example:1080",
                )

        self.assertFalse(result["success"])
        self.assertTrue(result["inconclusive"])
        self.assertNotIn("", attempted)

    async def test_all_egress_failed_is_logged_without_credentials(self):
        def make_client(session, api_id, api_hash, candidate,
                        device_model=None):
            return _FailingClient()

        proxy = "socks5://user-abc:hunter2@dc.oxylabs.io:8823"
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = f"{temp_dir}/proxy-session_123.session"
            with open(session_path, "w", encoding="utf-8"):
                pass
            with patch.object(
                login_service, "_SESSIONS_DIR", temp_dir,
            ), patch.object(
                login_service, "_load_session_proxy", return_value=proxy,
            ), patch.object(
                login_service, "_make_client", side_effect=make_client,
            ), self.assertLogs(login_service.logger, "WARNING") as logs:
                result = await login_service.check_session_valid(
                    phone="+123", api_id=1, api_hash="hash", proxy=proxy,
                )

        self.assertTrue(result["inconclusive"])
        logged = "\n".join(logs.output)
        self.assertIn("+123", logged)
        self.assertIn("dc.oxylabs.io:8823", logged)
        self.assertNotIn("hunter2", logged)

    def test_login_clients_retry_reconnects_check_session_does_not(self):
        captured = []

        def fake_client(session, **kwargs):
            captured.append(kwargs["connection_retries"])
            return types.SimpleNamespace(
                session=_FakeSession(2, "149.154.167.51"))

        with patch.object(login_service, "TelegramClient", fake_client):
            login_service._make_client("s", 1, "hash", "socks5://p:1080")
            login_service._make_client("s", 1, "hash", "socks5://p:1080",
                                       retries=login_service.
                                       _LOGIN_CONNECT_RETRIES)

        self.assertEqual(
            captured,
            [login_service._CONNECT_RETRIES,
             login_service._LOGIN_CONNECT_RETRIES],
        )
        self.assertGreater(login_service._LOGIN_CONNECT_RETRIES,
                           login_service._CONNECT_RETRIES)

    async def test_check_session_uses_default_connect_timeout(self):
        captured = []

        def fake_client(session, **kwargs):
            captured.append(kwargs)

            class _CheckClient:
                def __init__(self):
                    self.session = _FakeSession(2, "149.154.167.51")

                async def connect(self):
                    return None

                async def get_me(self):
                    return object()

                async def disconnect(self):
                    return None

            return _CheckClient()

        with patch.object(login_service, "TelegramClient", fake_client):
            with tempfile.TemporaryDirectory() as temp_dir:
                session_path = f"{temp_dir}/proxy-session_123.session"
                with open(session_path, "w", encoding="utf-8"):
                    pass
                with patch.object(login_service, "_SESSIONS_DIR", temp_dir), \
                        patch.object(
                            login_service,
                            "_load_session_proxy",
                            return_value="socks5://p:1080",
                        ):
                    result = await login_service.check_session_valid(
                        phone="+123",
                        api_id=1,
                        api_hash="hash",
                        proxy="socks5://p:1080",
                    )

        self.assertTrue(result["success"])
        self.assertEqual(
            captured[0]["timeout"],
            login_service._CONNECT_TIMEOUT,
        )

    def test_ipv6_session_dc_is_remapped_back_to_ipv4(self):
        client = types.SimpleNamespace(
            session=_FakeSession(2, "2001:67c:4e8:f002::a"))

        login_service._remap_session_dc_to_ipv4(client)

        self.assertEqual(client.session.server_address,
                         login_service._DC_IPV4[2])
        self.assertEqual(client.session.dc_id, 2)

    def test_ipv4_session_dc_is_left_untouched(self):
        client = types.SimpleNamespace(
            session=_FakeSession(2, "149.154.167.99"))

        login_service._remap_session_dc_to_ipv4(client)

        self.assertEqual(client.session.server_address, "149.154.167.99")

    async def test_proxy_only_chain_spends_the_whole_budget_on_proxies(self):
        attempted = []

        def make_client(session, api_id, api_hash, candidate,
                        device_model=None):
            attempted.append(candidate)
            return _SlowFailingClient()

        pool = [f"socks5://p{i}.example:1080" for i in range(4)]
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = f"{temp_dir}/proxy-session_123.session"
            with open(session_path, "w", encoding="utf-8"):
                pass
            with patch.object(
                login_service, "_SESSIONS_DIR", temp_dir,
            ), patch.object(
                login_service, "_load_session_proxy",
                return_value="socks5://bound.example:1080",
            ), patch.object(
                login_service, "_request_budget", return_value=(1.0, 0.6),
            ), patch.object(
                login_service, "_MAX_POOL_CANDIDATES", len(pool),
            ), patch.object(
                login_service, "_make_client", side_effect=make_client,
            ):
                await login_service.check_session_valid(
                    phone="+123",
                    api_id=1,
                    api_hash="hash",
                    proxy_pool=pool,
                )

        # With the reserve applied to a proxy-only chain, the last two proxies
        # would have been skipped even though there was budget left.
        self.assertGreaterEqual(len(attempted), 4)

    async def test_check_session_uses_reported_client_timeout(self):
        reported = []

        def budget(client_timeout):
            reported.append(client_timeout)
            return (0.2, 0.1)

        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = f"{temp_dir}/proxy-session_123.session"
            with open(session_path, "w", encoding="utf-8"):
                pass
            with patch.object(
                login_service, "_SESSIONS_DIR", temp_dir,
            ), patch.object(
                login_service, "_load_session_proxy",
                return_value="socks5://bound.example:1080",
            ), patch.object(
                login_service, "_request_budget", side_effect=budget,
            ), patch.object(
                login_service, "_make_client",
                return_value=_SlowFailingClient(),
            ):
                await login_service.check_session_valid(
                    phone="+123",
                    api_id=1,
                    api_hash="hash",
                    client_timeout=90,
                )

        self.assertEqual(reported, [90])
