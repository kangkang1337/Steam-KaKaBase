"""Cached game read models and catalog-to-game materialization.

This module deliberately has no crawler-runtime dependency: web requests only
read SQLite and serialize the cached result, while the crawler owns refreshes.
"""

import json
import threading
import time
from collections import OrderedDict

from . import config
from .db import (
    query_catalog_game_stub,
    query_game_detail,
    query_hot_games,
    query_search_index,
    query_tracked_games,
    transaction,
)
from .pricing import (
    amount_int_to_cny,
    cached_historical_low_match as _cached_historical_low_match,
    effective_historical_low,
    price_row_cny,
)
from .steam_client import service_cooldown_remaining_seconds
from .utils import (
    UNKNOWN_GAME_NAME,
    clean_hot_name,
    clean_name,
    fallback_game_name,
    infer_name_from_description,
    is_obvious_non_game_name,
    now_iso,
)


_SEARCH_CACHE = OrderedDict()
_SEARCH_CACHE_LOCK = threading.Lock()
_SEARCH_METRICS = {
    "requests": 0,
    "cache_hits": 0,
    "database_queries": 0,
    "database_query_ms_total": 0.0,
    "database_query_ms_max": 0.0,
}


def cached_historical_low_match(current_cny, low_cny, source, discount_percent=0, observed_count=0):
    """Apply the product-wide price tolerance at the serialization boundary."""
    return _cached_historical_low_match(
        current_cny, low_cny, source, discount_percent, observed_count,
        tolerance_cny=config.HISTORICAL_LOW_TOLERANCE_CNY,
    )


def clean_game(row, summary=False):
    if not row:
        return None
    item = dict(row)
    display_name = clean_hot_name(item.get("name"))
    if not display_name:
        display_name = infer_name_from_description(item.get("short_description")) or fallback_game_name(item.get("appid"))
    if summary:
        current_cny = amount_int_to_cny(item.get("cn_price_final"), item.get("cn_price_currency") or "CNY")
        low_cny, low_source = effective_historical_low(item.get("cn_itad_low_cny", item.get("cn_historical_low_cny")), item.get("cn_observed_low_cny"))
        is_low = cached_historical_low_match(current_cny, low_cny, low_source, item.get("cn_discount_percent"), item.get("cn_observed_snapshot_count"))
        return {
            "appid": item.get("appid"), "name": display_name, "name_zh": display_name,
            "name_en": clean_hot_name(item.get("name_en")), "header_image": item.get("header_image"),
            "player_count": item.get("player_count"), "review_score": item.get("review_score"),
            "cn_price": item.get("cn_price"),
            "cn_price_display": item.get("cn_price") or ("免费" if item.get("is_free") else "国区暂无售价"),
            "is_free": bool(item.get("is_free")), "cn_price_historical_low": is_low,
            "cn_price_discounted": bool((item.get("cn_discount_percent") or 0) > 0 and not is_low),
            "cn_discount_percent": item.get("cn_discount_percent") or 0,
            "cn_historical_low_cny": low_cny, "cn_historical_low_source": low_source,
            "cn_observed_low_since": item.get("cn_observed_low_since"),
            "cn_observed_low_last_at": item.get("cn_observed_low_last_at"),
            "cn_observed_snapshot_count": item.get("cn_observed_snapshot_count") or 0,
            "favorite_status": item.get("favorite_status") or "wish",
            "favorite_created_at": item.get("favorite_created_at"),
            "updated_at": item.get("updated_at"), "tracked": bool(item.get("tracked")),
        }
    return {
        "appid": item.get("appid"), "name": display_name, "name_zh": display_name,
        "name_en": clean_hot_name(item.get("name_en")), "header_image": item.get("header_image"),
        "short_description": item.get("short_description"), "developer": item.get("developer"),
        "publisher": item.get("publisher"), "release_date": item.get("release_date"),
        "is_free": bool(item.get("is_free")), "updated_at": item.get("updated_at"),
        "tracked": bool(item.get("tracked")),
    }


