import sqlite3
from datetime import datetime, timedelta

import pytest


def _insert_pickable_niche_game(runtime, appid, score):
    stamp = runtime.now_iso()
    with runtime.database_connection() as conn:
        conn.execute(
            """
            INSERT INTO niche_pool(
                appid, name, current_players, peak_players, review_score,
                total_reviews, cn_price, cn_price_final, cn_price_currency,
                weighted_score, eligible, fetched_at, evaluated_at
            ) VALUES (?, ?, 50, 500, 90, 1000, '¥ 20.00', 2000, 'CNY', ?, 1, ?, ?)
            """,
            (appid, f"Niche {appid}", score, stamp, stamp),
        )


@pytest.mark.parametrize("pool_size", [20, 21, 39, 40, 41])
def test_niche_pool_keeps_twenty_display_rows(isolated_runtime, pool_size):
    runtime = isolated_runtime
    stamp = runtime.now_iso()
    rows = [
        (
            10000 + index,
            f"Niche {index}",
            20 + index,
            100 + index,
            90,
            1000 + index,
            float(pool_size - index),
            stamp,
            stamp,
        )
        for index in range(pool_size)
    ]
    with sqlite3.connect(runtime.DB_PATH) as conn:
        conn.executemany(
            """
            INSERT INTO niche_pool(
                appid, name, current_players, peak_players, review_score,
                total_reviews, weighted_score, eligible, fetched_at, evaluated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            rows,
        )

    displayed = runtime.list_niche_pool_games(20)

    assert len(displayed) == 20
    assert displayed == sorted(displayed, key=lambda game: game["weighted_score"], reverse=True)


def test_daily_niche_pick_excludes_previous_seven_recommendations(isolated_runtime):
    runtime = isolated_runtime
    refresh_key = runtime.daily_refresh_key()
    for index in range(8):
        _insert_pickable_niche_game(runtime, 9000 + index, 100 - index)
    with runtime.database_connection() as conn:
        for days_ago, appid in enumerate(range(9000, 9007), 1):
            recommendation_date = (
                datetime.strptime(refresh_key, "%Y-%m-%d") - timedelta(days=days_ago)
            ).strftime("%Y-%m-%d")
            conn.execute(
                """
                INSERT INTO niche_recommendation_snapshots(
                    recommendation_date, appid, name, weighted_score, created_at
                ) VALUES (?, ?, ?, 1, ?)
                """,
                (recommendation_date, appid, f"Niche {appid}", runtime.now_iso()),
            )

    chosen = runtime.list_niche_pool_pick()

    assert chosen["appid"] == 9007


def test_daily_niche_snapshot_repairs_a_saved_repeat(isolated_runtime):
    runtime = isolated_runtime
    refresh_key = runtime.daily_refresh_key()
    previous_key = (
        datetime.strptime(refresh_key, "%Y-%m-%d") - timedelta(days=1)
    ).strftime("%Y-%m-%d")
    _insert_pickable_niche_game(runtime, 9101, 100)
    _insert_pickable_niche_game(runtime, 9102, 90)
    with runtime.database_connection() as conn:
        for recommendation_date in (previous_key, refresh_key):
            conn.execute(
                """
                INSERT INTO niche_recommendation_snapshots(
                    recommendation_date, appid, name, current_players,
                    review_score, total_reviews, weighted_score, created_at
                ) VALUES (?, 9101, 'Repeated', 50, 90, 1000, 100, ?)
                """,
                (recommendation_date, runtime.now_iso()),
            )
        runtime.set_crawl_state(conn, "niche_pick_" + refresh_key, "9101")

    recommendation = runtime.get_daily_niche_recommendation()

    assert recommendation["appid"] == 9102
    with runtime.database_connection() as conn:
        assert conn.execute(
            "SELECT appid FROM niche_recommendation_snapshots WHERE recommendation_date=?",
            (refresh_key,),
        ).fetchone() == (9102,)


def test_remote_candidate_price_survives_when_game_is_not_yet_eligible(isolated_runtime):
    runtime = isolated_runtime
    runtime.upsert_niche_pool_rows(
        [
            {
                "appid": 4242,
                "name": "Future Candidate",
                "header_image": "https://cdn.cloudflare.steamstatic.com/header.jpg",
                "current_players": 0,
                "peak_players": 0,
                "review_score": 95,
                "total_reviews": 1000,
                "cn_price": "¥ 68.00",
                "cn_price_initial": 8800,
                "cn_price_final": 6800,
                "cn_price_currency": "CNY",
                "cn_discount_percent": 23,
                "is_free": 0,
                "release_date": "2026-01-01",
                "fetched_at": runtime.now_iso(),
            }
        ],
        persist_prices=True,
    )

    with sqlite3.connect(runtime.DB_PATH) as conn:
        assert conn.execute("SELECT 1 FROM niche_pool WHERE appid = 4242").fetchone() is None
        snapshot = conn.execute(
            "SELECT initial, final FROM price_snapshots WHERE appid = 4242 AND region = 'CN'"
        ).fetchone()
        latest = conn.execute(
            "SELECT cn_price, cn_price_final, price_updated_at FROM game_latest_state WHERE appid = 4242"
        ).fetchone()

    assert snapshot == (8800, 6800)
    assert latest[0:2] == ("¥ 68.00", 6800)
    assert latest[2] is not None


@pytest.mark.parametrize(
    ("total_reviews", "eligible"),
    [(50000, True), (50001, False)],
)
def test_niche_pool_enforces_review_count_cap(isolated_runtime, total_reviews, eligible):
    runtime = isolated_runtime
    runtime.upsert_niche_pool_rows([{
        "appid": 6000 + total_reviews,
        "name": "Review Cap Candidate",
        "current_players": 20,
        "peak_players": 100,
        "review_score": 90,
        "total_reviews": total_reviews,
        "release_date": "2026-01-01",
    }])

    assert (runtime.count_eligible_niche_pool() == 1) is eligible


def test_score_refresh_removes_candidate_over_review_count_cap(isolated_runtime):
    runtime = isolated_runtime
    stamp = runtime.now_iso()
    with sqlite3.connect(runtime.DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO niche_pool(
                appid, name, current_players, peak_players, review_score,
                total_reviews, release_date, weighted_score, eligible,
                fetched_at, evaluated_at
            ) VALUES (7001, 'Mainstream Candidate', 20, 100, 90, 50001,
                      '2026-01-01', 1, 1, ?, ?)
            """,
            (stamp, stamp),
        )

    runtime.refresh_niche_pool_scores_from_snapshots()

    with sqlite3.connect(runtime.DB_PATH) as conn:
        assert conn.execute(
            "SELECT 1 FROM niche_pool WHERE appid=7001"
        ).fetchone() is None


