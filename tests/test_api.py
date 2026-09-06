import json
import threading
import urllib.request

import pytest

from backend.server import create_server


def read_json(url):
    with urllib.request.urlopen(url, timeout=3) as response:
        return response.status, dict(response.headers), json.load(response)


@pytest.fixture
def api_server(isolated_runtime):
    server = create_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield isolated_runtime, f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_status_endpoint(api_server):
    _, base_url = api_server
    status, headers, payload = read_json(f"{base_url}/api/status")
    assert status == 200
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert "steam_cooldown_remaining_seconds" in payload
    assert "direct_cooldown_remaining_seconds" in payload
    assert set(payload["service_cooldowns"]) == {"steam_api", "steam_store", "itad", "image_cdn"}
    assert set(payload["direct_service_cooldowns"]) == {"steam_api", "steam_store", "itad", "image_cdn"}
    assert "proxy" in payload


def test_games_endpoint_reads_local_cache(api_server):
    runtime, base_url = api_server
    runtime.quick_track_game(730, "Counter-Strike 2")
    status, _, payload = read_json(f"{base_url}/api/games")
    assert status == 200
    assert payload["games"][0]["appid"] == 730


def test_hot_games_endpoint_does_not_require_network(api_server):
    runtime, base_url = api_server
    stamp = runtime.now_iso()
    with runtime.sqlite3.connect(runtime.DB_PATH) as conn:
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

    status, _, payload = read_json(f"{base_url}/api/hot-games?limit=100")
    assert status == 200
    assert payload["games"][0]["appid"] == 730
    assert payload["games"][0]["current_players"] == 100


def test_empty_search_never_calls_steam(api_server):
    _, base_url = api_server
    status, _, payload = read_json(f"{base_url}/api/search?q=")
    assert status == 200
    assert payload == {"items": []}
