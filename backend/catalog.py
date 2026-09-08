"""Steam catalog scan and incremental enrichment workflows."""

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

from . import _runtime as runtime


def sync_steam_catalog_once(force=False):
    """Advance the persistent lightweight AppList scan by one bounded batch."""
    if runtime.service_cooldown_remaining_seconds("steam_api"):
        return False
    today = datetime.now().strftime("%Y-%m-%d")
    stamp = runtime.now_iso()
    with runtime.database_connection() as conn:
        last_sync = runtime.get_crawl_state(conn, "steam_catalog_sync_date")
        cursor_value = runtime.get_crawl_state(conn, "steam_catalog_scan_cursor")
        generation = int(runtime.get_crawl_state(conn, "steam_catalog_scan_generation") or 1)
        completed_at = runtime.get_crawl_state(conn, "steam_catalog_scan_completed_at")
    if not force and last_sync == today:
        return False
    if completed_at and not force and not runtime.is_due(
        completed_at, runtime.CATALOG_RESCAN_DAYS * 24 * 60
    ):
        return False

    if completed_at:
        last_appid = 0
        generation += 1
        with runtime.database_connection() as conn:
            runtime.set_crawl_state(conn, "steam_catalog_scan_cursor", "0")
            runtime.set_crawl_state(conn, "steam_catalog_scan_generation", str(generation))
            runtime.set_crawl_state(conn, "steam_catalog_scan_started_at", stamp)
            runtime.set_crawl_state(conn, "steam_catalog_scan_completed_at", "")
    elif cursor_value is None:
        with runtime.database_connection() as conn:
            last_appid = int(
                conn.execute("SELECT COALESCE(MAX(appid), 0) FROM steam_catalog").fetchone()[0] or 0
            )
            runtime.set_crawl_state(conn, "steam_catalog_scan_cursor", str(last_appid))
            runtime.set_crawl_state(conn, "steam_catalog_scan_generation", str(generation))
            runtime.set_crawl_state(conn, "steam_catalog_scan_started_at", stamp)
    else:
        last_appid = int(cursor_value or 0)

    scanned = 0
    saved = 0
    scan_complete = False
    while scanned < runtime.CATALOG_SCAN_BATCH_LIMIT:
        page_size = min(500, runtime.CATALOG_SCAN_BATCH_LIMIT - scanned)
        payload = runtime.fetch_store_catalog_page(last_appid, page_size)
        response = (payload or {}).get("response") or payload or {}
        apps = response.get("apps") or response.get("items") or []
        if not apps:
            scan_complete = not bool(response.get("have_more_results"))
            break
        previous_last = last_appid
        rows = []
        for item in apps:
            try:
                appid = int(item.get("appid") or item.get("id"))
            except (TypeError, ValueError, AttributeError):
                continue
            name = runtime.clean_hot_name(item.get("name"))
            if appid > 0 and name:
                rows.append((appid, name, stamp, stamp, generation))
        last_appid = int(response.get("last_appid") or response.get("lastAppId") or 0)
        if not last_appid:
            last_appid = max((row[0] for row in rows), default=previous_last)
        if last_appid <= previous_last:
            raise runtime.ExternalDataUnavailable("Steam AppList cursor did not advance")
        with runtime.database_connection() as conn:
            conn.executemany(
                """
                INSERT INTO steam_catalog(
                    appid, name, updated_at, last_seen_at, scan_generation, enrich_status
                ) VALUES (?, ?, ?, ?, ?, 'pending')
                ON CONFLICT(appid) DO UPDATE SET
                    name=excluded.name,
                    updated_at=excluded.updated_at,
                    last_seen_at=excluded.last_seen_at,
                    scan_generation=excluded.scan_generation,
                    enrich_status=CASE
                        WHEN steam_catalog.app_type IN ('unknown', 'game')
                             AND steam_catalog.last_enriched_at IS NULL THEN 'pending'
                        ELSE steam_catalog.enrich_status
                    END
                """,
                rows,
            )
            runtime.set_crawl_state(conn, "steam_catalog_scan_cursor", str(last_appid))
            runtime.set_crawl_state(conn, "steam_catalog_scan_generation", str(generation))
            runtime.set_crawl_state(conn, "steam_catalog_scan_last_batch_at", stamp)
        scanned += len(apps)
        saved += len(rows)
        if not response.get("have_more_results"):
            scan_complete = True
            break

    with runtime.database_connection() as conn:
        runtime.set_crawl_state(conn, "steam_catalog_sync_date", today)
        if scan_complete:
            runtime.set_crawl_state(conn, "steam_catalog_scan_completed_at", stamp)
    runtime.log_event(
        f"steam catalog scan generation={generation} cursor={last_appid} "
        f"scanned={scanned} saved={saved} complete={scan_complete}"
    )
    return bool(scanned or scan_complete)


def catalog_enrich_quota():
    today = datetime.now().strftime("%Y-%m-%d")
    with runtime.database_connection() as conn:
        saved_date = runtime.get_crawl_state(conn, "steam_catalog_enrich_date")
        saved_count = (
            int(runtime.get_crawl_state(conn, "steam_catalog_enrich_count") or 0)
            if saved_date == today
            else 0
        )
    return today, saved_count