def test_new_price_state_updates_and_can_clear_niche_price(isolated_runtime, insert_game):
    runtime = isolated_runtime
    appid = insert_game(4343, "Price Sync")
    stamp = runtime.now_iso()
    with sqlite3.connect(runtime.DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO niche_pool(
                appid, name, current_players, peak_players, review_score,
                total_reviews, cn_price, cn_price_final, cn_price_currency,
                weighted_score, eligible, fetched_at, evaluated_at
            ) VALUES (?, ?, 20, 100, 90, 1000, '¥ 10.00', 1000, 'CNY', 1, 1, ?, ?)
            """,
            (appid, "Price Sync", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )

    runtime.upsert_hot_price_batch(
        [{
            "appid": appid,
            "name": "Price Sync",
            "is_free": 0,
            "currency": "CNY",
            "initial": 8800,
            "final": 6800,
            "discount_percent": 23,
            "final_formatted": "¥ 68.00",
            "has_price": True,
        }],
        stamp,
    )
    assert runtime.list_niche_pool_games(20)[0]["cn_price_final"] == 6800

    later = "2099-01-01T00:00:00+00:00"
    runtime.upsert_hot_price_batch(
        [{"appid": appid, "name": "Price Sync", "is_free": 0, "has_price": False}],
        later,
    )
    listed = runtime.list_niche_pool_games(20)[0]
    with sqlite3.connect(runtime.DB_PATH) as conn:
        niche = conn.execute(
            "SELECT cn_price, cn_price_final FROM niche_pool WHERE appid = ?", (appid,)
        ).fetchone()
        latest = conn.execute(
            "SELECT cn_price, cn_price_final, price_updated_at FROM game_latest_state WHERE appid = ?", (appid,)
        ).fetchone()

    assert listed["cn_price"] is None
    assert niche == (None, None)
    assert latest == (None, None, later)


def test_startup_requeues_stale_niche_prices(isolated_runtime, insert_game):
    runtime = isolated_runtime
    appid = insert_game(4444, "Stale Price")
    old = "2020-01-01T00:00:00+00:00"
    with sqlite3.connect(runtime.DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO niche_pool(
                appid, name, current_players, peak_players, review_score,
                total_reviews, weighted_score, eligible, fetched_at, evaluated_at
            ) VALUES (?, ?, 20, 100, 90, 1000, 1, 1, ?, ?)
            """,
            (appid, "Stale Price", old, old),
        )
        conn.execute(
            """
            INSERT INTO crawl_tasks(
                appid, task_type, priority, status, next_attempt_at,
                completed_at, updated_at
            ) VALUES (?, 'preview', 20, 'done', ?, ?, ?)
            """,
            (appid, old, old, old),
        )

    runtime.init_db()

    with sqlite3.connect(runtime.DB_PATH) as conn:
        task = conn.execute(
            "SELECT status, priority, completed_at FROM crawl_tasks WHERE appid = ? AND task_type = 'preview'",
            (appid,),
        ).fetchone()

    assert task == ("pending", 60, None)


def test_score_refresh_does_not_delete_unclassified_cached_candidate(
    isolated_runtime, insert_game
):
    runtime = isolated_runtime
    appid = insert_game(5151, "Unclassified Candidate")
    with sqlite3.connect(runtime.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO steam_catalog(appid, name, updated_at) VALUES (?, ?, ?)",
            (appid, "Unclassified Candidate", runtime.now_iso()),
        )
        conn.execute(
            """
            INSERT INTO niche_pool(
                appid, name, current_players, peak_players, review_score,
                total_reviews, weighted_score, eligible, fetched_at, evaluated_at
            ) VALUES (?, ?, 0, 0, NULL, 0, 0, 1, ?, ?)
            """,
            (appid, "Unclassified Candidate", runtime.now_iso(), runtime.now_iso()),
        )

    runtime.refresh_niche_pool_scores_from_snapshots()

    with sqlite3.connect(runtime.DB_PATH) as conn:
        assert conn.execute(
            "SELECT eligible FROM niche_pool WHERE appid=?", (appid,)
        ).fetchone() == (1,)
