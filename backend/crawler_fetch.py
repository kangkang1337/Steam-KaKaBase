"""Bounded Steam batch fetches used exclusively by the crawler."""

import asyncio
import random
import re
from datetime import datetime, timedelta, timezone

from . import config
from .crawler_data import insert_player_batch
from .external_errors import ExternalDataUnavailable, SteamRateLimited
from .logging_utils import log_event
from .steam_client import async_get_json, async_get_text, require_httpx, steam_httpx_options
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


def discount_ends_at(price):
    """Return Steam's explicit sale end as UTC ISO time, never an estimate."""
    raw = (price or {}).get("discount_expiration")
    if raw in (None, "", 0, "0"):
        return None
    try:
        timestamp = int(raw)
        if timestamp > 0:
            return datetime.fromtimestamp(timestamp, timezone.utc).replace(microsecond=0).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return None


def select_discount_package_id(details):
    """Select the Store package whose discounted amount matches AppDetails."""
    details = details if isinstance(details, dict) else {}
    price = details.get("price_overview") or {}
    try:
        final = int(price.get("final"))
        discount = int(price.get("discount_percent") or 0)
    except (TypeError, ValueError):
        return None
    if discount <= 0:
        return None
    matches = []
    for group in details.get("package_groups") or []:
        if not isinstance(group, dict):
            continue
        for package in group.get("subs") or []:
            if not isinstance(package, dict):
                continue
            try:
                package_id = int(package.get("packageid"))
                package_final = int(package.get("price_in_cents_with_discount"))
            except (TypeError, ValueError):
                continue
            if package_id > 0 and package_final == final:
                matches.append(package_id)
    return min(matches) if matches else None


def parse_store_discount_expiration(html, package_id, *, now=None):
    """Extract one package's official Store countdown without executing HTML."""
    try:
        package_id = int(package_id)
    except (TypeError, ValueError):
        return None
    if package_id <= 0 or not isinstance(html, str):
        return None
    marker = re.compile(rf"['\"]#{package_id}_countdown_\d+['\"]")
    timer = re.compile(
        r"InitDailyDealTimer\s*\(\s*[^,]{1,200},\s*(\d{9,12})\s*\)",
        re.DOTALL,
    )
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    latest_allowed = current + timedelta(days=366)
    for found in marker.finditer(html):
        countdown = timer.search(html, found.start(), min(len(html), found.start() + 1500))
        if not countdown:
            continue
        try:
            expires = datetime.fromtimestamp(int(countdown.group(1)), timezone.utc)
        except (ValueError, OverflowError, OSError):
            continue
        if current < expires <= latest_allowed:
            return expires.replace(microsecond=0).isoformat()
    return None


async def fetch_discount_expirations_async(appids):
    """Resolve official CN Store countdowns serially for a very small queue."""
    httpx = require_httpx()
    semaphore = asyncio.Semaphore(1)
    rows, unavailable, retry = [], [], []
    headers = {
        "User-Agent": config.STEAM_USER_AGENT,
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        # Generic age-gate cookies only; no Steam account or session data.
        "Cookie": (
            "birthtime=568022401; lastagecheckage=1-January-1988; "
            "wants_mature_content=1; mature_content=1"
        ),
    }
    async with httpx.AsyncClient(
        timeout=config.STEAM_TIMEOUT_SECONDS,
        headers=headers,
        follow_redirects=True,
        **steam_httpx_options(),
    ) as client:
        for index, raw_appid in enumerate(appids):
            appid = int(raw_appid)
            try:
                if index:
                    await asyncio.sleep(random.uniform(
                        config.STORE_REQUEST_DELAY_MIN_SECONDS,
                        config.STORE_REQUEST_DELAY_MAX_SECONDS,
                    ))
                payload = await async_get_json(
                    client, semaphore,
                    "https://store.steampowered.com/api/appdetails",
                    {"appids": appid, "cc": "CN", "l": "schinese"},
                )
                details = (payload.get(str(appid)) or {}).get("data") or {}
                if not details:
                    unavailable.append(appid)
                    continue
                package_id = select_discount_package_id(details)
                if package_id is None:
                    rows.append({"appid": appid, "discount_ends_at": None})
                    continue
                await asyncio.sleep(random.uniform(
                    config.STORE_REQUEST_DELAY_MIN_SECONDS,
                    config.STORE_REQUEST_DELAY_MAX_SECONDS,
                ))
                html = await async_get_text(
                    client, semaphore,
                    f"https://store.steampowered.com/app/{appid}/",
                    {"cc": "CN", "l": "schinese"},
                    max_bytes=config.STORE_HTML_MAX_BYTES,
                )
                rows.append({
                    "appid": appid,
                    "discount_ends_at": parse_store_discount_expiration(html, package_id),
                })
            except ExternalDataUnavailable:
                unavailable.append(appid)
            except SteamRateLimited:
                raise
            except Exception as exc:
                log_event(f"discount expiry skipped appid={appid}: {exc}")
                retry.append(appid)
    return rows, unavailable, retry


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
                    "discount_ends_at": discount_ends_at(price),
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
