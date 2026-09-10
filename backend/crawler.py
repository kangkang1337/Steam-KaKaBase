"""Background scheduling and data-refresh orchestration."""

import asyncio
import sqlite3
import threading
import time

from . import config
from . import _runtime as runtime
from .migrations import create_database_backup


def run_daily_backup_task(force=False):
    if not config.DB_DAILY_BACKUP_ENABLED:
        return False
    with runtime.database_connection() as conn:
        last_backup = runtime.get_crawl_state(conn, "daily_database_backup_at")
    if not (force or runtime.is_due(last_backup, 24 * 60)):
        return False
    path = create_database_backup(
        config.DB_PATH,
        config.DB_MIGRATION_BACKUP_DIR,
        label="daily",
        keep=config.DB_DAILY_BACKUP_KEEP,
        retention_label="daily",
    )
    stamp = runtime.now_iso()
    with runtime.database_connection() as conn:
        runtime.set_crawl_state(conn, "daily_database_backup_at", stamp)
        runtime.set_crawl_state(conn, "daily_database_backup_path", str(path))
    runtime.log_event(f"daily database backup completed path={path.name}")
    return True


def run_hotlist_task(force=False):
    if runtime.service_cooldown_remaining_seconds("steam_api"):
        return False
    with runtime.database_connection() as conn:
        hotlist_at = runtime.get_crawl_state(conn, "hotlist_at")
    if not (force or runtime.is_due(hotlist_at, runtime.HOTLIST_REFRESH_HOURS * 60)):
        return False
    rows = asyncio.run(runtime.fetch_official_hotlist_async())
    if not rows:
        runtime.log_event("hotlist refresh skipped: no rows returned")
        return False
    stamp = runtime.now_iso()
    for batch in runtime.chunks(rows[:runtime.HOTLIST_TARGET], runtime.HOTLIST_BATCH_SIZE):
        runtime.upsert_hot_games_batch(batch, stamp)
    with runtime.database_connection() as conn:
        appids = [int(row["appid"]) for row in rows[:runtime.HOTLIST_TARGET]]
        if appids:
            conn.execute(
                f"DELETE FROM hot_games WHERE appid NOT IN ({','.join('?' for _ in appids)})",
                appids,
            )
        runtime.set_crawl_state(conn, "hotlist_at", stamp)
        generation = int(runtime.get_crawl_state(conn, "hotlist_generation") or 0) + 1
        runtime.set_crawl_state(conn, "hotlist_generation", str(generation))
    runtime.enqueue_hot_work()
    try:
        runtime.refresh_steam_app_names_once()
    except Exception as exc:
        runtime.log_event(f"steam app names refresh skipped: {exc}")
    queued = runtime.enqueue_missing_hot_previews(
        limit=runtime.HOT_FULL_METADATA_TOP_LIMIT, priority=90
    )
    if queued:
        runtime.log_event(f"hot preview metadata queued rows={queued}")
    runtime.log_event(f"hotlist refreshed rows={len(rows[:runtime.HOTLIST_TARGET])}")
    return True


def run_players_task(force=False):
    if runtime.service_cooldown_remaining_seconds("steam_api"):
        return False
    due_appids = runtime.get_hot_appids(runtime.HOTLIST_TARGET) if force else runtime.get_due_hot_player_appids()
    runtime.enqueue_crawl_tasks(due_appids, "players", 20)
    appids = runtime.claim_crawl_tasks("players", runtime.HOTLIST_TARGET)
    if not appids:
        return False
    try:
        report = asyncio.run(runtime.fetch_players_for_appids_async(appids))
        successful = report["success_appids"]
        failed = [appid for appid in appids if appid not in set(successful)]
        runtime.complete_crawl_tasks(successful, "players")
        runtime.fail_crawl_tasks(failed, "players", "Steam player request failed", retry_minutes=30)
        with runtime.database_connection() as conn:
            runtime.set_crawl_state(conn, "hot_players_at", report["stamp"])
        runtime.log_event(
            f"hot players refreshed success={report['success']} failed={report['failed']} "
            f"skipped={report['skipped']}"
        )
        return True
    except sqlite3.Error as exc:
        runtime.fail_crawl_tasks(appids, "players", exc, terminal=True)
        raise
    except runtime.SteamRateLimited as exc:
        runtime.fail_crawl_tasks(appids, "players", exc, retry_minutes=10)
        raise
    except Exception as exc:
        runtime.fail_crawl_tasks(appids, "players", exc, retry_minutes=30)
        raise


