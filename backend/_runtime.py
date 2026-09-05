import asyncio
import hashlib
import json
import os
import random
import math
import sqlite3
import socket
import ssl
import threading
import time
import mimetypes
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_dotenv():
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()

DATA_DIR = ROOT / "data"
IMAGE_CACHE_DIR = DATA_DIR / "image-cache"
BADGE_ASSETS_DIR = ROOT / "assets" / "badges"
DB_PATH = Path(os.getenv("STEAMKB_DB", str(DATA_DIR / "steamkb.sqlite3")))
LOG_PATH = Path(os.getenv("STEAMKB_LOG", str(DATA_DIR / "steamkb.log")))
LOG_RETENTION_DAYS = max(7, int(os.getenv("STEAMKB_LOG_RETENTION_DAYS", "30")))
PRICE_RETENTION_DAYS = max(30, int(os.getenv("STEAMKB_PRICE_RETENTION_DAYS", "730")))
RECOMMENDATION_RETENTION_DAYS = max(30, int(os.getenv("STEAMKB_RECOMMENDATION_RETENTION_DAYS", "730")))
CRAWL_TASK_RETENTION_DAYS = max(7, int(os.getenv("STEAMKB_CRAWL_TASK_RETENTION_DAYS", "60")))
IMAGE_CACHE_MAX_BYTES = max(16 * 1024 * 1024, int(os.getenv("STEAMKB_IMAGE_CACHE_MAX_BYTES", str(512 * 1024 * 1024))))
IMAGE_CACHE_RETENTION_DAYS = max(1, int(os.getenv("STEAMKB_IMAGE_CACHE_RETENTION_DAYS", "30")))
IMAGE_CACHE_MAX_FILE_BYTES = max(256 * 1024, int(os.getenv("STEAMKB_IMAGE_CACHE_MAX_FILE_BYTES", str(2 * 1024 * 1024))))
PORT = int(os.getenv("STEAMKB_PORT", "8765"))
PLAYER_REFRESH_MINUTES = max(30, int(os.getenv("STEAMKB_PLAYER_REFRESH_MINUTES", "30")))
PRICE_REFRESH_HOURS = max(24, int(os.getenv("STEAMKB_PRICE_REFRESH_HOURS", "24")))
HISTORICAL_LOW_TOLERANCE_CNY = float(os.getenv("STEAMKB_HISTORICAL_LOW_TOLERANCE_CNY", "0.5"))
SCHEDULER_CHECK_SECONDS = 60
HOTLIST_TARGET = max(100, int(os.getenv("STEAMKB_HOTLIST_TARGET", "100")))
HOTLIST_CONCURRENCY = min(10, max(1, int(os.getenv("STEAMKB_HOTLIST_CONCURRENCY", "8"))))
HOTLIST_BATCH_SIZE = max(50, int(os.getenv("STEAMKB_HOTLIST_BATCH_SIZE", "200")))
HOTLIST_REFRESH_HOURS = max(24, int(os.getenv("STEAMKB_HOTLIST_REFRESH_HOURS", "24")))
HOT_METADATA_CONCURRENCY = min(4, max(1, int(os.getenv("STEAMKB_HOT_METADATA_CONCURRENCY", "2"))))
HOT_PREVIEW_TOP_LIMIT = max(50, int(os.getenv("STEAMKB_HOT_PREVIEW_TOP_LIMIT", "200")))
HOT_PREVIEW_BATCH_LIMIT = max(20, int(os.getenv("STEAMKB_HOT_PREVIEW_BATCH_LIMIT", "100")))
HOT_FULL_METADATA_TOP_LIMIT = max(10, int(os.getenv("STEAMKB_HOT_FULL_METADATA_TOP_LIMIT", "50")))
HOT_METADATA_BATCH_LIMIT = max(10, int(os.getenv("STEAMKB_HOT_METADATA_BATCH_LIMIT", "50")))
NICHE_POOL_BATCH_LIMIT = max(10, int(os.getenv("STEAMKB_NICHE_POOL_BATCH_LIMIT", "30")))
NICHE_POOL_REFRESH_MINUTES = max(30, int(os.getenv("STEAMKB_NICHE_POOL_REFRESH_MINUTES", "1440")))
NICHE_POOL_DISPLAY_LIMIT = 20
# A small pool needs a little extra attention, but probing Steam too often is
# counterproductive. Once the display target is met, normal daily upkeep wins.
NICHE_POOL_BOOTSTRAP_REFRESH_MINUTES = max(60, int(os.getenv("STEAMKB_NICHE_POOL_BOOTSTRAP_REFRESH_MINUTES", "180")))
STEAM_CATALOG_LIMIT = max(1000, int(os.getenv("STEAMKB_CATALOG_LIMIT", "20000")))
CATALOG_ENRICH_DAILY_LIMIT = max(100, int(os.getenv("STEAMKB_CATALOG_ENRICH_DAILY_LIMIT", "500")))
CATALOG_ENRICH_BATCH_LIMIT = max(20, int(os.getenv("STEAMKB_CATALOG_ENRICH_BATCH_LIMIT", "50")))
NICHE_POOL_LIMIT = max(50, int(os.getenv("STEAMKB_NICHE_POOL_LIMIT", "500")))
TRACKED_REFRESH_BATCH_LIMIT = max(1, int(os.getenv("STEAMKB_TRACKED_REFRESH_BATCH_LIMIT", "1")))
ITAD_HISTORYLOW_BATCH_LIMIT = max(1, int(os.getenv("STEAMKB_ITAD_HISTORYLOW_BATCH_LIMIT", "50")))
STORE_REQUEST_DELAY_MIN_SECONDS = max(0, float(os.getenv("STEAMKB_STORE_DELAY_MIN_SECONDS", "1.5")))
STORE_REQUEST_DELAY_MAX_SECONDS = max(
    STORE_REQUEST_DELAY_MIN_SECONDS,
    float(os.getenv("STEAMKB_STORE_DELAY_MAX_SECONDS", "4.0")),
)
ITAD_API_KEY = os.getenv("ITAD_API_KEY", "")
STEAM_API_KEY = os.getenv("STEAM_API_KEY", "").strip()
DB_TIMEOUT_SECONDS = 30
STEAM_USER_AGENT = "Steam-KaKaBase/1.0 (+local personal dashboard)"
STEAM_TIMEOUT_SECONDS = float(os.getenv("STEAMKB_HTTP_TIMEOUT_SECONDS", "15"))
STEAM_PROXY_URL = os.getenv("STEAMKB_PROXY_URL", "").strip()
USE_PROXY = os.getenv("USE_PROXY", os.getenv("UNE_PROXY", "false")).strip().lower() in {"1", "true", "yes", "on"}
# Local proxy applications often intercept HTTPS using a locally generated
# certificate. Direct requests always verify TLS; this only affects fallback.
STEAM_PROXY_VERIFY_TLS = os.getenv("STEAMKB_PROXY_VERIFY_TLS", "false").strip().lower() in {"1", "true", "yes", "on"}
STEAM_MAX_RETRIES = max(0, int(os.getenv("STEAMKB_HTTP_MAX_RETRIES", "2")))
STEAM_RETRY_STATUSES = {429, 500, 502, 503}
APP_VERSION = "2026.09.05-modular-backend"
ITAD_MISSING_GAME_ID = "__itad_missing__"
# Used only when restarting a rate-limited local service: cache reads remain
# available while external Steam work stays paused for the supplied duration.
STEAM_COOLDOWN_UNTIL = time.time() + max(0, int(os.getenv("STEAMKB_START_COOLDOWN_SECONDS", "0")))
STEAM_COOLDOWN_LOCK = threading.Lock()
PROXY_STATUS = {
    "configured": bool(STEAM_PROXY_URL), "enabled": USE_PROXY, "reachable": None,
    "tls_verify": STEAM_PROXY_VERIFY_TLS, "fallback_successes": 0, "fallback_failures": 0,
    "message": "直连优先" if not USE_PROXY else "待检测",
}
PROXY_FALLBACK_LOCK = threading.Lock()
PROXY_FALLBACK_LOGGED_AT = {}
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
SEARCH_CACHE = {}
SEARCH_CACHE_TTL_SECONDS = 600
APP_NAME_REFRESH_HOURS = max(24, int(os.getenv("STEAMKB_APP_NAME_REFRESH_HOURS", "24")))
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
BADGE_ASSET_EXTENSIONS = (".png", ".webp", ".jpg", ".jpeg", ".gif")
ALLOWED_IMAGE_HOSTS = {
    "shared.akamai.steamstatic.com",
    "shared.cloudflare.steamstatic.com",
    "cdn.akamai.steamstatic.com",
    "cdn.cloudflare.steamstatic.com",
    "steamcdn-a.akamaihd.net",
}


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


def steam_cooldown_remaining_seconds():
    with STEAM_COOLDOWN_LOCK:
        return max(0, int(STEAM_COOLDOWN_UNTIL - time.time()))


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
    pass


def set_steam_cooldown(minutes=10):
    global STEAM_COOLDOWN_UNTIL
    until = time.time() + (minutes * 60)
    with STEAM_COOLDOWN_LOCK:
        extended = until > STEAM_COOLDOWN_UNTIL
        STEAM_COOLDOWN_UNTIL = max(STEAM_COOLDOWN_UNTIL, until)
    if extended:
        log_event(f"steam cooldown enabled for {minutes} minutes")


def check_steam_cooldown():
    with STEAM_COOLDOWN_LOCK:
        remaining = STEAM_COOLDOWN_UNTIL - time.time()
    if remaining > 0:
        raise SteamRateLimited(f"Steam rate limited, retry after {int(remaining)}s")


class ExternalDataUnavailable(Exception):
    pass


