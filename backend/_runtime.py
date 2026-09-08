import asyncio
import hashlib
import json
import random
import math
import sqlite3
import socket
import ssl
import threading
import time
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

from . import config


ROOT = config.ROOT
DATA_DIR = config.DATA_DIR
IMAGE_CACHE_DIR = config.IMAGE_CACHE_DIR
DB_PATH = config.DB_PATH
LOG_PATH = config.LOG_PATH
DB_MIGRATION_BACKUP_DIR = config.DB_MIGRATION_BACKUP_DIR
DB_MIGRATION_BACKUP_KEEP = config.DB_MIGRATION_BACKUP_KEEP
DB_TIMEOUT_SECONDS = config.DB_TIMEOUT_SECONDS
PORT = config.PORT
PLAYER_REFRESH_MINUTES = config.PLAYER_REFRESH_MINUTES
PRICE_REFRESH_HOURS = config.PRICE_REFRESH_HOURS
SCHEDULER_CHECK_SECONDS = config.SCHEDULER_CHECK_SECONDS
HISTORICAL_LOW_TOLERANCE_CNY = config.HISTORICAL_LOW_TOLERANCE_CNY
HOTLIST_TARGET = config.HOTLIST_TARGET
HOTLIST_CONCURRENCY = config.HOTLIST_CONCURRENCY
HOTLIST_BATCH_SIZE = config.HOTLIST_BATCH_SIZE
HOTLIST_REFRESH_HOURS = config.HOTLIST_REFRESH_HOURS
HOT_METADATA_CONCURRENCY = config.HOT_METADATA_CONCURRENCY
HOT_PREVIEW_TOP_LIMIT = config.HOT_PREVIEW_TOP_LIMIT
HOT_PREVIEW_BATCH_LIMIT = config.HOT_PREVIEW_BATCH_LIMIT
HOT_FULL_METADATA_TOP_LIMIT = config.HOT_FULL_METADATA_TOP_LIMIT
HOT_METADATA_BATCH_LIMIT = config.HOT_METADATA_BATCH_LIMIT
NICHE_POOL_BATCH_LIMIT = config.NICHE_POOL_BATCH_LIMIT
NICHE_POOL_REFRESH_MINUTES = config.NICHE_POOL_REFRESH_MINUTES
NICHE_POOL_DISPLAY_LIMIT = config.NICHE_POOL_DISPLAY_LIMIT
NICHE_POOL_BOOTSTRAP_REFRESH_MINUTES = config.NICHE_POOL_BOOTSTRAP_REFRESH_MINUTES
NICHE_POOL_LIMIT = config.NICHE_POOL_LIMIT
NICHE_MAX_REVIEWS = config.NICHE_MAX_REVIEWS
STEAM_CATALOG_LIMIT = config.STEAM_CATALOG_LIMIT
CATALOG_SCAN_BATCH_LIMIT = config.CATALOG_SCAN_BATCH_LIMIT
CATALOG_RESCAN_DAYS = config.CATALOG_RESCAN_DAYS
CATALOG_ENRICH_DAILY_LIMIT = config.CATALOG_ENRICH_DAILY_LIMIT
CATALOG_ENRICH_BATCH_LIMIT = config.CATALOG_ENRICH_BATCH_LIMIT
HOME_RECOMMENDATION_REPEAT_DAYS = config.HOME_RECOMMENDATION_REPEAT_DAYS
HOME_POPULAR_MIN_REVIEWS = config.HOME_POPULAR_MIN_REVIEWS
HOME_POPULAR_MIN_PLAYERS = config.HOME_POPULAR_MIN_PLAYERS
TRACKED_REFRESH_BATCH_LIMIT = config.TRACKED_REFRESH_BATCH_LIMIT
ITAD_HISTORYLOW_BATCH_LIMIT = config.ITAD_HISTORYLOW_BATCH_LIMIT
STORE_REQUEST_DELAY_MIN_SECONDS = config.STORE_REQUEST_DELAY_MIN_SECONDS
STORE_REQUEST_DELAY_MAX_SECONDS = config.STORE_REQUEST_DELAY_MAX_SECONDS
ITAD_API_KEY = config.ITAD_API_KEY
STEAM_API_KEY = config.STEAM_API_KEY
STEAM_USER_AGENT = config.STEAM_USER_AGENT
STEAM_TIMEOUT_SECONDS = config.STEAM_TIMEOUT_SECONDS
STEAM_PROXY_URL = config.STEAM_PROXY_URL
USE_PROXY = config.USE_PROXY
STEAM_PROXY_VERIFY_TLS = config.STEAM_PROXY_VERIFY_TLS
DIRECT_COOLDOWN_MINUTES = config.DIRECT_COOLDOWN_MINUTES
STEAM_MAX_RETRIES = config.STEAM_MAX_RETRIES
STEAM_RETRY_STATUSES = config.STEAM_RETRY_STATUSES
APP_VERSION = config.APP_VERSION
ITAD_MISSING_GAME_ID = config.ITAD_MISSING_GAME_ID
EXTERNAL_SERVICES = config.EXTERNAL_SERVICES
LOG_RETENTION_DAYS = config.LOG_RETENTION_DAYS
PRICE_RETENTION_DAYS = config.PRICE_RETENTION_DAYS
RECOMMENDATION_RETENTION_DAYS = config.RECOMMENDATION_RETENTION_DAYS
CRAWL_TASK_RETENTION_DAYS = config.CRAWL_TASK_RETENTION_DAYS
IMAGE_CACHE_MAX_BYTES = config.IMAGE_CACHE_MAX_BYTES
IMAGE_CACHE_RETENTION_DAYS = config.IMAGE_CACHE_RETENTION_DAYS
IMAGE_CACHE_MAX_FILE_BYTES = config.IMAGE_CACHE_MAX_FILE_BYTES
SEARCH_CACHE_TTL_SECONDS = config.SEARCH_CACHE_TTL_SECONDS
SEARCH_CACHE_EMPTY_TTL_SECONDS = config.SEARCH_CACHE_EMPTY_TTL_SECONDS
SEARCH_CACHE_MAX_ENTRIES = config.SEARCH_CACHE_MAX_ENTRIES
APP_NAME_REFRESH_HOURS = config.APP_NAME_REFRESH_HOURS
START_COOLDOWN_UNTIL = time.time() + config.START_COOLDOWN_SECONDS
SERVICE_COOLDOWN_UNTIL = {
    "steam_api": START_COOLDOWN_UNTIL,
    "steam_store": START_COOLDOWN_UNTIL,
    "itad": 0.0,
    "image_cdn": 0.0,
}
SERVICE_COOLDOWN_LOCK = threading.Lock()
PROXY_STATUS = {
    "configured": bool(STEAM_PROXY_URL), "enabled": USE_PROXY, "reachable": None,
    "tls_verify": STEAM_PROXY_VERIFY_TLS, "fallback_successes": 0, "fallback_failures": 0,
    "message": "直连优先" if not USE_PROXY else "待检测",
}
PROXY_FALLBACK_LOCK = threading.Lock()
PROXY_FALLBACK_LOGGED_AT = {}
DIRECT_COOLDOWN_LOCK = threading.Lock()
DIRECT_COOLDOWN_UNTIL = {service: 0.0 for service in EXTERNAL_SERVICES}
DIRECT_FAILURE_COUNT = {service: 0 for service in EXTERNAL_SERVICES}
REFRESH_LOCK = threading.Lock()
HOT_REFRESH_LOCK = threading.Lock()
STATUS_LOCK = threading.Lock()
LOG_LOCK = threading.Lock()
DETAIL_BACKFILL_LOCK = threading.Lock()
DETAIL_BACKFILLING = set()
PREVIEW_BACKFILL_LOCK = threading.Lock()
PREVIEW_BACKFILLING = set()
NICHE_POOL_LOCK = threading.Lock()
TRACK_BACKFILL_LOCK = threading.Lock()
TRACK_BACKFILLING = set()
HISTORYLOW_BACKFILL_LOCK = threading.Lock()
HISTORYLOW_BACKFILLING = set()
SEARCH_CACHE = OrderedDict()
SEARCH_CACHE_LOCK = threading.Lock()
SEARCH_METRICS = {
    "requests": 0,
    "cache_hits": 0,
    "database_queries": 0,
    "database_query_ms_total": 0.0,
    "database_query_ms_max": 0.0,
}
REFRESH_STATUS = {
    "running": False,
    "last_started_at": None,
    "last_finished_at": None,
    "last_errors": [],
    "hot_running": False,
    "hot_last_started_at": None,
    "hot_last_finished_at": None,
    "hot_last_errors": [],
    "track_running": False,
    "detail_running": False,
    "historylow_running": False,
}
TRACKED_REGIONS = ["US", "CN", "JP", "HK", "TW", "KR", "GB", "DE", "FR", "BR", "RU", "TR", "AR"]
CNY_RATES = {
    "CNY": 1,
    "USD": 7.2,
    "EUR": 7.8,
    "GBP": 9.1,
    "JPY": 0.049,
    "KRW": 0.0052,
    "HKD": 0.92,
    "TWD": 0.23,
    "BRL": 1.35,
    "RUB": 0.08,
    "TRY": 0.17,
    "ARS": 0.005,
}
SEEDED_DEFAULT_APPS = [
    {"appid": 730, "name": "Counter-Strike 2"},
    {"appid": 570, "name": "Dota 2"},
    {"appid": 1172470, "name": "Apex Legends"},
    {"appid": 578080, "name": "PUBG: BATTLEGROUNDS"},
]
DEFAULT_APPS = []
UNKNOWN_GAME_NAME = "未命名游戏"
PLACEHOLDER_NAME_RE = re.compile(r"^(?:Steam\s+)?App\s+\d+$", re.IGNORECASE)
ALLOWED_IMAGE_HOSTS = {
    "shared.akamai.steamstatic.com",
    "shared.cloudflare.steamstatic.com",
    "cdn.akamai.steamstatic.com",
    "cdn.cloudflare.steamstatic.com",
    "steamcdn-a.akamaihd.net",
}
MEME_EXTENSIONS = {".gif", ".webp", ".png", ".apng", ".jpg", ".jpeg", ".jfif", ".avif", ".bmp"}


@contextmanager
def database_connection():
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def log_event(message):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{now_iso()}] {message}"
    print(line)
    try:
        with LOG_LOCK:
            with LOG_PATH.open("a", encoding="utf-8") as fp:
                fp.write(line + "\n")
    except OSError as exc:
        print(f"[log] {exc}")


def polite_store_delay():
    delay = random.uniform(STORE_REQUEST_DELAY_MIN_SECONDS, STORE_REQUEST_DELAY_MAX_SECONDS)
    if delay > 0:
        time.sleep(delay)


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
    """Use 00:10 local time as the boundary for all daily homepage picks."""
    current = moment or datetime.now()
    boundary = current.replace(hour=0, minute=10, second=0, microsecond=0)
    if current < boundary:
        current -= timedelta(days=1)
    return current.strftime("%Y-%m-%d")


def retry_delay(attempt):
    return min(8, (0.8 * (2**attempt)) + random.uniform(0, 0.35))


def safe_log_url(url):
    """Keep diagnostics useful without writing API credentials to disk."""
    parsed = urllib.parse.urlsplit(str(url))
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted = [
        (key, "***" if key.lower() in {"key", "api_key", "apikey", "token", "access_token"} else value)
        for key, value in pairs
    ]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(redacted), ""))


def external_service_for_url(url):
    host = (urllib.parse.urlsplit(str(url)).hostname or "").lower()
    if host == "api.isthereanydeal.com" or host.endswith(".isthereanydeal.com"):
        return "itad"
    if host == "store.steampowered.com":
        return "steam_store"
    if host in ALLOWED_IMAGE_HOSTS or host.endswith("steamstatic.com"):
        return "image_cdn"
    return "steam_api"


def service_cooldown_remaining_seconds(service):
    with SERVICE_COOLDOWN_LOCK:
        return max(0, int(SERVICE_COOLDOWN_UNTIL.get(service, 0) - time.time()))


def steam_cooldown_remaining_seconds():
    return max(
        service_cooldown_remaining_seconds("steam_api"),
        service_cooldown_remaining_seconds("steam_store"),
    )


def probe_proxy():
    if not USE_PROXY:
        PROXY_STATUS.update(message="直连优先，代理回退已关闭")
        return
    if not STEAM_PROXY_URL:
        PROXY_STATUS.update(reachable=False, message="代理回退已启用，但未配置 STEAMKB_PROXY_URL")
        return
    parsed = urllib.parse.urlparse(STEAM_PROXY_URL)
    host, port = parsed.hostname, parsed.port
    if not host or not port:
        PROXY_STATUS.update(reachable=False, message="代理地址格式无效")
        log_event("proxy unavailable: invalid STEAMKB_PROXY_URL")
        return
    try:
        with socket.create_connection((host, port), timeout=2):
            pass
        PROXY_STATUS.update(reachable=True, message="代理可连接")
        log_event(f"proxy reachable: {host}:{port}")
    except OSError as exc:
        PROXY_STATUS.update(reachable=False, message=f"代理不可连接: {exc}")
        log_event(f"proxy unavailable: {exc}; Steam requests will use direct connection")


def proxy_fallback_enabled():
    return USE_PROXY and PROXY_STATUS.get("reachable") is True


def direct_cooldown_remaining_seconds(service=None):
    with DIRECT_COOLDOWN_LOCK:
        if service:
            return max(0, int(DIRECT_COOLDOWN_UNTIL.get(service, 0) - time.time()))
        return max((max(0, int(value - time.time())) for value in DIRECT_COOLDOWN_UNTIL.values()), default=0)


def reserve_direct_attempt(service):
    """Allow one half-open direct probe after a cooldown expires."""
    if not proxy_fallback_enabled():
        return True
    now = time.time()
    with DIRECT_COOLDOWN_LOCK:
        if DIRECT_COOLDOWN_UNTIL.get(service, 0) > now:
            return False
        if DIRECT_FAILURE_COUNT.get(service, 0):
            DIRECT_COOLDOWN_UNTIL[service] = now + min(60, max(10, int(STEAM_TIMEOUT_SECONDS) + 5))
        return True


def record_direct_success(service):
    with DIRECT_COOLDOWN_LOCK:
        DIRECT_COOLDOWN_UNTIL[service] = 0.0
        DIRECT_FAILURE_COUNT[service] = 0


def set_direct_cooldown(service, reason=None):
    if not proxy_fallback_enabled():
        return
    now = time.time()
    until = now + (DIRECT_COOLDOWN_MINUTES * 60)
    with DIRECT_COOLDOWN_LOCK:
        was_active = DIRECT_COOLDOWN_UNTIL.get(service, 0) > now
        DIRECT_COOLDOWN_UNTIL[service] = max(DIRECT_COOLDOWN_UNTIL.get(service, 0), until)
        DIRECT_FAILURE_COUNT[service] = DIRECT_FAILURE_COUNT.get(service, 0) + 1
    if not was_active:
        suffix = f" reason={reason}" if reason else ""
        log_event(f"direct connection cooldown enabled service={service} for {DIRECT_COOLDOWN_MINUTES} minutes{suffix}")


def log_proxy_fallback_once(url, reason):
    """Avoid one identical fallback line per concurrent store request."""
    parsed = urllib.parse.urlsplit(url)
    path = re.sub(r"/appreviews/\d+(?:/|$)", "/appreviews/{appid}", parsed.path)
    key = f"{parsed.scheme}://{parsed.netloc}{path}"
    now = time.time()
    with PROXY_FALLBACK_LOCK:
        previous = PROXY_FALLBACK_LOGGED_AT.get(key, 0)
        if now - previous < 300:
            return
        PROXY_FALLBACK_LOGGED_AT[key] = now
    log_event(f"direct request failed; proxy fallback started url={key} reason={reason}")


def record_proxy_fallback(success, error=None):
    with PROXY_FALLBACK_LOCK:
        key = "fallback_successes" if success else "fallback_failures"
        PROXY_STATUS[key] = int(PROXY_STATUS.get(key) or 0) + 1
        if success and str(PROXY_STATUS.get("message") or "").startswith("代理回退失败"):
            PROXY_STATUS["message"] = "代理可连接，回退正常"
        elif not success and error:
            PROXY_STATUS["message"] = f"代理回退失败: {str(error)[:120]}"


def cleanup_image_cache_once():
    if not IMAGE_CACHE_DIR.is_dir():
        return
    cutoff = time.time() - (IMAGE_CACHE_RETENTION_DAYS * 86400)
    entries = []
    for path in IMAGE_CACHE_DIR.iterdir():
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
        if total <= IMAGE_CACHE_MAX_BYTES:
            break
        path.unlink(missing_ok=True)
        total -= size


class SteamRateLimited(Exception):
    def __init__(self, message, service="steam_api"):
        super().__init__(message)
        self.service = service


def set_service_cooldown(service, minutes=10):
    now = time.time()
    until = time.time() + (minutes * 60)
    with SERVICE_COOLDOWN_LOCK:
        previous = SERVICE_COOLDOWN_UNTIL.get(service, 0)
        was_active = previous > now
        SERVICE_COOLDOWN_UNTIL[service] = max(previous, until)
    if not was_active:
        log_event(f"external service cooldown enabled service={service} for {minutes} minutes")


def set_steam_cooldown(minutes=10, service="steam_api"):
    set_service_cooldown(service, minutes)


def check_service_cooldown(service):
    with SERVICE_COOLDOWN_LOCK:
        remaining = SERVICE_COOLDOWN_UNTIL.get(service, 0) - time.time()
    if remaining > 0:
        raise SteamRateLimited(f"{service} rate limited, retry after {int(remaining)}s", service)


def check_steam_cooldown(service="steam_api"):
    check_service_cooldown(service)


class ExternalDataUnavailable(Exception):
    pass


def request_json(url, timeout=STEAM_TIMEOUT_SECONDS, headers=None, missing_statuses=None, max_retries=None, service=None):
    from .steam_client import request_json as implementation

    return implementation(url, timeout, headers, missing_statuses, max_retries, service)


