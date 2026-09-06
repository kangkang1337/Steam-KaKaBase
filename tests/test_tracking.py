import sqlite3


def read_game(runtime, appid):
    with sqlite3.connect(runtime.DB_PATH) as conn:
        return conn.execute("SELECT name, tracked FROM games WHERE appid = ?", (appid,)).fetchone()


def test_track_and_untrack_persist(isolated_runtime):
    runtime = isolated_runtime
    runtime.quick_track_game(730, "Counter-Strike 2")
    assert read_game(runtime, 730) == ("Counter-Strike 2", 1)

    runtime.untrack_game(730)
    assert read_game(runtime, 730) == ("Counter-Strike 2", 0)


def test_placeholder_does_not_replace_known_name(isolated_runtime):
    runtime = isolated_runtime
    runtime.quick_track_game(730, "Counter-Strike 2")
    runtime.quick_track_game(730, "App 730")
    assert read_game(runtime, 730) == ("Counter-Strike 2", 1)


def test_list_games_returns_only_tracked_games(isolated_runtime):
    runtime = isolated_runtime
    runtime.quick_track_game(730, "Counter-Strike 2")
    runtime.quick_track_game(570, "Dota 2")
    runtime.untrack_game(570)

    games = runtime.list_games()
    assert [game["appid"] for game in games] == [730]