def run_catalog_enrich_task():
    if runtime.service_cooldown_remaining_seconds("steam_store"):
        return False
    today, used = catalog_enrich_quota()
    remaining = runtime.CATALOG_ENRICH_DAILY_LIMIT - used
    if remaining <= 0:
        return False
    limit = min(runtime.CATALOG_ENRICH_BATCH_LIMIT, remaining)
    now = runtime.now_iso()
    with runtime.database_connection() as conn:
        rows = conn.execute(
            """
            SELECT appid FROM steam_catalog
            WHERE (
                enrich_status IN ('pending', 'retry')
                OR (enrich_status='done' AND next_enrich_at <= ?)
            )
              AND app_type IN ('unknown', 'game')
            ORDER BY CASE WHEN last_enriched_at IS NULL THEN 0 ELSE 1 END,
                     enrich_attempts ASC, updated_at ASC
            LIMIT ?
            """,
            (now, limit),
        ).fetchall()
    appids = [int(row[0]) for row in rows]
    if not appids:
        return False

    try:
        with runtime.database_connection() as conn:
            conn.executemany(
                "UPDATE steam_catalog SET enrich_status='running', enrich_attempts=enrich_attempts+1 WHERE appid=?",
                [(appid,) for appid in appids],
            )
        fetched = asyncio.run(runtime.fetch_niche_candidates_async(appids))
        game_rows = [row for row in fetched if row.get("catalog_result") == "game"]
        saved = runtime.upsert_niche_pool_rows(game_rows, persist_prices=True)
        results = {int(row["appid"]): row for row in fetched}
        next_week = (datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=0).isoformat()
        next_hour = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).isoformat()
        with runtime.database_connection() as conn:
            for appid in appids:
                result = results.get(appid) or {
                    "catalog_result": "retry",
                    "error": "missing async result",
                }
                outcome = result.get("catalog_result")
                if outcome == "game":
                    conn.execute(
                        """
                        UPDATE steam_catalog SET app_type='game', app_type_checked_at=?,
                            enrich_status='done', last_enriched_at=?, next_enrich_at=?,
                            last_error=NULL WHERE appid=?
                        """,
                        (now, now, next_week, appid),
                    )
                elif outcome == "excluded":
                    conn.execute(
                        """
                        UPDATE steam_catalog SET app_type=?, app_type_checked_at=?,
                            enrich_status='excluded', last_enriched_at=?, next_enrich_at=NULL,
                            last_error=NULL WHERE appid=?
                        """,
                        (result.get("app_type") or "other", now, now, appid),
                    )
                    conn.execute("DELETE FROM niche_pool WHERE appid=?", (appid,))
                elif outcome == "not_available":
                    conn.execute(
                        """
                        UPDATE steam_catalog SET app_type_checked_at=?,
                            enrich_status='not_available', last_enriched_at=?,
                            next_enrich_at=NULL, last_error=? WHERE appid=?
                        """,
                        (now, now, result.get("error") or "Steam AppDetails unavailable", appid),
                    )
                    conn.execute("DELETE FROM niche_pool WHERE appid=?", (appid,))
                else:
                    conn.execute(
                        "UPDATE steam_catalog SET enrich_status='retry', next_enrich_at=?, last_error=? WHERE appid=?",
                        (next_hour, result.get("error") or "temporary enrich failure", appid),
                    )
            runtime.set_crawl_state(conn, "steam_catalog_enrich_date", today)
            runtime.set_crawl_state(conn, "steam_catalog_enrich_count", str(used + len(appids)))
        excluded = sum(1 for row in fetched if row.get("catalog_result") == "excluded")
        runtime.log_event(
            f"catalog enrich batch attempted={len(appids)} games={len(game_rows)} "
            f"excluded={excluded} saved={saved} daily={used + len(appids)}/"
            f"{runtime.CATALOG_ENRICH_DAILY_LIMIT}"
        )
        return True
    except runtime.SteamRateLimited as exc:
        with runtime.database_connection() as conn:
            conn.executemany(
                "UPDATE steam_catalog SET enrich_status='retry', next_enrich_at=?, last_error=? WHERE appid=?",
                [
                    (
                        (datetime.now(timezone.utc) + timedelta(minutes=10)).replace(microsecond=0).isoformat(),
                        str(exc),
                        appid,
                    )
                    for appid in appids
                ],
            )
        runtime.log_event(f"catalog enrich paused by rate limit: {exc}")
        return False
    except sqlite3.Error:
        raise
    except Exception as exc:
        with runtime.database_connection() as conn:
            conn.executemany(
                "UPDATE steam_catalog SET enrich_status='retry', next_enrich_at=?, last_error=? WHERE appid=?",
                [
                    (
                        (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).isoformat(),
                        str(exc),
                        appid,
                    )
                    for appid in appids
                ],
            )
        runtime.log_event(f"catalog enrich failed: {exc}")
        return False