def clean_price(row):
    item = dict(row)
    current_cny = price_row_cny(item)
    observed_low_cny = amount_int_to_cny(item.get("observed_low_amount_int"), item.get("currency"))
    low_cny, low_source = effective_historical_low(item.get("historical_low_cny"), observed_low_cny)
    return {
        "region": item.get("region"), "currency": item.get("currency"), "initial": item.get("initial"),
        "final": item.get("final"), "discount_percent": item.get("discount_percent"),
        "final_formatted": item.get("final_formatted"), "source": item.get("source"), "fetched_at": item.get("fetched_at"),
        "historical_low": cached_historical_low_match(current_cny, low_cny, low_source, item.get("discount_percent"), item.get("observed_snapshot_count")),
        "current_cny": current_cny, "historical_low_cny": low_cny, "historical_low_source": low_source,
        "itad_historical_low_cny": item.get("historical_low_cny"), "observed_low_cny": observed_low_cny,
        "observed_low_amount_int": item.get("observed_low_amount_int"), "observed_low_since": item.get("observed_low_since"),
        "observed_snapshot_count": item.get("observed_snapshot_count") or 0,
        "historical_low_currency": item.get("historical_low_currency"), "historical_low_amount_int": item.get("historical_low_amount_int"),
        "historical_low_at": item.get("historical_low_at"),
    }


def _clean_player(row):
    item = dict(row)
    return {"player_count": item.get("player_count"), "fetched_at": item.get("fetched_at")}


def _clean_review(row):
    if not row:
        return None
    item = dict(row)
    return {key: item.get(key) for key in ("review_score", "review_score_desc", "total_positive", "total_negative", "total_reviews", "fetched_at")}


def _history_limit(value):
    try:
        return min(2000, max(1, int(value)))
    except (TypeError, ValueError):
        return 500


def ensure_game_from_catalog(conn, appid):
    row = conn.execute("SELECT appid, name, app_type FROM steam_catalog WHERE appid=?", (int(appid),)).fetchone()
    if not row or row[2] not in ("unknown", "game") or (row[2] == "unknown" and is_obvious_non_game_name(row[1])):
        return False
    appid = int(appid)
    conn.execute(
        """INSERT INTO games(appid,name,header_image,tracked,updated_at) VALUES(?,?,?,0,?)
        ON CONFLICT(appid) DO UPDATE SET
          name=CASE WHEN games.name=? OR games.name=? THEN excluded.name ELSE games.name END,
          header_image=COALESCE(games.header_image, excluded.header_image), updated_at=excluded.updated_at""",
        (appid, clean_hot_name(row[1]) or fallback_game_name(appid),
         f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg", now_iso(),
         UNKNOWN_GAME_NAME, f"App {appid}"),
    )
    return True


def get_game_payload(appid, history_limit=500):
    appid = int(appid)
    with transaction(rows=True) as conn:
        detail = query_game_detail(conn, appid, _history_limit(history_limit))
        if not detail:
            catalog_game = query_catalog_game_stub(conn, appid)
            if not catalog_game or catalog_game["app_type"] not in ("unknown", "game") or (catalog_game["app_type"] == "unknown" and is_obvious_non_game_name(catalog_game["name"])):
                return None
            name = clean_hot_name(catalog_game["name"]) or fallback_game_name(appid)
            return {
                "game": {"appid": appid, "name": name, "name_zh": name, "name_en": name,
                         "header_image": f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg",
                         "short_description": None, "developer": None, "publisher": None,
                         "release_date": None, "is_free": False, "updated_at": catalog_game["updated_at"],
                         "tracked": False, "site_peak_players": None, "site_peak_recorded_since": None},
                "prices": [], "priceHistory": [], "players": [], "reviews": None,
                "refresh_pending": False, "data_incomplete": True,
                "pending_fields": ["prices", "players", "reviews", "metadata"],
                "refresh_started": False, "queued_tasks": 0,
                "retry_after_seconds": service_cooldown_remaining_seconds("steam_store"),
            }
    prices = [clean_price(row) for row in detail["prices"]]
    game = clean_game(detail["game"])
    peak = detail["site_peak"]
    game["site_peak_players"] = peak[0] if peak else None
    game["site_peak_recorded_since"] = peak[1] if peak else None
    missing = [field for field, value in (("prices", prices), ("players", detail["players"]), ("reviews", detail["reviews"]), ("metadata", game.get("short_description"))) if not value]
    return {"game": game, "prices": prices, "priceHistory": [clean_price(row) for row in detail["price_history"]],
            "players": [_clean_player(row) for row in detail["players"]], "reviews": _clean_review(detail["reviews"]),
            "refresh_pending": False, "data_incomplete": bool(missing), "pending_fields": missing,
            "refresh_started": False, "queued_tasks": 0,
            "retry_after_seconds": service_cooldown_remaining_seconds("steam_store")}


