"""Steam catalog scan and incremental enrichment workflows."""

import asyncio
import math
import random
import sqlite3
from datetime import datetime, timedelta, timezone

from . import config
from .db import get_crawl_state, is_due, set_crawl_state, transaction
from .external_errors import ExternalDataUnavailable, SteamRateLimited
from .logging_utils import log_event
from .steam_client import (
    async_get_json,
    fetch_store_catalog_page,
    require_httpx,
    service_cooldown_remaining_seconds,
    steam_httpx_options,
)
from .utils import (
    UNKNOWN_GAME_NAME,
    clean_hot_name,
    fallback_game_name,
    is_obvious_non_game_name,
    is_recent_release,
    now_iso,
    release_recency_factor,
)


def sync_steam_catalog_once(force=False):
    """Advance the persistent lightweight AppList scan by one bounded batch."""
    if service_cooldown_remaining_seconds("steam_api"):
        return False
    today = datetime.now().strftime("%Y-%m-%d")
    stamp = now_iso()
    with transaction() as conn:
        last_sync = get_crawl_state(conn, "steam_catalog_sync_date")
        cursor_value = get_crawl_state(conn, "steam_catalog_scan_cursor")
        generation = int(get_crawl_state(conn, "steam_catalog_scan_generation") or 1)
        completed_at = get_crawl_state(conn, "steam_catalog_scan_completed_at")
    if not force and last_sync == today:
        return False
    if completed_at and not force and not is_due(
        completed_at, config.CATALOG_RESCAN_DAYS * 24 * 60
    ):
        return False

    if completed_at:
        last_appid = 0
        generation += 1
        with transaction() as conn:
            set_crawl_state(conn, "steam_catalog_scan_cursor", "0")
            set_crawl_state(conn, "steam_catalog_scan_generation", str(generation))
            set_crawl_state(conn, "steam_catalog_scan_started_at", stamp)
            set_crawl_state(conn, "steam_catalog_scan_completed_at", "")
    elif cursor_value is None:
        with transaction() as conn:
            last_appid = int(
                conn.execute("SELECT COALESCE(MAX(appid), 0) FROM steam_catalog").fetchone()[0] or 0
            )
            set_crawl_state(conn, "steam_catalog_scan_cursor", str(last_appid))
            set_crawl_state(conn, "steam_catalog_scan_generation", str(generation))
            set_crawl_state(conn, "steam_catalog_scan_started_at", stamp)
    else:
        last_appid = int(cursor_value or 0)

    scanned = 0
    saved = 0
    scan_complete = False
    while scanned < config.CATALOG_SCAN_BATCH_LIMIT:
        page_size = min(500, config.CATALOG_SCAN_BATCH_LIMIT - scanned)
        payload = fetch_store_catalog_page(last_appid, page_size)
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
            name = clean_hot_name(item.get("name"))
            if appid > 0 and name:
                rows.append((appid, name, stamp, stamp, generation))
        last_appid = int(response.get("last_appid") or response.get("lastAppId") or 0)
        if not last_appid:
            last_appid = max((row[0] for row in rows), default=previous_last)
        if last_appid <= previous_last:
            raise ExternalDataUnavailable("Steam AppList cursor did not advance")
        with transaction() as conn:
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
            set_crawl_state(conn, "steam_catalog_scan_cursor", str(last_appid))
            set_crawl_state(conn, "steam_catalog_scan_generation", str(generation))
            set_crawl_state(conn, "steam_catalog_scan_last_batch_at", stamp)
        scanned += len(apps)
        saved += len(rows)
        if not response.get("have_more_results"):
            scan_complete = True
            break

    with transaction() as conn:
        set_crawl_state(conn, "steam_catalog_sync_date", today)
        if scan_complete:
            set_crawl_state(conn, "steam_catalog_scan_completed_at", stamp)
    log_event(
        f"steam catalog scan generation={generation} cursor={last_appid} "
        f"scanned={scanned} saved={saved} complete={scan_complete}"
    )
    return bool(scanned or scan_complete)


def catalog_enrich_quota():
    today = datetime.now().strftime("%Y-%m-%d")
    with transaction() as conn:
        saved_date = get_crawl_state(conn, "steam_catalog_enrich_date")
        saved_count = (
            int(get_crawl_state(conn, "steam_catalog_enrich_count") or 0)
            if saved_date == today
            else 0
        )
    return today, saved_count


