import asyncio

from backend import crawler, steam_client


def test_itad_lookup_orchestration_persists_resolved_ids(monkeypatch):
    saved = []

    async def fake_lookup(appids):
        assert appids == [10, 20]
        return {10: "itad-10", 20: "itad-20"}

    monkeypatch.setattr(steam_client, "lookup_itad_game_ids", fake_lookup)
    monkeypatch.setattr(crawler.runtime, "save_itad_game_ids", lambda rows: saved.extend(rows))

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
    monkeypatch.setattr(runtime, "upsert_historical_lows", lambda rows: captured.extend(rows))

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

    monkeypatch.setattr(runtime, "get_hot_full_metadata_due_appids", lambda _limit: [appid])
    monkeypatch.setattr(runtime, "fetch_hot_metadata_async", fake_metadata)
    monkeypatch.setattr(runtime, "upsert_hot_metadata_batch", lambda *_args: None)

    assert crawler.run_metadata_task() is True
    with runtime.database_connection() as conn:
        task = conn.execute(
            "SELECT status, completed_at FROM crawl_tasks WHERE appid=? AND task_type='regional_prices'",
            (appid,),
        ).fetchone()
    assert tuple(task)[0] == "done"
    assert tuple(task)[1] is not None
