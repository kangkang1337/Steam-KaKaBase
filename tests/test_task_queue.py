import sqlite3

import pytest


def task_row(runtime, appid, task_type):
    with sqlite3.connect(runtime.DB_PATH) as conn:
        return conn.execute(
            "SELECT status, completed_at, locked_until, last_error FROM crawl_tasks WHERE appid = ? AND task_type = ?",
            (appid, task_type),
        ).fetchone()


def test_database_connection_closes_after_use(isolated_runtime):
    with isolated_runtime.database_connection() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_task_claim_and_complete(isolated_runtime, insert_game):
    runtime = isolated_runtime
    appid = insert_game()

    runtime.enqueue_crawl_tasks([appid], "preview", 50)
    assert runtime.claim_crawl_tasks("preview", 1) == [appid]

    runtime.complete_crawl_tasks([appid], "preview")
    row = task_row(runtime, appid, "preview")
    assert row[0] == "done"
    assert row[1] is not None
    assert row[2] is None
    assert row[3] is None


def test_unavailable_task_requires_user_priority_to_requeue(isolated_runtime, insert_game):
    runtime = isolated_runtime
    appid = insert_game()

    runtime.enqueue_crawl_tasks([appid], "metadata", 50)
    runtime.claim_crawl_tasks("metadata", 1)
    runtime.mark_crawl_tasks_not_available([appid], "metadata", "no public data")

    runtime.enqueue_crawl_tasks([appid], "metadata", 80)
    assert task_row(runtime, appid, "metadata")[0] == "not_available"

    runtime.enqueue_crawl_tasks([appid], "metadata", 100)
    assert task_row(runtime, appid, "metadata")[0] == "pending"


def test_temporary_and_permanent_failures_are_distinct(isolated_runtime, insert_game):
    runtime = isolated_runtime
    first = insert_game(101, "Temporary")
    second = insert_game(102, "Permanent")
    runtime.enqueue_crawl_tasks([first, second], "reviews", 50)
    runtime.claim_crawl_tasks("reviews", 2)

    runtime.fail_crawl_tasks([first], "reviews", "timeout", retry_minutes=10)
    runtime.fail_crawl_tasks([second], "reviews", "bad schema", terminal=True)

    assert task_row(runtime, first, "reviews")[0] == "retry"
    assert task_row(runtime, second, "reviews")[0] == "permanent_failed"


def test_empty_price_and_zero_reviews_record_freshness(isolated_runtime, insert_game):
    runtime = isolated_runtime
    appid = insert_game()
    stamp = runtime.now_iso()

    runtime.upsert_hot_price_batch(
        [{"appid": appid, "name": "Counter-Strike 2", "is_free": 0, "has_price": False}],
        stamp,
    )
    runtime.upsert_review_batch(
        [{"appid": appid, "review_score": None, "total_reviews": 0, "has_reviews": False}],
        stamp,
    )

    with sqlite3.connect(runtime.DB_PATH) as conn:
        row = conn.execute(
            "SELECT price_updated_at, review_updated_at, total_reviews FROM game_latest_state WHERE appid = ?",
            (appid,),
        ).fetchone()

    assert row == (stamp, stamp, 0)