def run_catalog_enrich_task():
    if service_cooldown_remaining_seconds("steam_store"):
        return False
    today, used = catalog_enrich_quota()
    remaining = config.CATALOG_ENRICH_DAILY_LIMIT - used
    if remaining <= 0:
        return False
    limit = min(config.CATALOG_ENRICH_BATCH_LIMIT, remaining)
    now = now_iso()
    with transaction() as conn:
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
        with transaction() as conn:
            conn.executemany(
                "UPDATE steam_catalog SET enrich_status='running', enrich_attempts=enrich_attempts+1 WHERE appid=?",
                [(appid,) for appid in appids],
            )
        fetched = asyncio.run(fetch_niche_candidates_async(appids))
        game_rows = [row for row in fetched if row.get("catalog_result") == "game"]
        saved = upsert_niche_pool_rows(game_rows, persist_prices=True)
        results = {int(row["appid"]): row for row in fetched}
        next_week = (datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=0).isoformat()
        next_hour = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).isoformat()
        with transaction() as conn:
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
            set_crawl_state(conn, "steam_catalog_enrich_date", today)
            set_crawl_state(conn, "steam_catalog_enrich_count", str(used + len(appids)))
        excluded = sum(1 for row in fetched if row.get("catalog_result") == "excluded")
        log_event(
            f"catalog enrich batch attempted={len(appids)} games={len(game_rows)} "
            f"excluded={excluded} saved={saved} daily={used + len(appids)}/"
            f"{config.CATALOG_ENRICH_DAILY_LIMIT}"
        )
        return True
    except SteamRateLimited as exc:
        with transaction() as conn:
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
        log_event(f"catalog enrich paused by rate limit: {exc}")
        return False
    except sqlite3.Error:
        raise
    except Exception as exc:
        with transaction() as conn:
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
        log_event(f"catalog enrich failed: {exc}")
        return False


def _known_peak_players(appids):
    if not appids:
        return {}
    placeholders = ",".join("?" for _ in appids)
    with transaction() as conn:
        rows = conn.execute(
            f"SELECT appid, MAX(player_count) FROM player_snapshots WHERE appid IN ({placeholders}) GROUP BY appid",
            [int(appid) for appid in appids],
        ).fetchall()
    return {int(appid): int(peak or 0) for appid, peak in rows}