def list_games():
    return [clean_game(row, summary=True) for row in query_tracked_games()]


def list_hot_games(limit=100):
    limit = min(max(1, int(limit)), config.HOTLIST_TARGET)
    games = []
    for rank, row in enumerate(query_hot_games(limit, UNKNOWN_GAME_NAME), 1):
        current_cny = amount_int_to_cny(row["cn_price_final"], row["cn_price_currency"] or "CNY")
        low_cny, low_source = effective_historical_low(row["cn_itad_low_cny"], row["cn_observed_low_cny"])
        is_low = cached_historical_low_match(current_cny, low_cny, low_source, row["cn_discount_percent"], row["cn_observed_snapshot_count"])
        games.append({"appid": row["appid"], "rank": rank, "original_rank": row["original_rank"],
            "name": fallback_game_name(row["appid"], row["name"]), "name_zh": fallback_game_name(row["appid"], row["name_zh"] or row["name"]),
            "name_en": clean_hot_name(row["name_en"]), "header_image": row["header_image"], "current_players": row["current_players"], "peak_players": row["peak_players"],
            "review_score": row["review_score"], "is_free": bool(row["is_free"]), "is_paid": not bool(row["is_free"]),
            "cn_price": row["cn_price"], "cn_price_display": row["cn_price"] or ("免费" if row["is_free"] else "国区暂无售价"),
            "cn_price_final": row["cn_price_final"], "cn_discount_percent": row["cn_discount_percent"] or 0,
            "cn_price_historical_low": is_low, "cn_price_discounted": bool((row["cn_discount_percent"] or 0) > 0 and not is_low),
            "cn_historical_low_cny": low_cny, "cn_historical_low_source": low_source,
            "cn_observed_low_since": row["cn_observed_low_since"], "source": row["source"], "fetched_at": row["fetched_at"], "tracked": bool(row["tracked"])})
    return games


def _normalize_search_term(term):
    return " ".join(str(term or "").strip().lower().split())


def search_games(term, limit=12, offset=0):
    term = _normalize_search_term(term)
    limit, offset = min(50, max(1, int(limit))), max(0, int(offset))
    if not term:
        return []
    key = (str(config.DB_PATH), term, limit, offset)
    with _SEARCH_CACHE_LOCK:
        _SEARCH_METRICS["requests"] += 1
        cached = _SEARCH_CACHE.get(key)
        if cached and time.monotonic() - cached["at"] < (config.SEARCH_CACHE_TTL_SECONDS if cached["items"] else config.SEARCH_CACHE_EMPTY_TTL_SECONDS):
            _SEARCH_CACHE.move_to_end(key)
            _SEARCH_METRICS["cache_hits"] += 1
            return [dict(item) for item in cached["items"]]
        _SEARCH_CACHE.pop(key, None)
    started = time.perf_counter()
    items = [{"appid": row["appid"], "name": clean_name(row["name"]), "name_zh": clean_hot_name(row["name_zh"]),
              "name_en": clean_hot_name(row["name_en"]), "tiny_image": row["header_image"], "price": None,
              "current_players": row["current_players"], "tracked": bool(row["tracked"])}
             for row in query_search_index(term, limit, offset, UNKNOWN_GAME_NAME)
             if row["name"] and not is_obvious_non_game_name(row["name"])]
    elapsed = (time.perf_counter() - started) * 1000
    with _SEARCH_CACHE_LOCK:
        _SEARCH_METRICS["database_queries"] += 1
        _SEARCH_METRICS["database_query_ms_total"] += elapsed
        _SEARCH_METRICS["database_query_ms_max"] = max(_SEARCH_METRICS["database_query_ms_max"], elapsed)
        _SEARCH_CACHE[key] = {"at": time.monotonic(), "items": [dict(item) for item in items]}
        _SEARCH_CACHE.move_to_end(key)
        while len(_SEARCH_CACHE) > config.SEARCH_CACHE_MAX_ENTRIES:
            _SEARCH_CACHE.popitem(last=False)
    return items
