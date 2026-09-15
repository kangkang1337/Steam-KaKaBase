import asyncio
from datetime import datetime, timedelta, timezone

from backend import config, crawler, crawler_data, crawler_fetch, steam_client


def test_coverage_tiers_prioritize_hot_favorites_and_recent_interest(
    isolated_runtime, insert_game
):
    runtime = isolated_runtime
    hot, favorite, recent, cold = (
        insert_game(5101, "Hot"), insert_game(5102, "Favorite"),
        insert_game(5103, "Recent"), insert_game(5104, "Cold"),
    )
    old = (datetime.now(timezone.utc) - timedelta(days=8)).replace(microsecond=0).isoformat()
    with runtime.database_connection() as conn:
        conn.execute(
            "INSERT INTO hot_games(appid,rank,name,source,fetched_at) VALUES (?,1,'Hot','test',?)",
            (hot, old),
        )
        conn.execute(
            "INSERT INTO users(username,password_hash,password_salt,created_at) VALUES ('coverage-user','h','s',?)",
            (old,),
        )
        user_id = conn.execute("SELECT id FROM users WHERE username='coverage-user'").fetchone()[0]
        conn.execute(
            "INSERT INTO user_favorites(user_id,appid,created_at) VALUES (?,?,?)",
            (user_id, favorite, old),
        )
        crawler_data.record_game_interest(conn, recent)

    rows = crawler_data.get_due_coverage_appids("players", 100)
    priorities = {appid: priority for appid, priority, _stamp in rows}
    assert priorities[hot] == 40
    assert priorities[favorite] == 30
    assert priorities[recent] == 20
    assert priorities[cold] == 10


def test_hotlist_extension_uses_only_fresh_distinct_local_observations(isolated_runtime, insert_game):
    runtime = isolated_runtime
    now = runtime.now_iso()
    stale = (datetime.now(timezone.utc) - timedelta(hours=25)).replace(microsecond=0).isoformat()
    insert_game(5201, "Fresh local")
    insert_game(5202, "Official duplicate")
    insert_game(5203, "Stale local")
    with runtime.database_connection() as conn:
        conn.executemany(
            "INSERT INTO game_latest_state(appid,current_players,players_updated_at,updated_at) VALUES (?, ?, ?, ?)",
            [(5201, 500, now, now), (5202, 400, now, now), (5203, 900, stale, now)],
        )
    rows = crawler_data.extend_hotlist_with_recent_players(
        [{"appid": 5202, "rank": 1, "name": "Official duplicate", "current_players": 400}], 3
    )

    assert [row["appid"] for row in rows] == [5202, 5201]
    assert rows[-1]["rank"] == 2
    assert rows[-1]["source"] == "local_player_snapshot"
    assert rows[-1]["header_image"].endswith("/5201/header.jpg")


def test_coverage_budget_is_daily_and_bounded(isolated_runtime):
    end_of_day = datetime(2030, 1, 1, 23, 59, tzinfo=config.DAILY_REFRESH_TZINFO)
    assert crawler_data.remaining_coverage_budget("players", 3, now=end_of_day) == 3
    assert crawler_data.reserve_coverage_budget("players", 2, 3, now=end_of_day) == 2
    assert crawler_data.remaining_coverage_budget("players", 3, now=end_of_day) == 1
    assert crawler_data.reserve_coverage_budget("players", 2, 3, now=end_of_day) == 1
    assert crawler_data.remaining_coverage_budget("players", 3, now=end_of_day) == 0


def test_coverage_budget_is_released_gradually_through_the_day(isolated_runtime):
    noon = datetime(2030, 1, 2, 12, 0, tzinfo=config.DAILY_REFRESH_TZINFO)
    assert crawler_data.released_coverage_budget(1300, now=noon) == 650
    assert crawler_data.reserve_coverage_budget("price", 1300, 1300, now=noon) == 650
    assert crawler_data.remaining_coverage_budget("price", 1300, now=noon) == 0