def cache_image(url):
    service = "image_cdn"
    check_service_cooldown(service)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in ALLOWED_IMAGE_HOSTS:
        raise ValueError("unsupported image host")

    suffix = Path(parsed.path).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        suffix = ".img"
    filename = hashlib.sha256(url.encode("utf-8")).hexdigest() + suffix
    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = IMAGE_CACHE_DIR / filename
    if cache_path.is_file() and cache_path.stat().st_size > 0:
        return cache_path

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": STEAM_USER_AGENT,
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=STEAM_TIMEOUT_SECONDS) as res:
            content_type = res.headers.get_content_type()
            if not content_type.startswith("image/"):
                raise ValueError(f"unexpected content type: {content_type}")
            body = res.read(IMAGE_CACHE_MAX_FILE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            set_service_cooldown(service, 10)
            raise SteamRateLimited("image_cdn HTTP 429", service) from exc
        raise
    if len(body) > IMAGE_CACHE_MAX_FILE_BYTES:
        raise ValueError("image exceeds cache file limit")
    cache_path.write_bytes(body)
    return cache_path


def require_httpx():
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("缺少 httpx，请先运行：python -m pip install httpx") from exc
    return httpx


def steam_httpx_options():
    # Direct connection is always tried first; proxy is a failure fallback.
    return {"proxy": None, "trust_env": False}


def proxy_httpx_options():
    proxy = STEAM_PROXY_URL if proxy_fallback_enabled() else None
    return {"proxy": proxy, "trust_env": False, "verify": STEAM_PROXY_VERIFY_TLS}


def is_placeholder_name(value):
    return not value or bool(PLACEHOLDER_NAME_RE.match(str(value).strip()))


def clean_name(value):
    return UNKNOWN_GAME_NAME if is_placeholder_name(value) else str(value).strip()


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
    if match:
        return match.group(1).strip()
    return None


def parse_release_date(value):
    text = str(value or "").strip()
    match = re.search(r"\b(19\d{2}|20\d{2})\b", text)
    if not match:
        return None
    year, month, day = int(match.group(1)), 1, 1
    month_match = re.search(r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)", text, re.IGNORECASE)
    if month_match:
        month = datetime.strptime(month_match.group(2)[:3].title(), "%b").month
        day = int(month_match.group(1))
    else:
        chinese_match = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})?", text)
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
    if released is None:
        return False
    return released >= datetime.now(timezone.utc) - timedelta(days=365.25 * years)


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