def _run_appdetails_task(task_type, due_appids, limit, priority, persist, unavailable_message, success_message):
    if runtime.service_cooldown_remaining_seconds("steam_store"):
        return False
    runtime.enqueue_crawl_tasks(due_appids(limit), task_type, priority)
    appids = runtime.claim_crawl_tasks(task_type, limit)
    if not appids:
        return False
    try:
        rows, unavailable, retry, stamp = asyncio.run(
            runtime.fetch_hot_metadata_async(appids, full=task_type == "metadata", include_reviews=False)
        )
        persist(rows, stamp)
        successful_appids = [row["appid"] for row in rows]
        runtime.complete_crawl_tasks(successful_appids, task_type)
        if task_type == "metadata" and successful_appids:
            # The hot-list metadata pass has already earned this game a richer
            # cache.  Regional prices can now be expanded slowly in a separate
            # task without making the AppDetails pass fan out by region. Do
            # not revive an already completed regional task: on a fresh
            # deployment the bootstrap task may have completed just before
            # metadata, and immediately repeating all 13 Store requests is
            # unnecessary.
            with runtime.database_connection() as conn:
                for appid in successful_appids:
                    runtime.enqueue_crawl_task_once_in_conn(
                        conn, appid, "regional_prices", 70
                    )
        runtime.mark_crawl_tasks_not_available(unavailable, task_type, unavailable_message)
        runtime.fail_crawl_tasks(retry, task_type, "Steam AppDetails request failed")
        runtime.log_event(
            f"{success_message} success={len(rows)} unavailable={len(unavailable)} retry={len(retry)}"
        )
        return True
    except sqlite3.Error as exc:
        runtime.fail_crawl_tasks(appids, task_type, exc, terminal=True)
        raise
    except runtime.SteamRateLimited as exc:
        runtime.fail_crawl_tasks(appids, task_type, exc, retry_minutes=10)
        raise
    except Exception as exc:
        runtime.fail_crawl_tasks(appids, task_type, exc)
        raise


def run_price_task():
    return _run_appdetails_task(
        "price", runtime.get_hot_price_due_appids, runtime.HOT_PREVIEW_BATCH_LIMIT, 50,
        runtime.upsert_hot_price_batch, "Steam AppDetails unavailable", "hot prices refreshed",
    )


def run_preview_task():
    def persist(rows, stamp):
        runtime.upsert_hot_price_batch(rows, stamp)
        runtime.upsert_release_date_batch(rows, stamp)

    return _run_appdetails_task(
        "preview", runtime.get_hot_preview_due_appids, runtime.HOT_PREVIEW_BATCH_LIMIT, 50,
        persist, "Steam AppDetails unavailable", "hot preview refreshed",
    )


def run_static_task():
    return _run_appdetails_task(
        "static", runtime.get_hot_static_due_appids, runtime.HOT_PREVIEW_BATCH_LIMIT, 50,
        runtime.upsert_release_date_batch, "Steam AppDetails unavailable", "hot static fields refreshed",
    )


def run_metadata_task():
    return _run_appdetails_task(
        "metadata", runtime.get_hot_full_metadata_due_appids, runtime.HOT_METADATA_BATCH_LIMIT, 80,
        runtime.upsert_hot_metadata_batch, "Steam AppDetails unavailable", "hot metadata refreshed",
    )


