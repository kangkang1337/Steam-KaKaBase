import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend import services
from backend.server import create_app


@pytest.fixture
def api_client(isolated_runtime):
    with TestClient(create_app(start_background=False)) as client:
        yield isolated_runtime, client


def test_health_and_readiness(api_client):
    _, client = api_client
    assert client.get("/health").json() == {"status": "ok"}
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"ready": True, "database": "ok", "schema_version": 4}


def test_status_endpoint(api_client):
    _, client = api_client
    response = client.get("/api/status", headers={"Origin": "http://localhost:8765"})
    payload = response.json()
    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert response.headers["Cache-Control"] == "no-store"
    assert "steam_cooldown_remaining_seconds" in payload
    assert "direct_cooldown_remaining_seconds" in payload
    assert set(payload["service_cooldowns"]) == {"steam_api", "steam_store", "itad", "image_cdn"}
    assert set(payload["direct_service_cooldowns"]) == {"steam_api", "steam_store", "itad", "image_cdn"}
    assert "proxy" in payload
    assert payload["database_schema_version"] == 4
    assert payload["niche_max_reviews"] == 50000


def test_games_endpoint_reads_local_cache(api_client):
    runtime, client = api_client
    runtime.quick_track_game(730, "Counter-Strike 2")
    response = client.get("/api/games")
    assert response.status_code == 200
    assert response.json()["games"][0]["appid"] == 730


def test_hot_games_endpoint_does_not_require_network(api_client):
    runtime, client = api_client
    stamp = runtime.now_iso()
    with sqlite3.connect(runtime.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO games(appid, name, tracked, updated_at) VALUES (?, ?, 0, ?)",
            (730, "Counter-Strike 2", stamp),
        )
        conn.execute(
            """
            INSERT INTO hot_games(appid, rank, name, current_players, header_image, source, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (730, 1, "Counter-Strike 2", 100, "https://example.test/header.jpg", "test", stamp),
        )

    response = client.get("/api/hot-games?limit=100")
    assert response.status_code == 200
    assert response.json()["games"][0]["appid"] == 730
    assert response.json()["games"][0]["current_players"] == 100


def test_empty_search_never_calls_steam(api_client, monkeypatch):
    _, client = api_client
    monkeypatch.setattr(services._runtime, "search_steam", lambda _term: pytest.fail("Steam request attempted"))
    response = client.get("/api/search?q=")
    assert response.status_code == 200
    assert response.json() == {"items": []}


@pytest.mark.parametrize("path", ["/api/games/0", "/api/games/not-a-number"])
def test_invalid_appid_returns_validation_error(api_client, path):
    _, client = api_client
    assert client.get(path).status_code == 422


def test_track_payload_is_validated(api_client):
    _, client = api_client
    assert client.post("/api/track", json={"appid": -1}).status_code == 422


def test_asset_path_traversal_is_rejected(api_client):
    _, client = api_client
    response = client.get("/assets/%2e%2e/steamkb.html")
    assert response.status_code == 404


def test_unsupported_image_redirect_is_rejected(api_client, monkeypatch):
    _, client = api_client

    def fail_cache(_url):
        raise RuntimeError("download failed")

    monkeypatch.setattr(services, "cache_remote_image", fail_cache)
    response = client.get(
        "/api/image-cache",
        params={"url": "https://example.test/not-steam.jpg"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "location" not in response.headers
