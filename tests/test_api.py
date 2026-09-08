import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend import services
from backend.server import create_app


@pytest.fixture
def api_client(isolated_runtime):
    with TestClient(create_app()) as client:
        yield isolated_runtime, client


def test_health_and_readiness(api_client):
    _, client = api_client
    assert client.get("/health").json() == {"status": "ok"}
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"ready": True, "database": "ok", "schema_version": 6}


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
    assert payload["database_schema_version"] == 6
    assert payload["niche_max_reviews"] == 50000
    assert payload["search"]["storage"] == "sqlite_fts5_trigram"
    assert payload["search"]["connection_strategy"] == "short_lived_per_request"
    assert payload["task_monitor"]["active_total"] == 0
    assert set(payload["rate_limits"]) == {"steam_api", "steam_store", "itad", "image_cdn"}
    assert payload["crawler"]["heartbeat_age_seconds"] is None
    assert payload["crawler"]["lease_remaining_seconds"] == 0
    assert payload["storage"]["database_bytes"] > 0
    assert payload["storage"]["wal_bytes"] >= 0


def test_status_does_not_report_stale_crawler_as_running(api_client):
    runtime, client = api_client
    with runtime.database_connection() as conn:
        runtime.set_crawl_state(
            conn,
            "crawler_runtime_status",
            json.dumps({"state": "running", "error": None}),
        )

    payload = client.get("/api/status").json()

    assert payload["crawler"]["running"] is False
    assert payload["crawler"]["state"] == "stopped"


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


def test_detail_poll_does_not_revive_completed_missing_tasks(api_client, insert_game, monkeypatch):
    runtime, client = api_client
    appid = insert_game(7401, "No Price Yet")
    monkeypatch.setattr(runtime, "backfill_preview_async", lambda *_args, **_kwargs: False)
    with runtime.database_connection() as conn:
        runtime.enqueue_crawl_tasks_in_conn(conn, [appid], "preview", 100)
    runtime.complete_crawl_tasks([appid], "preview")

    first = client.get(f"/api/games/{appid}").json()
    second = client.get(f"/api/games/{appid}").json()

    assert first["pending_fields"] == ["prices", "players", "reviews", "metadata"]
    assert second["refresh_pending"] is True
    with runtime.database_connection() as conn:
        task = conn.execute(
            "SELECT status, attempts, completed_at FROM crawl_tasks WHERE appid=? AND task_type='preview'",
            (appid,),
        ).fetchone()
    assert task[0] == "done"
    assert task[1] == 0
    assert task[2] is not None


def test_empty_search_never_calls_steam(api_client, monkeypatch):
    _, client = api_client
    monkeypatch.setattr(services._runtime, "search_steam", lambda _term: pytest.fail("Steam request attempted"))
    response = client.get("/api/search?q=")
    assert response.status_code == 200
    assert response.json() == {
        "items": [], "limit": 12, "offset": 0, "has_more": False
    }


def test_search_endpoint_supports_pagination(api_client, monkeypatch):
    _, client = api_client
    monkeypatch.setattr(
        services._runtime,
        "search_steam",
        lambda _term, limit, offset: [
            {"appid": offset + index, "name": f"Game {index}"}
            for index in range(limit)
        ],
    )

    response = client.get("/api/search?q=game&limit=2&offset=4")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {"appid": 4, "name": "Game 0"},
            {"appid": 5, "name": "Game 1"},
        ],
        "limit": 2,
        "offset": 4,
        "has_more": True,
    }


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


def test_unsupported_image_redirect_is_rejected(api_client):
    _, client = api_client
    response = client.get(
        "/api/image-cache",
        params={"url": "https://example.test/not-steam.jpg"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "location" not in response.headers


def test_web_detail_only_enqueues_missing_data(api_client, insert_game, monkeypatch):
    runtime, client = api_client
    appid = insert_game(8801, "Queue Only")
    monkeypatch.setattr(
        runtime,
        "backfill_preview_async",
        lambda *_args, **_kwargs: pytest.fail("Web attempted Steam collection"),
    )
    monkeypatch.setattr(
        runtime,
        "backfill_historylow_async",
        lambda *_args, **_kwargs: pytest.fail("Web attempted ITAD collection"),
    )

    response = client.get(f"/api/games/{appid}")

    assert response.status_code == 200
    assert response.json()["refresh_started"] is False
    with runtime.database_connection() as conn:
        task_types = {
            row[0]
            for row in conn.execute(
                "SELECT task_type FROM crawl_tasks WHERE appid=? AND status='pending'",
                (appid,),
            ).fetchall()
        }
    assert task_types == {"players", "preview", "reviews", "metadata", "historylow"}


def test_web_refresh_endpoints_only_enqueue_tasks(api_client, insert_game):
    runtime, client = api_client
    appid = insert_game(8802, "Tracked Queue", tracked=1)

    game_response = client.post(f"/api/games/{appid}/refresh")
    all_response = client.post("/api/refresh-all")

    assert game_response.status_code == 200
    assert game_response.json()["queued"] is True
    assert all_response.status_code == 200
    assert all_response.json()["running"] is False
    with runtime.database_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM crawl_tasks WHERE appid=?", (appid,)
        ).fetchone()[0] == 5


def test_cache_only_pages_do_not_start_collection(api_client, monkeypatch):
    runtime, client = api_client
    monkeypatch.setattr(
        runtime,
        "refresh_hot_database_async",
        lambda *_args, **_kwargs: pytest.fail("Web started hot-list collection"),
    )
    monkeypatch.setattr(
        runtime,
        "snapshot_daily_niche_recommendation",
        lambda *_args, **_kwargs: pytest.fail("Web created a homepage snapshot"),
    )

    ensure_response = client.get("/api/hot-games/ensure?target=100")
    home_response = client.get("/api/home-picks")

    assert ensure_response.status_code == 200
    assert ensure_response.json()["cache_only"] is True
    assert home_response.status_code == 200


def test_image_cache_miss_redirects_without_downloading(api_client, monkeypatch):
    runtime, client = api_client
    monkeypatch.setattr(
        runtime,
        "cache_image",
        lambda *_args, **_kwargs: pytest.fail("Web downloaded a CDN image"),
    )
    image_url = "https://cdn.akamai.steamstatic.com/steam/apps/730/header.jpg"

    response = client.get(
        "/api/image-cache", params={"url": image_url}, follow_redirects=False
    )

    assert response.status_code == 302
    assert response.headers["location"] == image_url
