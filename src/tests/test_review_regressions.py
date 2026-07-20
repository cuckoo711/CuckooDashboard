"""本轮全项目逻辑审查所修复缺陷的回归测试。

覆盖：NUG 重登策略、NFK token TTL/401 重登/TLS 判定、GitHub 空结果缓存、
歌词负缓存、调度器单飞窗口。
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest


# ── NUG：仅会话失效才重登 ──


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class TestNugRetryPolicy:
    def _client(self, **kwargs):
        from providers.nug.client import NUGClient

        return NUGClient("http://nug.example", "user", "pass", session_cookies={"session": "abc"}, **kwargs)

    def test_500_does_not_relogin(self):
        client = self._client()
        calls = {"login": 0}

        def fake_do(method, url, **kwargs):
            return _FakeResponse(500)

        def fake_login():
            calls["login"] += 1
            return True

        with patch.object(client, "_do_request", side_effect=fake_do), \
                patch.object(client, "_login", side_effect=fake_login):
            assert client._request_with_retry("get", "http://nug.example/api/x") is None
        assert calls["login"] == 0

    def test_401_relogins_and_retries(self):
        client = self._client()
        responses = [_FakeResponse(401), _FakeResponse(200)]
        calls = {"login": 0}

        def fake_do(method, url, **kwargs):
            return responses.pop(0)

        def fake_login():
            calls["login"] += 1
            client._logged_in = True
            return True

        with patch.object(client, "_do_request", side_effect=fake_do), \
                patch.object(client, "_login", side_effect=fake_login):
            resp = client._request_with_retry("get", "http://nug.example/api/x")
        assert resp is not None and resp.status_code == 200
        assert calls["login"] == 1

    def test_persist_skipped_when_cookies_unchanged(self):
        updates = []
        client = self._client(on_session_update=updates.append)
        client._persist_session()
        assert updates == []


# ── NFK：token TTL / 401 重登 / TLS 判定 ──


class TestNfkClient:
    def _client(self):
        from providers.nfk.client import LocalMimoAPI

        return LocalMimoAPI("http://192.168.1.10:7778", "user", "pass", account_id="acc")

    def test_cached_token_keeps_vault_issue_time(self):
        client = self._client()
        issued_at = time.time() - 4.9 * 86400
        with patch("providers.nfk.client.load_cached_token", return_value=("jwt", issued_at)):
            assert client._ensure_token() is True
        assert client._token_ts == issued_at
        # 内存 TTL 与 Vault 一致：再过 0.2 天即视为过期，而不是再宽限 5 天。
        assert (time.time() - client._token_ts) > 4.8 * 86400

    def test_401_triggers_relogin_and_retry(self):
        client = self._client()
        client._token = "dead"
        client._token_ts = time.time()
        responses = [
            _FakeResponse(401),
            _FakeResponse(200, {"points": [{"timestamp": "2099-01-01T00:00:00Z", "requestCount": 1}]}),
        ]
        calls = {"login": 0}

        def fake_login():
            calls["login"] += 1
            client._token = "fresh"
            client._token_ts = time.time()
            return True

        with patch.object(client, "_get_timeseries", side_effect=lambda: responses.pop(0)), \
                patch.object(client, "_login", side_effect=fake_login):
            client.get_today_usage()
        assert calls["login"] == 1
        assert not responses  # 重登后确实重试了

    @pytest.mark.parametrize(
        ("url", "verify"),
        [
            ("https://192.168.31.211:7778", False),
            ("https://10.0.0.5", False),
            ("https://172.16.0.1", False),
            ("https://localhost:7778", False),
            # 公网主机不能因 URL 含 "10."/"172." 子串而被跳过证书验证
            ("https://app10.example.com", True),
            ("https://nfk.jingtanggame.com", True),
            ("http://192.168.1.1", True),  # 非 HTTPS 不涉及验证开关
        ],
    )
    def test_tls_verify_by_host(self, url, verify):
        from providers.nfk.client import LocalMimoAPI

        assert LocalMimoAPI(url, "u", "p")._verify is verify


# ── GitHub：空贡献结果是合法缓存 ──


class TestGithubCache:
    def test_empty_contributions_hit_memory_cache(self):
        import services.github_service as gh

        gh._cache.set({})
        try:
            with patch.object(gh, "_read_disk_cache", side_effect=AssertionError("不应回落到磁盘缓存")):
                payload = gh.get_github_data()
            assert payload["contributions"] == {}
        finally:
            gh._cache.clear()

    def test_total_failure_is_cached(self):
        import services.github_service as gh

        gh._cache.clear()
        try:
            with patch.object(gh, "_read_disk_cache", return_value=None), \
                    patch.object(gh, "_get_token", return_value=""), \
                    patch.object(gh, "_fetch_from_github", side_effect=RuntimeError("net down")), \
                    patch.object(gh.time, "sleep"):
                gh.get_github_data()
                # 失败结果已缓存：第二次调用不得再触发抓取
                with patch.object(gh, "_fetch_from_github", side_effect=AssertionError("不应重试")):
                    payload = gh.get_github_data()
            assert payload["contributions"] == {}
        finally:
            gh._cache.clear()


# ── 歌词负缓存 ──


class TestLyricsNegativeCache:
    def test_missing_lyrics_not_researched_within_ttl(self):
        import services.media_service as media

        key = ("no-such-song", "no-such-artist")
        with media._lyrics_cache_lock:
            media._lyrics_cache.pop(key, None)
            media._lyrics_negative.pop(key, None)
        calls = {"search": 0}

        def fake_search(title, artist):
            calls["search"] += 1
            return None

        try:
            with patch.object(media, "_search_and_fetch", side_effect=fake_search):
                first = media._get_lyrics_for(*key)
                second = media._get_lyrics_for(*key)
            assert first["lyrics"] == [] and second["lyrics"] == []
            assert calls["search"] == 1
        finally:
            with media._lyrics_cache_lock:
                media._lyrics_negative.pop(key, None)


# ── RefreshScheduler：单飞窗口 ──


class TestSchedulerSingleFlight:
    def _scheduler(self, getter):
        from contracts.workspace import DataSourceDescriptor
        from runtime.refresh_scheduler import RefreshScheduler
        from workspaces.data_sources import DataSourceDefinition

        descriptor = DataSourceDescriptor.from_payload({
            "id": "test.source",
            "refresh_policy": {"default_interval_ms": 1000},
        })
        definition = DataSourceDefinition(descriptor=descriptor, getter=getter)
        return RefreshScheduler({"test.source": definition})

    def test_concurrent_refresh_now_runs_getter_once(self):
        release = threading.Event()
        runs = []

        def slow_getter():
            runs.append(1)
            release.wait(5)
            return {"ok": True}

        scheduler = self._scheduler(slow_getter)
        try:
            futures = []

            def call():
                futures.append(scheduler.refresh_now("test.source"))

            threads = [threading.Thread(target=call) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
            release.set()
            for future in futures:
                future.result(timeout=5)
            scheduler.wait_for_idle(("test.source",))
            assert len(runs) == 1
        finally:
            release.set()
            scheduler.stop()