def run_regional_prices_task():
    """Expand regional prices only after a game has earned detailed coverage."""
    if runtime.service_cooldown_remaining_seconds("steam_store"):
        return False
    appids = runtime.claim_crawl_tasks("regional_prices", 1)
    if not appids:
        return False
    appid = appids[0]
    try:
        result = runtime.refresh_regional_prices(appid)
        runtime.complete_crawl_tasks([appid], "regional_prices")
        runtime.log_event(
            f"regional prices refreshed appid={appid} regions={result['regions']}"
        )
        return True
    except sqlite3.Error as exc:
        runtime.fail_crawl_tasks([appid], "regional_prices", exc, terminal=True)
        raise
    except runtime.SteamRateLimited as exc:
        runtime.fail_crawl_tasks([appid], "regional_prices", exc, retry_minutes=10)
        raise
    except runtime.ExternalDataUnavailable as exc:
        runtime.mark_crawl_tasks_not_available([appid], "regional_prices", str(exc))
        return False
    except Exception as exc:
        runtime.fail_crawl_tasks([appid], "regional_prices", exc, retry_minutes=60)
        runtime.log_event(f"regional prices deferred appid={appid}: {exc}")
        return False


def run_review_task():
    if runtime.service_cooldown_remaining_seconds("steam_store"):
        return False
    runtime.enqueue_crawl_tasks(
        runtime.get_hot_review_due_appids(runtime.HOT_PREVIEW_BATCH_LIMIT), "reviews", 50
    )
    appids = runtime.claim_crawl_tasks("reviews", runtime.HOT_PREVIEW_BATCH_LIMIT)
    if not appids:
        return False
    try:
        rows, unavailable, retry, stamp = asyncio.run(runtime.fetch_hot_reviews_async(appids))
        runtime.upsert_review_batch(rows, stamp)
        runtime.complete_crawl_tasks([row["appid"] for row in rows], "reviews")
        runtime.mark_crawl_tasks_not_available(unavailable, "reviews", "Steam reviews unavailable")
        runtime.fail_crawl_tasks(retry, "reviews", "Steam review request failed")
        runtime.log_event(
            f"hot reviews refreshed success={len(rows)} unavailable={len(unavailable)} retry={len(retry)}"
        )
        return True
    except sqlite3.Error as exc:
        runtime.fail_crawl_tasks(appids, "reviews", exc, terminal=True)
        raise
    except runtime.SteamRateLimited as exc:
        runtime.fail_crawl_tasks(appids, "reviews", exc, retry_minutes=10)
        raise
    except Exception as exc:
        runtime.fail_crawl_tasks(appids, "reviews", exc)
        raise


async def fetch_itad_game_ids_async(appids):
    from .steam_client import ItadLookupBatchError, lookup_itad_game_ids

    try:
        found = await lookup_itad_game_ids(appids)
    except ItadLookupBatchError as exc:
        runtime.save_itad_game_ids(exc.partial_results.items())
        raise
    runtime.save_itad_game_ids(found.items())
    return found


async def fetch_itad_history_lows_async(appids, countries=("US", "CN")):
    if not runtime.ITAD_API_KEY:
        return runtime.now_iso()
    appids = [int(appid) for appid in appids]
    stamp = runtime.now_iso()
    if not appids:
        return stamp
    with runtime.database_connection() as conn:
        existing = {
            int(row[0]): row[1]
            for row in conn.execute(
                "SELECT appid, itad_game_id FROM games WHERE appid IN (%s) AND itad_game_id IS NOT NULL"
                % ",".join("?" for _ in appids),
                appids,
            ).fetchall()
        }
    missing = [appid for appid in appids if appid not in existing]
    if missing:
        existing.update(await fetch_itad_game_ids_async(missing))
    gid_to_appid = {
        game_id: appid for appid, game_id in existing.items()
        if game_id and game_id != runtime.ITAD_MISSING_GAME_ID
    }
    from .steam_client import fetch_itad_history_low_rows

    rows = await fetch_itad_history_low_rows(gid_to_appid, countries, stamp)
    runtime.upsert_historical_lows(rows)
    return stamp


