import asyncio

import pytest

from backend import _runtime


class FakeHttpError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.response = type("ResponseRef", (), {"status_code": status_code})()


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise FakeHttpError(self.status_code)

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    async def request(self, method, url, params=None, json=None):
        self.calls += 1
        return self.response


class FailingClient:
    def __init__(self):
        self.calls = 0

    async def request(self, method, url, params=None, json=None):
        self.calls += 1
        raise TimeoutError("direct timeout")


class FakeProxyClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def request(self, method, url, params=None, json=None):
        return FakeResponse(200, {"via": "proxy"})


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://api.steampowered.com/IStoreService/GetAppList/v1/", "steam_api"),
        ("https://store.steampowered.com/api/appdetails", "steam_store"),
        ("https://api.isthereanydeal.com/games/historylow/v1", "itad"),
        ("https://shared.cloudflare.steamstatic.com/store_item_assets/header.jpg", "image_cdn"),
    ],
)
def test_external_service_classification(url, expected):
    assert _runtime.external_service_for_url(url) == expected


def test_service_cooldowns_are_isolated(monkeypatch):
    for service in _runtime.EXTERNAL_SERVICES:
        monkeypatch.setitem(_runtime.SERVICE_COOLDOWN_UNTIL, service, 0)

    _runtime.set_service_cooldown("itad", 10)

    assert _runtime.service_cooldown_remaining_seconds("itad") > 0
    assert _runtime.service_cooldown_remaining_seconds("steam_api") == 0
    assert _runtime.service_cooldown_remaining_seconds("steam_store") == 0
    assert _runtime.service_cooldown_remaining_seconds("image_cdn") == 0


def test_successful_json_request(monkeypatch):
    monkeypatch.setitem(_runtime.SERVICE_COOLDOWN_UNTIL, "steam_api", 0)
    client = FakeClient(FakeResponse(200, {"ok": True}))
    result = asyncio.run(_runtime.async_get_json(client, asyncio.Semaphore(1), "https://example.test"))
    assert result == {"ok": True}
    assert client.calls == 1


def test_404_is_not_retried(monkeypatch):
    monkeypatch.setitem(_runtime.SERVICE_COOLDOWN_UNTIL, "steam_api", 0)
    client = FakeClient(FakeResponse(404))
    with pytest.raises(_runtime.ExternalDataUnavailable):
        asyncio.run(_runtime.async_get_json(client, asyncio.Semaphore(1), "https://example.test/missing"))
    assert client.calls == 1


def test_429_starts_global_cooldown_without_retry(monkeypatch, isolated_runtime):
    monkeypatch.setitem(_runtime.SERVICE_COOLDOWN_UNTIL, "steam_api", 0)
    monkeypatch.setitem(_runtime.SERVICE_COOLDOWN_UNTIL, "itad", 0)
    client = FakeClient(FakeResponse(429))

    with pytest.raises(_runtime.SteamRateLimited):
        asyncio.run(_runtime.async_get_json(client, asyncio.Semaphore(1), "https://example.test/limited"))

    assert client.calls == 1
    assert _runtime.service_cooldown_remaining_seconds("steam_api") > 0
    assert _runtime.service_cooldown_remaining_seconds("itad") == 0

    itad_client = FakeClient(FakeResponse(200, {"ok": True}))
    result = asyncio.run(
        _runtime.async_get_json(
            itad_client,
            asyncio.Semaphore(1),
            "https://api.isthereanydeal.com/games/lookup/v1",
        )
    )
    assert result == {"ok": True}
    assert itad_client.calls == 1


def test_direct_failure_uses_proxy_and_starts_direct_cooldown(monkeypatch):
    monkeypatch.setattr(_runtime, "DIRECT_COOLDOWN_UNTIL", {service: 0 for service in _runtime.EXTERNAL_SERVICES})
    monkeypatch.setattr(_runtime, "DIRECT_FAILURE_COUNT", {service: 0 for service in _runtime.EXTERNAL_SERVICES})
    monkeypatch.setattr(_runtime, "proxy_fallback_enabled", lambda: True)
    monkeypatch.setattr(
        _runtime,
        "require_httpx",
        lambda: type("FakeHttpx", (), {"AsyncClient": FakeProxyClient}),
    )
    direct_client = FailingClient()

    response = asyncio.run(
        _runtime.async_request_direct_then_proxy(direct_client, "GET", "https://example.test")
    )

    assert response.json() == {"via": "proxy"}
    assert direct_client.calls == 1
    assert _runtime.direct_cooldown_remaining_seconds("steam_api") > 0
    assert _runtime.direct_cooldown_remaining_seconds("itad") == 0

    skipped_direct_client = FakeClient(FakeResponse(200, {"via": "direct"}))
    response = asyncio.run(
        _runtime.async_request_direct_then_proxy(skipped_direct_client, "GET", "https://example.test")
    )
    assert response.json() == {"via": "proxy"}
    assert skipped_direct_client.calls == 0