def init_db():
    from .migrations import migrate_database

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    migration_result = migrate_database(
        DB_PATH,
        timeout=DB_TIMEOUT_SECONDS,
        backup_dir=DB_MIGRATION_BACKUP_DIR,
        backup_keep=DB_MIGRATION_BACKUP_KEEP,
    )
    if migration_result["applied"]:
        log_event(
            f"database migrated v{migration_result['from_version']} -> "
            f"v{migration_result['to_version']} backup={migration_result['backup_path'] or 'not-needed'}"
        )
    with database_connection() as conn:
        conn.executescript(
            """
            PRAGMA journal_mode = WAL;

            CREATE TABLE IF NOT EXISTS games (
                appid INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                header_image TEXT,
                short_description TEXT,
                developer TEXT,
                publisher TEXT,
                release_date TEXT,
                is_free INTEGER DEFAULT 0,
                screenshots_json TEXT,
                tracked INTEGER DEFAULT 1,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS price_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appid INTEGER NOT NULL,
                region TEXT NOT NULL,
                currency TEXT,
                initial INTEGER,
                final INTEGER,
                discount_percent INTEGER,
                final_formatted TEXT,
                source TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                FOREIGN KEY(appid) REFERENCES games(appid)
            );

            CREATE TABLE IF NOT EXISTS player_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appid INTEGER NOT NULL,
                player_count INTEGER NOT NULL,
                fetched_at TEXT NOT NULL,
                FOREIGN KEY(appid) REFERENCES games(appid)
            );

            CREATE TABLE IF NOT EXISTS review_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appid INTEGER NOT NULL,
                review_score INTEGER,
                review_score_desc TEXT,
                total_positive INTEGER,
                total_negative INTEGER,
                total_reviews INTEGER,
                fetched_at TEXT NOT NULL,
                FOREIGN KEY(appid) REFERENCES games(appid)
            );

            CREATE TABLE IF NOT EXISTS hot_games (
                appid INTEGER PRIMARY KEY,
                rank INTEGER,
                name TEXT,
                current_players INTEGER,
                peak_players INTEGER,
                header_image TEXT,
                source TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS game_latest_state (
                appid INTEGER PRIMARY KEY,
                current_players INTEGER,
                players_updated_at TEXT,
                cn_price TEXT,
                cn_price_final INTEGER,
                cn_price_currency TEXT,
                cn_discount_percent INTEGER DEFAULT 0,
                price_updated_at TEXT,
                review_score REAL,
                total_reviews INTEGER,
                review_updated_at TEXT,
                metadata_updated_at TEXT,
                historical_low_cny REAL,
                historical_low_updated_at TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS historical_lows (
                appid INTEGER NOT NULL,
                itad_game_id TEXT NOT NULL,
                country TEXT NOT NULL,
                shop_id INTEGER,
                shop_name TEXT,
                currency TEXT,
                amount REAL,
                amount_int INTEGER,
                amount_cny REAL,
                regular_amount_int INTEGER,
                cut INTEGER,
                low_at TEXT,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY(appid, country),
                FOREIGN KEY(appid) REFERENCES games(appid)
            );

            CREATE TABLE IF NOT EXISTS crawl_state (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS crawl_tasks (
                appid INTEGER NOT NULL,
                task_type TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                locked_until TEXT,
                completed_at TEXT,
                updated_at TEXT NOT NULL,
                generation INTEGER,
                PRIMARY KEY(appid, task_type),
                FOREIGN KEY(appid) REFERENCES games(appid)
            );

            CREATE TABLE IF NOT EXISTS steam_app_names (
                appid INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS niche_pool (
                appid INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                header_image TEXT,
                current_players INTEGER,
                peak_players INTEGER,
                review_score REAL,
                total_reviews INTEGER,
                cn_price TEXT,
                cn_price_final INTEGER,
                cn_price_currency TEXT,
                cn_discount_percent INTEGER DEFAULT 0,
                is_free INTEGER DEFAULT 0,
                release_date TEXT,
                weighted_score REAL,
                source TEXT NOT NULL DEFAULT 'steam_discovery',
                eligible INTEGER NOT NULL DEFAULT 0,
                fetched_at TEXT NOT NULL,
                evaluated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS steam_catalog (
                appid INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'steam_applist',
                updated_at TEXT NOT NULL,
                last_seen_at TEXT,
                app_type TEXT NOT NULL DEFAULT 'unknown',
                app_type_checked_at TEXT,
                scan_generation INTEGER,
                last_enriched_at TEXT,
                next_enrich_at TEXT,
                enrich_status TEXT NOT NULL DEFAULT 'pending',
                enrich_attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            );

            CREATE TABLE IF NOT EXISTS niche_recommendation_snapshots (
                recommendation_date TEXT PRIMARY KEY,
                appid INTEGER NOT NULL,
                name TEXT NOT NULL,
                current_players INTEGER,
                review_score REAL,
                total_reviews INTEGER,
                weighted_score REAL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_home_snapshots (
                recommendation_date TEXT PRIMARY KEY,
                historical_low_appid INTEGER,
                meme_url TEXT,
                created_at TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_games_appid ON games(appid);
            CREATE INDEX IF NOT EXISTS idx_price_app_region_time ON price_snapshots(appid, region, fetched_at);
            CREATE INDEX IF NOT EXISTS idx_players_app_time ON player_snapshots(appid, fetched_at);
            CREATE INDEX IF NOT EXISTS idx_reviews_app_time ON review_snapshots(appid, fetched_at);
            CREATE INDEX IF NOT EXISTS idx_hot_games_rank ON hot_games(rank);
            CREATE INDEX IF NOT EXISTS idx_hot_games_players ON hot_games(current_players);
            CREATE INDEX IF NOT EXISTS idx_game_latest_state_updated ON game_latest_state(updated_at);
            CREATE INDEX IF NOT EXISTS idx_historical_lows_app_country ON historical_lows(appid, country);
            CREATE INDEX IF NOT EXISTS idx_crawl_tasks_due ON crawl_tasks(task_type, next_attempt_at, priority, locked_until);
            CREATE INDEX IF NOT EXISTS idx_niche_pool_eligible ON niche_pool(eligible, weighted_score DESC);
            CREATE INDEX IF NOT EXISTS idx_catalog_enrich_queue ON steam_catalog(enrich_status, next_enrich_at, updated_at);
            """
        )
        conn.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS latest_player_snapshot AFTER INSERT ON player_snapshots BEGIN
              INSERT INTO game_latest_state(appid, current_players, players_updated_at, updated_at)
              VALUES (NEW.appid, NEW.player_count, NEW.fetched_at, NEW.fetched_at)
              ON CONFLICT(appid) DO UPDATE SET
                current_players=CASE WHEN excluded.players_updated_at >= game_latest_state.players_updated_at OR game_latest_state.players_updated_at IS NULL THEN excluded.current_players ELSE game_latest_state.current_players END,
                players_updated_at=MAX(COALESCE(game_latest_state.players_updated_at, ''), excluded.players_updated_at),
                updated_at=MAX(game_latest_state.updated_at, excluded.updated_at);
            END;
            CREATE TRIGGER IF NOT EXISTS latest_review_snapshot AFTER INSERT ON review_snapshots BEGIN
              INSERT INTO game_latest_state(appid, review_score, total_reviews, review_updated_at, updated_at)
              VALUES (NEW.appid, NEW.review_score, NEW.total_reviews, NEW.fetched_at, NEW.fetched_at)
              ON CONFLICT(appid) DO UPDATE SET
                review_score=CASE WHEN excluded.review_updated_at >= game_latest_state.review_updated_at OR game_latest_state.review_updated_at IS NULL THEN excluded.review_score ELSE game_latest_state.review_score END,
                total_reviews=CASE WHEN excluded.review_updated_at >= game_latest_state.review_updated_at OR game_latest_state.review_updated_at IS NULL THEN excluded.total_reviews ELSE game_latest_state.total_reviews END,
                review_updated_at=MAX(COALESCE(game_latest_state.review_updated_at, ''), excluded.review_updated_at),
                updated_at=MAX(game_latest_state.updated_at, excluded.updated_at);
            END;
            CREATE TRIGGER IF NOT EXISTS latest_cn_price_snapshot AFTER INSERT ON price_snapshots WHEN NEW.region = 'CN' BEGIN
              INSERT INTO game_latest_state(appid, cn_price, cn_price_final, cn_price_currency, cn_discount_percent, price_updated_at, updated_at)
              VALUES (NEW.appid, NEW.final_formatted, NEW.final, NEW.currency, NEW.discount_percent, NEW.fetched_at, NEW.fetched_at)
              ON CONFLICT(appid) DO UPDATE SET
                cn_price=CASE WHEN excluded.price_updated_at >= game_latest_state.price_updated_at OR game_latest_state.price_updated_at IS NULL THEN excluded.cn_price ELSE game_latest_state.cn_price END,
                cn_price_final=CASE WHEN excluded.price_updated_at >= game_latest_state.price_updated_at OR game_latest_state.price_updated_at IS NULL THEN excluded.cn_price_final ELSE game_latest_state.cn_price_final END,
                cn_price_currency=CASE WHEN excluded.price_updated_at >= game_latest_state.price_updated_at OR game_latest_state.price_updated_at IS NULL THEN excluded.cn_price_currency ELSE game_latest_state.cn_price_currency END,
                cn_discount_percent=CASE WHEN excluded.price_updated_at >= game_latest_state.price_updated_at OR game_latest_state.price_updated_at IS NULL THEN excluded.cn_discount_percent ELSE game_latest_state.cn_discount_percent END,
                price_updated_at=MAX(COALESCE(game_latest_state.price_updated_at, ''), excluded.price_updated_at),
                updated_at=MAX(game_latest_state.updated_at, excluded.updated_at);
            END;
            CREATE TRIGGER IF NOT EXISTS latest_cn_historical_low AFTER INSERT ON historical_lows WHEN NEW.country = 'CN' BEGIN
              INSERT INTO game_latest_state(appid, historical_low_cny, historical_low_updated_at, updated_at)
              VALUES (NEW.appid, NEW.amount_cny, NEW.fetched_at, NEW.fetched_at)
              ON CONFLICT(appid) DO UPDATE SET
                historical_low_cny=excluded.historical_low_cny,
                historical_low_updated_at=excluded.historical_low_updated_at,
                updated_at=MAX(game_latest_state.updated_at, excluded.updated_at);
            END;
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO game_latest_state(appid, current_players, players_updated_at, cn_price, cn_price_final, cn_price_currency, cn_discount_percent, price_updated_at, review_score, total_reviews, review_updated_at, historical_low_cny, historical_low_updated_at, updated_at)
            SELECT g.appid,
              (SELECT p.player_count FROM player_snapshots p WHERE p.appid=g.appid ORDER BY p.fetched_at DESC LIMIT 1),
              (SELECT p.fetched_at FROM player_snapshots p WHERE p.appid=g.appid ORDER BY p.fetched_at DESC LIMIT 1),
              (SELECT p.final_formatted FROM price_snapshots p WHERE p.appid=g.appid AND p.region='CN' ORDER BY p.fetched_at DESC LIMIT 1),
              (SELECT p.final FROM price_snapshots p WHERE p.appid=g.appid AND p.region='CN' ORDER BY p.fetched_at DESC LIMIT 1),
              (SELECT p.currency FROM price_snapshots p WHERE p.appid=g.appid AND p.region='CN' ORDER BY p.fetched_at DESC LIMIT 1),
              (SELECT p.discount_percent FROM price_snapshots p WHERE p.appid=g.appid AND p.region='CN' ORDER BY p.fetched_at DESC LIMIT 1),
              (SELECT p.fetched_at FROM price_snapshots p WHERE p.appid=g.appid AND p.region='CN' ORDER BY p.fetched_at DESC LIMIT 1),
              (SELECT r.review_score FROM review_snapshots r WHERE r.appid=g.appid ORDER BY r.fetched_at DESC LIMIT 1),
              (SELECT r.total_reviews FROM review_snapshots r WHERE r.appid=g.appid ORDER BY r.fetched_at DESC LIMIT 1),
              (SELECT r.fetched_at FROM review_snapshots r WHERE r.appid=g.appid ORDER BY r.fetched_at DESC LIMIT 1),
              (SELECT h.amount_cny FROM historical_lows h WHERE h.appid=g.appid AND h.country='CN' LIMIT 1),
              (SELECT h.fetched_at FROM historical_lows h WHERE h.appid=g.appid AND h.country='CN' LIMIT 1),
              g.updated_at
            FROM games g
            """
        )
        conn.execute(
            """
            UPDATE niche_pool
            SET cn_price = (SELECT s.cn_price FROM game_latest_state s WHERE s.appid = niche_pool.appid),
                cn_price_final = (SELECT s.cn_price_final FROM game_latest_state s WHERE s.appid = niche_pool.appid),
                cn_price_currency = (SELECT s.cn_price_currency FROM game_latest_state s WHERE s.appid = niche_pool.appid),
                cn_discount_percent = COALESCE((SELECT s.cn_discount_percent FROM game_latest_state s WHERE s.appid = niche_pool.appid), 0)
            WHERE EXISTS (
                SELECT 1 FROM game_latest_state s
                WHERE s.appid = niche_pool.appid
                  AND s.price_updated_at IS NOT NULL
                  AND s.price_updated_at >= niche_pool.fetched_at
            )
            """
        )
        for item in DEFAULT_APPS:
            conn.execute(
                "INSERT OR IGNORE INTO games(appid, name, tracked, updated_at) VALUES (?, ?, 1, ?)",
                (item["appid"], item["name"], now_iso()),
            )
        clear_seeded_defaults(conn)
        repair_placeholder_names(conn)
        niche_price_cutoff = (datetime.now(timezone.utc) - timedelta(hours=PRICE_REFRESH_HOURS)).replace(microsecond=0).isoformat()
        due_niche_prices = [
            int(row[0])
            for row in conn.execute(
                """
                SELECT n.appid
                FROM niche_pool n
                LEFT JOIN game_latest_state s ON s.appid = n.appid
                WHERE n.eligible = 1
                  AND (s.price_updated_at IS NULL OR s.price_updated_at < ?)
                ORDER BY n.weighted_score DESC, n.total_reviews DESC
                LIMIT 50
                """,
                (niche_price_cutoff,),
            ).fetchall()
        ]
        enqueue_crawl_tasks_in_conn(conn, due_niche_prices, "preview", 60)
        conn.execute("PRAGMA optimize")


def ensure_schema(conn):
    """Compatibility validator; schema changes live in migrations.py."""
    from .migrations import validate_schema

    return validate_schema(conn)


def clear_seeded_defaults(conn):
    if get_crawl_state(conn, "default_apps_cleared_v1"):
        return
    default_appids = [item["appid"] for item in SEEDED_DEFAULT_APPS]
    if default_appids:
        placeholders = ",".join("?" for _ in default_appids)
        conn.execute(
            f"UPDATE games SET tracked = 0, updated_at = ? WHERE appid IN ({placeholders})",
            (now_iso(), *default_appids),
        )
    set_crawl_state(conn, "default_apps_cleared_v1", now_iso())


def chunks(rows, size):
    for index in range(0, len(rows), size):
        yield rows[index : index + size]


def get_crawl_state(conn, key):
    from .db import get_crawl_state as implementation

    return implementation(conn, key)


def set_crawl_state(conn, key, value):
    from .db import set_crawl_state as implementation

    return implementation(conn, key, value)


def enqueue_crawl_tasks_in_conn(conn, appids, task_type, priority, next_attempt_at=None, generation=None):
    from .db import enqueue_crawl_tasks_in_conn as implementation

    return implementation(conn, appids, task_type, priority, next_attempt_at, generation)


def enqueue_crawl_tasks(appids, task_type, priority, next_attempt_at=None, generation=None):
    from .db import enqueue_crawl_tasks as implementation

    return implementation(appids, task_type, priority, next_attempt_at, generation)


def enqueue_crawl_task_once_in_conn(conn, appid, task_type, priority, next_attempt_at=None):
    from .db import enqueue_crawl_task_once_in_conn as implementation

    return implementation(conn, appid, task_type, priority, next_attempt_at)


def claim_crawl_tasks(task_type, limit, lock_minutes=15):
    from .db import claim_crawl_tasks as implementation

    return implementation(task_type, limit, lock_minutes)


def complete_crawl_tasks(appids, task_type):
    from .db import complete_crawl_tasks as implementation

    return implementation(appids, task_type)


def mark_crawl_tasks_not_available(appids, task_type, reason):
    from .db import mark_crawl_tasks_not_available as implementation

    return implementation(appids, task_type, reason)


def fail_crawl_tasks(appids, task_type, error, retry_minutes=60, terminal=False):
    from .db import fail_crawl_tasks as implementation

    return implementation(appids, task_type, error, retry_minutes, terminal)


def detail_attempt_key(appid):
    return f"details_attempt_{int(appid)}"


def historylow_attempt_key(appid):
    return f"historylow_attempt_{int(appid)}"


def preview_attempt_key(appid):
    return f"preview_attempt_{int(appid)}"


def clean_hot_name(value):
    text = str(value).strip() if value is not None else ""
    return None if is_placeholder_name(text) or text == UNKNOWN_GAME_NAME else text


def fallback_game_name(appid, name=None):
    cleaned = clean_hot_name(name)
    return cleaned or UNKNOWN_GAME_NAME


def parse_hot_chart(payload):
    response = payload.get("response") if isinstance(payload, dict) else {}
    candidates = []

    def visit(value):
        if isinstance(value, dict):
            appid = value.get("appid") or value.get("app_id") or value.get("steam_appid")
            if appid:
                item = value.get("item") if isinstance(value.get("item"), dict) else {}
                assets = item.get("assets") if isinstance(item.get("assets"), dict) else {}
                candidates.append(
                    {
                        "appid": int(appid),
                        "rank": value.get("rank"),
                        "name": value.get("name") or item.get("name"),
                        "current_players": value.get("concurrent_in_game")
                        or value.get("current_players")
                        or value.get("players"),
                        "peak_players": value.get("peak_in_game") or value.get("peak_players"),
                        "header_image": value.get("header_image") or assets.get("header") or assets.get("small_capsule"),
                        "source": "steam_charts",
                    }
                )
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(response)
    seen = set()
    rows = []
    for index, row in enumerate(candidates, 1):
        appid = row["appid"]
        if appid in seen:
            continue
        seen.add(appid)
        row["rank"] = int(row.get("rank") or index)
        rows.append(row)
        if len(rows) >= HOTLIST_TARGET:
            break
    return rows


def hot_placeholder_appids(limit=200):
    with database_connection() as conn:
        rows = conn.execute(
            """
            SELECT h.appid
            FROM hot_games h
            LEFT JOIN games g ON g.appid = h.appid
            LEFT JOIN steam_app_names san ON san.appid = h.appid
            WHERE (h.name IS NULL OR TRIM(h.name) = '' OR h.name = ? OR h.name LIKE 'App %' OR h.name LIKE 'Steam App %')
              AND (g.name IS NULL OR TRIM(g.name) = '' OR g.name = ? OR g.name LIKE 'App %' OR g.name LIKE 'Steam App %')
              AND san.appid IS NULL
            ORDER BY COALESCE(h.rank, 999999), COALESCE(h.current_players, 0) DESC
            LIMIT ?
            """,
            (UNKNOWN_GAME_NAME, UNKNOWN_GAME_NAME, limit),
        ).fetchall()
    return [int(row[0]) for row in rows]


def hot_preview_missing_appids(limit=100):
    with database_connection() as conn:
        rows = conn.execute(
            """
            SELECT h.appid
            FROM hot_games h
            LEFT JOIN games g ON g.appid = h.appid
            LEFT JOIN steam_app_names san ON san.appid = h.appid
            WHERE (
                    COALESCE(NULLIF(TRIM(g.header_image), ''), NULLIF(TRIM(h.header_image), '')) IS NULL
                 OR (
                        (g.name IS NULL OR TRIM(g.name) = '' OR g.name = ? OR g.name LIKE 'App %' OR g.name LIKE 'Steam App %')
                    AND (h.name IS NULL OR TRIM(h.name) = '' OR h.name = ? OR h.name LIKE 'App %' OR h.name LIKE 'Steam App %')
                    AND (san.name IS NULL OR TRIM(san.name) = '')
                    )
            )
            ORDER BY COALESCE(h.rank, 999999), COALESCE(h.current_players, 0) DESC
            LIMIT ?
            """,
            (UNKNOWN_GAME_NAME, UNKNOWN_GAME_NAME, limit),
        ).fetchall()
    return [int(row[0]) for row in rows]


def enqueue_missing_hot_previews(limit=100, priority=90):
    appids = hot_preview_missing_appids(limit)
    if not appids:
        return 0
    enqueue_crawl_tasks(appids, "preview", priority)
    return len(appids)


def upsert_steam_app_names(name_rows):
    rows = [
        (int(appid), clean_hot_name(name), now_iso())
        for appid, name in name_rows
        if appid and clean_hot_name(name)
    ]
    if not rows:
        return 0
    with database_connection() as conn:
        conn.executemany(
            """
            INSERT INTO steam_app_names(appid, name, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(appid) DO UPDATE SET
                name=excluded.name,
                updated_at=excluded.updated_at
            """,
            rows,
        )
        conn.executemany(
            """
            UPDATE hot_games
            SET name = ?, fetched_at = ?
            WHERE appid = ?
              AND (name IS NULL OR TRIM(name) = '' OR name = ? OR name LIKE 'App %' OR name LIKE 'Steam App %')
            """,
            [(name, stamp, appid, UNKNOWN_GAME_NAME) for appid, name, stamp in rows],
        )
        conn.executemany(
            """
            UPDATE games
            SET name = ?, updated_at = ?
            WHERE appid = ?
              AND (name IS NULL OR TRIM(name) = '' OR name = ? OR name LIKE 'App %' OR name LIKE 'Steam App %')
            """,
            [(name, stamp, appid, UNKNOWN_GAME_NAME) for appid, name, stamp in rows],
        )
    return len(rows)


def refresh_steam_app_names_once(force=False):
    missing_appids = set(hot_placeholder_appids())
    if not missing_appids:
        return False
    with database_connection() as conn:
        app_names_at = get_crawl_state(conn, "steam_app_names_at")
    if not (force or is_due(app_names_at, APP_NAME_REFRESH_HOURS * 60)):
        return False
    with database_connection() as conn:
        name_rows = conn.execute(
            "SELECT appid, name FROM steam_catalog WHERE appid IN ({})".format(",".join("?" * len(missing_appids))),
            tuple(missing_appids),
        ).fetchall() if missing_appids else []
    updated_count = upsert_steam_app_names(name_rows)
    stamp = now_iso()
    with database_connection() as conn:
        set_crawl_state(conn, "steam_app_names_at", stamp)
    log_event(f"steam app names refreshed matched={updated_count} missing={len(missing_appids)}")
    return True


def upsert_hot_games_batch(rows, stamp):
    if not rows:
        return
    with database_connection() as conn:
        conn.executemany(
            """
            INSERT INTO hot_games(appid, rank, name, current_players, peak_players, header_image, source, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(appid) DO UPDATE SET
                rank=excluded.rank,
                name=COALESCE(excluded.name, hot_games.name),
                current_players=COALESCE(excluded.current_players, hot_games.current_players),
                peak_players=COALESCE(excluded.peak_players, hot_games.peak_players),
                header_image=COALESCE(excluded.header_image, hot_games.header_image),
                source=excluded.source,
                fetched_at=excluded.fetched_at
            """,
            [
                (
                    row["appid"],
                    row.get("rank"),
                    clean_hot_name(row.get("name")),
                    row.get("current_players"),
                    row.get("peak_players"),
                    row.get("header_image"),
                    row.get("source") or "steam_charts",
                    stamp,
                )
                for row in rows
            ],
        )
        conn.executemany(
            """
            INSERT INTO games(appid, name, header_image, tracked, updated_at)
            VALUES (?, ?, ?, 0, ?)
            ON CONFLICT(appid) DO UPDATE SET
                name=CASE
                    WHEN excluded.name != ? THEN excluded.name
                    ELSE games.name
                END,
                header_image=COALESCE(excluded.header_image, games.header_image),
                updated_at=excluded.updated_at
            """,
            [
                (
                    row["appid"],
                    fallback_game_name(row["appid"], row.get("name")),
                    row.get("header_image"),
                    stamp,
                    UNKNOWN_GAME_NAME,
                )
                for row in rows
            ],
        )


def insert_player_batch(rows):
    if not rows:
        return
    with database_connection() as conn:
        conn.executemany(
            "INSERT INTO player_snapshots(appid, player_count, fetched_at) VALUES (?, ?, ?)",
            rows,
        )
        conn.executemany(
            "UPDATE hot_games SET current_players = ?, fetched_at = ? WHERE appid = ?",
            [(player_count, fetched_at, appid) for appid, player_count, fetched_at in rows],
        )


def upsert_hot_metadata_batch(rows, stamp):
    if not rows:
        return
    with database_connection() as conn:
        conn.executemany(
            """
            INSERT INTO games(appid, name, header_image, short_description, developer, publisher, release_date, is_free, screenshots_json, tracked, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(appid) DO UPDATE SET
                name=CASE
                    WHEN excluded.name != ? THEN excluded.name
                    ELSE games.name
                END,
                header_image=COALESCE(excluded.header_image, games.header_image),
                short_description=COALESCE(excluded.short_description, games.short_description),
                developer=COALESCE(excluded.developer, games.developer),
                publisher=COALESCE(excluded.publisher, games.publisher),
                release_date=COALESCE(excluded.release_date, games.release_date),
                is_free=excluded.is_free,
                screenshots_json=COALESCE(excluded.screenshots_json, games.screenshots_json),
                updated_at=excluded.updated_at
            """,
            [
                (
                    row["appid"],
                    fallback_game_name(row["appid"], row.get("name")),
                    row.get("header_image"),
                    row.get("short_description"),
                    row.get("developer"),
                    row.get("publisher"),
                    row.get("release_date"),
                    row.get("is_free"),
                    row.get("screenshots_json"),
                    stamp,
                    UNKNOWN_GAME_NAME,
                )
                for row in rows
            ],
        )
        conn.executemany(
            """
            INSERT INTO price_snapshots(appid, region, currency, initial, final, discount_percent, final_formatted, source, fetched_at)
            VALUES (?, 'CN', ?, ?, ?, ?, ?, 'steam', ?)
            """,
            [
                (
                    row["appid"],
                    row.get("currency"),
                    row.get("initial"),
                    row.get("final"),
                    row.get("discount_percent"),
                    row.get("final_formatted"),
                    stamp,
                )
                for row in rows
                if row.get("has_price")
            ],
        )
        conn.executemany(
            """
            INSERT INTO review_snapshots(appid, review_score, review_score_desc, total_positive, total_negative, total_reviews, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["appid"],
                    row.get("review_score"),
                    row.get("review_score_desc"),
                    row.get("total_positive"),
                    row.get("total_negative"),
                    row.get("total_reviews"),
                    stamp,
                )
                for row in rows
                if row.get("has_reviews")
            ],
        )


async def async_request_direct_then_proxy(client, method, url, params=None, json_body=None):
    from .steam_client import async_request_direct_then_proxy as implementation

    return await implementation(client, method, url, params, json_body)


async def async_get_json(client, semaphore, url, params=None):
    from .steam_client import async_get_json as implementation

    return await implementation(client, semaphore, url, params)


async def async_post_json(client, semaphore, url, params=None, json_body=None):
    from .steam_client import async_post_json as implementation

    return await implementation(client, semaphore, url, params, json_body)


def amount_int_to_cny(amount_int, currency):
    if amount_int is None:
        return None
    return (float(amount_int) / 100) * CNY_RATES.get(str(currency or "").upper(), 1)


def price_row_cny(row):
    item = dict(row)
    region = item.get("region")
    currency = item.get("currency") or ("CNY" if region == "CN" else "USD" if region in ("US", "ITAD-US") else "")
    return amount_int_to_cny(item.get("final"), currency)


def compare_historical_low(current_cny, low_cny):
    if current_cny is None or low_cny is None:
        return False
    return abs(float(current_cny) - float(low_cny)) <= HISTORICAL_LOW_TOLERANCE_CNY


def itad_headers():
    headers = {
        "User-Agent": STEAM_USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if ITAD_API_KEY:
        headers["ITAD-API-Key"] = ITAD_API_KEY
    return headers


def save_itad_game_ids(rows):
    rows = [(int(appid), gid, now_iso()) for appid, gid in rows if gid]
    if not rows:
        return
    with database_connection() as conn:
        conn.executemany(
            "UPDATE games SET itad_game_id = ?, updated_at = ? WHERE appid = ?",
            [(gid, stamp, appid) for appid, gid, stamp in rows],
        )


def upsert_historical_lows(rows):
    if not rows:
        return
    with database_connection() as conn:
        conn.executemany(
            """
            INSERT INTO historical_lows(appid, itad_game_id, country, shop_id, shop_name, currency, amount, amount_int, amount_cny, regular_amount_int, cut, low_at, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(appid, country) DO UPDATE SET
                itad_game_id=excluded.itad_game_id,
                shop_id=excluded.shop_id,
                shop_name=excluded.shop_name,
                currency=excluded.currency,
                amount=excluded.amount,
                amount_int=excluded.amount_int,
                amount_cny=excluded.amount_cny,
                regular_amount_int=excluded.regular_amount_int,
                cut=excluded.cut,
                low_at=excluded.low_at,
                fetched_at=excluded.fetched_at
            """,
            rows,
        )


async def fetch_itad_game_ids_async(appids):
    from .crawler import fetch_itad_game_ids_async as implementation

    return await implementation(appids)


async def fetch_itad_history_lows_async(appids, countries=("US", "CN")):
    from .crawler import fetch_itad_history_lows_async as implementation

    return await implementation(appids, countries)


async def fetch_official_hotlist_async():
    from .steam_client import fetch_official_hotlist_async as implementation

    return await implementation()


async def fetch_players_for_appids_async(appids):
    httpx = require_httpx()
    semaphore = asyncio.Semaphore(HOTLIST_CONCURRENCY)
    headers = {"User-Agent": STEAM_USER_AGENT}
    url = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
    stamp = now_iso()
    rows, successful_appids, failed = [], [], 0
    async with httpx.AsyncClient(timeout=STEAM_TIMEOUT_SECONDS, headers=headers, follow_redirects=True, **steam_httpx_options()) as client:
        async def fetch_one(appid):
            try:
                payload = await async_get_json(client, semaphore, url, {"appid": appid})
                count = int((payload.get("response") or {}).get("player_count") or 0)
                return (int(appid), count, stamp)
            except Exception as exc:
                return exc

        results = await asyncio.gather(*(fetch_one(appid) for appid in appids), return_exceptions=True)
        rate_limited = any(isinstance(result, SteamRateLimited) for result in results)
        for result in results:
            if isinstance(result, tuple):
                rows.append(result)
                successful_appids.append(result[0])
                if len(rows) >= HOTLIST_BATCH_SIZE:
                    insert_player_batch(rows)
                    rows = []
            else:
                failed += 1
    insert_player_batch(rows)
    if rate_limited:
        raise SteamRateLimited("Steam player requests paused by global cooldown")
    return {"stamp": stamp, "success": len(successful_appids), "success_appids": successful_appids, "failed": failed, "skipped": 0}


def upsert_hot_price_batch(rows, stamp):
    if not rows:
        return
    with database_connection() as conn:
        conn.executemany(
            """
            UPDATE games
            SET name = COALESCE(?, name),
                header_image = COALESCE(?, header_image),
                is_free = COALESCE(?, is_free),
                updated_at = ?
            WHERE appid = ?
            """,
            [
                (
                    row.get("name"),
                    row.get("header_image"),
                    row.get("is_free"),
                    stamp,
                    row["appid"],
                )
                for row in rows
            ],
        )
        conn.executemany(
            """
            INSERT INTO price_snapshots(appid, region, currency, initial, final, discount_percent, final_formatted, source, fetched_at)
            VALUES (?, 'CN', ?, ?, ?, ?, ?, 'steam', ?)
            """,
            [
                (
                    row["appid"],
                    row.get("currency"),
                    row.get("initial"),
                    row.get("final"),
                    row.get("discount_percent"),
                    row.get("final_formatted"),
                    stamp,
                )
                for row in rows
                if row.get("has_price")
            ],
        )
        conn.executemany(
            """
            INSERT INTO game_latest_state(
                appid, cn_price, cn_price_final, cn_price_currency,
                cn_discount_percent, price_updated_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(appid) DO UPDATE SET
                cn_price = excluded.cn_price,
                cn_price_final = excluded.cn_price_final,
                cn_price_currency = excluded.cn_price_currency,
                cn_discount_percent = excluded.cn_discount_percent,
                price_updated_at = excluded.price_updated_at,
                updated_at = excluded.updated_at
            """,
            [
                (
                    row["appid"],
                    row.get("final_formatted") if row.get("has_price") else None,
                    row.get("final") if row.get("has_price") else None,
                    row.get("currency") if row.get("has_price") else None,
                    row.get("discount_percent", 0) if row.get("has_price") else 0,
                    stamp,
                    stamp,
                )
                for row in rows
            ],
        )
        conn.executemany(
            """
            UPDATE niche_pool
            SET cn_price = ?, cn_price_final = ?, cn_price_currency = ?,
                cn_discount_percent = ?, is_free = ?
            WHERE appid = ?
            """,
            [
                (
                    row.get("final_formatted") if row.get("has_price") else None,
                    row.get("final") if row.get("has_price") else None,
                    row.get("currency") if row.get("has_price") else None,
                    row.get("discount_percent", 0) if row.get("has_price") else 0,
                    row.get("is_free", 0),
                    row["appid"],
                )
                for row in rows
            ],
        )


def upsert_release_date_batch(rows, stamp):
    if not rows:
        return
    with database_connection() as conn:
        conn.executemany(
            """
            UPDATE games
            SET name = COALESCE(?, name),
                header_image = COALESCE(?, header_image),
                release_date = COALESCE(release_date, ?),
                is_free = COALESCE(?, is_free),
                updated_at = ?
            WHERE appid = ?
            """,
            [
                (
                    row.get("name"),
                    row.get("header_image"),
                    row.get("release_date"),
                    row.get("is_free"),
                    stamp,
                    row["appid"],
                )
                for row in rows
            ],
        )


def upsert_review_batch(rows, stamp):
    if not rows:
        return
    with database_connection() as conn:
        conn.executemany(
            """
            INSERT INTO review_snapshots(appid, review_score, review_score_desc, total_positive, total_negative, total_reviews, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["appid"],
                    row.get("review_score"),
                    row.get("review_score_desc"),
                    row.get("total_positive"),
                    row.get("total_negative"),
                    row.get("total_reviews"),
                    stamp,
                )
                for row in rows
                if row.get("has_reviews")
            ],
        )
        conn.executemany(
            """
            INSERT INTO game_latest_state(appid, metadata_updated_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(appid) DO UPDATE SET
                metadata_updated_at = excluded.metadata_updated_at,
                updated_at = excluded.updated_at
            """,
            [(row["appid"], stamp, stamp) for row in rows],
        )
        # Zero reviews are valid, and should not cause another request on the
        # next scheduler pass just because no historical row was inserted.
        conn.executemany(
            """
            INSERT INTO game_latest_state(appid, review_score, total_reviews, review_updated_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(appid) DO UPDATE SET
                review_score = excluded.review_score,
                total_reviews = excluded.total_reviews,
                review_updated_at = excluded.review_updated_at,
                updated_at = excluded.updated_at
            """,
            [
                (row["appid"], row.get("review_score"), row.get("total_reviews"), stamp, stamp)
                for row in rows
            ],
        )


async def fetch_hot_metadata_async(appids, full=True, include_reviews=False):
    httpx = require_httpx()
    semaphore = asyncio.Semaphore(HOT_METADATA_CONCURRENCY)
    headers = {"User-Agent": STEAM_USER_AGENT}
    details_url = "https://store.steampowered.com/api/appdetails"
    stamp = now_iso()
    rows, unavailable_appids, retry_appids = [], [], []
    async with httpx.AsyncClient(timeout=STEAM_TIMEOUT_SECONDS, headers=headers, follow_redirects=True, **steam_httpx_options()) as client:
        async def fetch_one(appid):
            await asyncio.sleep(random.uniform(STORE_REQUEST_DELAY_MIN_SECONDS, STORE_REQUEST_DELAY_MAX_SECONDS))
            try:
                payload = await async_get_json(client, semaphore, details_url, {"appids": appid, "cc": "CN", "l": "schinese"})
                data = (payload.get(str(appid)) or {}).get("data") or {}
                if not data:
                    return ("not_available", int(appid), "Steam AppDetails returned no public data")
                price = data.get("price_overview") or {}
                release = data.get("release_date") or {}
                row = {
                    "appid": int(appid),
                    "name": data.get("name"),
                    "header_image": data.get("header_image"),
                    "short_description": data.get("short_description") if full else None,
                    "developer": ", ".join(data.get("developers") or []) if full else None,
                    "publisher": ", ".join(data.get("publishers") or []) if full else None,
                    "release_date": release.get("date") if isinstance(release, dict) else None,
                    "is_free": 1 if data.get("is_free") else 0,
                    "screenshots_json": None,
                    "currency": price.get("currency"),
                    "initial": price.get("initial", 0),
                    "final": price.get("final", 0),
                    "discount_percent": price.get("discount_percent", 0),
                    "final_formatted": price.get("final_formatted", "Free") if price or data.get("is_free") else None,
                    "has_price": bool(price or data.get("is_free")),
                }
                if include_reviews:
                    review_qs = {
                        "json": 1,
                        "language": "all",
                        "purchase_type": "all",
                        "num_per_page": 0,
                        "filter": "summary",
                    }
                    try:
                        review_payload = await async_get_json(
                            client,
                            semaphore,
                            f"https://store.steampowered.com/appreviews/{appid}",
                            review_qs,
                        )
                        summary = review_payload.get("query_summary") or {}
                        total_positive = int(summary.get("total_positive") or 0)
                        total_negative = int(summary.get("total_negative") or 0)
                        total = total_positive + total_negative
                        row.update(
                            {
                                "review_score": round((total_positive / total) * 100) if total else None,
                                "review_score_desc": summary.get("review_score_desc"),
                                "total_positive": total_positive,
                                "total_negative": total_negative,
                                "total_reviews": total,
                                "has_reviews": total > 0,
                            }
                        )
                    except Exception as exc:
                        log_event(f"hot review skipped appid={appid}: {exc}")
                return row
            except ExternalDataUnavailable as exc:
                return ("not_available", int(appid), str(exc))
            except Exception as exc:
                if isinstance(exc, SteamRateLimited):
                    raise
                log_event(f"hot metadata skipped appid={appid}: {exc}")
                return ("retry", int(appid), str(exc))

        results = await asyncio.gather(*(fetch_one(appid) for appid in appids), return_exceptions=True)
        if any(isinstance(result, SteamRateLimited) for result in results):
            raise SteamRateLimited("Steam metadata requests paused by global cooldown")
        for result in results:
            if isinstance(result, dict):
                rows.append(result)
            elif isinstance(result, tuple) and result[0] == "not_available":
                unavailable_appids.append(result[1])
            elif isinstance(result, tuple) and result[0] == "retry":
                retry_appids.append(result[1])
    return rows, unavailable_appids, retry_appids, stamp


async def fetch_hot_reviews_async(appids):
    httpx = require_httpx()
    semaphore = asyncio.Semaphore(HOT_METADATA_CONCURRENCY)
    headers = {"User-Agent": STEAM_USER_AGENT}
    stamp = now_iso()
    rows, unavailable_appids, retry_appids = [], [], []
    async with httpx.AsyncClient(timeout=STEAM_TIMEOUT_SECONDS, headers=headers, follow_redirects=True, **steam_httpx_options()) as client:
        async def fetch_one(appid):
            await asyncio.sleep(random.uniform(STORE_REQUEST_DELAY_MIN_SECONDS, STORE_REQUEST_DELAY_MAX_SECONDS))
            review_qs = {
                "json": 1,
                "language": "all",
                "purchase_type": "all",
                "num_per_page": 0,
                "filter": "summary",
            }
            try:
                payload = await async_get_json(
                    client,
                    semaphore,
                    f"https://store.steampowered.com/appreviews/{appid}",
                    review_qs,
                )
                summary = payload.get("query_summary") or {}
                total_positive = int(summary.get("total_positive") or 0)
                total_negative = int(summary.get("total_negative") or 0)
                total = total_positive + total_negative
                return {
                    "appid": int(appid),
                    "review_score": round((total_positive / total) * 100) if total else None,
                    "review_score_desc": summary.get("review_score_desc"),
                    "total_positive": total_positive,
                    "total_negative": total_negative,
                    "total_reviews": total,
                    "has_reviews": total > 0,
                }
            except ExternalDataUnavailable as exc:
                return ("not_available", int(appid), str(exc))
            except Exception as exc:
                if isinstance(exc, SteamRateLimited):
                    raise
                log_event(f"hot review skipped appid={appid}: {exc}")
                return ("retry", int(appid), str(exc))

        results = await asyncio.gather(*(fetch_one(appid) for appid in appids), return_exceptions=True)
        if any(isinstance(result, SteamRateLimited) for result in results):
            raise SteamRateLimited("Steam review requests paused by global cooldown")
        for result in results:
            if isinstance(result, dict):
                rows.append(result)
            elif isinstance(result, tuple) and result[0] == "not_available":
                unavailable_appids.append(result[1])
            elif isinstance(result, tuple) and result[0] == "retry":
                retry_appids.append(result[1])
    return rows, unavailable_appids, retry_appids, stamp


def discover_niche_appids(limit=NICHE_POOL_BATCH_LIMIT):
    """Find candidates independently of the current hot list.

    Existing games are preferred so a transient AppList failure does not stop
    the pool. The official AppList is only used to add a small rotating sample
    of apps that the local database has never seen.
    """
    selected = []
    with database_connection() as conn:
        rows = conn.execute(
            """
            SELECT g.appid
            FROM games g
            LEFT JOIN niche_pool n ON n.appid = g.appid
            LEFT JOIN steam_catalog c ON c.appid = g.appid
            WHERE n.appid IS NULL
              AND COALESCE(c.app_type, 'unknown') IN ('unknown', 'game')
              AND g.name IS NOT NULL
              AND g.name != ?
              AND g.header_image IS NOT NULL
            ORDER BY g.updated_at ASC
            LIMIT ?
            """,
            (UNKNOWN_GAME_NAME, limit),
        ).fetchall()
        selected.extend(int(row[0]) for row in rows)
        known = {int(row[0]) for row in conn.execute("SELECT appid FROM games").fetchall()}
        known.update(int(row[0]) for row in conn.execute("SELECT appid FROM hot_games").fetchall())
        seen_pool = {int(row[0]) for row in conn.execute("SELECT appid FROM niche_pool").fetchall()}

    if len(selected) < limit:
        with database_connection() as conn:
            candidates = [
                int(row[0]) for row in conn.execute(
                    "SELECT appid FROM steam_catalog WHERE app_type IN ('unknown', 'game') AND appid NOT IN ({}) ORDER BY CASE app_type WHEN 'game' THEN 0 ELSE 1 END, updated_at ASC LIMIT ?".format(
                        ",".join("?" * max(1, len(known | seen_pool)))
                    ),
                    tuple(known | seen_pool) + (max(0, limit - len(selected)),),
                ).fetchall()
            ] if known | seen_pool else [
                int(row[0]) for row in conn.execute(
                    "SELECT appid FROM steam_catalog WHERE app_type IN ('unknown', 'game') ORDER BY CASE app_type WHEN 'game' THEN 0 ELSE 1 END, updated_at ASC LIMIT ?",
                    (max(0, limit - len(selected)),),
                ).fetchall()
            ]
        random.shuffle(candidates)
        selected.extend(candidates[: max(0, limit - len(selected))])
    return list(dict.fromkeys(selected))[:limit]


def known_peak_players(appids):
    if not appids:
        return {}
    placeholders = ",".join("?" for _ in appids)
    with database_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT p.appid, MAX(p.player_count) AS peak_players
            FROM player_snapshots p
            WHERE p.appid IN ({placeholders})
            GROUP BY p.appid
            """,
            [int(appid) for appid in appids],
        ).fetchall()
    return {int(appid): int(peak or 0) for appid, peak in rows}


async def fetch_niche_candidates_async(appids):
    httpx = require_httpx()
    semaphore = asyncio.Semaphore(min(8, max(1, HOTLIST_CONCURRENCY)))
    headers = {"User-Agent": STEAM_USER_AGENT}
    details_url = "https://store.steampowered.com/api/appdetails"
    players_url = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
    stamp = now_iso()
    known_peaks = known_peak_players(appids)
    rows = []
    async with httpx.AsyncClient(timeout=STEAM_TIMEOUT_SECONDS, headers=headers, follow_redirects=True, **steam_httpx_options()) as client:
        async def fetch_one(appid):
            try:
                await asyncio.sleep(random.uniform(STORE_REQUEST_DELAY_MIN_SECONDS, STORE_REQUEST_DELAY_MAX_SECONDS))
                details_payload = await async_get_json(
                    client, semaphore, details_url,
                    {"appids": appid, "cc": "CN", "l": "schinese"},
                )
                app_payload = details_payload.get(str(appid)) or {}
                data = app_payload.get("data") or {}
                if not app_payload.get("success") or not data:
                    return {"appid": int(appid), "catalog_result": "not_available", "app_type": "unknown"}
                app_type = str(data.get("type") or "unknown").strip().lower()
                if app_type != "game":
                    return {"appid": int(appid), "catalog_result": "excluded", "app_type": app_type}
                try:
                    review_payload = await async_get_json(
                        client, semaphore, f"https://store.steampowered.com/appreviews/{appid}",
                        {"json": 1, "language": "all", "purchase_type": "all", "num_per_page": 0, "filter": "summary"},
                    )
                except ExternalDataUnavailable:
                    review_payload = {}
                summary = review_payload.get("query_summary") or {}
                positive = int(summary.get("total_positive") or 0)
                negative = int(summary.get("total_negative") or 0)
                total = positive + negative
                try:
                    player_payload = await async_get_json(client, semaphore, players_url, {"appid": appid})
                except ExternalDataUnavailable:
                    player_payload = {}
                players = int((player_payload.get("response") or {}).get("player_count") or 0)
                price = data.get("price_overview") or {}
                return {
                    "appid": int(appid),
                    "catalog_result": "game",
                    "app_type": "game",
                    "name": data.get("name") or UNKNOWN_GAME_NAME,
                    "header_image": data.get("header_image"),
                    "current_players": players,
                    "peak_players": max(players, known_peaks.get(int(appid), 0)),
                    "review_score": round((positive / total) * 100, 2) if total else None,
                    "total_reviews": total,
                    "cn_price": price.get("final_formatted", "Free") if price or data.get("is_free") else None,
                    "cn_price_initial": price.get("initial", 0) if price or data.get("is_free") else None,
                    "cn_price_final": price.get("final", 0) if price or data.get("is_free") else None,
                    "cn_price_currency": price.get("currency") or ("CNY" if data.get("is_free") else None),
                    "cn_discount_percent": price.get("discount_percent", 0),
                    "is_free": 1 if data.get("is_free") else 0,
                    "release_date": (data.get("release_date") or {}).get("date"),
                    "fetched_at": stamp,
                }
            except SteamRateLimited:
                raise
            except ExternalDataUnavailable as exc:
                return {"appid": int(appid), "catalog_result": "not_available", "app_type": "unknown", "error": str(exc)}
            except Exception as exc:
                log_event(f"niche candidate skipped appid={appid}: {exc}")
                return {"appid": int(appid), "catalog_result": "retry", "app_type": "unknown", "error": str(exc)}

        results = await asyncio.gather(*(fetch_one(appid) for appid in appids), return_exceptions=True)
        if any(isinstance(result, SteamRateLimited) for result in results):
            raise SteamRateLimited("Steam niche requests paused by global cooldown")
        for result in results:
            if isinstance(result, dict):
                rows.append(result)
    return rows


def niche_weighted_score(row):
    score = float(row.get("review_score") or 0)
    reviews = max(0, int(row.get("total_reviews") or 0))
    players = int(row.get("current_players") or 0)
    peak_players = max(players, int(row.get("peak_players") or 0))
    review_part = max(0.0, min(1.0, (score - 85.0) / 15.0))
    review_count_part = min(1.0, math.log1p(reviews) / math.log1p(100000))
    peak_part = min(1.0, math.log1p(peak_players) / math.log1p(2000))
    release_part = release_recency_factor(row.get("release_date"))
    return round((review_part * 0.45) + (review_count_part * 0.30) + (peak_part * 0.15) + (release_part * 0.10), 6)


def upsert_niche_pool_rows(rows, persist_prices=False):
    rows = [row for row in rows if row.get("catalog_result", "game") == "game"]
    if not rows:
        return 0
    evaluated_at = now_iso()
    prepared = []
    for row in rows:
        eligible = bool(
            not is_obvious_non_game_name(row.get("name"))
            and int(row.get("current_players") or 0) >= 10
            and 0 < int(row.get("peak_players") or 0) <= 2000
            and float(row.get("review_score") or 0) >= 85
            and 0 < int(row.get("total_reviews") or 0) <= NICHE_MAX_REVIEWS
            and is_recent_release(row.get("release_date"))
        )
        prepared.append((row, niche_weighted_score(row) if eligible else 0.0, 1 if eligible else 0))
    with database_connection() as conn:
        conn.executemany(
            """
            INSERT INTO games(appid, name, header_image, release_date, is_free, tracked, updated_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(appid) DO UPDATE SET
                name=CASE WHEN excluded.name != ? THEN excluded.name ELSE games.name END,
                header_image=COALESCE(excluded.header_image, games.header_image),
                release_date=COALESCE(games.release_date, excluded.release_date),
                is_free=excluded.is_free,
                updated_at=excluded.updated_at
            """,
            [
                (r["appid"], fallback_game_name(r["appid"], r["name"]), r.get("header_image"), r.get("release_date"), r.get("is_free", 0), evaluated_at, UNKNOWN_GAME_NAME)
                for r, _, _ in prepared
            ],
        )
        conn.executemany(
            """INSERT INTO player_snapshots(appid, player_count, fetched_at) VALUES (?, ?, ?)""",
            [(r["appid"], r.get("current_players") or 0, r.get("fetched_at") or evaluated_at) for r, _, _ in prepared],
        )
        conn.executemany(
            """INSERT INTO review_snapshots(appid, review_score, review_score_desc, total_positive, total_negative, total_reviews, fetched_at)
               VALUES (?, ?, NULL, NULL, NULL, ?, ?)""",
            [(r["appid"], r.get("review_score"), r.get("total_reviews") or 0, r.get("fetched_at") or evaluated_at) for r, _, _ in prepared],
        )
        conn.executemany(
            """
            INSERT INTO niche_pool(appid, name, header_image, current_players, peak_players, review_score, total_reviews,
                                   cn_price, cn_price_final, cn_price_currency, cn_discount_percent, is_free,
                                   release_date, weighted_score, source, eligible, fetched_at, evaluated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'steam_discovery', ?, ?, ?)
            ON CONFLICT(appid) DO UPDATE SET
                name=excluded.name, header_image=excluded.header_image,
                current_players=excluded.current_players, peak_players=MAX(COALESCE(niche_pool.peak_players, 0), COALESCE(excluded.peak_players, 0)), review_score=excluded.review_score,
                total_reviews=excluded.total_reviews, cn_price=excluded.cn_price,
                cn_price_final=excluded.cn_price_final, cn_price_currency=excluded.cn_price_currency,
                cn_discount_percent=excluded.cn_discount_percent, is_free=excluded.is_free,
                release_date=COALESCE(excluded.release_date, niche_pool.release_date),
                weighted_score=excluded.weighted_score, eligible=excluded.eligible,
                fetched_at=excluded.fetched_at, evaluated_at=excluded.evaluated_at
            """,
            [
                (r["appid"], fallback_game_name(r["appid"], r["name"]), r.get("header_image"), r.get("current_players") or 0,
                 max(r.get("current_players") or 0, r.get("peak_players") or 0), r.get("review_score"), r.get("total_reviews") or 0, r.get("cn_price"), r.get("cn_price_final"),
                 r.get("cn_price_currency"), r.get("cn_discount_percent") or 0, r.get("is_free", 0), r.get("release_date"), score, eligible,
                 r.get("fetched_at") or evaluated_at, evaluated_at)
                for r, score, eligible in prepared
            ],
        )
        conn.execute("DELETE FROM niche_pool WHERE eligible = 0")
        conn.execute(
            """
            DELETE FROM niche_pool
            WHERE appid NOT IN (
                SELECT appid FROM niche_pool WHERE eligible = 1
                ORDER BY weighted_score DESC, total_reviews DESC LIMIT ?
            )
            """,
            (NICHE_POOL_LIMIT,),
        )
    if persist_prices:
        upsert_hot_price_batch(
            [
                {
                    "appid": row["appid"],
                    "name": row.get("name"),
                    "header_image": row.get("header_image"),
                    "is_free": row.get("is_free", 0),
                    "currency": row.get("cn_price_currency"),
                    "initial": row.get("cn_price_initial"),
                    "final": row.get("cn_price_final"),
                    "discount_percent": row.get("cn_discount_percent", 0),
                    "final_formatted": row.get("cn_price"),
                    "has_price": row.get("cn_price_final") is not None or bool(row.get("is_free")),
                }
                for row in rows
            ],
            evaluated_at,
        )
    return len(prepared)


def seed_niche_pool_from_local(limit=NICHE_POOL_BATCH_LIMIT, scan_limit=None):
    """Promote already-collected server data into the independent pool."""
    with database_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT g.appid, g.name, g.header_image, g.is_free,
                   COALESCE((SELECT player_count FROM player_snapshots p
                             WHERE p.appid = g.appid ORDER BY p.fetched_at DESC LIMIT 1), 0) AS current_players,
                   COALESCE((SELECT MAX(player_count) FROM player_snapshots p WHERE p.appid = g.appid), 0) AS peak_players,
                   (SELECT review_score FROM review_snapshots r
                    WHERE r.appid = g.appid ORDER BY r.fetched_at DESC LIMIT 1) AS review_score,
                   (SELECT total_reviews FROM review_snapshots r
                    WHERE r.appid = g.appid ORDER BY r.fetched_at DESC LIMIT 1) AS total_reviews,
                   (SELECT final_formatted FROM price_snapshots p
                    WHERE p.appid = g.appid AND p.region = 'CN'
                    ORDER BY p.fetched_at DESC LIMIT 1) AS cn_price,
                   (SELECT final FROM price_snapshots p
                    WHERE p.appid = g.appid AND p.region = 'CN'
                    ORDER BY p.fetched_at DESC LIMIT 1) AS cn_price_final,
                   (SELECT currency FROM price_snapshots p
                    WHERE p.appid = g.appid AND p.region = 'CN'
                    ORDER BY p.fetched_at DESC LIMIT 1) AS cn_price_currency,
                   (SELECT discount_percent FROM price_snapshots p
                    WHERE p.appid = g.appid AND p.region = 'CN'
                    ORDER BY p.fetched_at DESC LIMIT 1) AS cn_discount_percent,
                   g.release_date
            FROM games g
            LEFT JOIN steam_catalog c ON c.appid = g.appid
            WHERE g.header_image IS NOT NULL
              AND g.name IS NOT NULL
              AND g.name != ?
              AND COALESCE(c.app_type, 'game') = 'game'
            ORDER BY g.updated_at DESC
            LIMIT ?
            """,
            (UNKNOWN_GAME_NAME, scan_limit or max(limit * 4, 1000)),
        ).fetchall()
    return upsert_niche_pool_rows([dict(row) for row in rows])


def list_niche_pool_pick():
    with database_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT n.* FROM niche_pool n
            LEFT JOIN steam_catalog c ON c.appid=n.appid
            WHERE n.eligible=1 AND n.cn_price IS NOT NULL
              AND n.total_reviews BETWEEN 1 AND ?
              AND COALESCE(c.app_type, 'game')='game'
            ORDER BY n.weighted_score DESC LIMIT 60
            """,
            (NICHE_MAX_REVIEWS,),
        ).fetchall()
        today = daily_refresh_key()
        recent_appids = {
            int(row[0])
            for row in conn.execute(
                """
                SELECT appid FROM niche_recommendation_snapshots
                WHERE recommendation_date < ?
                ORDER BY recommendation_date DESC
                LIMIT ?
                """,
                (today, HOME_RECOMMENDATION_REPEAT_DAYS),
            ).fetchall()
        }
        rows = [
            row for row in rows
            if int(row["appid"]) not in recent_appids
            and not is_obvious_non_game_name(row["name"])
        ][:30]
        if not rows:
            return None
        # Keep the daily result server-side. A weighted draw from the top 30
        # avoids showing the same top-ranked game every day.
        state_key = "niche_pick_" + today
        saved = get_crawl_state(conn, state_key)
        chosen = None
        if saved:
            chosen = next((row for row in rows if str(row["appid"]) == str(saved)), None)
        if chosen is None:
            weights = [max(0.01, float(row["weighted_score"] or 0)) for row in rows]
            chosen = random.SystemRandom().choices(rows, weights=weights, k=1)[0]
            set_crawl_state(conn, state_key, str(chosen["appid"]))
        return chosen


def sync_steam_catalog_once(force=False):
    from .catalog import sync_steam_catalog_once as implementation

    return implementation(force)


def fetch_store_catalog_page(last_appid=0, max_results=500):
    from .steam_client import fetch_store_catalog_page as implementation

    return implementation(last_appid, max_results)


def catalog_enrich_quota():
    from .catalog import catalog_enrich_quota as implementation

    return implementation()


def run_catalog_enrich_task():
    from .catalog import run_catalog_enrich_task as implementation

    return implementation()


def snapshot_daily_niche_recommendation():
    today = daily_refresh_key()
    with database_connection() as conn:
        current = conn.execute(
            "SELECT appid FROM niche_recommendation_snapshots WHERE recommendation_date = ?",
            (today,),
        ).fetchone()
        if current:
            repeated = conn.execute(
                """
                SELECT 1 FROM (
                    SELECT appid FROM niche_recommendation_snapshots
                    WHERE recommendation_date < ?
                    ORDER BY recommendation_date DESC
                    LIMIT ?
                ) recent
                WHERE appid = ?
                """,
                (today, HOME_RECOMMENDATION_REPEAT_DAYS, int(current[0])),
            ).fetchone()
            if not repeated:
                return False
            conn.execute(
                "DELETE FROM niche_recommendation_snapshots WHERE recommendation_date = ?",
                (today,),
            )
            conn.execute("DELETE FROM crawl_state WHERE key = ?", ("niche_pick_" + today,))
    chosen = list_niche_pool_pick()
    if not chosen:
        return False
    with database_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO niche_recommendation_snapshots
            (recommendation_date, appid, name, current_players, review_score, total_reviews, weighted_score, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (today, chosen["appid"], chosen["name"], chosen["current_players"], chosen["review_score"], chosen["total_reviews"], chosen["weighted_score"], now_iso()),
        )
    log_event(f"daily niche recommendation snapshotted date={today} appid={chosen['appid']}")
    return True


def get_daily_niche_recommendation():
    today = daily_refresh_key()
    snapshot_daily_niche_recommendation()
    with database_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT n.*, g.tracked, COALESCE(c.app_type, 'game') AS app_type
            FROM niche_recommendation_snapshots n
            LEFT JOIN games g ON g.appid=n.appid
            LEFT JOIN steam_catalog c ON c.appid=n.appid
            WHERE recommendation_date=?
            """,
            (today,),
        ).fetchone()
        if not row:
            return None
        pool = conn.execute("SELECT * FROM niche_pool WHERE appid=?", (row["appid"],)).fetchone()
    if (not pool or not pool["eligible"] or not pool["cn_price"]
            or not 0 < int(pool["total_reviews"] or 0) <= NICHE_MAX_REVIEWS
            or row["app_type"] != "game" or is_obvious_non_game_name(row["name"])):
        with database_connection() as conn:
            conn.execute("DELETE FROM niche_recommendation_snapshots WHERE recommendation_date = ?", (today,))
        snapshot_daily_niche_recommendation()
        return None
    item = dict(pool)
    item["tracked"] = bool(row["tracked"])
    item["cn_historical_low_cny"] = None
    return clean_home_pick(item)


def refresh_niche_pool_scores_from_snapshots():
    """Apply the latest 30-minute player snapshots without refetching metadata."""
    with database_connection() as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            UPDATE niche_pool
            SET release_date = (
                SELECT g.release_date FROM games g WHERE g.appid = niche_pool.appid
            )
            WHERE release_date IS NULL
            """
        )
        rows = conn.execute(
            """
            SELECT n.appid, n.name, n.review_score, n.total_reviews, n.current_players,
                   n.peak_players, n.release_date, COALESCE(c.app_type, 'game') AS app_type
            FROM niche_pool n
            LEFT JOIN steam_catalog c ON c.appid=n.appid
            WHERE c.appid IS NULL OR c.app_type='game'
            """
        ).fetchall()
        updates = []
        for row in rows:
            latest = conn.execute(
                "SELECT player_count FROM player_snapshots WHERE appid = ? ORDER BY fetched_at DESC LIMIT 1",
                (row["appid"],),
            ).fetchone()
            players = int(latest[0]) if latest else int(row["current_players"] or 0)
            peak_players = max(players, int(row["peak_players"] or 0))
            eligible = int(
                row["app_type"] == "game"
                and not is_obvious_non_game_name(row["name"])
                and players >= 10
                and 0 < peak_players <= 2000
                and float(row["review_score"] or 0) >= 85
                and 0 < int(row["total_reviews"] or 0) <= NICHE_MAX_REVIEWS
                and is_recent_release(row["release_date"])
            )
            score = niche_weighted_score({
                "current_players": players,
                "peak_players": peak_players,
                "review_score": row["review_score"],
                "total_reviews": row["total_reviews"],
                "release_date": row["release_date"],
            }) if eligible else 0.0
            updates.append((players, peak_players, score, eligible, now_iso(), row["appid"]))
        conn.executemany(
            "UPDATE niche_pool SET current_players = ?, peak_players = ?, weighted_score = ?, eligible = ?, evaluated_at = ? WHERE appid = ?",
            updates,
        )
        conn.execute("DELETE FROM niche_pool WHERE eligible = 0")
    return len(updates)


def get_hot_appids(limit=HOTLIST_TARGET):
    with database_connection() as conn:
        rows = conn.execute(
            "SELECT appid FROM hot_games ORDER BY COALESCE(current_players, 0) DESC, COALESCE(rank, 999999) LIMIT ?",
            (limit,),
        ).fetchall()
    return [row[0] for row in rows]


def get_due_hot_player_appids():
    """Refresh top games more often without turning lower ranks into a request storm."""
    with database_connection() as conn:
        rows = conn.execute(
            """
            SELECT h.appid, COALESCE(h.rank, 999999),
                   (SELECT MAX(p.fetched_at) FROM player_snapshots p WHERE p.appid = h.appid)
            FROM hot_games h
            ORDER BY COALESCE(h.rank, 999999)
            LIMIT ?
            """,
            (HOTLIST_TARGET,),
        ).fetchall()
    due = []
    for appid, rank, fetched_at in rows:
        interval = 15 if rank <= 10 else 30 if rank <= 50 else 60 if rank <= 100 else 240
        if is_due(fetched_at, interval):
            due.append(int(appid))
    return due


def get_hot_price_due_appids(limit=HOT_PREVIEW_BATCH_LIMIT):
    with database_connection() as conn:
        rows = conn.execute(
            """
            SELECT h.appid,
                   h.rank,
                   COALESCE(p.fetched_at, s.price_updated_at) AS price_fetched_at
            FROM hot_games h
            LEFT JOIN (
                SELECT appid, MAX(fetched_at) AS fetched_at
                FROM price_snapshots
                WHERE region = 'CN' AND source = 'steam'
                GROUP BY appid
            ) p ON p.appid = h.appid
            LEFT JOIN game_latest_state s ON s.appid = h.appid
            WHERE COALESCE(h.rank, 999999) <= ?
            ORDER BY COALESCE(h.rank, 999999), h.current_players DESC
            """,
            (HOT_PREVIEW_TOP_LIMIT,),
        ).fetchall()
    due = []
    for appid, rank, price_fetched_at in rows:
        if is_due(price_fetched_at, PRICE_REFRESH_HOURS * 60):
            due.append(appid)
        if len(due) >= limit:
            break
    return due


def get_hot_review_due_appids(limit=HOT_PREVIEW_BATCH_LIMIT):
    with database_connection() as conn:
        rows = conn.execute(
            """
            SELECT h.appid,
                   h.rank,
                   COALESCE(r.fetched_at, s.review_updated_at) AS review_fetched_at
            FROM hot_games h
            LEFT JOIN (
                SELECT appid, MAX(fetched_at) AS fetched_at
                FROM review_snapshots
                GROUP BY appid
            ) r ON r.appid = h.appid
            LEFT JOIN game_latest_state s ON s.appid = h.appid
            WHERE COALESCE(h.rank, 999999) <= ?
            ORDER BY COALESCE(h.rank, 999999), h.current_players DESC
            """,
            (HOT_PREVIEW_TOP_LIMIT,),
        ).fetchall()
    due = []
    for appid, rank, review_fetched_at in rows:
        if is_due(review_fetched_at, PRICE_REFRESH_HOURS * 60):
            due.append(appid)
        if len(due) >= limit:
            break
    return due


def get_hot_static_due_appids(limit=HOT_PREVIEW_BATCH_LIMIT):
    with database_connection() as conn:
        rows = conn.execute(
            """
            SELECT h.appid,
                   h.rank,
                   g.release_date
            FROM hot_games h
            LEFT JOIN games g ON g.appid = h.appid
            WHERE COALESCE(h.rank, 999999) <= ?
              AND g.release_date IS NULL
            ORDER BY COALESCE(h.rank, 999999), h.current_players DESC
            LIMIT ?
            """,
            (HOT_PREVIEW_TOP_LIMIT, limit),
        ).fetchall()
    return [int(row[0]) for row in rows]


def get_hot_preview_due_appids(limit=HOT_PREVIEW_BATCH_LIMIT):
    """One AppDetails request supplies price, free/discount and release fields."""
    appids = []
    for appid in get_hot_price_due_appids(limit) + get_hot_static_due_appids(limit):
        if appid not in appids:
            appids.append(appid)
        if len(appids) >= limit:
            break
    return appids


def get_hot_full_metadata_due_appids(limit=HOT_METADATA_BATCH_LIMIT):
    with database_connection() as conn:
        rows = conn.execute(
            """
            SELECT h.appid,
                   h.rank,
                   s.metadata_updated_at
            FROM hot_games h
            LEFT JOIN games g ON g.appid = h.appid
            LEFT JOIN game_latest_state s ON s.appid = h.appid
            WHERE COALESCE(h.rank, 999999) <= ?
            ORDER BY COALESCE(h.rank, 999999), h.current_players DESC
            """,
            (HOT_FULL_METADATA_TOP_LIMIT,),
        ).fetchall()
    due = []
    for appid, rank, metadata_updated_at in rows:
        # A valid AppDetails payload can omit individual descriptive fields.
        # Record the fetch time instead of repeatedly fetching the same app.
        if is_due(metadata_updated_at, 7 * 24 * 60):
            due.append(appid)
        if len(due) >= limit:
            break
    return due


def enqueue_hot_work():
    top_appids = get_hot_appids(HOTLIST_TARGET)
    with database_connection() as conn:
        generation = int(get_crawl_state(conn, "hotlist_generation") or 0)
    enqueue_crawl_tasks(top_appids, "players", 20, generation=generation)
    enqueue_crawl_tasks(get_hot_preview_due_appids(HOT_PREVIEW_TOP_LIMIT), "preview", 50, generation=generation)
    enqueue_crawl_tasks(get_hot_review_due_appids(HOT_PREVIEW_TOP_LIMIT), "reviews", 50, generation=generation)
    enqueue_crawl_tasks(get_hot_full_metadata_due_appids(HOT_FULL_METADATA_TOP_LIMIT), "metadata", 80, generation=generation)
    enqueue_crawl_tasks(get_missing_historylow_appids(ITAD_HISTORYLOW_BATCH_LIMIT), "historylow", 10, generation=generation)


def run_hotlist_task(force=False):
    from .crawler import run_hotlist_task as implementation

    return implementation(force)


def run_players_task(force=False):
    from .crawler import run_players_task as implementation

    return implementation(force)


def run_price_task():
    from .crawler import run_price_task as implementation

    return implementation()


def run_preview_task():
    from .crawler import run_preview_task as implementation

    return implementation()


def run_review_task():
    from .crawler import run_review_task as implementation

    return implementation()


def run_static_task():
    from .crawler import run_static_task as implementation

    return implementation()


def run_metadata_task():
    from .crawler import run_metadata_task as implementation

    return implementation()


def run_historylow_task():
    from .crawler import run_historylow_task as implementation

    return implementation()


def run_niche_pool_task(force=False):
    if service_cooldown_remaining_seconds("steam_store") or service_cooldown_remaining_seconds("steam_api"):
        return False
    if not NICHE_POOL_LOCK.acquire(blocking=False):
        return False
    try:
        refresh_niche_pool_scores_from_snapshots()
        pool_count = count_eligible_niche_pool()
        refresh_minutes = (
            min(NICHE_POOL_REFRESH_MINUTES, NICHE_POOL_BOOTSTRAP_REFRESH_MINUTES)
            if pool_count < NICHE_POOL_DISPLAY_LIMIT
            else NICHE_POOL_REFRESH_MINUTES
        )
        with database_connection() as conn:
            refreshed_at = get_crawl_state(conn, "niche_pool_at")
        if not force and not is_due(refreshed_at, refresh_minutes):
            return False
        local_count = seed_niche_pool_from_local(
            NICHE_POOL_BATCH_LIMIT,
            scan_limit=5000 if force and pool_count < NICHE_POOL_DISPLAY_LIMIT else None,
        )
        batches = 2 if force and count_eligible_niche_pool() < NICHE_POOL_DISPLAY_LIMIT else 1
        saved, attempted = 0, 0
        for _ in range(batches):
            appids = discover_niche_appids(NICHE_POOL_BATCH_LIMIT)
            if not appids:
                break
            attempted += len(appids)
            rows = asyncio.run(fetch_niche_candidates_async(appids))
            game_rows = [row for row in rows if row.get("catalog_result") == "game"]
            saved += upsert_niche_pool_rows(game_rows, persist_prices=True)
            with database_connection() as conn:
                for result in rows:
                    appid = int(result["appid"])
                    outcome = result.get("catalog_result")
                    if outcome == "game":
                        conn.execute(
                            "UPDATE steam_catalog SET app_type='game', app_type_checked_at=?, enrich_status='done', last_enriched_at=?, last_error=NULL WHERE appid=?",
                            (now_iso(), now_iso(), appid),
                        )
                    elif outcome == "excluded":
                        conn.execute(
                            "UPDATE steam_catalog SET app_type=?, app_type_checked_at=?, enrich_status='excluded', last_enriched_at=?, next_enrich_at=NULL, last_error=NULL WHERE appid=?",
                            (result.get("app_type") or "other", now_iso(), now_iso(), appid),
                        )
                        conn.execute("DELETE FROM niche_pool WHERE appid=?", (appid,))
                    elif outcome == "not_available":
                        conn.execute(
                            "UPDATE steam_catalog SET app_type_checked_at=?, enrich_status='not_available', last_enriched_at=?, next_enrich_at=NULL, last_error=? WHERE appid=?",
                            (now_iso(), now_iso(), result.get("error") or "Steam AppDetails unavailable", appid),
                        )
            fetched_ids = {int(row["appid"]) for row in rows}
            unavailable_ids = [appid for appid in appids if appid not in fetched_ids]
            if unavailable_ids:
                # Catalog candidates that fail validity/player checks should
                # not be selected again by the next bootstrap batch.
                next_week = (datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=0).isoformat()
                with database_connection() as conn:
                    conn.executemany(
                        """
                        UPDATE steam_catalog
                        SET enrich_status = 'skipped', last_enriched_at = ?, next_enrich_at = ?,
                            last_error = 'not available for niche pool'
                        WHERE appid = ?
                        """,
                        [(now_iso(), next_week, appid) for appid in unavailable_ids],
                    )
            if count_eligible_niche_pool() >= NICHE_POOL_DISPLAY_LIMIT:
                break
        if not attempted:
            log_event(f"niche pool used local cache rows={local_count}")
            return bool(local_count)
        stamp = now_iso()
        with database_connection() as conn:
            set_crawl_state(conn, "niche_pool_at", stamp)
        log_event(
            f"niche pool refreshed local={local_count} candidates={attempted} saved={saved} "
            f"pool={count_eligible_niche_pool()}/{NICHE_POOL_DISPLAY_LIMIT} next={refresh_minutes}m"
        )
        return bool(local_count or saved)
    except sqlite3.Error as exc:
        log_event(f"niche pool database error: {exc}")
        raise
    except SteamRateLimited as exc:
        log_event(f"niche pool rate limited: {exc}")
        return False
    except Exception as exc:
        log_event(f"niche pool refresh failed: {exc}")
        return False
    finally:
        NICHE_POOL_LOCK.release()


def count_eligible_niche_pool():
    with database_connection() as conn:
        rows = conn.execute(
            """
            SELECT n.name
            FROM niche_pool n
            LEFT JOIN steam_catalog c ON c.appid=n.appid
            WHERE n.eligible=1
              AND n.total_reviews BETWEEN 1 AND ?
              AND COALESCE(c.app_type, 'game')='game'
            """,
            (NICHE_MAX_REVIEWS,),
        ).fetchall()
    return sum(1 for row in rows if not is_obvious_non_game_name(row[0]))


def compact_player_snapshots_once():
    with database_connection() as conn:
        compacted_at = get_crawl_state(conn, "player_snapshot_compacted_at")
        if not is_due(compacted_at, 24 * 60):
            return False
        cutoff_daily = (datetime.now(timezone.utc) - timedelta(days=7)).replace(microsecond=0).isoformat()
        cutoff_monthly = (datetime.now(timezone.utc) - timedelta(days=365)).replace(microsecond=0).isoformat()
        cutoff_delete = (datetime.now(timezone.utc) - timedelta(days=730)).replace(microsecond=0).isoformat()
        conn.execute("DELETE FROM player_snapshots WHERE fetched_at < ?", (cutoff_delete,))
        conn.execute(
            """
            DELETE FROM player_snapshots
            WHERE fetched_at < ?
              AND id NOT IN (
                  SELECT MIN(id)
                  FROM player_snapshots
                  WHERE fetched_at < ?
                  GROUP BY appid, substr(fetched_at, 1, 10)
              )
            """,
            (cutoff_daily, cutoff_daily),
        )
        conn.execute(
            """
            DELETE FROM player_snapshots
            WHERE fetched_at < ?
              AND id NOT IN (
                  SELECT MIN(id)
                  FROM player_snapshots
                  WHERE fetched_at < ?
                  GROUP BY appid, substr(fetched_at, 1, 7)
              )
            """,
            (cutoff_monthly, cutoff_monthly),
        )
        set_crawl_state(conn, "player_snapshot_compacted_at", now_iso())
    log_event("player snapshots compacted")
    return True


def rotate_logs_once():
    """Rotate the active log daily and retain only the configured window."""
    today = datetime.now().strftime("%Y-%m-%d")
    with database_connection() as conn:
        if get_crawl_state(conn, "log_rotation_date") == today:
            return False
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_LOCK:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 0:
            archive = LOG_PATH.with_name(f"{LOG_PATH.name}.{today}")
            if archive.exists():
                archive = LOG_PATH.with_name(f"{LOG_PATH.name}.{today}.{int(time.time())}")
            LOG_PATH.replace(archive)
        for archive in LOG_PATH.parent.glob(f"{LOG_PATH.name}.*"):
            try:
                age_days = (time.time() - archive.stat().st_mtime) / 86400
                if age_days > LOG_RETENTION_DAYS:
                    archive.unlink()
            except OSError:
                continue
    with database_connection() as conn:
        set_crawl_state(conn, "log_rotation_date", today)
    return True


def compact_price_snapshots_once():
    """Keep recent prices precise, then reduce old history to daily/monthly points."""
    with database_connection() as conn:
        compacted_at = get_crawl_state(conn, "price_snapshot_compacted_at")
        if not is_due(compacted_at, 24 * 60):
            return False
        now = datetime.now(timezone.utc)
        cutoff_daily = (now - timedelta(days=30)).replace(microsecond=0).isoformat()
        cutoff_monthly = (now - timedelta(days=365)).replace(microsecond=0).isoformat()
        cutoff_delete = (now - timedelta(days=PRICE_RETENTION_DAYS)).replace(microsecond=0).isoformat()
        conn.execute("DELETE FROM price_snapshots WHERE fetched_at < ?", (cutoff_delete,))
        conn.execute(
            """
            DELETE FROM price_snapshots
            WHERE fetched_at < ?
              AND fetched_at >= ?
              AND id NOT IN (
                  SELECT MIN(id) FROM price_snapshots
                  WHERE fetched_at < ? AND fetched_at >= ?
                  GROUP BY appid, region, source, substr(fetched_at, 1, 10)
              )
            """,
            (cutoff_daily, cutoff_monthly, cutoff_daily, cutoff_monthly),
        )
        conn.execute(
            """
            DELETE FROM price_snapshots
            WHERE fetched_at < ?
              AND id NOT IN (
                  SELECT MIN(id) FROM price_snapshots
                  WHERE fetched_at < ?
                  GROUP BY appid, region, source, substr(fetched_at, 1, 7)
              )
            """,
            (cutoff_monthly, cutoff_monthly),
        )
        set_crawl_state(conn, "price_snapshot_compacted_at", now_iso())
    log_event("price snapshots compacted")
    return True


def cleanup_old_records_once():
    """Remove old terminal tasks and recommendation snapshots."""
    with database_connection() as conn:
        cleanup_at = get_crawl_state(conn, "old_records_cleaned_at")
        if not is_due(cleanup_at, 24 * 60):
            return False
        task_cutoff = (datetime.now(timezone.utc) - timedelta(days=CRAWL_TASK_RETENTION_DAYS)).replace(microsecond=0).isoformat()
        recommendation_cutoff = (datetime.now(timezone.utc) - timedelta(days=RECOMMENDATION_RETENTION_DAYS)).strftime("%Y-%m-%d")
        conn.execute(
            """
            DELETE FROM crawl_tasks
            WHERE status IN ('done', 'failed', 'permanent_failed', 'not_available', 'skipped')
              AND COALESCE(completed_at, updated_at) < ?
            """,
            (task_cutoff,),
        )
        conn.execute(
            "DELETE FROM niche_recommendation_snapshots WHERE recommendation_date < ?",
            (recommendation_cutoff,),
        )
        conn.execute(
            "DELETE FROM daily_home_snapshots WHERE recommendation_date < ?",
            (recommendation_cutoff,),
        )
        set_crawl_state(conn, "old_records_cleaned_at", now_iso())
    log_event("old crawl tasks and recommendation snapshots cleaned")
    return True


def maintain_storage_once():
    rotate_logs_once()
    compact_price_snapshots_once()
    cleanup_old_records_once()
    cleanup_image_cache_once()


def refresh_hot_database_once(force_hotlist=False, quick=False):
    from .crawler import refresh_hot_database_once as implementation

    return implementation(force_hotlist, quick)


def count_hot_games():
    with database_connection() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM hot_games").fetchone()[0] or 0)


def hot_games_version():
    with database_connection() as conn:
        row = conn.execute(
            """
            SELECT MAX(COALESCE(s.updated_at, h.fetched_at, ''))
            FROM hot_games h
            LEFT JOIN game_latest_state s ON s.appid = h.appid
            """
        ).fetchone()
    return row[0] or ""


def refresh_hot_database_async(force_hotlist=False, quick=False):
    from .crawler import refresh_hot_database_async as implementation

    return implementation(force_hotlist, quick)


def repair_placeholder_names(conn):
    rows = conn.execute("SELECT appid, name, short_description FROM games").fetchall()
    for appid, name, description in rows:
        if not is_placeholder_name(name):
            continue
        inferred_name = infer_name_from_description(description)
        if inferred_name:
            conn.execute(
                "UPDATE games SET name = ?, updated_at = ? WHERE appid = ?",
                (inferred_name, now_iso(), appid),
            )


def upsert_game(conn, appid, details=None, name=None, mark_tracked=True):
    details = details or {}
    developers = ", ".join(details.get("developers") or [])
    publishers = ", ".join(details.get("publishers") or [])
    release = details.get("release_date") or {}
    supplied_name = None if is_placeholder_name(name) else name
    inferred_name = infer_name_from_description(details.get("short_description"))
    resolved_name = details.get("name") or supplied_name or inferred_name or UNKNOWN_GAME_NAME
    conn.execute(
        """
        INSERT INTO games(appid, name, header_image, short_description, developer, publisher, release_date, is_free, screenshots_json, tracked, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(appid) DO UPDATE SET
            name=CASE
                WHEN excluded.name != ? THEN excluded.name
                ELSE games.name
            END,
            header_image=COALESCE(excluded.header_image, games.header_image),
            short_description=COALESCE(excluded.short_description, games.short_description),
            developer=COALESCE(excluded.developer, games.developer),
            publisher=COALESCE(excluded.publisher, games.publisher),
            release_date=COALESCE(excluded.release_date, games.release_date),
            is_free=excluded.is_free,
            screenshots_json=COALESCE(excluded.screenshots_json, games.screenshots_json),
            tracked=CASE
                WHEN ? THEN 1
                ELSE games.tracked
            END,
            updated_at=excluded.updated_at
        """,
        (
            int(appid),
            resolved_name,
            details.get("header_image"),
            details.get("short_description"),
            developers,
            publishers,
            release.get("date") if isinstance(release, dict) else None,
            1 if details.get("is_free") else 0,
            None,
            1 if mark_tracked else 0,
            now_iso(),
            UNKNOWN_GAME_NAME,
            1 if mark_tracked else 0,
        ),
    )


def quick_track_game(appid, name=None, header_image=None):
    resolved_name = clean_name(name)
    with database_connection() as conn:
        conn.execute(
            """
            INSERT INTO games(appid, name, header_image, tracked, updated_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(appid) DO UPDATE SET
                name=CASE
                    WHEN excluded.name != ? THEN excluded.name
                    ELSE games.name
                END,
                header_image=COALESCE(excluded.header_image, games.header_image),
                tracked=1,
                updated_at=excluded.updated_at
            """,
            (int(appid), resolved_name, header_image, now_iso(), UNKNOWN_GAME_NAME),
        )


def untrack_game(appid):
    with database_connection() as conn:
        conn.execute(
            "UPDATE games SET tracked = 0, updated_at = ? WHERE appid = ?",
            (now_iso(), int(appid)),
        )


def remember_search_games(items):
    rows = [
        (int(item["appid"]), clean_name(item.get("name")), item.get("tiny_image") or item.get("header_image"), now_iso())
        for item in items
        if item.get("appid")
    ]
    if not rows:
        return
    with database_connection() as conn:
        conn.executemany(
            """
            INSERT INTO games(appid, name, header_image, tracked, updated_at)
            VALUES (?, ?, ?, 0, ?)
            ON CONFLICT(appid) DO UPDATE SET
                name=CASE
                    WHEN excluded.name != ? THEN excluded.name
                    ELSE games.name
                END,
                header_image=COALESCE(excluded.header_image, games.header_image),
                updated_at=excluded.updated_at
            """,
            [(appid, name, image, stamp, UNKNOWN_GAME_NAME) for appid, name, image, stamp in rows],
        )


def fetch_appdetails(appid, region="US"):
    from .steam_client import fetch_appdetails as implementation

    return implementation(appid, region)


def record_catalog_app_type(appid, app_type, stamp=None):
    app_type = str(app_type or "unknown").strip().lower()
    if app_type == "unknown":
        return
    stamp = stamp or now_iso()
    with database_connection() as conn:
        conn.execute(
            """
            UPDATE steam_catalog
            SET app_type=?, app_type_checked_at=?,
                enrich_status=CASE WHEN ?='game' THEN enrich_status ELSE 'excluded' END,
                next_enrich_at=CASE WHEN ?='game' THEN next_enrich_at ELSE NULL END,
                last_error=CASE WHEN ?='game' THEN last_error ELSE NULL END
            WHERE appid=?
            """,
            (app_type, stamp, app_type, app_type, app_type, int(appid)),
        )
        if app_type != "game":
            conn.execute("DELETE FROM niche_pool WHERE appid=?", (int(appid),))


def fetch_players(appid):
    from .steam_client import fetch_players as implementation

    return implementation(appid)


def fetch_reviews(appid):
    from .steam_client import fetch_reviews as implementation

    return implementation(appid)


def fetch_itad_prices(appid):
    from .steam_client import fetch_itad_prices as implementation

    return implementation(appid)


def refresh_itad_history_lows(appids):
    from .crawler import refresh_itad_history_lows as implementation

    return implementation(appids)


def get_missing_historylow_appids(limit=ITAD_HISTORYLOW_BATCH_LIMIT):
    from .crawler import get_missing_historylow_appids as implementation

    return implementation(limit)


def refresh_missing_history_lows_once():
    from .crawler import refresh_missing_history_lows_once as implementation

    return implementation()


def backfill_historylow_async(appid):
    from .crawler import backfill_historylow_async as implementation

    return implementation(appid)


def backfill_preview_async(appid, name=None):
    appid = int(appid)
    if service_cooldown_remaining_seconds("steam_store"):
        return False
    with database_connection() as conn:
        if not is_due(get_crawl_state(conn, preview_attempt_key(appid)), 30):
            return False
        set_crawl_state(conn, preview_attempt_key(appid), now_iso())
    with PREVIEW_BACKFILL_LOCK:
        if appid in PREVIEW_BACKFILLING:
            return False
        PREVIEW_BACKFILLING.add(appid)
    with STATUS_LOCK:
        REFRESH_STATUS["detail_running"] = True

    def worker():
        try:
            result = refresh_game(
                appid,
                name,
                include_prices=True,
                include_players=True,
                include_reviews=True,
                include_details=True,
                mark_tracked=False,
                price_regions=["US", "CN"],
            )
        except Exception as exc:
            log_event(f"preview backfill failed appid={appid}: {exc}")
        finally:
            with PREVIEW_BACKFILL_LOCK:
                PREVIEW_BACKFILLING.discard(appid)
                preview_running = bool(PREVIEW_BACKFILLING)
            with DETAIL_BACKFILL_LOCK:
                detail_running = bool(DETAIL_BACKFILLING)
            with STATUS_LOCK:
                REFRESH_STATUS["detail_running"] = preview_running or detail_running

    threading.Thread(target=worker, daemon=True).start()
    return True


def refresh_game(
    appid,
    name=None,
    include_prices=True,
    include_players=True,
    include_reviews=True,
    include_details=False,
    mark_tracked=True,
    price_regions=None,
):
    with REFRESH_LOCK:
        appid = int(appid)
        stamp = now_iso()
        errors = []
        details = None
        price_rows = []
        players = None
        reviews = None
        itad_rows = []
        store_deferred = bool(
            (include_details or include_prices or include_reviews)
            and service_cooldown_remaining_seconds("steam_store")
        )

        if (include_details or include_prices or include_reviews) and not store_deferred:
            try:
                details = fetch_appdetails(appid, "US")
            except SteamRateLimited as exc:
                store_deferred = True
                log_event(f"steam store work deferred appid={appid}: {exc}")
                errors.append(f"steam store deferred: {exc}")
            except Exception as exc:
                log_event(f"appdetails failed appid={appid} region=US: {exc}")
                errors.append(f"details: {exc}")

        if details and details.get("type"):
            app_type = str(details.get("type")).strip().lower()
            record_catalog_app_type(appid, app_type, stamp)
            if app_type != "game":
                return {"appid": appid, "fetched_at": stamp, "errors": [f"excluded app type: {app_type}"]}

        if include_prices and not store_deferred:
            for region in (price_regions or TRACKED_REGIONS):
                try:
                    if region != "US" or not details:
                        polite_store_delay()
                    region_details = details if region == "US" and details else fetch_appdetails(appid, region)
                    price = (region_details or {}).get("price_overview")
                    is_free = bool((region_details or {}).get("is_free"))
                    if price or is_free:
                        price_rows.append(
                            (
                                appid,
                                region,
                                (price or {}).get("currency"),
                                (price or {}).get("initial", 0),
                                (price or {}).get("final", 0),
                                (price or {}).get("discount_percent", 0),
                                (price or {}).get("final_formatted", "Free"),
                                stamp,
                            )
                        )
                except SteamRateLimited as exc:
                    store_deferred = True
                    log_event(f"steam store work deferred appid={appid} region={region}: {exc}")
                    errors.append(f"steam store deferred: {exc}")
                    break
                except Exception as exc:
                    log_event(f"price skipped appid={appid} region={region}: {exc}")
                    errors.append(f"price {region}: {exc}")

        if include_players:
            try:
                players = fetch_players(appid)
            except Exception as exc:
                log_event(f"players skipped appid={appid}: {exc}")

        if include_reviews and not store_deferred:
            try:
                reviews = fetch_reviews(appid)
            except SteamRateLimited as exc:
                store_deferred = True
                log_event(f"steam store work deferred appid={appid} reviews: {exc}")
                errors.append(f"steam store deferred: {exc}")
            except Exception as exc:
                log_event(f"reviews failed appid={appid}: {exc}")
                errors.append(f"reviews: {exc}")

        if include_prices and not store_deferred:
            try:
                polite_store_delay()
                itad_rows = fetch_itad_prices(appid)
            except ExternalDataUnavailable as exc:
                log_event(f"itad skipped appid={appid}: {exc}")
            except Exception as exc:
                log_event(f"itad failed appid={appid}: {exc}")
                errors.append(f"itad: {exc}")
            try:
                refresh_itad_history_lows([appid])
            except Exception as exc:
                log_event(f"itad historylow failed appid={appid}: {exc}")
                errors.append(f"itad historylow: {exc}")

        with database_connection() as conn:
            upsert_game(conn, appid, details, name, mark_tracked=mark_tracked)

            conn.executemany(
                """
                INSERT INTO price_snapshots(appid, region, currency, initial, final, discount_percent, final_formatted, source, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'steam', ?)
                """,
                price_rows,
            )

            if players is not None:
                conn.execute(
                    "INSERT INTO player_snapshots(appid, player_count, fetched_at) VALUES (?, ?, ?)",
                    (appid, players, stamp),
                )
                conn.execute(
                    "UPDATE hot_games SET current_players = ?, fetched_at = ? WHERE appid = ?",
                    (players, stamp, appid),
                )

            if reviews is not None:
                conn.execute(
                    """
                    INSERT INTO review_snapshots(appid, review_score, review_score_desc, total_positive, total_negative, total_reviews, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        appid,
                        reviews["review_score"],
                        reviews["review_score_desc"],
                        reviews["total_positive"],
                        reviews["total_negative"],
                        reviews["total_reviews"],
                        stamp,
                    ),
                )

            for row in itad_rows:
                conn.execute(
                    """
                    INSERT INTO price_snapshots(appid, region, currency, initial, final, discount_percent, final_formatted, source, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        appid,
                        row["region"],
                        row["currency"],
                        row["initial"],
                        row["final"],
                        row["discount_percent"],
                        row["final_formatted"],
                        row["source"],
                        stamp,
                    ),
                )

        return {
            "appid": appid,
            "fetched_at": stamp,
            "errors": errors,
            "store_deferred": store_deferred,
        }


def backfill_details_async(appid, name=None):
    appid = int(appid)
    with database_connection() as conn:
        if not is_due(get_crawl_state(conn, detail_attempt_key(appid)), PRICE_REFRESH_HOURS * 60):
            return
        set_crawl_state(conn, detail_attempt_key(appid), now_iso())
    with DETAIL_BACKFILL_LOCK:
        if appid in DETAIL_BACKFILLING:
            return
        DETAIL_BACKFILLING.add(appid)
    with STATUS_LOCK:
        REFRESH_STATUS["detail_running"] = True

    def worker():
        try:
            result = refresh_game(
                appid,
                name,
                include_prices=True,
                include_players=True,
                include_reviews=True,
                include_details=True,
                mark_tracked=False,
                price_regions=["US", "CN"],
            )
            if result.get("store_deferred"):
                with database_connection() as conn:
                    set_crawl_state(conn, detail_attempt_key(appid), "")
        except Exception as exc:
            log_event(f"details backfill failed appid={appid}: {exc}")
        finally:
            with DETAIL_BACKFILL_LOCK:
                DETAIL_BACKFILLING.discard(appid)
                detail_running = bool(DETAIL_BACKFILLING)
            with PREVIEW_BACKFILL_LOCK:
                preview_running = bool(PREVIEW_BACKFILLING)
            with STATUS_LOCK:
                REFRESH_STATUS["detail_running"] = detail_running or preview_running

    threading.Thread(target=worker, daemon=True).start()


def refresh_tracked_game_async(appid, name=None):
    appid = int(appid)
    with TRACK_BACKFILL_LOCK:
        if appid in TRACK_BACKFILLING:
            return False
        TRACK_BACKFILLING.add(appid)
    with STATUS_LOCK:
        REFRESH_STATUS["track_running"] = True

    def worker():
        try:
            result = refresh_game(
                appid,
                name,
                include_prices=False,
                include_players=True,
                include_reviews=True,
                include_details=True,
                mark_tracked=False,
            )
            if result.get("errors"):
                log_event(f"track background partial appid={appid}: {result['errors'][0]}")
        except Exception as exc:
            log_event(f"track background failed appid={appid}: {exc}")
        finally:
            with TRACK_BACKFILL_LOCK:
                TRACK_BACKFILLING.discard(appid)
            with STATUS_LOCK:
                REFRESH_STATUS["track_running"] = bool(TRACK_BACKFILLING)

    threading.Thread(target=worker, daemon=True).start()
    return True


def latest_snapshot_times(conn, appid):
    player_at = conn.execute(
        "SELECT MAX(fetched_at) FROM player_snapshots WHERE appid = ?",
        (appid,),
    ).fetchone()[0]
    price_at = conn.execute(
        "SELECT MAX(fetched_at) FROM price_snapshots WHERE appid = ? AND source = 'steam'",
        (appid,),
    ).fetchone()[0]
    review_at = conn.execute(
        "SELECT MAX(fetched_at) FROM review_snapshots WHERE appid = ?",
        (appid,),
    ).fetchone()[0]
    return player_at, price_at, review_at


def is_due(last_fetched_at, interval_minutes):
    current_age = age_minutes(last_fetched_at)
    return current_age is None or current_age >= interval_minutes


def refresh_tracked_once(force_all=False):
    with STATUS_LOCK:
        REFRESH_STATUS["running"] = True
        REFRESH_STATUS["last_started_at"] = now_iso()
        REFRESH_STATUS["last_errors"] = []
    all_errors = []
    try:
        with database_connection() as conn:
            rows = conn.execute(
                "SELECT appid, name, short_description, developer, publisher, updated_at FROM games WHERE tracked = 1 ORDER BY name"
            ).fetchall()
            due_rows = []
            for appid, name, short_description, developer, publisher, updated_at in rows:
                player_at, price_at, review_at = latest_snapshot_times(conn, appid)
                detail_attempt_at = get_crawl_state(conn, detail_attempt_key(appid))
                missing_details = not short_description or not developer or not publisher
                include_details = missing_details and is_due(detail_attempt_at, PRICE_REFRESH_HOURS * 60)
                include_players = force_all or is_due(player_at, PLAYER_REFRESH_MINUTES)
                include_prices = force_all or is_due(price_at, PRICE_REFRESH_HOURS * 60)
                include_reviews = force_all or is_due(review_at, PRICE_REFRESH_HOURS * 60)
                if include_details or include_players or include_prices or include_reviews:
                    due_rows.append((appid, name, include_prices, include_players, include_reviews, include_details))
        if not force_all:
            due_rows = due_rows[:TRACKED_REFRESH_BATCH_LIMIT]
        for appid, name, include_prices, include_players, include_reviews, include_details in due_rows:
            result = refresh_game(
                appid,
                name,
                include_prices=include_prices,
                include_players=include_players,
                include_reviews=include_reviews,
                include_details=include_details,
                mark_tracked=False,
            )
            for error in result["errors"]:
                all_errors.append(f"{name}: {error}")
            if include_details and not result.get("store_deferred"):
                with database_connection() as conn:
                    set_crawl_state(conn, detail_attempt_key(appid), now_iso())
            if (include_details or include_prices or include_reviews) and not result.get("store_deferred"):
                polite_store_delay()
            else:
                time.sleep(1)
        return all_errors
    finally:
        with STATUS_LOCK:
            REFRESH_STATUS["running"] = False
            REFRESH_STATUS["last_finished_at"] = now_iso()
            REFRESH_STATUS["last_errors"] = all_errors[:20]


def scheduler_loop():
    from .crawler import scheduler_loop as implementation

    return implementation()


def startup_prewarm_async():
    from .crawler import startup_prewarm_async as implementation

    return implementation()


def get_status():
    with STATUS_LOCK:
        status = dict(REFRESH_STATUS)
    status["player_refresh_minutes"] = PLAYER_REFRESH_MINUTES
    status["price_refresh_hours"] = PRICE_REFRESH_HOURS
    status["store_delay_seconds"] = {
        "min": STORE_REQUEST_DELAY_MIN_SECONDS,
        "max": STORE_REQUEST_DELAY_MAX_SECONDS,
    }
    status["itad_configured"] = bool(ITAD_API_KEY)
    status["steam_api_key_configured"] = bool(STEAM_API_KEY)
    status["historical_low_tolerance_cny"] = HISTORICAL_LOW_TOLERANCE_CNY
    status["steam_cooldown_remaining_seconds"] = steam_cooldown_remaining_seconds()
    status["direct_cooldown_remaining_seconds"] = direct_cooldown_remaining_seconds()
    status["direct_cooldown_minutes"] = DIRECT_COOLDOWN_MINUTES
    status["service_cooldowns"] = {
        service: service_cooldown_remaining_seconds(service)
        for service in EXTERNAL_SERVICES
    }
    status["direct_service_cooldowns"] = {
        service: direct_cooldown_remaining_seconds(service)
        for service in EXTERNAL_SERVICES
    }
    status["proxy"] = dict(PROXY_STATUS)
    status["proxy"]["direct_cooldown_remaining_seconds"] = status["direct_cooldown_remaining_seconds"]
    try:
        with database_connection() as conn:
            status["historical_low_count"] = conn.execute("SELECT COUNT(*) FROM historical_lows").fetchone()[0]
            status["niche_pool_count"] = conn.execute("SELECT COUNT(*) FROM niche_pool WHERE eligible = 1").fetchone()[0]
            status["steam_catalog_count"] = conn.execute("SELECT COUNT(*) FROM steam_catalog").fetchone()[0]
            status["steam_catalog_enriched_count"] = conn.execute("SELECT COUNT(*) FROM steam_catalog WHERE last_enriched_at IS NOT NULL").fetchone()[0]
            status["steam_catalog_game_count"] = conn.execute("SELECT COUNT(*) FROM steam_catalog WHERE app_type='game'").fetchone()[0]
            status["steam_catalog_excluded_count"] = conn.execute("SELECT COUNT(*) FROM steam_catalog WHERE app_type NOT IN ('unknown', 'game')").fetchone()[0]
            status["steam_catalog_unknown_count"] = conn.execute("SELECT COUNT(*) FROM steam_catalog WHERE app_type='unknown'").fetchone()[0]
            status["steam_catalog_scan_cursor"] = int(get_crawl_state(conn, "steam_catalog_scan_cursor") or 0)
            status["steam_catalog_scan_generation"] = int(get_crawl_state(conn, "steam_catalog_scan_generation") or 1)
            status["steam_catalog_scan_started_at"] = get_crawl_state(conn, "steam_catalog_scan_started_at")
            status["steam_catalog_scan_last_batch_at"] = get_crawl_state(conn, "steam_catalog_scan_last_batch_at")
            status["steam_catalog_scan_completed_at"] = get_crawl_state(conn, "steam_catalog_scan_completed_at") or None
            status["steam_catalog_scan_complete"] = bool(status["steam_catalog_scan_completed_at"])
            status["database_schema_version"] = int(conn.execute("PRAGMA user_version").fetchone()[0])
            status["crawl_task_count"] = conn.execute(
                "SELECT COUNT(*) FROM crawl_tasks WHERE status IN ('pending', 'retry', 'running')"
            ).fetchone()[0]
            status["crawl_task_counts"] = [
                {"task_type": row[0], "status": row[1], "count": row[2]}
                for row in conn.execute(
                    """
                    SELECT task_type, status, COUNT(*)
                    FROM crawl_tasks
                    GROUP BY task_type, status
                    ORDER BY task_type, status
                    """
                ).fetchall()
            ]
            hot_total = conn.execute("SELECT COUNT(*) FROM hot_games").fetchone()[0]
            status["hot_games_version"] = hot_games_version()
            status["task_progress"] = {
                "hotlist": {"done": hot_total, "total": HOTLIST_TARGET},
                "players": {"done": conn.execute("SELECT COUNT(*) FROM hot_games h JOIN game_latest_state s ON s.appid=h.appid WHERE s.players_updated_at IS NOT NULL").fetchone()[0], "total": hot_total},
                "preview": {"done": conn.execute("SELECT COUNT(*) FROM hot_games h JOIN game_latest_state s ON s.appid=h.appid WHERE COALESCE(h.rank, 999999) <= ? AND s.price_updated_at IS NOT NULL", (HOT_PREVIEW_TOP_LIMIT,)).fetchone()[0], "total": min(hot_total, HOT_PREVIEW_TOP_LIMIT)},
                "reviews": {"done": conn.execute("SELECT COUNT(*) FROM hot_games h JOIN game_latest_state s ON s.appid=h.appid WHERE COALESCE(h.rank, 999999) <= ? AND s.review_updated_at IS NOT NULL", (HOT_PREVIEW_TOP_LIMIT,)).fetchone()[0], "total": min(hot_total, HOT_PREVIEW_TOP_LIMIT)},
                "metadata": {"done": conn.execute("SELECT COUNT(*) FROM hot_games h JOIN games g ON g.appid=h.appid WHERE COALESCE(h.rank, 999999) <= ? AND g.short_description IS NOT NULL", (HOT_FULL_METADATA_TOP_LIMIT,)).fetchone()[0], "total": min(hot_total, HOT_FULL_METADATA_TOP_LIMIT)},
            }
    except sqlite3.Error:
        status["historical_low_count"] = 0
        status["niche_pool_count"] = 0
        status["steam_catalog_count"] = 0
        status["steam_catalog_enriched_count"] = 0
        status["steam_catalog_game_count"] = 0
        status["steam_catalog_excluded_count"] = 0
        status["steam_catalog_unknown_count"] = 0
        status["steam_catalog_scan_cursor"] = 0
        status["steam_catalog_scan_generation"] = 1
        status["steam_catalog_scan_started_at"] = None
        status["steam_catalog_scan_last_batch_at"] = None
        status["steam_catalog_scan_completed_at"] = None
        status["steam_catalog_scan_complete"] = False
        status["database_schema_version"] = 0
        status["crawl_task_count"] = 0
        status["crawl_task_counts"] = []
        status["hot_games_version"] = ""
        status["task_progress"] = {}
    status["version"] = APP_VERSION
    try:
        require_httpx()
        status["httpx_available"] = True
    except RuntimeError:
        status["httpx_available"] = False
    status["hotlist_target"] = HOTLIST_TARGET
    status["hotlist_concurrency"] = HOTLIST_CONCURRENCY
    status["hot_preview_top_limit"] = HOT_PREVIEW_TOP_LIMIT
    status["hot_preview_batch_limit"] = HOT_PREVIEW_BATCH_LIMIT
    status["hot_full_metadata_top_limit"] = HOT_FULL_METADATA_TOP_LIMIT
    status["hot_metadata_concurrency"] = HOT_METADATA_CONCURRENCY
    status["hot_metadata_batch_limit"] = HOT_METADATA_BATCH_LIMIT
    niche_count = int(status.get("niche_pool_count") or 0)
    status["niche_pool_refresh_minutes"] = (
        min(NICHE_POOL_REFRESH_MINUTES, NICHE_POOL_BOOTSTRAP_REFRESH_MINUTES)
        if niche_count < NICHE_POOL_DISPLAY_LIMIT
        else NICHE_POOL_REFRESH_MINUTES
    )
    status["niche_pool_display_limit"] = NICHE_POOL_DISPLAY_LIMIT
    status["niche_pool_batch_limit"] = NICHE_POOL_BATCH_LIMIT
    status["steam_catalog_limit"] = STEAM_CATALOG_LIMIT
    status["catalog_scan_batch_limit"] = CATALOG_SCAN_BATCH_LIMIT
    status["catalog_rescan_days"] = CATALOG_RESCAN_DAYS
    status["catalog_enrich_daily_limit"] = CATALOG_ENRICH_DAILY_LIMIT
    status["catalog_enrich_batch_limit"] = CATALOG_ENRICH_BATCH_LIMIT
    status["niche_pool_limit"] = NICHE_POOL_LIMIT
    status["niche_max_reviews"] = NICHE_MAX_REVIEWS
    status["home_repeat_days"] = HOME_RECOMMENDATION_REPEAT_DAYS
    status["home_popular_min_reviews"] = HOME_POPULAR_MIN_REVIEWS
    status["home_popular_min_players"] = HOME_POPULAR_MIN_PLAYERS
    status["search"] = get_search_metrics()
    status["tracked_refresh_batch_limit"] = TRACKED_REFRESH_BATCH_LIMIT
    status["itad_historylow_batch_limit"] = ITAD_HISTORYLOW_BATCH_LIMIT
    with TRACK_BACKFILL_LOCK:
        status["track_running"] = bool(TRACK_BACKFILLING)
    with DETAIL_BACKFILL_LOCK:
        detail_running = bool(DETAIL_BACKFILLING)
    with PREVIEW_BACKFILL_LOCK:
        preview_running = bool(PREVIEW_BACKFILLING)
    status["detail_running"] = detail_running or preview_running
    with HISTORYLOW_BACKFILL_LOCK:
        historylow_running = bool(HISTORYLOW_BACKFILLING)
    status["historylow_running"] = bool(status.get("historylow_running")) or historylow_running
    return status


def clean_game(row, summary=False):
    if not row:
        return None
    item = dict(row)
    display_name = clean_hot_name(item.get("name"))
    if not display_name:
        display_name = infer_name_from_description(item.get("short_description")) or fallback_game_name(item.get("appid"))
    if summary:
        cn_current_cny = amount_int_to_cny(item.get("cn_price_final"), item.get("cn_price_currency") or "CNY")
        cn_is_low = compare_historical_low(cn_current_cny, item.get("cn_historical_low_cny"))
        cn_discounted = bool((item.get("cn_discount_percent") or 0) > 0 and not cn_is_low)
        return {
            "appid": item.get("appid"),
            "name": display_name,
            "header_image": item.get("header_image"),
            "player_count": item.get("player_count"),
            "review_score": item.get("review_score"),
            "cn_price": item.get("cn_price"),
            "cn_price_display": item.get("cn_price") or ("免费" if item.get("is_free") else "国区暂无售价"),
            "is_free": bool(item.get("is_free")),
            "cn_price_historical_low": cn_is_low,
            "cn_price_discounted": cn_discounted,
            "cn_discount_percent": item.get("cn_discount_percent") or 0,
            "cn_historical_low_cny": item.get("cn_historical_low_cny"),
            "updated_at": item.get("updated_at"),
            "tracked": bool(item.get("tracked")),
        }
    payload = {
        "appid": item.get("appid"),
        "name": display_name,
        "header_image": item.get("header_image"),
        "short_description": item.get("short_description"),
        "developer": item.get("developer"),
        "publisher": item.get("publisher"),
        "release_date": item.get("release_date"),
        "is_free": bool(item.get("is_free")),
        "updated_at": item.get("updated_at"),
        "tracked": bool(item.get("tracked")),
    }
    return payload


def clean_price(row):
    item = dict(row)
    current_cny = price_row_cny(item)
    low_cny = item.get("historical_low_cny")
    return {
        "region": item.get("region"),
        "currency": item.get("currency"),
        "initial": item.get("initial"),
        "final": item.get("final"),
        "discount_percent": item.get("discount_percent"),
        "final_formatted": item.get("final_formatted"),
        "source": item.get("source"),
        "fetched_at": item.get("fetched_at"),
        "historical_low": compare_historical_low(current_cny, low_cny),
        "current_cny": current_cny,
        "historical_low_cny": low_cny,
        "historical_low_currency": item.get("historical_low_currency"),
        "historical_low_amount_int": item.get("historical_low_amount_int"),
        "historical_low_at": item.get("historical_low_at"),
    }


def clean_player(row):
    item = dict(row)
    return {
        "player_count": item.get("player_count"),
        "fetched_at": item.get("fetched_at"),
    }


def clean_review(row):
    if not row:
        return None
    item = dict(row)
    return {
        "review_score": item.get("review_score"),
        "review_score_desc": item.get("review_score_desc"),
        "total_positive": item.get("total_positive"),
        "total_negative": item.get("total_negative"),
        "total_reviews": item.get("total_reviews"),
        "fetched_at": item.get("fetched_at"),
    }


def latest_by_region(conn, appid):
    from .db import query_latest_prices_by_region

    return [clean_price(row) for row in query_latest_prices_by_region(conn, appid)]


def normalized_history_limit(value):
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return 500
    return min(2000, max(1, limit))


def ensure_game_from_catalog(conn, appid):
    row = conn.execute(
        "SELECT appid, name, app_type FROM steam_catalog WHERE appid = ?",
        (appid,),
    ).fetchone()
    if not row:
        return False
    if row[2] not in ("unknown", "game") or (row[2] == "unknown" and is_obvious_non_game_name(row[1])):
        return False
    name = clean_hot_name(row[1]) or fallback_game_name(appid)
    header = f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg"
    conn.execute(
        """
        INSERT INTO games(appid, name, header_image, tracked, updated_at)
        VALUES (?, ?, ?, 0, ?)
        ON CONFLICT(appid) DO UPDATE SET
            name=CASE WHEN games.name = ? OR games.name = ? THEN excluded.name ELSE games.name END,
            header_image=COALESCE(games.header_image, excluded.header_image),
            updated_at=excluded.updated_at
        """,
        (appid, name, header, now_iso(), UNKNOWN_GAME_NAME, f"App {appid}"),
    )
    conn.commit()
    return True


def get_game_payload(appid, history_limit=500):
    history_limit = normalized_history_limit(history_limit)
    with database_connection() as conn:
        conn.row_factory = sqlite3.Row
        from .db import query_game_detail

        detail = query_game_detail(conn, appid, history_limit)
        if not detail:
            ensure_game_from_catalog(conn, appid)
            detail = query_game_detail(conn, appid, history_limit)
        if not detail:
            return None
        prices = [clean_price(row) for row in detail["prices"]]
        price_history = detail["price_history"]
        players = detail["players"]
        reviews = detail["reviews"]
        game_payload = clean_game(detail["game"])
        site_peak = detail["site_peak"]
        game_payload["site_peak_players"] = site_peak[0] if site_peak else None
        game_payload["site_peak_recorded_since"] = site_peak[1] if site_peak else None
        missing_fields = []
        if not prices:
            missing_fields.append("prices")
        if not players:
            missing_fields.append("players")
        if not reviews:
            missing_fields.append("reviews")
        if not game_payload.get("short_description"):
            missing_fields.append("metadata")
        refresh_started = False
        if missing_fields:
            with PREVIEW_BACKFILL_LOCK:
                preview_running = appid in PREVIEW_BACKFILLING
            if not preview_running:
                refresh_started = backfill_preview_async(appid, game_payload.get("name"))
            if not (preview_running or refresh_started):
                if "prices" in missing_fields or "players" in missing_fields:
                    enqueue_crawl_task_once_in_conn(conn, appid, "preview", 100)
                if "reviews" in missing_fields:
                    enqueue_crawl_task_once_in_conn(conn, appid, "reviews", 100)
                if "metadata" in missing_fields:
                    enqueue_crawl_task_once_in_conn(conn, appid, "metadata", 100)
                conn.commit()
        if not detail["has_historical_low"]:
            enqueue_crawl_tasks_in_conn(conn, [appid], "historylow", 100)
            conn.commit()
            backfill_historylow_async(appid)
        return {
            "game": game_payload,
            "prices": prices,
            "priceHistory": [clean_price(row) for row in price_history],
            "players": [clean_player(row) for row in players],
            "reviews": clean_review(reviews),
            "refresh_pending": bool(missing_fields),
            "pending_fields": missing_fields,
            "refresh_started": bool(refresh_started),
            "retry_after_seconds": service_cooldown_remaining_seconds("steam_store"),
        }


def list_games():
    from .db import query_tracked_games

    return [clean_game(row, summary=True) for row in query_tracked_games()]


def list_hot_games(limit=100):
    limit = min(max(1, int(limit)), HOTLIST_TARGET)
    from .db import query_hot_games

    rows = query_hot_games(limit, UNKNOWN_GAME_NAME)
    games = []
    for index, row in enumerate(rows, 1):
        current_cny = amount_int_to_cny(row["cn_price_final"], row["cn_price_currency"] or "CNY")
        is_low = compare_historical_low(current_cny, row["cn_historical_low_cny"])
        games.append(
            {
            "appid": row["appid"],
            "rank": index,
            "original_rank": row["original_rank"],
            "name": fallback_game_name(row["appid"], row["name"]),
            "header_image": row["header_image"],
            "current_players": row["current_players"],
            "peak_players": row["peak_players"],
            "review_score": row["review_score"],
            "is_free": bool(row["is_free"]),
            "is_paid": not bool(row["is_free"]),
            "cn_price": row["cn_price"],
            "cn_price_display": row["cn_price"] or ("免费" if row["is_free"] else "国区暂无售价"),
            "cn_price_final": row["cn_price_final"],
            "cn_discount_percent": row["cn_discount_percent"] or 0,
            "cn_price_historical_low": is_low,
            "cn_price_discounted": bool((row["cn_discount_percent"] or 0) > 0 and not is_low),
            "cn_historical_low_cny": row["cn_historical_low_cny"],
            "source": row["source"],
            "fetched_at": row["fetched_at"],
            "tracked": bool(row["tracked"]),
            }
        )
    return games


def daily_index(total, refresh_key=None):
    from .services import daily_index as implementation

    return implementation(total, refresh_key)


def list_local_memes():
    from .services import list_local_memes as implementation

    return implementation()


def ensure_daily_home_snapshot(historical_lows, memes):
    from .services import ensure_daily_home_snapshot as implementation

    return implementation(historical_lows, memes)


def clean_home_pick(row):
    from .services import clean_home_pick as implementation

    return implementation(row)


def list_popular_historical_low_games(limit=200):
    from .services import list_popular_historical_low_games as implementation

    return implementation(limit)


def list_niche_candidates(limit=24):
    with database_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT n.appid, n.name, n.header_image, n.current_players, n.peak_players,
                   n.review_score, n.total_reviews, n.cn_price, n.cn_price_final,
                   n.cn_price_currency, n.cn_discount_percent, n.is_free,
                   (SELECT amount_cny FROM historical_lows h
                    WHERE h.appid = n.appid AND h.country = 'CN' LIMIT 1) AS cn_historical_low_cny,
                   g.tracked, n.weighted_score
            FROM niche_pool n
            LEFT JOIN games g ON g.appid = n.appid
            LEFT JOIN steam_catalog c ON c.appid = n.appid
            WHERE n.eligible = 1
              AND n.total_reviews BETWEEN 1 AND ?
              AND COALESCE(c.app_type, 'game') = 'game'
            ORDER BY n.weighted_score DESC, n.total_reviews DESC
            LIMIT ?
            """,
            (NICHE_MAX_REVIEWS, limit),
        ).fetchall()
    return [clean_home_pick(row) for row in rows if not is_obvious_non_game_name(row["name"])]


def list_niche_pool_games(limit=NICHE_POOL_DISPLAY_LIMIT):
    limit = min(max(1, int(limit)), NICHE_POOL_DISPLAY_LIMIT)
    with database_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT n.appid, n.name, n.header_image, n.current_players, n.peak_players,
                   n.review_score, n.total_reviews,
                   CASE WHEN s.price_updated_at >= n.fetched_at THEN s.cn_price ELSE n.cn_price END AS cn_price,
                   CASE WHEN s.price_updated_at >= n.fetched_at THEN s.cn_price_final ELSE n.cn_price_final END AS cn_price_final,
                   CASE WHEN s.price_updated_at >= n.fetched_at THEN s.cn_price_currency ELSE n.cn_price_currency END AS cn_price_currency,
                   CASE WHEN s.price_updated_at >= n.fetched_at THEN COALESCE(s.cn_discount_percent, 0) ELSE n.cn_discount_percent END AS cn_discount_percent,
                   COALESCE(g.is_free, n.is_free) AS is_free, n.fetched_at,
                   n.weighted_score, COALESCE(g.tracked, 0) AS tracked,
                   (SELECT amount_cny FROM historical_lows h
                    WHERE h.appid = n.appid AND h.country = 'CN' LIMIT 1) AS cn_historical_low_cny
            FROM niche_pool n
            LEFT JOIN games g ON g.appid = n.appid
            LEFT JOIN game_latest_state s ON s.appid = n.appid
            LEFT JOIN steam_catalog c ON c.appid = n.appid
            WHERE n.eligible = 1
              AND n.total_reviews BETWEEN 1 AND ?
              AND COALESCE(c.app_type, 'game') = 'game'
            ORDER BY n.weighted_score DESC, n.total_reviews DESC
            """,
            (NICHE_MAX_REVIEWS,),
        ).fetchall()
    rows = [row for row in rows if not is_obvious_non_game_name(row["name"])]
    # Keep a small pool fully visible while it is still being built. Once it
    # has enough choices, draw from the stronger half for variety, then sort
    # the displayed games back into score order for easy comparison.
    if len(rows) <= limit:
        candidates = list(rows)
    elif len(rows) < limit * 2:
        candidates = list(rows[:limit])
    else:
        top_half_count = max(1, (len(rows) + 1) // 2)
        top_half = list(rows[:top_half_count])
        candidates = random.SystemRandom().sample(top_half, min(limit, len(top_half)))
        candidates.sort(
            key=lambda row: (float(row["weighted_score"] or 0), int(row["total_reviews"] or 0)),
            reverse=True,
        )
    games = []
    for rank, row in enumerate(candidates, 1):
        current_cny = amount_int_to_cny(row["cn_price_final"], row["cn_price_currency"] or "CNY")
        is_low = compare_historical_low(current_cny, row["cn_historical_low_cny"])
        games.append(
            {
                "appid": row["appid"],
                "rank": rank,
                "original_rank": None,
                "name": fallback_game_name(row["appid"], row["name"]),
                "header_image": row["header_image"],
                "current_players": row["current_players"] or 0,
                "peak_players": row["peak_players"],
                "weighted_score": row["weighted_score"],
                "review_score": row["review_score"],
                "is_free": bool(row["is_free"]),
                "is_paid": not bool(row["is_free"]),
                "cn_price": row["cn_price"],
                "cn_price_display": row["cn_price"] or ("免费" if row["is_free"] else "国区暂无售价"),
                "cn_price_final": row["cn_price_final"],
                "cn_discount_percent": row["cn_discount_percent"] or 0,
                "cn_price_historical_low": is_low,
                "cn_price_discounted": bool((row["cn_discount_percent"] or 0) > 0 and not is_low),
                "cn_historical_low_cny": row["cn_historical_low_cny"],
                "source": "niche_pool",
                "fetched_at": row["fetched_at"],
                "tracked": bool(row["tracked"]),
            }
        )
    return games


def get_home_picks():
    from .services import get_home_picks as implementation

    return implementation()


def search_local_games(term):
    pattern = f"%{term}%"
    with database_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT g.appid, g.name, g.header_image, g.tracked,
                   (SELECT player_count FROM player_snapshots WHERE appid = g.appid ORDER BY fetched_at DESC LIMIT 1) AS player_count
            FROM games g
            LEFT JOIN steam_catalog c ON c.appid=g.appid
            WHERE (g.name LIKE ? OR c.name LIKE ? OR g.short_description LIKE ?)
              AND g.name != ?
              AND COALESCE(c.app_type, 'game') = 'game'
            ORDER BY g.tracked DESC,
                     CASE
                       WHEN g.name LIKE ? THEN 0
                       WHEN c.name LIKE ? THEN 1
                       WHEN g.short_description LIKE ? THEN 2
                       ELSE 3
                     END,
                     player_count DESC, g.name ASC
            LIMIT 12
            """,
            (pattern, pattern, pattern, UNKNOWN_GAME_NAME, pattern, pattern, f"%《{term}》%"),
        ).fetchall()
    return [
        {
            "appid": row["appid"],
            "name": clean_name(row["name"]),
            "tiny_image": row["header_image"],
            "price": None,
            "current_players": row["player_count"],
            "tracked": bool(row["tracked"]),
        }
        for row in rows
        if not is_obvious_non_game_name(row["name"])
    ]


def search_catalog_games(term):
    pattern = f"%{term}%"
    with database_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT c.appid,
                   CASE WHEN g.name IS NOT NULL AND g.name != ? THEN g.name ELSE c.name END AS name,
                   COALESCE(g.header_image, 'https://cdn.akamai.steamstatic.com/steam/apps/' || c.appid || '/header.jpg') AS header_image,
                   COALESCE(g.tracked, 0) AS tracked
            FROM steam_catalog c
            LEFT JOIN games g ON g.appid = c.appid
            WHERE c.name LIKE ?
              AND c.app_type IN ('unknown', 'game')
            ORDER BY CASE c.app_type WHEN 'game' THEN 0 ELSE 1 END, c.name ASC
            LIMIT 36
            """,
            (UNKNOWN_GAME_NAME, pattern),
        ).fetchall()
    return [
        {
            "appid": row["appid"],
            "name": row["name"],
            "tiny_image": row["header_image"],
            "price": None,
            "current_players": None,
            "tracked": bool(row["tracked"]),
        }
        for row in rows
        if not is_obvious_non_game_name(row["name"])
    ][:12]


def normalize_search_term(term):
    return re.sub(r"\s+", " ", str(term or "").strip()).lower()


def _search_cache_get(key):
    with SEARCH_CACHE_LOCK:
        SEARCH_METRICS["requests"] += 1
        cached = SEARCH_CACHE.get(key)
        if not cached:
            return None
        ttl = SEARCH_CACHE_TTL_SECONDS if cached["items"] else SEARCH_CACHE_EMPTY_TTL_SECONDS
        if time.monotonic() - cached["at"] >= ttl:
            SEARCH_CACHE.pop(key, None)
            return None
        SEARCH_CACHE.move_to_end(key)
        SEARCH_METRICS["cache_hits"] += 1
        return [dict(item) for item in cached["items"]]


def _search_cache_set(key, items):
    with SEARCH_CACHE_LOCK:
        SEARCH_CACHE[key] = {
            "at": time.monotonic(),
            "items": [dict(item) for item in items],
        }
        SEARCH_CACHE.move_to_end(key)
        while len(SEARCH_CACHE) > SEARCH_CACHE_MAX_ENTRIES:
            SEARCH_CACHE.popitem(last=False)


def get_search_metrics():
    with SEARCH_CACHE_LOCK:
        metrics = dict(SEARCH_METRICS)
        requests = int(metrics["requests"])
        queries = int(metrics["database_queries"])
        metrics["cache_entries"] = len(SEARCH_CACHE)
        metrics["cache_max_entries"] = SEARCH_CACHE_MAX_ENTRIES
        metrics["cache_hit_rate"] = round(metrics["cache_hits"] / requests, 4) if requests else 0.0
        metrics["average_database_query_ms"] = round(
            metrics.pop("database_query_ms_total") / queries, 3
        ) if queries else 0.0
        metrics["max_database_query_ms"] = round(metrics.pop("database_query_ms_max"), 3)
        metrics["cache_estimated_bytes"] = sum(
            len(str(key).encode("utf-8"))
            + len(json.dumps(value["items"], ensure_ascii=False).encode("utf-8"))
            for key, value in SEARCH_CACHE.items()
        )
    metrics["storage"] = "sqlite_fts5_trigram"
    metrics["connection_strategy"] = "short_lived_per_request"
    return metrics


def search_index_games(term, limit=12, offset=0):
    term = normalize_search_term(term)
    limit = min(50, max(1, int(limit)))
    offset = max(0, int(offset))
    if not term:
        return []
    from .db import query_search_index

    rows = query_search_index(term, limit, offset, UNKNOWN_GAME_NAME)

    return [
        {
            "appid": row["appid"],
            "name": clean_name(row["name"]),
            "tiny_image": row["header_image"],
            "price": None,
            "current_players": row["current_players"],
            "tracked": bool(row["tracked"]),
        }
        for row in rows
        if row["name"] and not is_obvious_non_game_name(row["name"])
    ]


def search_steam(term, limit=12, offset=0):
    """Compatibility name for fast local search; text queries never call Steam."""
    cache_key = (str(DB_PATH), normalize_search_term(term), int(limit), int(offset))
    cached = _search_cache_get(cache_key)
    if cached is not None:
        return cached

    started = time.perf_counter()
    items = search_index_games(term, limit=limit, offset=offset)
    elapsed_ms = (time.perf_counter() - started) * 1000
    with SEARCH_CACHE_LOCK:
        SEARCH_METRICS["database_queries"] += 1
        SEARCH_METRICS["database_query_ms_total"] += elapsed_ms
        SEARCH_METRICS["database_query_ms_max"] = max(
            SEARCH_METRICS["database_query_ms_max"], elapsed_ms
        )
    _search_cache_set(cache_key, items)
    return items