def refresh_itad_history_lows(appids):
    if not runtime.ITAD_API_KEY:
        return None
    try:
        return asyncio.run(fetch_itad_history_lows_async(appids))
    except Exception as exc:
        sample = ",".join(str(appid) for appid in appids[:5])
        runtime.log_event(f"itad historylow failed appids={sample}: {exc}")
        raise


def get_missing_historylow_appids(limit=None):
    if not runtime.ITAD_API_KEY:
        return []
    from .db import query_missing_historylow_appids

    return query_missing_historylow_appids(
        limit or runtime.ITAD_HISTORYLOW_BATCH_LIMIT, runtime.ITAD_MISSING_GAME_ID
    )


def refresh_missing_history_lows_once():
    appids = get_missing_historylow_appids()
    if not appids:
        return []
    with runtime.STATUS_LOCK:
        runtime.REFRESH_STATUS["historylow_running"] = True
    try:
        stamp = refresh_itad_history_lows(appids)
        runtime.log_event(f"itad historylow backfilled rows={len(appids)} stamp={stamp}")
        return appids
    finally:
        with runtime.STATUS_LOCK:
            runtime.REFRESH_STATUS["historylow_running"] = False


def backfill_historylow_async(appid):
    if not runtime.ITAD_API_KEY:
        return
    appid = int(appid)
    with runtime.database_connection() as conn:
        if not runtime.is_due(
            runtime.get_crawl_state(conn, runtime.historylow_attempt_key(appid)),
            runtime.ITAD_HISTORYLOW_REFRESH_DAYS * 24 * 60,
        ):
            return
        runtime.set_crawl_state(conn, runtime.historylow_attempt_key(appid), runtime.now_iso())
    with runtime.HISTORYLOW_BACKFILL_LOCK:
        if appid in runtime.HISTORYLOW_BACKFILLING:
            return
        runtime.HISTORYLOW_BACKFILLING.add(appid)
    with runtime.STATUS_LOCK:
        runtime.REFRESH_STATUS["historylow_running"] = True

    def worker():
        try:
            refresh_itad_history_lows([appid])
        finally:
            with runtime.HISTORYLOW_BACKFILL_LOCK:
                runtime.HISTORYLOW_BACKFILLING.discard(appid)
                still_running = bool(runtime.HISTORYLOW_BACKFILLING)
            with runtime.STATUS_LOCK:
                runtime.REFRESH_STATUS["historylow_running"] = still_running

    threading.Thread(target=worker, daemon=True).start()


def run_historylow_task():
    if runtime.service_cooldown_remaining_seconds("itad"):
        return False
    with runtime.database_connection() as conn:
        last_run = runtime.get_crawl_state(conn, "historylow_backfill_at")
    if not runtime.is_due(last_run, 30):
        return False
    runtime.enqueue_crawl_tasks(get_missing_historylow_appids(), "historylow", 30)
    appids = runtime.claim_crawl_tasks("historylow", runtime.ITAD_HISTORYLOW_BATCH_LIMIT)
    if not appids:
        return False
    try:
        refresh_itad_history_lows(appids)
        runtime.complete_crawl_tasks(appids, "historylow")
        with runtime.database_connection() as conn:
            runtime.set_crawl_state(conn, "historylow_backfill_at", runtime.now_iso())
        runtime.log_event(f"itad historylow backfilled rows={len(appids)}")
        return True
    except sqlite3.Error as exc:
        runtime.fail_crawl_tasks(appids, "historylow", exc, terminal=True)
        raise
    except runtime.SteamRateLimited as exc:
        runtime.fail_crawl_tasks(appids, "historylow", exc, retry_minutes=10)
        raise
    except runtime.ExternalDataUnavailable as exc:
        runtime.fail_crawl_tasks(appids, "historylow", exc, retry_minutes=60)
        runtime.log_event(f"itad historylow deferred: {exc}")
        return False
    except Exception as exc:
        runtime.fail_crawl_tasks(appids, "historylow", exc)
        raise