def request_json(url, timeout=STEAM_TIMEOUT_SECONDS, headers=None, missing_statuses=None, max_retries=None):
    check_steam_cooldown()
    missing_statuses = set(missing_statuses or [])
    base_headers = {
        "User-Agent": STEAM_USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
    }
    if headers:
        base_headers.update(headers)
    last_exc = None
    retries = STEAM_MAX_RETRIES if max_retries is None else max(0, int(max_retries))
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=base_headers)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                res = opener.open(req, timeout=timeout)
            except urllib.error.URLError as direct_exc:
                if not proxy_fallback_enabled():
                    raise
                log_proxy_fallback_once(url, type(direct_exc).__name__)
                proxy_handler = urllib.request.ProxyHandler({"http": STEAM_PROXY_URL, "https": STEAM_PROXY_URL})
                handlers = [proxy_handler]
                if not STEAM_PROXY_VERIFY_TLS:
                    handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
                try:
                    res = urllib.request.build_opener(*handlers).open(req, timeout=timeout)
                    record_proxy_fallback(True)
                except urllib.error.HTTPError:
                    # The proxy transport worked; the Steam resource itself
                    # may still be unavailable (for example, HTTP 404).
                    record_proxy_fallback(True)
                    raise
                except Exception as proxy_exc:
                    record_proxy_fallback(False, proxy_exc)
                    raise
            with res:
                charset = res.headers.get_content_charset() or "utf-8"
                return json.loads(res.read().decode(charset, errors="replace"))
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code in missing_statuses:
                raise ExternalDataUnavailable(f"HTTP {exc.code}: external data unavailable")
            if exc.code == 429:
                set_steam_cooldown(10)
                raise SteamRateLimited("Steam HTTP 429")
            if exc.code not in STEAM_RETRY_STATUSES or attempt >= STEAM_MAX_RETRIES:
                log_event(f"steam request failed status={exc.code} url={url}: {exc}")
                raise
            log_event(f"steam request retry status={exc.code} attempt={attempt + 1} url={url}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_exc = exc
            if attempt >= STEAM_MAX_RETRIES:
                log_event(f"steam request failed url={url}: {exc}")
                raise
            log_event(f"steam request retry attempt={attempt + 1} url={url}: {exc}")
        time.sleep(retry_delay(attempt))
    raise last_exc


def cache_image(url):
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
    with urllib.request.urlopen(req, timeout=STEAM_TIMEOUT_SECONDS) as res:
        content_type = res.headers.get_content_type()
        if not content_type.startswith("image/"):
            raise ValueError(f"unexpected content type: {content_type}")
        body = res.read(IMAGE_CACHE_MAX_FILE_BYTES + 1)
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


def infer_name_from_description(value):
    if not value:
        return None
    match = re.search(r"《([^》]{2,80})》", str(value))
    if match:
        return match.group(1).strip()
    return None


def is_recent_release(value, years=5):
    text = str(value or "").strip()
    match = re.search(r"\b(19\d{2}|20\d{2})\b", text)
    if not match:
        return False
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
        released = datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return False
    return released >= datetime.now(timezone.utc) - timedelta(days=365.25 * years)


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
        ensure_schema(conn)
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
        for item in DEFAULT_APPS:
            conn.execute(
                "INSERT OR IGNORE INTO games(appid, name, tracked, updated_at) VALUES (?, ?, 1, ?)",
                (item["appid"], item["name"], now_iso()),
            )
        clear_seeded_defaults(conn)
        repair_placeholder_names(conn)
        conn.execute("PRAGMA optimize")


def ensure_schema(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(games)").fetchall()}
    if "screenshots_json" not in columns:
        conn.execute("ALTER TABLE games ADD COLUMN screenshots_json TEXT")
    if "itad_game_id" not in columns:
        conn.execute("ALTER TABLE games ADD COLUMN itad_game_id TEXT")
    niche_columns = {row[1] for row in conn.execute("PRAGMA table_info(niche_pool)").fetchall()}
    if niche_columns and "release_date" not in niche_columns:
        conn.execute("ALTER TABLE niche_pool ADD COLUMN release_date TEXT")
    if niche_columns and "peak_players" not in niche_columns:
        conn.execute("ALTER TABLE niche_pool ADD COLUMN peak_players INTEGER")
    # Niche-pool peaks are deliberately local observations, never Steam's
    # transient chart peak. Rebuild them from this site's own snapshots.
    conn.execute(
        """
        UPDATE niche_pool
        SET peak_players = COALESCE(
            (SELECT MAX(p.player_count) FROM player_snapshots p WHERE p.appid = niche_pool.appid),
            current_players,
            0
        )
        """
    )
    task_columns = {row[1] for row in conn.execute("PRAGMA table_info(crawl_tasks)").fetchall()}
    if task_columns and "status" not in task_columns:
        conn.execute("ALTER TABLE crawl_tasks ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
    if task_columns and "attempts" not in task_columns:
        conn.execute("ALTER TABLE crawl_tasks ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
    if task_columns and "generation" not in task_columns:
        conn.execute("ALTER TABLE crawl_tasks ADD COLUMN generation INTEGER")
    conn.execute(
        """
        UPDATE crawl_tasks
        SET status = 'skipped',
            locked_until = NULL,
            last_error = 'screenshots disabled: Steam-KaKaBase now loads header images and optional local badges only',
            updated_at = ?
        WHERE task_type = 'screenshots' AND status IN ('pending', 'retry', 'running')
        """,
        (now_iso(),),
    )
    conn.execute(
        """
        UPDATE crawl_tasks
        SET status = 'not_available', completed_at = ?, locked_until = NULL,
            last_error = 'merged into preview task', updated_at = ?
        WHERE task_type IN ('price', 'static') AND status IN ('pending', 'retry', 'running')
        """,
        (now_iso(), now_iso()),
    )
    conn.execute(
        """
        UPDATE hot_games
        SET name = NULL
        WHERE name LIKE 'App %' OR name LIKE 'Steam App %'
        """
    )
    conn.execute(
        """
        UPDATE games
        SET name = ?, updated_at = ?
        WHERE name LIKE 'App %' OR name LIKE 'Steam App %'
        """,
        (UNKNOWN_GAME_NAME, now_iso()),
    )


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


def list_badges(appid):
    badge_dir = BADGE_ASSETS_DIR / str(int(appid))
    if not badge_dir.is_dir():
        return []
    rows = []
    for level in range(1, 7):
        for suffix in BADGE_ASSET_EXTENSIONS:
            path = badge_dir / f"level-{level}{suffix}"
            if path.is_file():
                rows.append(
                    {
                        "level": level,
                        "image": f"/assets/badges/{int(appid)}/{path.name}",
                    }
                )
                break
    return rows


def chunks(rows, size):
    for index in range(0, len(rows), size):
        yield rows[index : index + size]


def get_crawl_state(conn, key):
    row = conn.execute("SELECT value FROM crawl_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_crawl_state(conn, key, value):
    conn.execute(
        "INSERT INTO crawl_state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def enqueue_crawl_tasks_in_conn(conn, appids, task_type, priority, next_attempt_at=None, generation=None):
    rows = [(int(appid), task_type, int(priority), next_attempt_at or now_iso(), now_iso(), generation) for appid in appids]
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO crawl_tasks(appid, task_type, priority, next_attempt_at, updated_at, generation)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(appid, task_type) DO UPDATE SET
            priority=MAX(crawl_tasks.priority, excluded.priority),
            status=CASE
                WHEN crawl_tasks.status IN ('failed', 'permanent_failed', 'not_available') AND excluded.priority < 100 THEN crawl_tasks.status
                ELSE 'pending'
            END,
            next_attempt_at=CASE
                WHEN crawl_tasks.status IN ('failed', 'permanent_failed', 'not_available') AND excluded.priority < 100 THEN crawl_tasks.next_attempt_at
                WHEN crawl_tasks.completed_at IS NOT NULL THEN excluded.next_attempt_at
                WHEN crawl_tasks.next_attempt_at IS NULL THEN excluded.next_attempt_at
                WHEN excluded.next_attempt_at < crawl_tasks.next_attempt_at THEN excluded.next_attempt_at
                ELSE crawl_tasks.next_attempt_at
            END,
            completed_at=CASE
                WHEN crawl_tasks.status IN ('failed', 'permanent_failed', 'not_available') AND excluded.priority < 100 THEN crawl_tasks.completed_at
                ELSE NULL
            END,
            updated_at=excluded.updated_at
            ,generation=COALESCE(excluded.generation, crawl_tasks.generation)
        """,
        rows,
    )
    return len(rows)


def enqueue_crawl_tasks(appids, task_type, priority, next_attempt_at=None, generation=None):
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        return enqueue_crawl_tasks_in_conn(conn, appids, task_type, priority, next_attempt_at, generation)


def claim_crawl_tasks(task_type, limit, lock_minutes=15):
    stamp = now_iso()
    locked_until = (datetime.now(timezone.utc) + timedelta(minutes=lock_minutes)).replace(microsecond=0).isoformat()
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        current_generation = int(get_crawl_state(conn, "hotlist_generation") or 0)
        rows = conn.execute(
            """
            SELECT appid
            FROM crawl_tasks
            WHERE task_type = ?
              AND completed_at IS NULL
              AND status IN ('pending', 'retry')
              AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
              AND (locked_until IS NULL OR locked_until <= ?)
              AND (generation IS NULL OR generation = ?)
            ORDER BY priority DESC, attempts ASC, next_attempt_at ASC
            LIMIT ?
            """,
            (task_type, stamp, stamp, current_generation, limit),
        ).fetchall()
        appids = [int(row[0]) for row in rows]
        if appids:
            placeholders = ",".join("?" for _ in appids)
            conn.execute(
                f"""
                UPDATE crawl_tasks
                SET locked_until = ?, status = 'running', attempt_count = attempt_count + 1, attempts = attempts + 1, updated_at = ?
                WHERE task_type = ? AND appid IN ({placeholders})
                """,
                (locked_until, stamp, task_type, *appids),
            )
    return appids


def complete_crawl_tasks(appids, task_type):
    if not appids:
        return
    stamp = now_iso()
    placeholders = ",".join("?" for _ in appids)
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        conn.execute(
            f"""
            UPDATE crawl_tasks
            SET status = 'done', completed_at = ?, locked_until = NULL, last_error = NULL, updated_at = ?
            WHERE task_type = ? AND appid IN ({placeholders})
            """,
            (stamp, stamp, task_type, *[int(appid) for appid in appids]),
        )


def mark_crawl_tasks_not_available(appids, task_type, reason):
    """Finish valid-but-unavailable Steam resources without retrying them forever."""
    if not appids:
        return
    stamp = now_iso()
    placeholders = ",".join("?" for _ in appids)
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        conn.execute(
            f"""
            UPDATE crawl_tasks
            SET status = 'not_available', completed_at = ?, locked_until = NULL,
                last_error = ?, updated_at = ?
            WHERE task_type = ? AND appid IN ({placeholders})
            """,
            (stamp, str(reason)[:500], stamp, task_type, *[int(appid) for appid in appids]),
        )


def fail_crawl_tasks(appids, task_type, error, retry_minutes=60, terminal=False):
    if not appids:
        return
    stamp = now_iso()
    next_attempt = (datetime.now(timezone.utc) + timedelta(minutes=retry_minutes)).replace(microsecond=0).isoformat()
    placeholders = ",".join("?" for _ in appids)
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        conn.execute(
            f"""
            UPDATE crawl_tasks
            SET status = ?, next_attempt_at = ?, locked_until = NULL, last_error = ?, updated_at = ?
            WHERE task_type = ? AND appid IN ({placeholders})
            """,
            ("permanent_failed" if terminal else "retry", next_attempt, str(error)[:500], stamp, task_type, *[int(appid) for appid in appids]),
        )


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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        app_names_at = get_crawl_state(conn, "steam_app_names_at")
    if not (force or is_due(app_names_at, APP_NAME_REFRESH_HOURS * 60)):
        return False
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        name_rows = conn.execute(
            "SELECT appid, name FROM steam_catalog WHERE appid IN ({})".format(",".join("?" * len(missing_appids))),
            tuple(missing_appids),
        ).fetchall() if missing_appids else []
    updated_count = upsert_steam_app_names(name_rows)
    stamp = now_iso()
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        set_crawl_state(conn, "steam_app_names_at", stamp)
    log_event(f"steam app names refreshed matched={updated_count} missing={len(missing_appids)}")
    return True


def upsert_hot_games_batch(rows, stamp):
    if not rows:
        return
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    response = None
    fallback_reason = None
    try:
        response = await client.request(method, url, params=params, json=json_body)
        if response.status_code < 500 and response.status_code != 403:
            return response
        fallback_reason = f"HTTP {response.status_code}"
    except Exception as direct_exc:
        if not proxy_fallback_enabled():
            raise direct_exc
        fallback_reason = type(direct_exc).__name__
    if not proxy_fallback_enabled():
        return response
    log_proxy_fallback_once(url, fallback_reason or "unavailable")
    httpx = require_httpx()
    try:
        async with httpx.AsyncClient(timeout=STEAM_TIMEOUT_SECONDS, headers={"User-Agent": STEAM_USER_AGENT}, follow_redirects=True, **proxy_httpx_options()) as proxy_client:
            proxied_response = await proxy_client.request(method, url, params=params, json=json_body)
        # A 404/403 is a valid response from the proxy transport even though
        # the requested Steam resource itself is unavailable.
        record_proxy_fallback(proxied_response.status_code < 500, f"HTTP {proxied_response.status_code}")
        return proxied_response
    except Exception as proxy_exc:
        record_proxy_fallback(False, proxy_exc)
        raise


async def async_get_json(client, semaphore, url, params=None):
    check_steam_cooldown()
    async with semaphore:
        last_exc = None
        for attempt in range(STEAM_MAX_RETRIES + 1):
            try:
                check_steam_cooldown()
                response = await async_request_direct_then_proxy(client, "GET", url, params=params)
                response.raise_for_status()
                return response.json()
            except SteamRateLimited:
                # Cooldown is a global stop signal, never a retryable timeout.
                raise
            except Exception as exc:
                last_exc = exc
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                if status_code == 429:
                    set_steam_cooldown(10)
                    raise SteamRateLimited("Steam HTTP 429")
                if status_code == 404:
                    # Steam uses 404 for unreleased apps, DLC, tools and
                    # titles without public concurrent-player statistics.
                    raise ExternalDataUnavailable("Steam resource unavailable (HTTP 404)")
                retryable = status_code in STEAM_RETRY_STATUSES or status_code is None
                if not retryable or attempt >= STEAM_MAX_RETRIES:
                    log_event(f"http async request failed status={status_code} url={safe_log_url(url)}: {exc}")
                    raise
                log_event(f"http async request retry status={status_code} attempt={attempt + 1} url={safe_log_url(url)}: {exc}")
                await asyncio.sleep(retry_delay(attempt))


async def async_post_json(client, semaphore, url, params=None, json_body=None):
    check_steam_cooldown()
    async with semaphore:
        last_exc = None
        for attempt in range(STEAM_MAX_RETRIES + 1):
            try:
                check_steam_cooldown()
                response = await async_request_direct_then_proxy(client, "POST", url, params=params, json_body=json_body)
                response.raise_for_status()
                return response.json()
            except SteamRateLimited:
                raise
            except Exception as exc:
                last_exc = exc
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                if status_code == 429:
                    set_steam_cooldown(10)
                    raise SteamRateLimited("HTTP 429")
                if status_code == 404:
                    raise ExternalDataUnavailable("Steam resource unavailable (HTTP 404)")
                retryable = status_code in STEAM_RETRY_STATUSES or status_code is None
                if not retryable or attempt >= STEAM_MAX_RETRIES:
                    log_event(f"itad async post failed status={status_code} url={safe_log_url(url)}: {exc}")
                    raise
                log_event(f"itad async post retry status={status_code} attempt={attempt + 1} url={safe_log_url(url)}: {exc}")
                await asyncio.sleep(retry_delay(attempt))
        raise last_exc


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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        conn.executemany(
            "UPDATE games SET itad_game_id = ?, updated_at = ? WHERE appid = ?",
            [(gid, stamp, appid) for appid, gid, stamp in rows],
        )


def upsert_historical_lows(rows):
    if not rows:
        return
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    if not ITAD_API_KEY:
        return {}
    httpx = require_httpx()
    semaphore = asyncio.Semaphore(min(5, HOTLIST_CONCURRENCY))
    url = "https://api.isthereanydeal.com/games/lookup/v1"
    found = {}
    async with httpx.AsyncClient(timeout=STEAM_TIMEOUT_SECONDS, headers=itad_headers(), follow_redirects=True, **steam_httpx_options()) as client:
        async def fetch_one(appid):
            try:
                payload = await async_get_json(client, semaphore, url, {"appid": int(appid)})
                game = payload.get("game") if payload.get("found") else None
                gid = game.get("id") if isinstance(game, dict) else None
                if not gid:
                    log_event(f"itad lookup unavailable appid={appid}")
                    return int(appid), ITAD_MISSING_GAME_ID
                return int(appid), gid
            except Exception as exc:
                log_event(f"itad lookup skipped appid={appid}: {exc}")
                return int(appid), None

        for appid, gid in await asyncio.gather(*(fetch_one(appid) for appid in appids)):
            if gid:
                found[appid] = gid
    save_itad_game_ids(found.items())
    return found


async def fetch_itad_history_lows_async(appids, countries=("US", "CN")):
    if not ITAD_API_KEY:
        return now_iso()
    appids = [int(appid) for appid in appids]
    if not appids:
        return now_iso()
    stamp = now_iso()
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    gid_to_appid = {gid: appid for appid, gid in existing.items() if gid and gid != ITAD_MISSING_GAME_ID}
    if not gid_to_appid:
        return stamp

    httpx = require_httpx()
    semaphore = asyncio.Semaphore(2)
    url = "https://api.isthereanydeal.com/games/historylow/v1"
    async with httpx.AsyncClient(timeout=STEAM_TIMEOUT_SECONDS, headers=itad_headers(), follow_redirects=True, **steam_httpx_options()) as client:
        for country in countries:
            rows = []
            for gid_batch in chunks(list(gid_to_appid.keys()), 200):
                try:
                    payload = await async_post_json(client, semaphore, url, {"country": country}, gid_batch)
                except Exception as exc:
                    log_event(f"itad historylow skipped country={country}: {exc}")
                    continue
                seen_gids = set()
                for item in payload or []:
                    gid = item.get("id")
                    low = item.get("low") or {}
                    price = low.get("price") or {}
                    regular = low.get("regular") or {}
                    shop = low.get("shop") or {}
                    appid = gid_to_appid.get(gid)
                    amount_int = price.get("amountInt")
                    currency = price.get("currency")
                    if not appid:
                        continue
                    seen_gids.add(gid)
                    if amount_int is None:
                        rows.append(
                            (
                                appid,
                                gid,
                                country,
                                None,
                                None,
                                None,
                                None,
                                None,
                                None,
                                None,
                                None,
                                None,
                                stamp,
                            )
                        )
                        continue
                    rows.append(
                        (
                            appid,
                            gid,
                            country,
                            shop.get("id"),
                            shop.get("name"),
                            currency,
                            price.get("amount"),
                            amount_int,
                            amount_int_to_cny(amount_int, currency),
                            regular.get("amountInt"),
                            low.get("cut"),
                            low.get("timestamp"),
                            stamp,
                        )
                    )
                for gid in gid_batch:
                    if gid in seen_gids:
                        continue
                    appid = gid_to_appid.get(gid)
                    if not appid:
                        continue
                    rows.append(
                        (
                            appid,
                            gid,
                            country,
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                            stamp,
                        )
                    )
            upsert_historical_lows(rows)
    return stamp


async def fetch_official_hotlist_async():
    httpx = require_httpx()
    semaphore = asyncio.Semaphore(1)
    headers = {"User-Agent": STEAM_USER_AGENT}
    async with httpx.AsyncClient(timeout=STEAM_TIMEOUT_SECONDS, headers=headers, follow_redirects=True, **steam_httpx_options()) as client:
        urls = [
            "https://api.steampowered.com/ISteamChartsService/GetGamesByConcurrentPlayers/v1/",
            "https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/",
        ]
        for url in urls:
            try:
                payload = await async_get_json(client, semaphore, url)
                rows = parse_hot_chart(payload)
                if rows:
                    return rows
            except Exception as exc:
                log_event(f"hotlist endpoint failed url={url}: {exc}")
    return []


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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
        # An AppDetails response without a CN price is still a successful
        # daily check, such as a title not sold in China.
        conn.executemany(
            """
            INSERT INTO game_latest_state(appid, price_updated_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(appid) DO UPDATE SET
                price_updated_at = excluded.price_updated_at,
                updated_at = excluded.updated_at
            """,
            [(row["appid"], stamp, stamp) for row in rows],
        )


def upsert_release_date_batch(rows, stamp):
    if not rows:
        return
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        rows = conn.execute(
            """
            SELECT g.appid
            FROM games g
            LEFT JOIN niche_pool n ON n.appid = g.appid
            WHERE n.appid IS NULL
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
        with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
            candidates = [
                int(row[0]) for row in conn.execute(
                    "SELECT appid FROM steam_catalog WHERE appid NOT IN ({}) ORDER BY updated_at DESC LIMIT ?".format(
                        ",".join("?" * max(1, len(known | seen_pool)))
                    ),
                    tuple(known | seen_pool) + (max(0, limit - len(selected)),),
                ).fetchall()
            ] if known | seen_pool else [
                int(row[0]) for row in conn.execute(
                    "SELECT appid FROM steam_catalog ORDER BY updated_at DESC LIMIT ?",
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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
                data = (details_payload.get(str(appid)) or {}).get("data") or {}
                if data.get("type") != "game" or not data.get("name") or not data.get("header_image"):
                    return None
                review_payload = await async_get_json(
                    client, semaphore, f"https://store.steampowered.com/appreviews/{appid}",
                    {"json": 1, "language": "all", "purchase_type": "all", "num_per_page": 0, "filter": "summary"},
                )
                summary = review_payload.get("query_summary") or {}
                positive = int(summary.get("total_positive") or 0)
                negative = int(summary.get("total_negative") or 0)
                total = positive + negative
                player_payload = await async_get_json(client, semaphore, players_url, {"appid": appid})
                players = int((player_payload.get("response") or {}).get("player_count") or 0)
                price = data.get("price_overview") or {}
                return {
                    "appid": int(appid),
                    "name": data.get("name") or UNKNOWN_GAME_NAME,
                    "header_image": data.get("header_image"),
                    "current_players": players,
                    "peak_players": max(players, known_peaks.get(int(appid), 0)),
                    "review_score": round((positive / total) * 100, 2) if total else None,
                    "total_reviews": total,
                    "cn_price": price.get("final_formatted", "Free") if price or data.get("is_free") else None,
                    "cn_price_final": price.get("final", 0) if price or data.get("is_free") else None,
                    "cn_price_currency": price.get("currency") or ("CNY" if data.get("is_free") else None),
                    "cn_discount_percent": price.get("discount_percent", 0),
                    "is_free": 1 if data.get("is_free") else 0,
                    "release_date": (data.get("release_date") or {}).get("date"),
                    "fetched_at": stamp,
                }
            except SteamRateLimited:
                raise
            except ExternalDataUnavailable:
                # Valid catalog entries can still lack player statistics. The
                # caller marks them skipped without creating error-log noise.
                return None
            except Exception as exc:
                log_event(f"niche candidate skipped appid={appid}: {exc}")
                return None

        results = await asyncio.gather(*(fetch_one(appid) for appid in appids), return_exceptions=True)
        if any(isinstance(result, SteamRateLimited) for result in results):
            raise SteamRateLimited("Steam niche requests paused by global cooldown")
        for result in results:
            if result:
                rows.append(result)
    return rows


def niche_weighted_score(row):
    score = float(row.get("review_score") or 0)
    reviews = max(0, int(row.get("total_reviews") or 0))
    players = int(row.get("current_players") or 0)
    peak_players = max(players, int(row.get("peak_players") or 0))
    review_part = max(0.0, min(1.0, (score - 85.0) / 15.0))
    review_count_part = min(1.0, math.log1p(reviews) / math.log1p(100000))
    player_part = 1.0 / (1.0 + (max(0, players - 1000) / 1000.0))
    peak_part = min(1.0, math.log1p(peak_players) / math.log1p(2000))
    return round((review_part * 0.45) + (review_count_part * 0.30) + (peak_part * 0.20) + (player_part * 0.05), 6)


def upsert_niche_pool_rows(rows):
    if not rows:
        return 0
    evaluated_at = now_iso()
    prepared = []
    for row in rows:
        eligible = bool(
            int(row.get("current_players") or 0) >= 10
            and 0 < int(row.get("peak_players") or 0) <= 2000
            and float(row.get("review_score") or 0) >= 85
            and int(row.get("total_reviews") or 0) > 0
            and is_recent_release(row.get("release_date"))
        )
        prepared.append((row, niche_weighted_score(row) if eligible else 0.0, 1 if eligible else 0))
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    return len(prepared)


def seed_niche_pool_from_local(limit=NICHE_POOL_BATCH_LIMIT, scan_limit=None):
    """Promote already-collected server data into the independent pool."""
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
            WHERE g.header_image IS NOT NULL
              AND g.name IS NOT NULL
              AND g.name != ?
            ORDER BY g.updated_at DESC
            LIMIT ?
            """,
            (UNKNOWN_GAME_NAME, scan_limit or max(limit * 4, 1000)),
        ).fetchall()
    return upsert_niche_pool_rows([dict(row) for row in rows])


def list_niche_pool_pick():
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM niche_pool WHERE eligible = 1 AND cn_price IS NOT NULL ORDER BY weighted_score DESC LIMIT 30"
        ).fetchall()
        if not rows:
            return None
        # Keep the daily result server-side. A weighted draw from the top 30
        # avoids showing the same top-ranked game every day.
        today = datetime.now().strftime("%Y-%m-%d")
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
    """Refresh the lightweight AppList catalog; no store details are fetched here."""
    today = datetime.now().strftime("%Y-%m-%d")
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        last_sync = get_crawl_state(conn, "steam_catalog_sync_date")
    if not force and last_sync == today:
        return False
    rows = []
    stamp = now_iso()
    last_appid = 0
    while len(rows) < STEAM_CATALOG_LIMIT:
        payload = fetch_store_catalog_page(last_appid, min(500, STEAM_CATALOG_LIMIT - len(rows)))
        response = (payload or {}).get("response") or payload or {}
        apps = response.get("apps") or response.get("items") or []
        if not apps:
            break
        previous_last = last_appid
        for item in apps:
            try:
                appid = int(item.get("appid") or item.get("id"))
            except (TypeError, ValueError, AttributeError):
                continue
            name = clean_hot_name(item.get("name"))
            if appid <= 0 or not name:
                continue
            rows.append((appid, name, stamp))
            if len(rows) >= STEAM_CATALOG_LIMIT:
                break
        last_appid = int(response.get("last_appid") or response.get("lastAppId") or 0)
        if not response.get("have_more_results") or not last_appid or last_appid <= previous_last or len(apps) < 500:
            break
    if not rows:
        log_event("steam catalog sync skipped: AppList returned no usable rows")
        return False
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        conn.executemany(
            """
            INSERT INTO steam_catalog(appid, name, updated_at, enrich_status)
            VALUES (?, ?, ?, 'pending')
            ON CONFLICT(appid) DO UPDATE SET
                name=excluded.name,
                updated_at=excluded.updated_at,
                enrich_status=CASE WHEN steam_catalog.last_enriched_at IS NULL THEN 'pending' ELSE steam_catalog.enrich_status END
            """,
            rows,
        )
        set_crawl_state(conn, "steam_catalog_sync_date", today)
    log_event(f"steam catalog synced rows={len(rows)} limit={STEAM_CATALOG_LIMIT}")
    return True


def fetch_store_catalog_page(last_appid=0, max_results=500):
    params = {"max_results": max(1, min(500, int(max_results)))}
    if last_appid:
        params["last_appid"] = int(last_appid)
    if STEAM_API_KEY:
        params["key"] = STEAM_API_KEY
    query = urllib.parse.urlencode(params)
    last_error = None
    for host in ("https://api.steampowered.com", "https://partner.steam-api.com"):
        try:
            return request_json(
                f"{host}/IStoreService/GetAppList/v1/?{query}",
                timeout=max(15, STEAM_TIMEOUT_SECONDS),
                max_retries=1,
            )
        except Exception as exc:
            last_error = exc
            log_event(f"store catalog endpoint failed host={host}: {exc}")
    raise ExternalDataUnavailable(str(last_error or "store catalog unavailable"))


def catalog_enrich_quota():
    today = datetime.now().strftime("%Y-%m-%d")
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        saved_date = get_crawl_state(conn, "steam_catalog_enrich_date")
        saved_count = int(get_crawl_state(conn, "steam_catalog_enrich_count") or 0) if saved_date == today else 0
    return today, saved_count


def run_catalog_enrich_task():
    today, used = catalog_enrich_quota()
    remaining = CATALOG_ENRICH_DAILY_LIMIT - used
    if remaining <= 0:
        return False
    limit = min(CATALOG_ENRICH_BATCH_LIMIT, remaining)
    now = now_iso()
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        rows = conn.execute(
            """
            SELECT appid
            FROM steam_catalog
            WHERE (
                enrich_status IN ('pending', 'retry')
                OR (enrich_status = 'done' AND next_enrich_at <= ?)
            )
            ORDER BY
              CASE WHEN last_enriched_at IS NULL THEN 0 ELSE 1 END,
              enrich_attempts ASC, updated_at ASC
            LIMIT ?
            """,
            (now, limit),
        ).fetchall()
    appids = [int(row[0]) for row in rows]
    if not appids:
        return False
    try:
        with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
            conn.executemany(
                "UPDATE steam_catalog SET enrich_status='running', enrich_attempts=enrich_attempts+1 WHERE appid = ?",
                [(appid,) for appid in appids],
            )
        fetched = asyncio.run(fetch_niche_candidates_async(appids))
        saved = upsert_niche_pool_rows(fetched)
        success_ids = {int(row["appid"]) for row in fetched}
        next_week = (datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=0).isoformat()
        with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
            conn.executemany(
                """
                UPDATE steam_catalog
                SET enrich_status = ?, last_enriched_at = ?, next_enrich_at = ?, last_error = NULL
                WHERE appid = ?
                """,
                [("done" if appid in success_ids else "skipped", now, next_week, appid) for appid in appids],
            )
            set_crawl_state(conn, "steam_catalog_enrich_date", today)
            set_crawl_state(conn, "steam_catalog_enrich_count", str(used + len(appids)))
        log_event(f"catalog enrich batch attempted={len(appids)} saved={saved} daily={used + len(appids)}/{CATALOG_ENRICH_DAILY_LIMIT}")
        return True
    except SteamRateLimited as exc:
        with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
            conn.executemany(
                "UPDATE steam_catalog SET enrich_status='retry', next_enrich_at=?, last_error=? WHERE appid=?",
                [((datetime.now(timezone.utc) + timedelta(minutes=10)).replace(microsecond=0).isoformat(), str(exc), appid) for appid in appids],
            )
        log_event(f"catalog enrich paused by rate limit: {exc}")
        return False
    except sqlite3.Error:
        raise
    except Exception as exc:
        with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
            conn.executemany(
                "UPDATE steam_catalog SET enrich_status='retry', next_enrich_at=?, last_error=? WHERE appid=?",
                [((datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).isoformat(), str(exc), appid) for appid in appids],
            )
        log_event(f"catalog enrich failed: {exc}")
        return False


def snapshot_daily_niche_recommendation():
    today = datetime.now().strftime("%Y-%m-%d")
    if datetime.now().hour == 0 and datetime.now().minute < 10:
        return False
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        if conn.execute("SELECT 1 FROM niche_recommendation_snapshots WHERE recommendation_date = ?", (today,)).fetchone():
            return False
    chosen = list_niche_pool_pick()
    if not chosen:
        return False
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    today = datetime.now().strftime("%Y-%m-%d")
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT n.*, g.tracked FROM niche_recommendation_snapshots n LEFT JOIN games g ON g.appid=n.appid WHERE recommendation_date=?",
            (today,),
        ).fetchone()
        if not row:
            return None
        pool = conn.execute("SELECT * FROM niche_pool WHERE appid=?", (row["appid"],)).fetchone()
    if not pool or not pool["eligible"] or not pool["cn_price"]:
        with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
            conn.execute("DELETE FROM niche_recommendation_snapshots WHERE recommendation_date = ?", (today,))
        snapshot_daily_niche_recommendation()
        return None
    item = dict(pool)
    item["tracked"] = bool(row["tracked"])
    item["cn_historical_low_cny"] = None
    return clean_home_pick(item)


def refresh_niche_pool_scores_from_snapshots():
    """Apply the latest 30-minute player snapshots without refetching metadata."""
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
            SELECT n.appid, n.review_score, n.total_reviews, n.current_players, n.peak_players, n.release_date
            FROM niche_pool n
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
            eligible = int(players >= 10 and 0 < peak_players <= 2000 and float(row["review_score"] or 0) >= 85 and int(row["total_reviews"] or 0) > 0 and is_recent_release(row["release_date"]))
            score = niche_weighted_score({
                "current_players": players,
                "peak_players": peak_players,
                "review_score": row["review_score"],
                "total_reviews": row["total_reviews"],
            }) if eligible else 0.0
            updates.append((players, peak_players, score, eligible, now_iso(), row["appid"]))
        conn.executemany(
            "UPDATE niche_pool SET current_players = ?, peak_players = ?, weighted_score = ?, eligible = ?, evaluated_at = ? WHERE appid = ?",
            updates,
        )
        conn.execute("DELETE FROM niche_pool WHERE eligible = 0")
    return len(updates)


def get_hot_appids(limit=HOTLIST_TARGET):
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        rows = conn.execute(
            "SELECT appid FROM hot_games ORDER BY COALESCE(current_players, 0) DESC, COALESCE(rank, 999999) LIMIT ?",
            (limit,),
        ).fetchall()
    return [row[0] for row in rows]


def get_due_hot_player_appids():
    """Refresh top games more often without turning lower ranks into a request storm."""
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        generation = int(get_crawl_state(conn, "hotlist_generation") or 0)
    enqueue_crawl_tasks(top_appids, "players", 20, generation=generation)
    enqueue_crawl_tasks(get_hot_preview_due_appids(HOT_PREVIEW_TOP_LIMIT), "preview", 50, generation=generation)
    enqueue_crawl_tasks(get_hot_review_due_appids(HOT_PREVIEW_TOP_LIMIT), "reviews", 50, generation=generation)
    enqueue_crawl_tasks(get_hot_full_metadata_due_appids(HOT_FULL_METADATA_TOP_LIMIT), "metadata", 80, generation=generation)
    enqueue_crawl_tasks(get_missing_historylow_appids(ITAD_HISTORYLOW_BATCH_LIMIT), "historylow", 10, generation=generation)


def run_hotlist_task(force=False):
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        hotlist_at = get_crawl_state(conn, "hotlist_at")
    if not (force or is_due(hotlist_at, HOTLIST_REFRESH_HOURS * 60)):
        return False
    rows = asyncio.run(fetch_official_hotlist_async())
    if not rows:
        log_event("hotlist refresh skipped: no rows returned")
        return False
    stamp = now_iso()
    for batch in chunks(rows[:HOTLIST_TARGET], HOTLIST_BATCH_SIZE):
        upsert_hot_games_batch(batch, stamp)
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        appids = [int(row["appid"]) for row in rows[:HOTLIST_TARGET]]
        if appids:
            conn.execute(
                f"DELETE FROM hot_games WHERE appid NOT IN ({','.join('?' for _ in appids)})",
                appids,
            )
        set_crawl_state(conn, "hotlist_at", stamp)
        generation = int(get_crawl_state(conn, "hotlist_generation") or 0) + 1
        set_crawl_state(conn, "hotlist_generation", str(generation))
    enqueue_hot_work()
    try:
        refresh_steam_app_names_once()
    except Exception as exc:
        log_event(f"steam app names refresh skipped: {exc}")
    queued_previews = enqueue_missing_hot_previews(limit=HOT_FULL_METADATA_TOP_LIMIT, priority=90)
    if queued_previews:
        log_event(f"hot preview metadata queued rows={queued_previews}")
    log_event(f"hotlist refreshed rows={len(rows[:HOTLIST_TARGET])}")
    return True


def run_players_task(force=False):
    if steam_cooldown_remaining_seconds():
        return False
    appids = get_hot_appids(HOTLIST_TARGET) if force else get_due_hot_player_appids()
    if not appids:
        return False
    report = asyncio.run(fetch_players_for_appids_async(appids))
    complete_crawl_tasks(report["success_appids"], "players")
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        set_crawl_state(conn, "hot_players_at", report["stamp"])
    log_event(f"hot players refreshed success={report['success']} failed={report['failed']} skipped={report['skipped']}")
    return True


def run_price_task():
    enqueue_crawl_tasks(get_hot_price_due_appids(HOT_PREVIEW_BATCH_LIMIT), "price", 50)
    appids = claim_crawl_tasks("price", HOT_PREVIEW_BATCH_LIMIT)
    if not appids:
        return False
    try:
        rows, unavailable, retry, stamp = asyncio.run(fetch_hot_metadata_async(appids, full=False, include_reviews=False))
        upsert_hot_price_batch(rows, stamp)
        complete_crawl_tasks([row["appid"] for row in rows], "price")
        mark_crawl_tasks_not_available(unavailable, "price", "Steam AppDetails unavailable")
        fail_crawl_tasks(retry, "price", "Steam AppDetails request failed")
        log_event(f"hot prices refreshed success={len(rows)} unavailable={len(unavailable)} retry={len(retry)}")
        return True
    except sqlite3.Error as exc:
        fail_crawl_tasks(appids, "price", exc, terminal=True)
        raise
    except SteamRateLimited as exc:
        fail_crawl_tasks(appids, "price", exc, retry_minutes=10)
        raise
    except Exception as exc:
        fail_crawl_tasks(appids, "price", exc)
        raise


def run_preview_task():
    if steam_cooldown_remaining_seconds():
        return False
    enqueue_crawl_tasks(get_hot_preview_due_appids(HOT_PREVIEW_BATCH_LIMIT), "preview", 50)
    appids = claim_crawl_tasks("preview", HOT_PREVIEW_BATCH_LIMIT)
    if not appids:
        return False
    try:
        rows, unavailable, retry, stamp = asyncio.run(fetch_hot_metadata_async(appids, full=False, include_reviews=False))
        upsert_hot_price_batch(rows, stamp)
        upsert_release_date_batch(rows, stamp)
        complete_crawl_tasks([row["appid"] for row in rows], "preview")
        mark_crawl_tasks_not_available(unavailable, "preview", "Steam AppDetails unavailable")
        fail_crawl_tasks(retry, "preview", "Steam AppDetails request failed")
        log_event(f"hot preview refreshed success={len(rows)} unavailable={len(unavailable)} retry={len(retry)}")
        return True
    except sqlite3.Error as exc:
        fail_crawl_tasks(appids, "preview", exc, terminal=True)
        raise
    except SteamRateLimited as exc:
        fail_crawl_tasks(appids, "preview", exc, retry_minutes=10)
        raise
    except Exception as exc:
        fail_crawl_tasks(appids, "preview", exc)
        raise


def run_review_task():
    enqueue_crawl_tasks(get_hot_review_due_appids(HOT_PREVIEW_BATCH_LIMIT), "reviews", 50)
    appids = claim_crawl_tasks("reviews", HOT_PREVIEW_BATCH_LIMIT)
    if not appids:
        return False
    try:
        rows, unavailable, retry, stamp = asyncio.run(fetch_hot_reviews_async(appids))
        upsert_review_batch(rows, stamp)
        complete_crawl_tasks([row["appid"] for row in rows], "reviews")
        mark_crawl_tasks_not_available(unavailable, "reviews", "Steam reviews unavailable")
        fail_crawl_tasks(retry, "reviews", "Steam review request failed")
        log_event(f"hot reviews refreshed success={len(rows)} unavailable={len(unavailable)} retry={len(retry)}")
        return True
    except sqlite3.Error as exc:
        fail_crawl_tasks(appids, "reviews", exc, terminal=True)
        raise
    except SteamRateLimited as exc:
        fail_crawl_tasks(appids, "reviews", exc, retry_minutes=10)
        raise
    except Exception as exc:
        fail_crawl_tasks(appids, "reviews", exc)
        raise


def run_static_task():
    enqueue_crawl_tasks(get_hot_static_due_appids(HOT_PREVIEW_BATCH_LIMIT), "static", 50)
    appids = claim_crawl_tasks("static", HOT_PREVIEW_BATCH_LIMIT)
    if not appids:
        return False
    try:
        rows, unavailable, retry, stamp = asyncio.run(fetch_hot_metadata_async(appids, full=False, include_reviews=False))
        upsert_release_date_batch(rows, stamp)
        complete_crawl_tasks([row["appid"] for row in rows], "static")
        mark_crawl_tasks_not_available(unavailable, "static", "Steam AppDetails unavailable")
        fail_crawl_tasks(retry, "static", "Steam AppDetails request failed")
        log_event(f"hot static fields refreshed success={len(rows)} unavailable={len(unavailable)} retry={len(retry)}")
        return True
    except sqlite3.Error as exc:
        fail_crawl_tasks(appids, "static", exc, terminal=True)
        raise
    except SteamRateLimited as exc:
        fail_crawl_tasks(appids, "static", exc, retry_minutes=10)
        raise
    except Exception as exc:
        fail_crawl_tasks(appids, "static", exc)
        raise


def run_metadata_task():
    enqueue_crawl_tasks(get_hot_full_metadata_due_appids(HOT_METADATA_BATCH_LIMIT), "metadata", 80)
    appids = claim_crawl_tasks("metadata", HOT_METADATA_BATCH_LIMIT)
    if not appids:
        return False
    try:
        rows, unavailable, retry, stamp = asyncio.run(fetch_hot_metadata_async(appids, full=True, include_reviews=False))
        upsert_hot_metadata_batch(rows, stamp)
        complete_crawl_tasks([row["appid"] for row in rows], "metadata")
        mark_crawl_tasks_not_available(unavailable, "metadata", "Steam AppDetails unavailable")
        fail_crawl_tasks(retry, "metadata", "Steam AppDetails request failed")
        log_event(f"hot metadata refreshed success={len(rows)} unavailable={len(unavailable)} retry={len(retry)}")
        return True
    except sqlite3.Error as exc:
        fail_crawl_tasks(appids, "metadata", exc, terminal=True)
        raise
    except SteamRateLimited as exc:
        fail_crawl_tasks(appids, "metadata", exc, retry_minutes=10)
        raise
    except Exception as exc:
        fail_crawl_tasks(appids, "metadata", exc)
        raise


def run_historylow_task():
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        historylow_backfill_at = get_crawl_state(conn, "historylow_backfill_at")
    if not is_due(historylow_backfill_at, 30):
        return False
    enqueue_crawl_tasks(get_missing_historylow_appids(ITAD_HISTORYLOW_BATCH_LIMIT), "historylow", 30)
    appids = claim_crawl_tasks("historylow", ITAD_HISTORYLOW_BATCH_LIMIT)
    if not appids:
        return False
    try:
        refresh_itad_history_lows(appids)
        complete_crawl_tasks(appids, "historylow")
        with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
            set_crawl_state(conn, "historylow_backfill_at", now_iso())
        log_event(f"itad historylow backfilled rows={len(appids)}")
        return True
    except sqlite3.Error as exc:
        fail_crawl_tasks(appids, "historylow", exc, terminal=True)
        raise
    except SteamRateLimited as exc:
        fail_crawl_tasks(appids, "historylow", exc, retry_minutes=10)
        raise
    except Exception as exc:
        fail_crawl_tasks(appids, "historylow", exc)
        raise


def run_niche_pool_task(force=False):
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
        with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
            refreshed_at = get_crawl_state(conn, "niche_pool_at")
        if not force and not is_due(refreshed_at, refresh_minutes):
            return False
        local_count = seed_niche_pool_from_local(
            NICHE_POOL_BATCH_LIMIT,
            scan_limit=1000 if force and pool_count < NICHE_POOL_DISPLAY_LIMIT else None,
        )
        batches = 2 if force and count_eligible_niche_pool() < NICHE_POOL_DISPLAY_LIMIT else 1
        saved, attempted = 0, 0
        for _ in range(batches):
            appids = discover_niche_appids(NICHE_POOL_BATCH_LIMIT)
            if not appids:
                break
            attempted += len(appids)
            rows = asyncio.run(fetch_niche_candidates_async(appids))
            saved += upsert_niche_pool_rows(rows)
            fetched_ids = {int(row["appid"]) for row in rows}
            unavailable_ids = [appid for appid in appids if appid not in fetched_ids]
            if unavailable_ids:
                # Catalog candidates that fail validity/player checks should
                # not be selected again by the next bootstrap batch.
                next_week = (datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=0).isoformat()
                with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
        with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM niche_pool WHERE eligible = 1").fetchone()[0] or 0)


def compact_player_snapshots_once():
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        set_crawl_state(conn, "log_rotation_date", today)
    return True


def compact_price_snapshots_once():
    """Keep recent prices precise, then reduce old history to daily/monthly points."""
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
        set_crawl_state(conn, "old_records_cleaned_at", now_iso())
    log_event("old crawl tasks and recommendation snapshots cleaned")
    return True


def maintain_storage_once():
    rotate_logs_once()
    compact_price_snapshots_once()
    cleanup_old_records_once()
    cleanup_image_cache_once()


def refresh_hot_database_once(force_hotlist=False, quick=False):
    remaining = steam_cooldown_remaining_seconds()
    if remaining:
        log_event(f"hot refresh deferred: Steam cooldown remaining={remaining}s")
        return ["Steam cooldown active"]
    if not HOT_REFRESH_LOCK.acquire(blocking=False):
        return ["hot refresh already running"]
    with STATUS_LOCK:
        REFRESH_STATUS["hot_running"] = True
        REFRESH_STATUS["hot_last_started_at"] = now_iso()
        REFRESH_STATUS["hot_last_errors"] = []
    errors = []
    try:
        run_hotlist_task(force=force_hotlist)
        if not quick:
            run_players_task()
            run_preview_task()
            run_review_task()
            run_metadata_task()
            run_historylow_task()
            run_niche_pool_task()
            try:
                sync_steam_catalog_once()
            except Exception as exc:
                log_event(f"steam catalog sync skipped: {exc}")
            run_catalog_enrich_task()
            snapshot_daily_niche_recommendation()
            compact_player_snapshots_once()
            maintain_storage_once()
    except Exception as exc:
        message = str(exc)
        errors.append(message)
        log_event(f"hot refresh failed: {message}")
    finally:
        with STATUS_LOCK:
            REFRESH_STATUS["hot_running"] = False
            REFRESH_STATUS["hot_last_finished_at"] = now_iso()
            REFRESH_STATUS["hot_last_errors"] = errors[:20]
        HOT_REFRESH_LOCK.release()
    return errors


def count_hot_games():
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM hot_games").fetchone()[0] or 0)


def hot_games_version():
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        row = conn.execute(
            """
            SELECT MAX(COALESCE(s.updated_at, h.fetched_at, ''))
            FROM hot_games h
            LEFT JOIN game_latest_state s ON s.appid = h.appid
            """
        ).fetchone()
    return row[0] or ""


def refresh_hot_database_async(force_hotlist=False, quick=False):
    if HOT_REFRESH_LOCK.locked():
        return False

    def worker():
        refresh_hot_database_once(force_hotlist=force_hotlist, quick=quick)

    threading.Thread(target=worker, daemon=True).start()
    return True


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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
    qs = urllib.parse.urlencode({"appids": appid, "cc": region, "l": "schinese"})
    url = f"https://store.steampowered.com/api/appdetails?{qs}"
    payload = request_json(url)
    record = payload.get(str(appid)) or {}
    if not record.get("success"):
        return None
    return record.get("data") or {}


def fetch_players(appid):
    qs = urllib.parse.urlencode({"appid": appid})
    url = f"https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?{qs}"
    payload = request_json(url)
    return int((payload.get("response") or {}).get("player_count") or 0)


def fetch_reviews(appid):
    qs = urllib.parse.urlencode(
        {
            "json": 1,
            "language": "all",
            "purchase_type": "all",
            "num_per_page": 0,
            "filter": "summary",
        }
    )
    url = f"https://store.steampowered.com/appreviews/{appid}?{qs}"
    payload = request_json(url)
    summary = payload.get("query_summary") or {}
    total_positive = int(summary.get("total_positive") or 0)
    total_negative = int(summary.get("total_negative") or 0)
    total = total_positive + total_negative
    score = round((total_positive / total) * 100) if total else None
    return {
        "review_score": score,
        "review_score_desc": summary.get("review_score_desc"),
        "total_positive": total_positive,
        "total_negative": total_negative,
        "total_reviews": total,
    }


def fetch_itad_prices(appid):
    if not ITAD_API_KEY:
        return []
    qs = urllib.parse.urlencode({"key": ITAD_API_KEY, "shop": "steam", "ids": f"app/{appid}", "region": "us"})
    url = f"https://api.isthereanydeal.com/v01/game/prices/?{qs}"
    try:
        payload = request_json(url, missing_statuses={404})
    except ExternalDataUnavailable:
        log_event(f"itad prices unavailable appid={appid}")
        return []
    data = payload.get("data") or {}
    rows = []
    for item in data.values():
        for deal in item.get("list") or []:
            price = deal.get("price_new")
            if price is not None:
                rows.append(
                    {
                        "region": "ITAD-US",
                        "currency": "USD",
                        "initial": int(float(deal.get("price_old") or price) * 100),
                        "final": int(float(price) * 100),
                        "discount_percent": int(deal.get("price_cut") or 0),
                        "final_formatted": f"${float(price):.2f}",
                        "source": "itad",
                    }
                )
    return rows


def refresh_itad_history_lows(appids):
    if not ITAD_API_KEY:
        return None
    try:
        return asyncio.run(fetch_itad_history_lows_async(appids))
    except Exception as exc:
        log_event(f"itad historylow failed appids={','.join(str(appid) for appid in appids[:5])}: {exc}")
        return None


def get_missing_historylow_appids(limit=ITAD_HISTORYLOW_BATCH_LIMIT):
    if not ITAD_API_KEY:
        return []
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT g.appid
            FROM games g
            JOIN price_snapshots ps ON ps.appid = g.appid
            LEFT JOIN historical_lows us_low ON us_low.appid = g.appid AND us_low.country = 'US'
            LEFT JOIN historical_lows cn_low ON cn_low.appid = g.appid AND cn_low.country = 'CN'
            WHERE ps.source = 'steam'
              AND (us_low.appid IS NULL OR cn_low.appid IS NULL)
              AND COALESCE(g.itad_game_id, '') != ?
            ORDER BY g.tracked DESC, g.updated_at DESC
            LIMIT ?
            """,
            (ITAD_MISSING_GAME_ID, limit),
        ).fetchall()
    return [int(row[0]) for row in rows]


def refresh_missing_history_lows_once():
    appids = get_missing_historylow_appids()
    if not appids:
        return []
    with STATUS_LOCK:
        REFRESH_STATUS["historylow_running"] = True
    try:
        stamp = refresh_itad_history_lows(appids)
        log_event(f"itad historylow backfilled rows={len(appids)} stamp={stamp}")
        return appids
    finally:
        with STATUS_LOCK:
            REFRESH_STATUS["historylow_running"] = False


def backfill_historylow_async(appid):
    if not ITAD_API_KEY:
        return
    appid = int(appid)
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        if not is_due(get_crawl_state(conn, historylow_attempt_key(appid)), PRICE_REFRESH_HOURS * 60):
            return
        set_crawl_state(conn, historylow_attempt_key(appid), now_iso())
    with HISTORYLOW_BACKFILL_LOCK:
        if appid in HISTORYLOW_BACKFILLING:
            return
        HISTORYLOW_BACKFILLING.add(appid)
    with STATUS_LOCK:
        REFRESH_STATUS["historylow_running"] = True

    def worker():
        try:
            refresh_itad_history_lows([appid])
        finally:
            with HISTORYLOW_BACKFILL_LOCK:
                HISTORYLOW_BACKFILLING.discard(appid)
                still_running = bool(HISTORYLOW_BACKFILLING)
            with STATUS_LOCK:
                REFRESH_STATUS["historylow_running"] = still_running

    threading.Thread(target=worker, daemon=True).start()


def backfill_preview_async(appid, name=None):
    appid = int(appid)
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
            refresh_game(
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

        if include_details or include_prices or include_reviews:
            try:
                details = fetch_appdetails(appid, "US")
            except Exception as exc:
                log_event(f"appdetails failed appid={appid} region=US: {exc}")
                errors.append(f"details: {exc}")

        if include_prices:
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
                except Exception as exc:
                    log_event(f"price skipped appid={appid} region={region}: {exc}")
                    errors.append(f"price {region}: {exc}")

        if include_players:
            try:
                players = fetch_players(appid)
            except Exception as exc:
                log_event(f"players skipped appid={appid}: {exc}")

        if include_reviews:
            try:
                reviews = fetch_reviews(appid)
            except Exception as exc:
                log_event(f"reviews failed appid={appid}: {exc}")
                errors.append(f"reviews: {exc}")

        if include_prices:
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

        with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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

        return {"appid": appid, "fetched_at": stamp, "errors": errors}


def backfill_details_async(appid, name=None):
    appid = int(appid)
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
            refresh_game(
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
        with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
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
            if include_details:
                with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
                    set_crawl_state(conn, detail_attempt_key(appid), now_iso())
            if include_details or include_prices or include_reviews:
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
    time.sleep(SCHEDULER_CHECK_SECONDS)
    while True:
        remaining = steam_cooldown_remaining_seconds()
        if remaining:
            # One scheduler tick is enough: do not wake every worker into the
            # same cooldown exception while Steam is explicitly paused.
            time.sleep(min(SCHEDULER_CHECK_SECONDS, max(1, remaining)))
            continue
        try:
            snapshot_daily_niche_recommendation()
        except Exception as exc:
            log_event(f"daily niche snapshot failed: {exc}")
        try:
            refresh_tracked_once()
        except Exception as exc:
            log_event(f"scheduler tracked refresh failed: {exc}")
        try:
            refresh_hot_database_once()
        except Exception as exc:
            log_event(f"scheduler hot refresh failed: {exc}")
        time.sleep(SCHEDULER_CHECK_SECONDS)


def startup_prewarm_async():
    def worker():
        try:
            refresh_hot_database_once(force_hotlist=count_hot_games() == 0, quick=True)
            # A daily pool should not remain nearly empty merely because the
            # previous scheduled pass happened while Steam was unreachable.
            run_niche_pool_task(force=count_eligible_niche_pool() < NICHE_POOL_DISPLAY_LIMIT)
        except Exception as exc:
            log_event(f"startup hotlist prewarm failed: {exc}")

    threading.Thread(target=worker, daemon=True).start()


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
    status["proxy"] = dict(PROXY_STATUS)
    try:
        with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
            status["historical_low_count"] = conn.execute("SELECT COUNT(*) FROM historical_lows").fetchone()[0]
            status["niche_pool_count"] = conn.execute("SELECT COUNT(*) FROM niche_pool WHERE eligible = 1").fetchone()[0]
            status["steam_catalog_count"] = conn.execute("SELECT COUNT(*) FROM steam_catalog").fetchone()[0]
            status["steam_catalog_enriched_count"] = conn.execute("SELECT COUNT(*) FROM steam_catalog WHERE last_enriched_at IS NOT NULL").fetchone()[0]
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
    status["catalog_enrich_daily_limit"] = CATALOG_ENRICH_DAILY_LIMIT
    status["catalog_enrich_batch_limit"] = CATALOG_ENRICH_BATCH_LIMIT
    status["niche_pool_limit"] = NICHE_POOL_LIMIT
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
        "badges": list_badges(item.get("appid")),
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
    rows = conn.execute(
        """
        SELECT ps.region,
               ps.currency,
               ps.initial,
               ps.final,
               ps.discount_percent,
               ps.final_formatted,
               ps.source,
               ps.fetched_at,
               hl.amount_cny AS historical_low_cny,
               hl.currency AS historical_low_currency,
               hl.amount_int AS historical_low_amount_int,
               hl.low_at AS historical_low_at
        FROM price_snapshots ps
        JOIN (
            SELECT region, MAX(fetched_at) AS fetched_at
            FROM price_snapshots
            WHERE appid = ?
            GROUP BY region
        ) latest ON latest.region = ps.region AND latest.fetched_at = ps.fetched_at
        LEFT JOIN historical_lows hl ON hl.appid = ps.appid
            AND hl.country = CASE
                WHEN ps.region = 'CN' THEN 'CN'
                ELSE 'US'
            END
        WHERE ps.appid = ?
        ORDER BY ps.region
        """,
        (appid, appid),
    ).fetchall()
    return [
        clean_price(row)
        for row in rows
    ]


def normalized_history_limit(value):
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return 500
    return min(2000, max(1, limit))


def ensure_game_from_catalog(conn, appid):
    row = conn.execute("SELECT appid, name FROM steam_catalog WHERE appid = ?", (appid,)).fetchone()
    if not row:
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
    enqueue_crawl_tasks_in_conn(conn, [appid], "metadata", 100)
    enqueue_crawl_tasks_in_conn(conn, [appid], "preview", 100)
    enqueue_crawl_tasks_in_conn(conn, [appid], "reviews", 100)
    conn.commit()
    return True


def get_game_payload(appid, history_limit=500):
    history_limit = normalized_history_limit(history_limit)
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        conn.row_factory = sqlite3.Row
        game = conn.execute("SELECT * FROM games WHERE appid = ?", (appid,)).fetchone()
        if not game:
            ensure_game_from_catalog(conn, appid)
            game = conn.execute("SELECT * FROM games WHERE appid = ?", (appid,)).fetchone()
        if not game:
            return None
        prices = latest_by_region(conn, appid)
        price_history = conn.execute(
            """
            SELECT region, currency, initial, final, discount_percent, final_formatted, source, fetched_at
            FROM (
                SELECT region, currency, initial, final, discount_percent, final_formatted, source, fetched_at
                FROM price_snapshots
                WHERE appid = ? AND region IN ('US', 'CN', 'ITAD-US')
                ORDER BY fetched_at DESC
                LIMIT ?
            )
            ORDER BY fetched_at ASC
            """,
            (appid, history_limit),
        ).fetchall()
        players = conn.execute(
            """
            SELECT player_count, fetched_at
            FROM (
                SELECT player_count, fetched_at
                FROM player_snapshots
                WHERE appid = ?
                ORDER BY fetched_at DESC
                LIMIT ?
            )
            ORDER BY fetched_at ASC
            """,
            (appid, history_limit),
        ).fetchall()
        reviews = conn.execute(
            """
            SELECT review_score, review_score_desc, total_positive, total_negative, total_reviews, fetched_at
            FROM review_snapshots
            WHERE appid = ?
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            (appid,),
        ).fetchone()
        game_payload = clean_game(game)
        site_peak = conn.execute(
            "SELECT MAX(player_count) AS peak_players, MIN(fetched_at) AS recorded_since FROM player_snapshots WHERE appid = ?",
            (appid,),
        ).fetchone()
        game_payload["site_peak_players"] = site_peak[0] if site_peak else None
        game_payload["site_peak_recorded_since"] = site_peak[1] if site_peak else None
        missing_core_data = not game_payload.get("short_description") or not prices or not players or not reviews
        if missing_core_data:
            enqueue_crawl_tasks_in_conn(conn, [appid], "preview", 100)
            enqueue_crawl_tasks_in_conn(conn, [appid], "reviews", 100)
            enqueue_crawl_tasks_in_conn(conn, [appid], "metadata", 100)
            conn.commit()
            backfill_preview_async(appid, game_payload.get("name"))
        has_historical_low = conn.execute(
            "SELECT 1 FROM historical_lows WHERE appid = ? LIMIT 1",
            (appid,),
        ).fetchone()
        if not has_historical_low:
            enqueue_crawl_tasks_in_conn(conn, [appid], "historylow", 100)
            conn.commit()
            backfill_historylow_async(appid)
        return {
            "game": game_payload,
            "prices": prices,
            "priceHistory": [clean_price(row) for row in price_history],
            "players": [clean_player(row) for row in players],
            "reviews": clean_review(reviews),
        }


def list_games():
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT g.*,
                   (SELECT player_count FROM player_snapshots WHERE appid = g.appid ORDER BY fetched_at DESC LIMIT 1) AS player_count,
                   (SELECT review_score FROM review_snapshots WHERE appid = g.appid ORDER BY fetched_at DESC LIMIT 1) AS review_score,
                   (SELECT final_formatted FROM price_snapshots WHERE appid = g.appid AND region = 'CN' ORDER BY fetched_at DESC LIMIT 1) AS cn_price,
                   (SELECT final FROM price_snapshots WHERE appid = g.appid AND region = 'CN' ORDER BY fetched_at DESC LIMIT 1) AS cn_price_final,
                   (SELECT currency FROM price_snapshots WHERE appid = g.appid AND region = 'CN' ORDER BY fetched_at DESC LIMIT 1) AS cn_price_currency,
                   (SELECT discount_percent FROM price_snapshots WHERE appid = g.appid AND region = 'CN' ORDER BY fetched_at DESC LIMIT 1) AS cn_discount_percent,
                   (SELECT amount_cny FROM historical_lows WHERE appid = g.appid AND country = 'CN' LIMIT 1) AS cn_historical_low_cny
            FROM games g
            WHERE tracked = 1
            ORDER BY player_count DESC, name ASC
            """
        ).fetchall()
        return [clean_game(row, summary=True) for row in rows]


def list_hot_games(limit=100):
    limit = min(max(1, int(limit)), HOTLIST_TARGET)
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            WITH hot AS (
                SELECT h.appid,
                       h.rank AS original_rank,
                       CASE
                           WHEN g.name IS NOT NULL
                                AND TRIM(g.name) != ''
                                AND g.name != ?
                                AND g.name NOT LIKE 'App %'
                                AND g.name NOT LIKE 'Steam App %'
                               THEN g.name
                           WHEN h.name IS NOT NULL
                                AND TRIM(h.name) != ''
                                AND h.name != ?
                                AND h.name NOT LIKE 'App %'
                                AND h.name NOT LIKE 'Steam App %'
                               THEN h.name
                           WHEN san.name IS NOT NULL
                                AND TRIM(san.name) != ''
                               THEN san.name
                           ELSE ?
                       END AS name,
                       COALESCE(NULLIF(TRIM(g.header_image), ''), NULLIF(TRIM(h.header_image), '')) AS header_image,
                       COALESCE(gls.current_players, h.current_players, 0) AS current_players,
                       h.peak_players,
                       h.source,
                       h.fetched_at,
                       g.tracked,
                       g.is_free,
                       gls.review_score,
                       gls.cn_price,
                       gls.cn_price_final,
                       gls.cn_price_currency,
                       gls.cn_discount_percent,
                       gls.historical_low_cny AS cn_historical_low_cny
                FROM hot_games h
                LEFT JOIN games g ON g.appid = h.appid
                LEFT JOIN steam_app_names san ON san.appid = h.appid
                LEFT JOIN game_latest_state gls ON gls.appid = h.appid
            )
            SELECT *
            FROM hot
            WHERE name != ?
              AND header_image IS NOT NULL
            ORDER BY current_players DESC, COALESCE(original_rank, 999999)
            LIMIT ?
            """,
            (UNKNOWN_GAME_NAME, UNKNOWN_GAME_NAME, UNKNOWN_GAME_NAME, UNKNOWN_GAME_NAME, limit),
        ).fetchall()
        if not rows:
            rows = conn.execute(
                """
                SELECT g.appid,
                       NULL AS original_rank,
                       g.name,
                       g.header_image,
                       COALESCE(gls.current_players, 0) AS current_players,
                       NULL AS peak_players,
                       'local_snapshots' AS source,
                       g.updated_at AS fetched_at,
                       g.tracked,
                       g.is_free,
                       gls.review_score,
                       gls.cn_price,
                       gls.cn_price_final,
                       gls.cn_price_currency,
                       gls.cn_discount_percent,
                       gls.historical_low_cny AS cn_historical_low_cny
                FROM games g
                LEFT JOIN game_latest_state gls ON gls.appid = g.appid
                WHERE g.name != ?
                ORDER BY current_players DESC, g.name ASC
                LIMIT ?
            """,
                (UNKNOWN_GAME_NAME, limit),
            ).fetchall()
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


def daily_index(total):
    if total <= 0:
        return 0
    today = datetime.now().strftime("%Y-%m-%d")
    digest = hashlib.sha256(today.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % total


def list_local_memes():
    meme_dir = ROOT / "assets" / "memes"
    if not meme_dir.is_dir():
        return []
    allowed = {".gif", ".webp", ".png", ".jpg", ".jpeg"}
    files = sorted(
        path
        for path in meme_dir.iterdir()
        if path.is_file() and path.suffix.lower() in allowed
    )
    return [f"/assets/memes/{path.name}" for path in files]


def clean_home_pick(row):
    if not row:
        return None
    item = dict(row)
    current_cny = amount_int_to_cny(item["cn_price_final"], item["cn_price_currency"] or "CNY")
    is_low = compare_historical_low(current_cny, item["cn_historical_low_cny"])
    return {
        "appid": item["appid"],
        "name": fallback_game_name(item["appid"], item["name"]),
        "header_image": item["header_image"],
        "current_players": item["current_players"] or 0,
        "review_score": item["review_score"],
        "total_reviews": item.get("total_reviews"),
        "cn_price": item["cn_price"],
        "cn_price_display": item["cn_price"] or ("免费" if item.get("is_free") else "国区暂无售价"),
        "is_free": bool(item.get("is_free")),
        "cn_price_final": item["cn_price_final"],
        "cn_discount_percent": item["cn_discount_percent"] or 0,
        "cn_price_historical_low": is_low,
        "cn_price_discounted": bool((item["cn_discount_percent"] or 0) > 0 and not is_low),
        "cn_historical_low_cny": item["cn_historical_low_cny"],
        "tracked": bool(item["tracked"]),
    }


def list_niche_candidates(limit=24):
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT n.appid, n.name, n.header_image, n.current_players, n.peak_players,
                   n.review_score, n.cn_price, n.cn_price_final,
                   n.cn_price_currency, n.cn_discount_percent, n.is_free,
                   (SELECT amount_cny FROM historical_lows h
                    WHERE h.appid = n.appid AND h.country = 'CN' LIMIT 1) AS cn_historical_low_cny,
                   g.tracked, n.weighted_score, n.total_reviews
            FROM niche_pool n
            LEFT JOIN games g ON g.appid = n.appid
            WHERE n.eligible = 1
            ORDER BY n.weighted_score DESC, n.total_reviews DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [clean_home_pick(row) for row in rows]


def list_niche_pool_games(limit=NICHE_POOL_DISPLAY_LIMIT):
    limit = min(max(1, int(limit)), NICHE_POOL_DISPLAY_LIMIT)
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT n.appid, n.name, n.header_image, n.current_players, n.peak_players,
                   n.review_score, n.cn_price, n.cn_price_final,
                   n.cn_price_currency, n.cn_discount_percent, n.is_free, n.fetched_at,
                   n.weighted_score, COALESCE(g.tracked, 0) AS tracked,
                   (SELECT amount_cny FROM historical_lows h
                    WHERE h.appid = n.appid AND h.country = 'CN' LIMIT 1) AS cn_historical_low_cny
            FROM niche_pool n
            LEFT JOIN games g ON g.appid = n.appid
            WHERE n.eligible = 1
            ORDER BY n.weighted_score DESC, n.total_reviews DESC
            """,
        ).fetchall()
    # Keep a small pool fully visible while it is still being built. Once it
    # has enough choices, draw from the stronger half for variety, then sort
    # the displayed games back into score order for easy comparison.
    if len(rows) <= limit:
        candidates = list(rows)
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
    hot_games = list_hot_games(HOTLIST_TARGET)
    historical_lows = [
        game for game in hot_games
        if game.get("is_paid") and game.get("cn_price_historical_low")
    ]
    historical_lows.sort(key=lambda game: int(game.get("current_players") or 0), reverse=True)

    daily_niche = get_daily_niche_recommendation()
    if daily_niche:
        niche_games = [daily_niche]
    else:
        niche_row = list_niche_pool_pick()
        niche_games = []
        if niche_row:
            niche_dict = dict(niche_row)
            niche_dict["tracked"] = False
            niche_dict["cn_historical_low_cny"] = None
            niche_games = [clean_home_pick(niche_dict)]

    memes = list_local_memes()
    meme_url = memes[daily_index(len(memes))] if memes else None
    return {
        "historical_low": historical_lows[0] if historical_lows else None,
        "niche": niche_games[0] if niche_games else None,
        "meme": {
            "url": meme_url,
            "count": len(memes),
        },
    }


def search_local_games(term):
    pattern = f"%{term}%"
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT appid, name, header_image, tracked,
                   (SELECT player_count FROM player_snapshots WHERE appid = games.appid ORDER BY fetched_at DESC LIMIT 1) AS player_count
            FROM games
            WHERE name LIKE ? AND name != ?
            ORDER BY tracked DESC, player_count DESC, name ASC
            LIMIT 12
            """,
            (pattern, UNKNOWN_GAME_NAME),
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
    ]


def search_catalog_games(term):
    pattern = f"%{term}%"
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT c.appid, c.name,
                   COALESCE(g.header_image, 'https://cdn.akamai.steamstatic.com/steam/apps/' || c.appid || '/header.jpg') AS header_image,
                   COALESCE(g.tracked, 0) AS tracked
            FROM steam_catalog c
            LEFT JOIN games g ON g.appid = c.appid
            WHERE c.name LIKE ?
            ORDER BY CASE WHEN c.enrich_status = 'done' THEN 0 ELSE 1 END, c.name ASC
            LIMIT 12
            """,
            (pattern,),
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
    ]


def search_steam(term):
    cache_key = term.strip().lower()
    cached = SEARCH_CACHE.get(cache_key)
    if cached and time.time() - cached["at"] < SEARCH_CACHE_TTL_SECONDS:
        return cached["items"]

    local_items = search_local_games(term)
    catalog_items = search_catalog_games(term)
    local_and_catalog = []
    seen = set()
    for item in local_items + catalog_items:
        if item["appid"] in seen:
            continue
        seen.add(item["appid"])
        local_and_catalog.append(item)
        if len(local_and_catalog) >= 12:
            break
    if local_and_catalog:
        SEARCH_CACHE[cache_key] = {"at": time.time(), "items": local_and_catalog}
        return local_and_catalog

    if term.isdigit():
        appid = int(term)
        try:
            details = fetch_appdetails(appid, "US")
            items = [
                {
                    "appid": appid,
                    "name": (details or {}).get("name") or UNKNOWN_GAME_NAME,
                    "tiny_image": (details or {}).get("header_image"),
                    "price": ((details or {}).get("price_overview") or {}).get("final"),
                }
            ]
            remember_search_games(items)
            SEARCH_CACHE[cache_key] = {"at": time.time(), "items": items}
            return items
        except Exception as exc:
            log_event(f"search appid details skipped appid={appid}: {exc}")
            items = [{"appid": appid, "name": UNKNOWN_GAME_NAME, "tiny_image": None, "price": None}]
            remember_search_games(items)
            SEARCH_CACHE[cache_key] = {"at": time.time(), "items": items}
            return items
    qs = urllib.parse.urlencode({"term": term, "cc": "US", "l": "schinese"})
    try:
        payload = request_json(f"https://store.steampowered.com/api/storesearch/?{qs}", timeout=3, max_retries=0)
    except Exception:
        return []
    items = payload.get("items") or []
    remote_items = [
        {
            "appid": item.get("id"),
            "name": item.get("name"),
            "tiny_image": item.get("tiny_image"),
            "price": item.get("price", {}).get("final") if isinstance(item.get("price"), dict) else None,
        }
        for item in items
        if item.get("type") == "app" and item.get("id")
    ][:12]
    merged = []
    seen = set()
    for item in remote_items + local_and_catalog:
        if item["appid"] in seen:
            continue
        seen.add(item["appid"])
        merged.append(item)
        if len(merged) >= 12:
            break
    remember_search_games(merged)
    SEARCH_CACHE[cache_key] = {"at": time.time(), "items": merged}
    return merged


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def read_json_body(self):
        length = int(self.headers.get("Content-Length") or "0")
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/favicon.ico":
                self.send_file(ROOT / "assets" / "favicon.png", "image/png")
                return
            if path.startswith("/assets/"):
                asset_rel = Path(urllib.parse.unquote(path.removeprefix("/assets/")))
                if asset_rel.is_absolute() or ".." in asset_rel.parts:
                    self.send_response(404)
                    self.send_cors_headers()
                    self.end_headers()
                    return
                asset_path = ROOT / "assets" / asset_rel
                self.send_file(asset_path)
                return
            if path == "/api/image-cache":
                url = (query.get("url") or [""])[0].strip()
                if not url:
                    self.send_json({"error": "missing url"}, 400)
                    return
                try:
                    image_path = cache_image(url)
                    self.send_file(image_path, mimetypes.guess_type(str(image_path))[0] or "image/jpeg", max_age=604800)
                except Exception as exc:
                    log_event(f"image cache failed url={url}: {exc}")
                    self.send_response(302)
                    self.send_cors_headers()
                    self.send_header("Location", url)
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                return
            if path == "/api/games":
                self.send_json({"games": list_games()})
                return
            if path == "/api/hot-games/ensure":
                target_raw = (query.get("target") or ["100"])[0]
                try:
                    target = min(max(100, int(target_raw)), HOTLIST_TARGET)
                except ValueError:
                    target = 100
                hot_count = count_hot_games()
                with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as conn:
                    hotlist_at = get_crawl_state(conn, "hotlist_at")
                force_hotlist = hot_count < target and is_due(hotlist_at, 30)
                queued = False
                if force_hotlist or is_due(hotlist_at, HOTLIST_REFRESH_HOURS * 60):
                    queued = refresh_hot_database_async(force_hotlist=force_hotlist, quick=True)
                preview_queued = enqueue_missing_hot_previews(limit=HOT_FULL_METADATA_TOP_LIMIT, priority=90)
                self.send_json({"queued": queued, "count": hot_count, "target": target, "preview_queued": preview_queued})
                return
            if path == "/api/hot-games":
                limit = (query.get("limit") or ["100"])[0]
                try:
                    requested_limit = min(max(1, int(limit)), HOTLIST_TARGET)
                except ValueError:
                    requested_limit = 100
                self.send_json({"games": list_hot_games(requested_limit), "count": count_hot_games(), "version": hot_games_version(), "queued": False})
                return
            if path == "/api/hot-games/version":
                self.send_json({"version": hot_games_version()})
                return
            if path == "/api/niche-pool":
                games = list_niche_pool_games(20)
                pool_count = count_eligible_niche_pool()
                self.send_json({
                    "games": games,
                    "count": len(games),
                    "pool_count": pool_count,
                    "selection_mode": "all" if pool_count <= NICHE_POOL_DISPLAY_LIMIT else "top_half_random",
                    "queued": False,
                })
                return
            if path == "/api/home-picks":
                self.send_json(get_home_picks())
                return
            if path == "/api/status":
                status = get_status()
                self.send_json(
                    {
                        "running": bool(status.get("running")),
                        "hot_running": bool(status.get("hot_running")),
                        "track_running": bool(status.get("track_running")),
                        "detail_running": bool(status.get("detail_running")),
                        "historylow_running": bool(status.get("historylow_running")),
                        "last_started_at": status.get("last_started_at"),
                        "last_finished_at": status.get("last_finished_at"),
                        "last_errors": list(status.get("last_errors") or []),
                        "hot_last_started_at": status.get("hot_last_started_at"),
                        "hot_last_finished_at": status.get("hot_last_finished_at"),
                        "hot_last_errors": list(status.get("hot_last_errors") or []),
                        "player_refresh_minutes": int(status.get("player_refresh_minutes") or PLAYER_REFRESH_MINUTES),
                        "price_refresh_hours": int(status.get("price_refresh_hours") or PRICE_REFRESH_HOURS),
                        "store_delay_seconds": status.get("store_delay_seconds"),
                        "itad_configured": bool(status.get("itad_configured")),
                        "steam_api_key_configured": bool(status.get("steam_api_key_configured")),
                        "historical_low_tolerance_cny": status.get("historical_low_tolerance_cny"),
                        "historical_low_count": status.get("historical_low_count"),
                        "niche_pool_count": status.get("niche_pool_count"),
                        "proxy": status.get("proxy"),
                        "steam_cooldown_remaining_seconds": status.get("steam_cooldown_remaining_seconds"),
                        "task_progress": status.get("task_progress"),
                        "hot_games_version": status.get("hot_games_version"),
                        "version": status.get("version"),
                        "httpx_available": bool(status.get("httpx_available")),
                        "hotlist_target": status.get("hotlist_target"),
                        "hotlist_concurrency": status.get("hotlist_concurrency"),
                        "hot_preview_top_limit": status.get("hot_preview_top_limit"),
                        "hot_preview_batch_limit": status.get("hot_preview_batch_limit"),
                        "hot_full_metadata_top_limit": status.get("hot_full_metadata_top_limit"),
                        "hot_metadata_concurrency": status.get("hot_metadata_concurrency"),
                        "hot_metadata_batch_limit": status.get("hot_metadata_batch_limit"),
                        "tracked_refresh_batch_limit": status.get("tracked_refresh_batch_limit"),
                        "itad_historylow_batch_limit": status.get("itad_historylow_batch_limit"),
                        "niche_pool_refresh_minutes": status.get("niche_pool_refresh_minutes"),
                        "niche_pool_display_limit": status.get("niche_pool_display_limit"),
                        "niche_pool_batch_limit": status.get("niche_pool_batch_limit"),
                        "steam_catalog_count": status.get("steam_catalog_count"),
                        "steam_catalog_enriched_count": status.get("steam_catalog_enriched_count"),
                        "steam_catalog_limit": status.get("steam_catalog_limit"),
                        "catalog_enrich_daily_limit": status.get("catalog_enrich_daily_limit"),
                        "catalog_enrich_batch_limit": status.get("catalog_enrich_batch_limit"),
                        "niche_pool_limit": status.get("niche_pool_limit"),
                        "crawl_task_count": status.get("crawl_task_count"),
                        "crawl_task_counts": status.get("crawl_task_counts"),
                    }
                )
                return
            if path == "/api/search":
                term = (query.get("q") or [""])[0].strip()
                self.send_json({"items": search_steam(term) if term else []})
                return
            if path.startswith("/api/games/"):
                appid = int(path.rsplit("/", 1)[-1])
                history_limit = (query.get("history_limit") or ["500"])[0]
                payload = get_game_payload(appid, history_limit)
                self.send_json(payload if payload else {"error": "not found"}, 200 if payload else 404)
                return
            if path in ("/", "/steamkb.html"):
                body = (ROOT / "steamkb.html").read_bytes()
                self.send_response(200)
                self.send_cors_headers()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.send_cors_headers()
            self.end_headers()
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def send_file(self, path, content_type=None, max_age=3600):
        if not path.is_file():
            self.send_response(404)
            self.send_cors_headers()
            self.end_headers()
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_cors_headers()
        self.send_header("Content-Type", content_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        self.send_header("Cache-Control", f"public, max-age={int(max_age)}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/api/track":
                body = self.read_json_body()
                appid = int(body.get("appid"))
                name = body.get("name")
                header_image = body.get("header_image") or body.get("tiny_image")
                quick_track_game(appid, name, header_image)
                queued = refresh_tracked_game_async(appid, name)
                self.send_json({"ok": True, "appid": appid, "queued": queued})
                return
            if parsed.path == "/api/untrack":
                body = self.read_json_body()
                appid = int(body.get("appid"))
                untrack_game(appid)
                self.send_json({"ok": True, "appid": appid, "tracked": False})
                return
            if parsed.path == "/api/refresh-all":
                if REFRESH_STATUS["running"]:
                    self.send_json({"ok": True, "running": True, "message": "refresh already running"})
                    return
                errors = refresh_tracked_once(force_all=True)
                self.send_json({"ok": True, "running": False, "errors": errors})
                return
            if parsed.path.startswith("/api/games/") and parsed.path.endswith("/refresh"):
                appid = int(parsed.path.split("/")[3])
                result = refresh_game(appid)
                self.send_json({"ok": True, **result})
                return
            self.send_response(404)
            self.send_cors_headers()
            self.end_headers()
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)


def main():
    init_db()
    probe_proxy()
    cleanup_image_cache_once()
    startup_prewarm_async()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Steam-KaKaBase running at http://127.0.0.1:{PORT}")
    print(f"SQLite database: {DB_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    main()
