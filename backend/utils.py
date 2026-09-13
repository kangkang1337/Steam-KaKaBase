"""Pure shared helpers for dates, retries, names and cache maintenance.

This module deliberately has no database, network, or runtime-module dependency.
"""

import random
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config


UNKNOWN_GAME_NAME = "未命名游戏"
PLACEHOLDER_NAME_RE = re.compile(r"^(?:Steam\s+)?App\s+\d+$", re.IGNORECASE)


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def age_minutes(value):
    parsed = parse_iso(value)
    if not parsed:
        return None
    return (datetime.now(timezone.utc) - parsed).total_seconds() / 60


def daily_refresh_key(moment=None):
    """Use 00:10 in the configured business time zone for daily homepage picks."""
    current = moment or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=config.DAILY_REFRESH_TZINFO)
    else:
        current = current.astimezone(config.DAILY_REFRESH_TZINFO)
    boundary = current.replace(hour=0, minute=10, second=0, microsecond=0)
    if current < boundary:
        current -= timedelta(days=1)
    return current.strftime("%Y-%m-%d")


def retry_delay(attempt):
    return min(8, (0.8 * (2 ** int(attempt))) + random.uniform(0, 0.35))


def safe_log_url(url):
    """Keep diagnostics useful without writing API credentials to disk."""
    parsed = urllib.parse.urlsplit(str(url))
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted = [
        (key, "***" if key.lower() in {"key", "api_key", "apikey", "token", "access_token"} else value)
        for key, value in pairs
    ]
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(redacted), "")
    )


def is_placeholder_name(value):
    return not value or bool(PLACEHOLDER_NAME_RE.match(str(value).strip()))


def clean_name(value):
    return UNKNOWN_GAME_NAME if is_placeholder_name(value) else str(value).strip()


def clean_hot_name(value):
    text = str(value).strip() if value is not None else ""
    return None if is_placeholder_name(text) or text == UNKNOWN_GAME_NAME else text


def fallback_game_name(appid, name=None):
    return clean_hot_name(name) or UNKNOWN_GAME_NAME


def is_obvious_non_game_name(value):
    """Hide obvious non-games while AppDetails classification is pending."""
    text = str(value or "").strip().lower()
    return bool(re.search(
        r"(?:\bdemo\b|\bdlc\b|soundtrack|dedicated server|\bserver tool\b|"
        r"sdk\b|editor\b|benchmark\b|artbook|wallpaper)",
        text,
    ))


def infer_name_from_description(value):
    if not value:
        return None
    match = re.search(r"《([^》]{2,80})》", str(value))
    return match.group(1).strip() if match else None


def parse_release_date(value):
    text = str(value or "").strip()
    match = re.search(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", text)
    if not match:
        return None
    year, month, day = int(match.group(1)), 1, 1
    month_match = re.search(r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)", text, re.IGNORECASE)
    if month_match:
        month = datetime.strptime(month_match.group(2)[:3].title(), "%b").month
        day = int(month_match.group(1))
    else:
        chinese_match = re.search(
            r"(\d{4})\s*\u5e74\s*(\d{1,2})\s*\u6708\s*(\d{1,2})?", text
        )
        if chinese_match:
            year = int(chinese_match.group(1))
            month = int(chinese_match.group(2))
            day = int(chinese_match.group(3) or 1)
    try:
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None


def is_recent_release(value, years=8):
    released = parse_release_date(value)
    return bool(released and released >= datetime.now(timezone.utc) - timedelta(days=365.25 * years))


def release_recency_factor(value):
    released = parse_release_date(value)
    if released is None:
        return 0.0
    age_days = max(0.0, (datetime.now(timezone.utc) - released).total_seconds() / 86400)
    if age_days <= 365.25 * 3:
        return 1.0
    if age_days <= 365.25 * 5:
        return 0.95
    if age_days <= 365.25 * 8:
        return 0.85
    return 0.0


def cleanup_image_cache(directory: Path, retention_days, max_bytes):
    """Delete expired entries, then evict least-recently-used files by size."""
    if not directory.is_dir():
        return
    cutoff = time.time() - (int(retention_days) * 86400)
    entries = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_atime < cutoff:
            path.unlink(missing_ok=True)
            continue
        entries.append((stat.st_atime, stat.st_size, path))
    total = sum(size for _, size, _ in entries)
    for _, size, path in sorted(entries):
        if total <= int(max_bytes):
            break
        path.unlink(missing_ok=True)
        total -= size