async def fetch_niche_candidates_async(appids):
    """Fetch a bounded catalog-enrichment batch through the shared Steam client."""
    httpx = require_httpx()
    semaphore = asyncio.Semaphore(min(8, max(1, config.HOTLIST_CONCURRENCY)))
    headers = {"User-Agent": config.STEAM_USER_AGENT}
    stamp = now_iso()
    peaks = _known_peak_players(appids)
    async with httpx.AsyncClient(
        timeout=config.STEAM_TIMEOUT_SECONDS, headers=headers, follow_redirects=True,
        **steam_httpx_options(),
    ) as client:
        async def fetch_one(appid):
            try:
                await asyncio.sleep(random.uniform(config.STORE_REQUEST_DELAY_MIN_SECONDS, config.STORE_REQUEST_DELAY_MAX_SECONDS))
                payload = await async_get_json(client, semaphore, "https://store.steampowered.com/api/appdetails", {"appids": appid, "cc": "CN", "l": "schinese"})
                app = payload.get(str(appid)) or {}
                data = app.get("data") or {}
                if not app.get("success") or not data:
                    return {"appid": int(appid), "catalog_result": "not_available", "app_type": "unknown"}
                app_type = str(data.get("type") or "unknown").strip().lower()
                if app_type != "game":
                    return {"appid": int(appid), "catalog_result": "excluded", "app_type": app_type}
                try:
                    review = await async_get_json(client, semaphore, f"https://store.steampowered.com/appreviews/{appid}", {"json": 1, "language": "all", "purchase_type": "all", "num_per_page": 0, "filter": "summary"})
                except ExternalDataUnavailable:
                    review = {}
                summary = review.get("query_summary") or {}
                positive, negative = int(summary.get("total_positive") or 0), int(summary.get("total_negative") or 0)
                try:
                    players_payload = await async_get_json(client, semaphore, "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/", {"appid": appid})
                except ExternalDataUnavailable:
                    players_payload = {}
                players = int((players_payload.get("response") or {}).get("player_count") or 0)
                price = data.get("price_overview") or {}
                total = positive + negative
                return {
                    "appid": int(appid), "catalog_result": "game", "app_type": "game",
                    "name": data.get("name") or UNKNOWN_GAME_NAME, "header_image": data.get("header_image"),
                    "current_players": players, "peak_players": max(players, peaks.get(int(appid), 0)),
                    "review_score": round((positive / total) * 100, 2) if total else None,
                    "total_reviews": total, "cn_price": price.get("final_formatted", "Free") if price or data.get("is_free") else None,
                    "cn_price_initial": price.get("initial", 0) if price or data.get("is_free") else None,
                    "cn_price_final": price.get("final", 0) if price or data.get("is_free") else None,
                    "cn_price_currency": price.get("currency") or ("CNY" if data.get("is_free") else None),
                    "cn_discount_percent": price.get("discount_percent", 0), "is_free": 1 if data.get("is_free") else 0,
                    "release_date": (data.get("release_date") or {}).get("date"), "fetched_at": stamp,
                }
            except SteamRateLimited:
                raise
            except ExternalDataUnavailable as exc:
                return {"appid": int(appid), "catalog_result": "not_available", "app_type": "unknown", "error": str(exc)}
            except Exception as exc:
                log_event(f"catalog candidate skipped appid={appid}: {exc}")
                return {"appid": int(appid), "catalog_result": "retry", "app_type": "unknown", "error": str(exc)}

        results = await asyncio.gather(*(fetch_one(appid) for appid in appids), return_exceptions=True)
    if any(isinstance(result, SteamRateLimited) for result in results):
        raise SteamRateLimited("Steam catalog requests paused by global cooldown")
    return [result for result in results if isinstance(result, dict)]


def _niche_score(row):
    reviews = max(0, int(row.get("total_reviews") or 0))
    players = int(row.get("current_players") or 0)
    peak = max(players, int(row.get("peak_players") or 0))
    review_score = float(row.get("review_score") or 0)
    return round(
        max(0.0, min(1.0, (review_score - 85.0) / 15.0)) * 0.45
        + min(1.0, math.log1p(reviews) / math.log1p(100000)) * 0.30
        + min(1.0, math.log1p(peak) / math.log1p(2000)) * 0.15
        + release_recency_factor(row.get("release_date")) * 0.10,
        6,
    )


def _upsert_catalog_prices(conn, rows, stamp):
    conn.executemany(
        """INSERT INTO price_snapshots(appid,region,currency,initial,final,discount_percent,final_formatted,source,fetched_at)
        VALUES (?, 'CN', ?, ?, ?, ?, ?, 'steam', ?)""",
        [(row["appid"], row.get("cn_price_currency"), row.get("cn_price_initial"), row.get("cn_price_final"), row.get("cn_discount_percent"), row.get("cn_price"), stamp)
         for row in rows if row.get("cn_price_final") is not None or row.get("is_free")],
    )
    conn.executemany(
        """INSERT INTO game_latest_state(appid,cn_price,cn_price_final,cn_price_currency,cn_discount_percent,price_updated_at,updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(appid) DO UPDATE SET
          cn_price=excluded.cn_price, cn_price_final=excluded.cn_price_final,
          cn_price_currency=excluded.cn_price_currency, cn_discount_percent=excluded.cn_discount_percent,
          price_updated_at=excluded.price_updated_at, updated_at=excluded.updated_at""",
        [(row["appid"], row.get("cn_price") if row.get("cn_price_final") is not None or row.get("is_free") else None,
          row.get("cn_price_final") if row.get("cn_price_final") is not None or row.get("is_free") else None,
          row.get("cn_price_currency") if row.get("cn_price_final") is not None or row.get("is_free") else None,
          row.get("cn_discount_percent", 0) if row.get("cn_price_final") is not None or row.get("is_free") else 0, stamp, stamp)
         for row in rows],
    )


