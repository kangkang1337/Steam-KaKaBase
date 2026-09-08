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
