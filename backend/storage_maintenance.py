"""Bounded local retention work run by the crawler after a refresh cycle."""

from datetime import datetime, timedelta, timezone

from . import config
from .db import get_crawl_state, is_due, set_crawl_state, transaction
from .logging_utils import log_event, rotate_log_file_once
from .utils import cleanup_image_cache, now_iso


def compact_player_snapshots_once():
    with transaction() as conn:
        if not is_due(get_crawl_state(conn, "player_snapshot_compacted_at"), 24 * 60):
            return False
        now = datetime.now(timezone.utc)
        cutoff_daily = (now - timedelta(days=7)).replace(microsecond=0).isoformat()
        cutoff_monthly = (now - timedelta(days=365)).replace(microsecond=0).isoformat()
        cutoff_delete = (now - timedelta(days=730)).replace(microsecond=0).isoformat()
        conn.execute("DELETE FROM player_snapshots WHERE fetched_at < ?", (cutoff_delete,))
        conn.execute(
            """DELETE FROM player_snapshots WHERE fetched_at < ? AND id NOT IN (
                SELECT MIN(id) FROM player_snapshots WHERE fetched_at < ?
                GROUP BY appid, substr(fetched_at, 1, 10))""",
            (cutoff_daily, cutoff_daily),
        )
        conn.execute(
            """DELETE FROM player_snapshots WHERE fetched_at < ? AND id NOT IN (
                SELECT MIN(id) FROM player_snapshots WHERE fetched_at < ?
                GROUP BY appid, substr(fetched_at, 1, 7))""",
            (cutoff_monthly, cutoff_monthly),
        )
        set_crawl_state(conn, "player_snapshot_compacted_at", now_iso())
    log_event("player snapshots compacted")
    return True


def rotate_logs_once():
    today = datetime.now().strftime("%Y-%m-%d")
    with transaction() as conn:
        if get_crawl_state(conn, "log_rotation_date") == today:
            return False
    rotate_log_file_once(today=today)
    with transaction() as conn:
        set_crawl_state(conn, "log_rotation_date", today)
    return True


def compact_price_snapshots_once():
    """Keep new samples exact and compact old history by day then month."""
    with transaction() as conn:
        if not is_due(get_crawl_state(conn, "price_snapshot_compacted_at"), 24 * 60):
            return False
        now = datetime.now(timezone.utc)
        cutoff_daily = (now - timedelta(days=30)).replace(microsecond=0).isoformat()
        cutoff_monthly = (now - timedelta(days=365)).replace(microsecond=0).isoformat()
        cutoff_delete = (now - timedelta(days=config.PRICE_RETENTION_DAYS)).replace(microsecond=0).isoformat()
        conn.execute("DELETE FROM price_snapshots WHERE fetched_at < ?", (cutoff_delete,))
        conn.execute(
            """DELETE FROM price_snapshots
            WHERE fetched_at < ? AND fetched_at >= ? AND id NOT IN (
                SELECT MIN(id) FROM price_snapshots WHERE fetched_at < ? AND fetched_at >= ?
                GROUP BY appid, region, source, substr(fetched_at, 1, 10))""",
            (cutoff_daily, cutoff_monthly, cutoff_daily, cutoff_monthly),
        )
        conn.execute(
            """DELETE FROM price_snapshots WHERE fetched_at < ? AND id NOT IN (
                SELECT MIN(id) FROM price_snapshots WHERE fetched_at < ?
                GROUP BY appid, region, source, substr(fetched_at, 1, 7))""",
            (cutoff_monthly, cutoff_monthly),
        )
        set_crawl_state(conn, "price_snapshot_compacted_at", now_iso())
    log_event("price snapshots compacted")
    return True


def cleanup_old_records_once():
    with transaction() as conn:
        if not is_due(get_crawl_state(conn, "old_records_cleaned_at"), 24 * 60):
            return False
        task_cutoff = (datetime.now(timezone.utc) - timedelta(days=config.CRAWL_TASK_RETENTION_DAYS)).replace(microsecond=0).isoformat()
        recommendation_cutoff = (datetime.now(timezone.utc) - timedelta(days=config.RECOMMENDATION_RETENTION_DAYS)).strftime("%Y-%m-%d")
        conn.execute(
            """DELETE FROM crawl_tasks WHERE status IN ('done','failed','permanent_failed','not_available','skipped')
            AND COALESCE(completed_at, updated_at) < ?""",
            (task_cutoff,),
        )
        conn.execute("DELETE FROM niche_recommendation_snapshots WHERE recommendation_date < ?", (recommendation_cutoff,))
        conn.execute("DELETE FROM daily_home_snapshots WHERE recommendation_date < ?", (recommendation_cutoff,))
        set_crawl_state(conn, "old_records_cleaned_at", now_iso())
    log_event("old crawl tasks and recommendation snapshots cleaned")
    return True


def maintain_storage_once():
    rotate_logs_once()
    compact_price_snapshots_once()
    cleanup_old_records_once()
    cleanup_image_cache(
        config.IMAGE_CACHE_DIR,
        config.IMAGE_CACHE_RETENTION_DAYS,
        config.IMAGE_CACHE_MAX_BYTES,
    )
