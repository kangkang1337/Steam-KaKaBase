import asyncio
import sqlite3
from types import SimpleNamespace

import pytest


def insert_catalog(runtime, rows):
    with sqlite3.connect(runtime.DB_PATH) as conn:
        conn.executemany(
            """
            INSERT INTO steam_catalog(appid, name, updated_at, enrich_status)
            VALUES (?, ?, ?, 'pending')
            """,
            [(appid, name, runtime.now_iso()) for appid, name in rows],
        )


def test_catalog_scan_resumes_existing_prefix_and_marks_complete(isolated_runtime, monkeypatch):
    runtime = isolated_runtime
    insert_catalog(runtime, [(10, "Ten"), (20, "Twenty")])
    calls = []

    def fetch(last_appid, max_results):
        calls.append((last_appid, max_results))
        return {
            "response": {
                "apps": [{"appid": 30, "name": "Thirty"}, {"appid": 40, "name": "Forty"}],
                "last_appid": 40,
                "have_more_results": False,
            }
        }

    monkeypatch.setattr(runtime, "fetch_store_catalog_page", fetch)
    monkeypatch.setattr(runtime, "CATALOG_SCAN_BATCH_LIMIT", 500)

    assert runtime.sync_steam_catalog_once(force=True) is True
    with sqlite3.connect(runtime.DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM steam_catalog").fetchone()[0] == 4
        assert runtime.get_crawl_state(conn, "steam_catalog_scan_cursor") == "40"
        assert runtime.get_crawl_state(conn, "steam_catalog_scan_completed_at")
    assert calls == [(20, 500)]


def test_catalog_scan_commits_page_and_cursor_before_later_failure(isolated_runtime, monkeypatch):
    runtime = isolated_runtime
    calls = []

    def fetch(last_appid, _max_results):
        calls.append(last_appid)
        if len(calls) == 1:
            return {
                "response": {
                    "apps": [{"appid": 10, "name": "Ten"}],
                    "last_appid": 10,
                    "have_more_results": True,
                }
            }
        raise runtime.ExternalDataUnavailable("offline")

    monkeypatch.setattr(runtime, "fetch_store_catalog_page", fetch)
    monkeypatch.setattr(runtime, "CATALOG_SCAN_BATCH_LIMIT", 1000)

    with pytest.raises(runtime.ExternalDataUnavailable):
        runtime.sync_steam_catalog_once(force=True)
    with sqlite3.connect(runtime.DB_PATH) as conn:
        assert conn.execute("SELECT name FROM steam_catalog WHERE appid=10").fetchone()[0] == "Ten"
        assert runtime.get_crawl_state(conn, "steam_catalog_scan_cursor") == "10"
        assert not runtime.get_crawl_state(conn, "steam_catalog_scan_completed_at")


def test_completed_catalog_starts_new_generation_on_rescan(isolated_runtime, monkeypatch):
    runtime = isolated_runtime
    with sqlite3.connect(runtime.DB_PATH) as conn:
        runtime.set_crawl_state(conn, "steam_catalog_scan_cursor", "999")
        runtime.set_crawl_state(conn, "steam_catalog_scan_generation", "2")
        runtime.set_crawl_state(conn, "steam_catalog_scan_completed_at", runtime.now_iso())
    calls = []

    def fetch(last_appid, _max_results):
        calls.append(last_appid)
        return {"response": {"apps": [], "have_more_results": False}}

    monkeypatch.setattr(runtime, "fetch_store_catalog_page", fetch)
    assert runtime.sync_steam_catalog_once(force=True) is True
    with sqlite3.connect(runtime.DB_PATH) as conn:
        assert runtime.get_crawl_state(conn, "steam_catalog_scan_generation") == "3"
        assert runtime.get_crawl_state(conn, "steam_catalog_scan_cursor") == "0"
        assert runtime.get_crawl_state(conn, "steam_catalog_scan_completed_at")
    assert calls == [0]


def test_catalog_enrich_classifies_and_excludes_non_games(isolated_runtime, monkeypatch):
    runtime = isolated_runtime
    insert_catalog(
        runtime,
        [(101, "Real Game"), (102, "Extra DLC"), (103, "Demo"), (104, "Tool"), (105, "Software")],
    )

    async def fetch(appids):
        assert set(appids) == {101, 102, 103, 104, 105}
        return [
            {
                "appid": 101, "catalog_result": "game", "app_type": "game",
                "name": "Real Game", "header_image": "header.jpg", "current_players": 30,
                "peak_players": 100, "review_score": 90, "total_reviews": 1000,
                "release_date": "1 Jan, 2025", "is_free": 0, "fetched_at": runtime.now_iso(),
            },
            *[
                {"appid": appid, "catalog_result": "excluded", "app_type": app_type}
                for appid, app_type in [(102, "dlc"), (103, "demo"), (104, "tool"), (105, "software")]
            ],
        ]

    monkeypatch.setattr(runtime, "fetch_niche_candidates_async", fetch)
    monkeypatch.setattr(runtime, "CATALOG_ENRICH_BATCH_LIMIT", 10)
    monkeypatch.setattr(runtime, "CATALOG_ENRICH_DAILY_LIMIT", 10)
    monkeypatch.setattr(runtime, "service_cooldown_remaining_seconds", lambda _service: 0)

    assert runtime.run_catalog_enrich_task() is True
    with sqlite3.connect(runtime.DB_PATH) as conn:
        states = dict(conn.execute("SELECT appid, app_type FROM steam_catalog"))
        statuses = dict(conn.execute("SELECT appid, enrich_status FROM steam_catalog"))
        pool_ids = {row[0] for row in conn.execute("SELECT appid FROM niche_pool")}
    assert states == {101: "game", 102: "dlc", 103: "demo", 104: "tool", 105: "software"}
    assert statuses[101] == "done"
    assert {statuses[appid] for appid in (102, 103, 104, 105)} == {"excluded"}
    assert pool_ids == {101}


def test_appdetails_type_is_authoritative_for_catalog_classification(isolated_runtime, monkeypatch):
    runtime = isolated_runtime

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    async def get_json(_client, _semaphore, url, params=None):
        if "appdetails" in url:
            appid = int(params["appids"])
            app_type = {401: "game", 402: "dlc", 403: "demo", 404: "tool", 405: "software"}[appid]
            return {
                str(appid): {
                    "success": True,
                    "data": {"type": app_type, "name": f"App {appid}", "header_image": "header.jpg"},
                }
            }
        if "appreviews" in url:
            return {"query_summary": {"total_positive": 90, "total_negative": 10}}
        return {"response": {"player_count": 20}}

    monkeypatch.setattr(runtime, "require_httpx", lambda: SimpleNamespace(AsyncClient=FakeClient))
    monkeypatch.setattr(runtime, "async_get_json", get_json)
    monkeypatch.setattr(runtime, "STORE_REQUEST_DELAY_MIN_SECONDS", 0)
    monkeypatch.setattr(runtime, "STORE_REQUEST_DELAY_MAX_SECONDS", 0)

    results = asyncio.run(runtime.fetch_niche_candidates_async([401, 402, 403, 404, 405]))
    outcomes = {row["appid"]: (row["catalog_result"], row["app_type"]) for row in results}
    assert outcomes == {
        401: ("game", "game"),
        402: ("excluded", "dlc"),
        403: ("excluded", "demo"),
        404: ("excluded", "tool"),
        405: ("excluded", "software"),
    }


def test_search_excludes_classified_and_obvious_non_games(isolated_runtime):
    runtime = isolated_runtime
    insert_catalog(runtime, [(201, "Quest"), (202, "Quest DLC"), (203, "Quest Demo"), (204, "Quest Tool")])
    with sqlite3.connect(runtime.DB_PATH) as conn:
        conn.execute("UPDATE steam_catalog SET app_type='game', enrich_status='done' WHERE appid=201")
        conn.execute("UPDATE steam_catalog SET app_type='dlc', enrich_status='excluded' WHERE appid=202")
        conn.execute("UPDATE steam_catalog SET app_type='tool', enrich_status='excluded' WHERE appid=204")

    results = runtime.search_catalog_games("Quest")
    assert [row["appid"] for row in results] == [201]


def test_local_search_matches_catalog_alias_but_displays_localized_name(isolated_runtime):
    runtime = isolated_runtime
    with sqlite3.connect(runtime.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO games(appid, name, tracked, updated_at) VALUES (1245620, 'Localized Elden Ring', 0, ?)",
            (runtime.now_iso(),),
        )
        conn.execute(
            """
            INSERT INTO steam_catalog(appid, name, app_type, updated_at)
            VALUES (1245620, 'ELDEN RING', 'game', ?)
            """,
            (runtime.now_iso(),),
        )

    results = runtime.search_local_games("elden")

    assert results[0]["appid"] == 1245620
    assert results[0]["name"] == "Localized Elden Ring"
    assert results[0]["name_zh"] == "Localized Elden Ring"
    assert results[0]["name_en"] == "ELDEN RING"


def test_local_search_matches_localized_title_in_description(isolated_runtime):
    runtime = isolated_runtime
    with sqlite3.connect(runtime.DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO games(appid, name, short_description, tracked, updated_at)
            VALUES (367520, 'Hollow Knight', 'Explore the localized title: 空洞骑士', 0, ?)
            """,
            (runtime.now_iso(),),
        )

    results = runtime.search_local_games("空洞骑士")

    assert results[0]["appid"] == 367520
    assert results[0]["name"] == "Hollow Knight"


def test_search_uses_only_local_index(isolated_runtime, monkeypatch):
    runtime = isolated_runtime
    runtime.SEARCH_CACHE.clear()
    with sqlite3.connect(runtime.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO steam_catalog(appid, name, app_type, updated_at) VALUES (1245620, 'ELDEN RING', 'game', ?)",
            (runtime.now_iso(),),
        )
    monkeypatch.setattr(
        runtime,
        "request_json",
        lambda *_args, **_kwargs: pytest.fail("text search attempted a Steam request"),
    )

    results = runtime.search_steam("elden")

    assert [row["appid"] for row in results] == [1245620]


@pytest.mark.parametrize("term", ["elden", "艾尔登法环"])
def test_search_bilingual_alias_does_not_wait_for_catalog(isolated_runtime, term):
    results = isolated_runtime.search_steam(term)

    assert results[0]["appid"] == 1245620


def test_search_lru_cache_avoids_second_database_query(isolated_runtime, monkeypatch):
    runtime = isolated_runtime
    runtime.SEARCH_CACHE.clear()
    expected = [{"appid": 10, "name": "Cached Game"}]
    calls = []
    monkeypatch.setattr(
        runtime,
        "search_index_games",
        lambda *_args, **_kwargs: calls.append(True) or expected,
    )

    assert runtime.search_steam("cached") == expected
    assert runtime.search_steam("cached") == expected
    assert len(calls) == 1
    assert runtime.get_search_metrics()["cache_hit_rate"] > 0


def test_upsert_niche_pool_ignores_non_game_result(isolated_runtime):
    runtime = isolated_runtime
    assert runtime.upsert_niche_pool_rows([
        {"appid": 301, "catalog_result": "excluded", "app_type": "dlc", "name": "DLC"}
    ]) == 0
    runtime.upsert_niche_pool_rows([{
        "appid": 302, "catalog_result": "game", "app_type": "game", "name": "Sample Game Demo",
        "header_image": "header.jpg", "current_players": 50, "peak_players": 100,
        "review_score": 95, "total_reviews": 5000, "release_date": "1 Jan, 2025",
    }])
    with sqlite3.connect(runtime.DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM niche_pool").fetchone()[0] == 0
