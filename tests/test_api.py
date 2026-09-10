import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend import config, services
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
    assert response.json() == {"ready": True, "database": "ok", "schema_version": 7}


@pytest.mark.parametrize("allowed_hosts", [(), ("*",)])
def test_production_rejects_missing_or_wildcard_host_allowlist(monkeypatch, allowed_hosts):
    monkeypatch.setattr(config, "ENVIRONMENT", "production")
    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    monkeypatch.setattr(config, "ADMIN_TOKEN", "a" * 32)
    monkeypatch.setattr(config, "ALLOWED_HOSTS", allowed_hosts)

    with pytest.raises(RuntimeError, match="STEAMKB_ALLOWED_HOSTS"):
        create_app()


def test_status_endpoint(api_client):
    _, client = api_client
    response = client.get("/api/status", headers={"Origin": "http://localhost:8765"})
    payload = response.json()
    assert response.status_code == 200
    assert "Access-Control-Allow-Origin" not in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"
    assert "steam_cooldown_remaining_seconds" in payload
    assert "direct_cooldown_remaining_seconds" in payload
    assert set(payload["service_cooldowns"]) == {"steam_api", "steam_store", "itad", "image_cdn"}
    assert set(payload["direct_service_cooldowns"]) == {"steam_api", "steam_store", "itad", "image_cdn"}
    assert "proxy" in payload
    assert payload["database_schema_version"] == 7
    assert payload["niche_max_reviews"] == 50000
    assert payload["daily_refresh_timezone"] == "Asia/Shanghai"
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
    assert second["refresh_pending"] is False
    assert second["data_incomplete"] is True
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
        params={"appid": 1, "url": "https://example.test/not-steam.jpg"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "location" not in response.headers


def test_web_detail_is_strictly_read_only(api_client, insert_game, monkeypatch):
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
    assert response.json()["data_incomplete"] is True
    with runtime.database_connection() as conn:
        task_count = conn.execute(
            "SELECT COUNT(*) FROM crawl_tasks WHERE appid=?", (appid,)
        ).fetchone()[0]
    assert task_count == 0


def test_catalog_detail_stub_does_not_materialize_or_enqueue(api_client):
    runtime, client = api_client
    with runtime.database_connection() as conn:
        conn.execute(
            """
            INSERT INTO steam_catalog(appid, name, app_type, updated_at)
            VALUES (8810, 'Catalog Only', 'game', ?)
            """,
            (runtime.now_iso(),),
        )

    response = client.get("/api/games/8810")

    assert response.status_code == 200
    assert response.json()["game"]["name"] == "Catalog Only"
    assert response.json()["data_incomplete"] is True
    with runtime.database_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM games WHERE appid=8810").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM crawl_tasks WHERE appid=8810").fetchone()[0] == 0


def test_admin_token_protects_write_routes(isolated_runtime, monkeypatch):
    token = "a" * 32
    monkeypatch.setattr(config, "ADMIN_TOKEN", token)
    with TestClient(create_app()) as client:
        assert client.post("/api/track", json={"appid": 730}).status_code == 401
        assert client.post(
            "/api/track",
            json={"appid": 730},
            headers={"Authorization": "Bearer wrong"},
        ).status_code == 401
        response = client.post(
            "/api/track",
            json={"appid": 730, "name": "Counter-Strike 2"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200


def test_production_requires_strong_admin_token(monkeypatch):
    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    monkeypatch.setattr(config, "ENVIRONMENT", "production")
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")
    with pytest.raises(RuntimeError, match="at least 32 characters"):
        create_app()


def test_production_rejects_wildcard_cors(monkeypatch):
    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    monkeypatch.setattr(config, "ENVIRONMENT", "production")
    monkeypatch.setattr(config, "ADMIN_TOKEN", "a" * 32)
    monkeypatch.setattr(config, "ALLOWED_HOSTS", ("steam.example",))
    monkeypatch.setattr(config, "CORS_ALLOWED_ORIGINS", ("*",))
    with pytest.raises(RuntimeError, match="wildcard CORS"):
        create_app()


def test_production_disables_api_documentation(isolated_runtime, monkeypatch):
    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    monkeypatch.setattr(config, "ENVIRONMENT", "production")
    monkeypatch.setattr(config, "ADMIN_TOKEN", "a" * 32)
    monkeypatch.setattr(config, "ALLOWED_HOSTS", ("testserver",))
    monkeypatch.setattr(config, "CORS_ALLOWED_ORIGINS", ())
    with TestClient(create_app()) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_cors_only_allows_configured_origin(isolated_runtime, monkeypatch):
    monkeypatch.setattr(config, "CORS_ALLOWED_ORIGINS", ("https://steam.example",))
    with TestClient(create_app()) as client:
        allowed = client.get("/health", headers={"Origin": "https://steam.example"})
        denied = client.get("/health", headers={"Origin": "https://evil.example"})
    assert allowed.headers["Access-Control-Allow-Origin"] == "https://steam.example"
    assert "Access-Control-Allow-Origin" not in denied.headers


def test_unhandled_exception_is_not_exposed(isolated_runtime, monkeypatch):
    monkeypatch.setattr(
        services,
        "list_games",
        lambda: (_ for _ in ()).throw(RuntimeError("/secret/path database failure")),
    )
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = client.get("/api/games")
    assert response.status_code == 500
    assert response.json() == {"error": "Internal server error"}
    assert "/secret/path" not in response.text


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
        ).fetchone()[0] == 6


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

    home_response = client.get("/api/home-picks")

    assert home_response.status_code == 200


def test_image_cache_miss_redirects_without_downloading(api_client, insert_game, monkeypatch):
    runtime, client = api_client
    monkeypatch.setattr(
        runtime,
        "cache_image",
        lambda *_args, **_kwargs: pytest.fail("Web downloaded a CDN image"),
    )
    appid = insert_game(730, "Cached Header")
    image_url = "https://cdn.akamai.steamstatic.com/steam/apps/730/header.jpg"
    with runtime.database_connection() as conn:
        conn.execute("UPDATE games SET header_image=? WHERE appid=?", (image_url, appid))

    response = client.get(
        "/api/image-cache", params={"appid": appid}, follow_redirects=False
    )

    assert response.status_code == 302
    assert response.headers["location"] == image_url


def test_image_cache_retry_uses_a_cache_busting_cdn_url(api_client, insert_game):
    runtime, client = api_client
    appid = insert_game(731, "Cached Header Retry")
    image_url = "https://cdn.akamai.steamstatic.com/steam/apps/730/header.jpg?t=1"
    with runtime.database_connection() as conn:
        conn.execute("UPDATE games SET header_image=? WHERE appid=?", (image_url, appid))

    response = client.get(
        "/api/image-cache", params={"appid": appid, "retry": 1}, follow_redirects=False
    )

    assert response.status_code == 302
    assert response.headers["location"] == f"{image_url}&_steamkb_retry=1"


def test_image_cache_ignores_user_supplied_redirect_urls(api_client, insert_game):
    runtime, client = api_client
    appid = insert_game(732, "Trusted Header")
    trusted_url = "https://cdn.akamai.steamstatic.com/steam/apps/732/header.jpg"
    with runtime.database_connection() as conn:
        conn.execute("UPDATE games SET header_image=? WHERE appid=?", (trusted_url, appid))

    response = client.get(
        "/api/image-cache",
        params={"appid": appid, "url": "https://example.test/phishing"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == trusted_url