def test_hot_players_use_a_separate_task_from_budgeted_history_coverage(monkeypatch):
    calls = []
    monkeypatch.setattr(crawler_data, "get_due_hot_player_appids", lambda: [11])
    monkeypatch.setattr(crawler_data, "enqueue_due_coverage_tasks", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        crawler,
        "_run_player_tasks",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )

    assert crawler.run_players_task() is True
    assert calls[0][0][:2] == ("hot_players", [11])
    assert calls[0][1]["budgeted"] is False
    assert calls[1][0][0] == "players"
    assert calls[1][1]["budgeted"] is True


def test_hot_prices_do_not_use_the_history_coverage_budget(monkeypatch):
    calls = []
    monkeypatch.setattr(crawler_data, "enqueue_due_coverage_tasks", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        crawler,
        "_run_appdetails_task",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )

    assert crawler.run_price_task() is True
    assert calls[0][0][0] == "hot_price"
    assert calls[0][1]["coverage_budget"] is False
    assert crawler.run_coverage_price_task() is True
    assert calls[1][0][0] == "price"
    assert calls[1][1]["coverage_budget"] is True


def test_store_requests_are_spaced_between_each_serial_request(monkeypatch):
    delays, seen = [], []

    async def fake_sleep(delay):
        delays.append(delay)

    async def fetch_one(appid):
        seen.append(appid)
        return appid * 10

    monkeypatch.setattr(crawler_fetch.config, "STORE_REQUEST_DELAY_MIN_SECONDS", 2.0)
    monkeypatch.setattr(crawler_fetch.config, "STORE_REQUEST_DELAY_MAX_SECONDS", 2.0)
    monkeypatch.setattr(crawler_fetch.asyncio, "sleep", fake_sleep)

    assert asyncio.run(crawler_fetch._stagger_store_requests([1, 2, 3], fetch_one)) == [10, 20, 30]
    assert seen == [1, 2, 3]
    assert delays == [2.0, 2.0]


