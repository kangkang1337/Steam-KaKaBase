import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor


def seed_search_catalog(runtime, count=3000):
    stamp = runtime.now_iso()
    with sqlite3.connect(runtime.DB_PATH) as conn:
        conn.executemany(
            """
            INSERT INTO steam_catalog(appid, name, app_type, updated_at)
            VALUES (?, ?, 'game', ?)
            """,
            [
                (100000 + index, f"Performance Quest {index}", stamp)
                for index in range(count)
            ],
        )


def test_search_cache_benchmark(isolated_runtime):
    runtime = isolated_runtime
    runtime.SEARCH_CACHE.clear()
    seed_search_catalog(runtime)
    assert runtime.search_steam("performance quest 2999")

    started = time.perf_counter()
    for _ in range(250):
        assert runtime.search_steam("performance quest 2999")
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5
    assert runtime.get_search_metrics()["cache_hit_rate"] > 0.9


def test_parallel_search_reads_are_stable(isolated_runtime):
    runtime = isolated_runtime
    runtime.SEARCH_CACHE.clear()
    seed_search_catalog(runtime, count=500)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(
            lambda index: runtime.search_steam(f"performance quest {index}"),
            range(32),
        ))

    assert all(rows for rows in results)