def refresh_hot_database_once(force_hotlist=False, quick=False):
    if not runtime.HOT_REFRESH_LOCK.acquire(blocking=False):
        return ["hot refresh already running"]
    with runtime.STATUS_LOCK:
        runtime.REFRESH_STATUS["hot_running"] = True
        runtime.REFRESH_STATUS["hot_last_started_at"] = runtime.now_iso()
        runtime.REFRESH_STATUS["hot_last_errors"] = []
    errors = []
    try:
        run_hotlist_task(force=force_hotlist)
        if not quick:
            run_players_task()
            # Detail requests are explicitly user-prioritized. Run their
            # region expansion before low-priority hot-list enrichment.
            run_regional_prices_task()
            run_preview_task()
            run_review_task()
            run_metadata_task()
            run_historylow_task()
            runtime.run_niche_pool_task()
            try:
                runtime.sync_steam_catalog_once()
            except Exception as exc:
                runtime.log_event(f"steam catalog sync skipped: {exc}")
            runtime.run_catalog_enrich_task()
            from .services import refresh_daily_home_picks

            refresh_daily_home_picks()
            runtime.compact_player_snapshots_once()
            runtime.maintain_storage_once()
    except Exception as exc:
        message = str(exc)
        errors.append(message)
        runtime.log_event(f"hot refresh failed: {message}")
    finally:
        with runtime.STATUS_LOCK:
            runtime.REFRESH_STATUS["hot_running"] = False
            runtime.REFRESH_STATUS["hot_last_finished_at"] = runtime.now_iso()
            runtime.REFRESH_STATUS["hot_last_errors"] = errors[:20]
        runtime.HOT_REFRESH_LOCK.release()
    return errors


def refresh_hot_database_async(force_hotlist=False, quick=False):
    if runtime.HOT_REFRESH_LOCK.locked():
        return False

    def worker():
        refresh_hot_database_once(force_hotlist=force_hotlist, quick=quick)

    threading.Thread(target=worker, daemon=True).start()
    return True


def run_scheduler_cycle():
    try:
        run_daily_backup_task()
    except Exception as exc:
        runtime.log_event(f"daily database backup failed: {exc}")
    try:
        from .services import refresh_daily_home_picks

        refresh_daily_home_picks()
    except Exception as exc:
        runtime.log_event(f"daily homepage snapshot failed: {exc}")
    try:
        runtime.refresh_tracked_once()
    except Exception as exc:
        runtime.log_event(f"scheduler tracked refresh failed: {exc}")
    try:
        refresh_hot_database_once()
    except Exception as exc:
        runtime.log_event(f"scheduler hot refresh failed: {exc}")


def run_startup_prewarm():
    try:
        run_daily_backup_task()
    except Exception as exc:
        runtime.log_event(f"startup database backup failed: {exc}")
    refresh_hot_database_once(force_hotlist=runtime.count_hot_games() == 0, quick=True)
    runtime.run_niche_pool_task(
        force=runtime.count_eligible_niche_pool() < runtime.NICHE_POOL_DISPLAY_LIMIT
    )
    from .services import refresh_daily_home_picks

    refresh_daily_home_picks()


def scheduler_loop(stop_event=None):
    stop_event = stop_event or threading.Event()
    while not stop_event.wait(runtime.SCHEDULER_CHECK_SECONDS):
        run_scheduler_cycle()


def startup_prewarm_async():
    def worker():
        try:
            run_startup_prewarm()
        except Exception as exc:
            runtime.log_event(f"startup hotlist prewarm failed: {exc}")

    threading.Thread(target=worker, daemon=True).start()


# Transitional exports that have not moved out of _runtime yet.
cleanup_image_cache_once = runtime.cleanup_image_cache_once
enqueue_hot_work = runtime.enqueue_hot_work
maintain_storage_once = runtime.maintain_storage_once
refresh_tracked_once = runtime.refresh_tracked_once
run_catalog_enrich_task = runtime.run_catalog_enrich_task
run_niche_pool_task = runtime.run_niche_pool_task
snapshot_daily_niche_recommendation = runtime.snapshot_daily_niche_recommendation
sync_steam_catalog_once = runtime.sync_steam_catalog_once


__all__ = [name for name in globals() if not name.startswith("_")]