def upsert_niche_pool_rows(rows, persist_prices=False):
    rows = [row for row in rows if row.get("catalog_result", "game") == "game"]
    if not rows:
        return 0
    evaluated_at = now_iso()
    prepared = []
    for row in rows:
        eligible = (
            not is_obvious_non_game_name(row.get("name"))
            and int(row.get("current_players") or 0) >= 10
            and 0 < int(row.get("peak_players") or 0) <= 2000
            and float(row.get("review_score") or 0) >= 85
            and 0 < int(row.get("total_reviews") or 0) <= config.NICHE_MAX_REVIEWS
            and is_recent_release(row.get("release_date"))
        )
        prepared.append((row, _niche_score(row) if eligible else 0.0, int(eligible)))
    with transaction() as conn:
        conn.executemany(
            """INSERT INTO games(appid,name,header_image,release_date,is_free,tracked,updated_at) VALUES (?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(appid) DO UPDATE SET name=CASE WHEN excluded.name != ? THEN excluded.name ELSE games.name END,
              header_image=COALESCE(excluded.header_image,games.header_image), release_date=COALESCE(games.release_date,excluded.release_date),
              is_free=excluded.is_free, updated_at=excluded.updated_at""",
            [(row["appid"], fallback_game_name(row["appid"], row["name"]), row.get("header_image"), row.get("release_date"), row.get("is_free", 0), evaluated_at, UNKNOWN_GAME_NAME)
             for row, _, _ in prepared],
        )
        conn.executemany("INSERT INTO player_snapshots(appid,player_count,fetched_at) VALUES (?, ?, ?)",
            [(row["appid"], row.get("current_players") or 0, row.get("fetched_at") or evaluated_at) for row, _, _ in prepared])
        conn.executemany("INSERT INTO review_snapshots(appid,review_score,review_score_desc,total_positive,total_negative,total_reviews,fetched_at) VALUES (?, ?, NULL, NULL, NULL, ?, ?)",
            [(row["appid"], row.get("review_score"), row.get("total_reviews") or 0, row.get("fetched_at") or evaluated_at) for row, _, _ in prepared])
        conn.executemany(
            """INSERT INTO niche_pool(appid,name,header_image,current_players,peak_players,review_score,total_reviews,cn_price,cn_price_final,cn_price_currency,cn_discount_percent,is_free,release_date,weighted_score,source,eligible,fetched_at,evaluated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'steam_discovery', ?, ?, ?)
            ON CONFLICT(appid) DO UPDATE SET name=excluded.name,header_image=excluded.header_image,current_players=excluded.current_players,
              peak_players=MAX(COALESCE(niche_pool.peak_players,0),COALESCE(excluded.peak_players,0)),review_score=excluded.review_score,total_reviews=excluded.total_reviews,
              cn_price=excluded.cn_price,cn_price_final=excluded.cn_price_final,cn_price_currency=excluded.cn_price_currency,cn_discount_percent=excluded.cn_discount_percent,
              is_free=excluded.is_free,release_date=COALESCE(excluded.release_date,niche_pool.release_date),weighted_score=excluded.weighted_score,
              eligible=excluded.eligible,fetched_at=excluded.fetched_at,evaluated_at=excluded.evaluated_at""",
            [(row["appid"], fallback_game_name(row["appid"], row["name"]), row.get("header_image"), row.get("current_players") or 0,
              max(row.get("current_players") or 0, row.get("peak_players") or 0), row.get("review_score"), row.get("total_reviews") or 0,
              row.get("cn_price"), row.get("cn_price_final"), row.get("cn_price_currency"), row.get("cn_discount_percent") or 0,
              row.get("is_free", 0), row.get("release_date"), score, eligible, row.get("fetched_at") or evaluated_at, evaluated_at)
             for row, score, eligible in prepared],
        )
        if persist_prices:
            _upsert_catalog_prices(conn, rows, evaluated_at)
        conn.execute("DELETE FROM niche_pool WHERE eligible=0")
        conn.execute("DELETE FROM niche_pool WHERE appid NOT IN (SELECT appid FROM niche_pool WHERE eligible=1 ORDER BY weighted_score DESC,total_reviews DESC LIMIT ?)", (config.NICHE_POOL_LIMIT,))
    return len(prepared)
