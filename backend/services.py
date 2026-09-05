"""User-facing application operations consumed by the HTTP layer."""

from . import _runtime
from .db import connect


def list_games():
    return _runtime.list_games()


def ensure_hot_games(target):
    target = min(max(100, int(target)), _runtime.HOTLIST_TARGET)
    hot_count = _runtime.count_hot_games()
    with connect() as conn:
        hotlist_at = _runtime.get_crawl_state(conn, "hotlist_at")
    force_hotlist = hot_count < target and _runtime.is_due(hotlist_at, 30)
    queued = False
    if force_hotlist or _runtime.is_due(hotlist_at, _runtime.HOTLIST_REFRESH_HOURS * 60):
        queued = _runtime.refresh_hot_database_async(force_hotlist=force_hotlist, quick=True)
    preview_queued = _runtime.enqueue_missing_hot_previews(
        limit=_runtime.HOT_FULL_METADATA_TOP_LIMIT,
        priority=90,
    )
    return {"queued": queued, "count": hot_count, "target": target, "preview_queued": preview_queued}


def list_hot_games(limit):
    requested = min(max(1, int(limit)), _runtime.HOTLIST_TARGET)
    return {
        "games": _runtime.list_hot_games(requested),
        "count": _runtime.count_hot_games(),
        "version": _runtime.hot_games_version(),
        "queued": False,
    }


def hot_games_version():
    return {"version": _runtime.hot_games_version()}


def list_niche_pool():
    games = _runtime.list_niche_pool_games(_runtime.NICHE_POOL_DISPLAY_LIMIT)
    pool_count = _runtime.count_eligible_niche_pool()
    return {
        "games": games,
        "count": len(games),
        "pool_count": pool_count,
        "selection_mode": "all" if pool_count <= _runtime.NICHE_POOL_DISPLAY_LIMIT else "top_half_random",
        "queued": False,
    }


def get_home_picks():
    return _runtime.get_home_picks()


def get_status():
    return _runtime.get_status()


def search(term):
    return {"items": _runtime.search_steam(term) if term else []}


def get_game(appid, history_limit=500):
    return _runtime.get_game_payload(int(appid), history_limit)


def track_game(appid, name=None, header_image=None):
    appid = int(appid)
    _runtime.quick_track_game(appid, name, header_image)
    queued = _runtime.refresh_tracked_game_async(appid, name)
    return {"ok": True, "appid": appid, "queued": queued}


def untrack_game(appid):
    appid = int(appid)
    _runtime.untrack_game(appid)
    return {"ok": True, "appid": appid, "tracked": False}


def refresh_all():
    if _runtime.REFRESH_STATUS["running"]:
        return {"ok": True, "running": True, "message": "refresh already running"}
    errors = _runtime.refresh_tracked_once(force_all=True)
    return {"ok": True, "running": False, "errors": errors}


def refresh_game(appid):
    return {"ok": True, **_runtime.refresh_game(int(appid))}


def cache_remote_image(url):
    return _runtime.cache_image(url)