def test_scheduler_does_not_add_a_full_extra_wait_after_a_slow_cycle(monkeypatch):
    waits = []

    class StopAfterOneCycle:
        def __init__(self):
            self.checked = False

        def is_set(self):
            if not self.checked:
                self.checked = True
                return False
            return True

        def wait(self, seconds):
            waits.append(seconds)
            return True

    ticks = iter([100.0, 155.0])
    monkeypatch.setattr(crawler.runtime, "SCHEDULER_CHECK_SECONDS", 60)
    monkeypatch.setattr(crawler.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(crawler, "run_scheduler_cycle", lambda: None)

    crawler.scheduler_loop(StopAfterOneCycle())

    assert waits == [5.0]


def test_deadlock_uses_official_hotlist_players_and_a_free_price_override(isolated_runtime):
    runtime = isolated_runtime
    stamp = runtime.now_iso()

    recorded = crawler_data.apply_special_free_app_overrides(
        [{"appid": 1422450, "current_players": 123456}], stamp
    )

    assert recorded == 1
    with runtime.database_connection() as conn:
        game = conn.execute("SELECT name,is_free FROM games WHERE appid=1422450").fetchone()
        player = conn.execute("SELECT player_count FROM player_snapshots WHERE appid=1422450").fetchone()
        price = conn.execute(
            "SELECT currency,initial,final,final_formatted FROM price_snapshots WHERE appid=1422450"
        ).fetchone()
    assert tuple(game) == ("Deadlock", 1)
    assert tuple(player) == (123456,)
    assert tuple(price) == ("CNY", 0, 0, "Free")


def test_itad_lookup_orchestration_persists_resolved_ids(monkeypatch):
    saved = []

    async def fake_lookup(appids):
        assert appids == [10, 20]
        return {10: "itad-10", 20: "itad-20"}

    monkeypatch.setattr(steam_client, "lookup_itad_game_ids", fake_lookup)
    monkeypatch.setattr(crawler_data, "save_itad_game_ids", lambda rows, _stamp: saved.extend(rows))

    result = asyncio.run(crawler.fetch_itad_game_ids_async([10, 20]))

    assert result == {10: "itad-10", 20: "itad-20"}
    assert sorted(saved) == [(10, "itad-10"), (20, "itad-20")]


def test_itad_history_orchestration_keeps_network_and_persistence_separate(
    isolated_runtime, monkeypatch, insert_game
):
    runtime = isolated_runtime
    appid = insert_game(30, "History Low Test")
    with runtime.database_connection() as conn:
        conn.execute("UPDATE games SET itad_game_id='itad-30' WHERE appid=?", (appid,))
    captured = []

    async def fake_fetch(mapping, countries, stamp):
        assert mapping == {"itad-30": appid}
        assert countries == ("CN",)
        return [(appid, "itad-30", "CN", None, None, "CNY", 10.0, 1000, 10.0, None, 0, None, stamp)]

    monkeypatch.setattr(runtime, "ITAD_API_KEY", "configured")
    monkeypatch.setattr(steam_client, "fetch_itad_history_low_rows", fake_fetch)
    monkeypatch.setattr(crawler_data, "upsert_historical_lows", lambda rows: captured.extend(rows))

    stamp = asyncio.run(crawler.fetch_itad_history_lows_async([appid], ("CN",)))

    assert captured[0][0:3] == (appid, "itad-30", "CN")
    assert captured[0][-1] == stamp


def test_failed_itad_refresh_preserves_cached_low(isolated_runtime, monkeypatch, insert_game):
    runtime = isolated_runtime
    appid = insert_game(31, "Cached ITAD Low")
    stamp = runtime.now_iso()
    with runtime.database_connection() as conn:
        conn.execute("UPDATE games SET itad_game_id='itad-31' WHERE appid=?", (appid,))
        conn.execute(
            """
            INSERT INTO historical_lows(
                appid, itad_game_id, country, currency, amount,
                amount_int, amount_cny, fetched_at
            ) VALUES (?, 'itad-31', 'CN', 'CNY', 12.0, 1200, 12.0, ?)
            """,
            (appid, stamp),
        )

    async def failed_fetch(*_args, **_kwargs):
        raise RuntimeError("ITAD unavailable")

    monkeypatch.setattr(runtime, "ITAD_API_KEY", "configured")
    monkeypatch.setattr(steam_client, "fetch_itad_history_low_rows", failed_fetch)

    try:
        asyncio.run(crawler.fetch_itad_history_lows_async([appid], ("CN",)))
    except RuntimeError:
        pass

    with runtime.database_connection() as conn:
        cached = conn.execute(
            "SELECT amount_cny, fetched_at FROM historical_lows WHERE appid=? AND country='CN'",
            (appid,),
        ).fetchone()
    assert tuple(cached) == (12.0, stamp)


def test_daily_backup_runs_once_per_day(isolated_runtime):
    runtime = isolated_runtime
    assert crawler.run_daily_backup_task() is True
    assert crawler.run_daily_backup_task() is False
    backups = list(
        runtime.DB_MIGRATION_BACKUP_DIR.glob(f"{runtime.DB_PATH.stem}-daily-*.sqlite3")
    )
    assert len(backups) == 1
    with runtime.database_connection() as conn:
        assert runtime.get_crawl_state(conn, "daily_database_backup_at")


def test_metadata_completion_does_not_revive_finished_regional_prices(
    isolated_runtime, monkeypatch, insert_game
):
    runtime = isolated_runtime
    appid = insert_game(32, "Regional Task")
    runtime.enqueue_crawl_tasks([appid], "regional_prices", 70)
    runtime.complete_crawl_tasks([appid], "regional_prices")

    async def fake_metadata(appids, **_kwargs):
        assert appids == [appid]
        return ([{"appid": appid}], [], [], runtime.now_iso())

    monkeypatch.setattr(crawler_data, "get_hot_full_metadata_due_appids", lambda _limit: [appid])
    monkeypatch.setattr(crawler_fetch, "fetch_hot_metadata_async", fake_metadata)
    monkeypatch.setattr(crawler_data, "upsert_hot_metadata_batch", lambda *_args: None)

    assert crawler.run_metadata_task() is True
    with runtime.database_connection() as conn:
        task = conn.execute(
            "SELECT status, completed_at FROM crawl_tasks WHERE appid=? AND task_type='regional_prices'",
            (appid,),
        ).fetchone()
    assert tuple(task)[0] == "done"
    assert tuple(task)[1] is not None
