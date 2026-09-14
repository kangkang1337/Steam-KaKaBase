"""Bounded Steam batch fetches used exclusively by the crawler."""

import asyncio
import random

from . import config
from .crawler_data import insert_player_batch
from .external_errors import ExternalDataUnavailable, SteamRateLimited
from .logging_utils import log_event
from .steam_client import async_get_json, require_httpx, steam_httpx_options
from .utils import now_iso


async def _stagger_store_requests(appids, fetch_one):
    """Run Store work serially and space every request, not just the batch."""
    results = []
    for index, appid in enumerate(appids):
        if index:
            await asyncio.sleep(random.uniform(
                config.STORE_REQUEST_DELAY_MIN_SECONDS,
                config.STORE_REQUEST_DELAY_MAX_SECONDS,
            ))
        results.append(await fetch_one(appid))
    return results


async def fetch_players_for_appids_async(appids):
    """Fetch one player endpoint at a time to keep collection staggered."""
    httpx = require_httpx()
    stamp = now_iso()
    rows, successful, failed = [], [], 0
    async with httpx.AsyncClient(timeout=config.STEAM_TIMEOUT_SECONDS, headers={"User-Agent": config.STEAM_USER_AGENT}, follow_redirects=True, **steam_httpx_options()) as client:
        for index, appid in enumerate(appids):
            try:
                if index:
                    await asyncio.sleep(config.PLAYER_REQUEST_DELAY_SECONDS)
                payload = await async_get_json(
                    client, asyncio.Semaphore(1),
                    "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/",
                    {"appid": appid},
                )
                rows.append((int(appid), int((payload.get("response") or {}).get("player_count") or 0), stamp))
                successful.append(int(appid))
                if len(rows) >= config.HOTLIST_BATCH_SIZE:
                    insert_player_batch(rows)
                    rows = []
            except SteamRateLimited:
                insert_player_batch(rows)
                raise
            except Exception:
                failed += 1
    insert_player_batch(rows)
    return {"stamp": stamp, "success": len(successful), "success_appids": successful, "failed": failed, "skipped": 0}


def _review_summary(summary):
    positive = int(summary.get("total_positive") or 0)
    negative = int(summary.get("total_negative") or 0)
    total = positive + negative
    return {
        "review_score": round((positive / total) * 100) if total else None,
        "review_score_desc": summary.get("review_score_desc"),
        "total_positive": positive, "total_negative": negative,
        "total_reviews": total, "has_reviews": total > 0,
    }


async def fetch_hot_metadata_async(appids, full=True, include_reviews=False):
    httpx = require_httpx()
    semaphore = asyncio.Semaphore(config.HOT_METADATA_CONCURRENCY)
    stamp, rows, unavailable, retry = now_iso(), [], [], []
    async with httpx.AsyncClient(timeout=config.STEAM_TIMEOUT_SECONDS, headers={"User-Agent": config.STEAM_USER_AGENT}, follow_redirects=True, **steam_httpx_options()) as client:
        async def fetch_one(appid):
            try:
                payload = await async_get_json(client, semaphore, "https://store.steampowered.com/api/appdetails", {"appids": appid, "cc": "CN", "l": "schinese"})
                data = (payload.get(str(appid)) or {}).get("data") or {}
                if not data:
                    return "not_available", int(appid), "Steam AppDetails returned no public data"
                price, release = data.get("price_overview") or {}, data.get("release_date") or {}
                row = {
                    "appid": int(appid), "name": data.get("name"), "header_image": data.get("header_image"),
                    "short_description": data.get("short_description") if full else None,
                    "developer": ", ".join(data.get("developers") or []) if full else None,
                    "publisher": ", ".join(data.get("publishers") or []) if full else None,
                    "release_date": release.get("date") if isinstance(release, dict) else None,
                    "is_free": 1 if data.get("is_free") else 0, "screenshots_json": None,
                    "currency": price.get("currency"), "initial": price.get("initial", 0), "final": price.get("final", 0),
                    "discount_percent": price.get("discount_percent", 0),
                    "final_formatted": price.get("final_formatted", "Free") if price or data.get("is_free") else None,
                    "has_price": bool(price or data.get("is_free")),
                }
                if include_reviews:
                    try:
                        await asyncio.sleep(random.uniform(
                            config.STORE_REQUEST_DELAY_MIN_SECONDS,
                            config.STORE_REQUEST_DELAY_MAX_SECONDS,
                        ))
                        review = await async_get_json(client, semaphore, f"https://store.steampowered.com/appreviews/{appid}", {"json": 1, "language": "all", "purchase_type": "all", "num_per_page": 0, "filter": "summary"})
                        row.update(_review_summary(review.get("query_summary") or {}))
                    except Exception as exc:
                        log_event(f"hot review skipped appid={appid}: {exc}")
                return row
            except ExternalDataUnavailable as exc:
                return "not_available", int(appid), str(exc)
            except Exception as exc:
                if isinstance(exc, SteamRateLimited):
                    raise
                log_event(f"hot metadata skipped appid={appid}: {exc}")
                return "retry", int(appid), str(exc)

        results = await _stagger_store_requests(appids, fetch_one)
    if any(isinstance(result, SteamRateLimited) for result in results):
        raise SteamRateLimited("Steam metadata requests paused by global cooldown")
    for result in results:
        if isinstance(result, dict):
            rows.append(result)
        elif isinstance(result, tuple) and result[0] == "not_available":
            unavailable.append(result[1])
        elif isinstance(result, tuple) and result[0] == "retry":
            retry.append(result[1])
    return rows, unavailable, retry, stamp


async def fetch_hot_reviews_async(appids):
    httpx = require_httpx()
    semaphore = asyncio.Semaphore(config.HOT_METADATA_CONCURRENCY)
    stamp, rows, unavailable, retry = now_iso(), [], [], []
    async with httpx.AsyncClient(timeout=config.STEAM_TIMEOUT_SECONDS, headers={"User-Agent": config.STEAM_USER_AGENT}, follow_redirects=True, **steam_httpx_options()) as client:
        async def fetch_one(appid):
            try:
                payload = await async_get_json(client, semaphore, f"https://store.steampowered.com/appreviews/{appid}", {"json": 1, "language": "all", "purchase_type": "all", "num_per_page": 0, "filter": "summary"})
                return {"appid": int(appid), **_review_summary(payload.get("query_summary") or {})}
            except ExternalDataUnavailable as exc:
                return "not_available", int(appid), str(exc)
            except Exception as exc:
                if isinstance(exc, SteamRateLimited):
                    raise
                log_event(f"hot review skipped appid={appid}: {exc}")
                return "retry", int(appid), str(exc)

        results = await _stagger_store_requests(appids, fetch_one)
    if any(isinstance(result, SteamRateLimited) for result in results):
        raise SteamRateLimited("Steam review requests paused by global cooldown")
    for result in results:
        if isinstance(result, dict):
            rows.append(result)
        elif isinstance(result, tuple) and result[0] == "not_available":
            unavailable.append(result[1])
        elif isinstance(result, tuple) and result[0] == "retry":
            retry.append(result[1])
    return rows, unavailable, retry, stamp
