"""Environment loading and immutable application settings."""

import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path=None):
    env_path = Path(path) if path else ROOT / ".env"
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


def env_int(name, default, *, minimum=None, maximum=None):
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def env_float(name, default, *, minimum=None):
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    return max(minimum, value) if minimum is not None else value


def env_bool(name, default=False, *, fallback=None):
    raw = os.getenv(name)
    if raw is None and fallback:
        raw = os.getenv(fallback)
    if raw is None:
        return bool(default)
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{name} must be true or false, got {raw!r}")


def env_list(name, default=""):
    return tuple(value.strip() for value in os.getenv(name, default).split(",") if value.strip())


load_dotenv()

DATA_DIR = ROOT / "data"
IMAGE_CACHE_DIR = DATA_DIR / "image-cache"
DB_PATH = Path(os.getenv("STEAMKB_DB", str(DATA_DIR / "steamkb.sqlite3")))
LOG_PATH = Path(os.getenv("STEAMKB_LOG", str(DATA_DIR / "steamkb.log")))
DB_MIGRATION_BACKUP_DIR = Path(os.getenv("STEAMKB_DB_BACKUP_DIR", str(DB_PATH.parent / "backups")))
DB_MIGRATION_BACKUP_KEEP = env_int("STEAMKB_DB_BACKUP_KEEP", 10, minimum=1)
DB_DAILY_BACKUP_ENABLED = env_bool("STEAMKB_DAILY_BACKUP_ENABLED", True)
DB_DAILY_BACKUP_KEEP = env_int("STEAMKB_DAILY_BACKUP_KEEP", 14, minimum=1, maximum=90)
DB_TIMEOUT_SECONDS = 30

ENVIRONMENT = os.getenv("STEAMKB_ENV", "development").strip().lower()
IS_PRODUCTION = ENVIRONMENT == "production"
ADMIN_TOKEN = os.getenv("STEAMKB_ADMIN_TOKEN", "").strip()
CORS_ALLOWED_ORIGINS = env_list("STEAMKB_CORS_ALLOWED_ORIGINS")
ALLOWED_HOSTS = env_list("STEAMKB_ALLOWED_HOSTS")
HOST = os.getenv("STEAMKB_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = env_int("STEAMKB_PORT", 8765, minimum=1, maximum=65535)
PLAYER_REFRESH_MINUTES = env_int("STEAMKB_PLAYER_REFRESH_MINUTES", 30, minimum=30)
PRICE_REFRESH_HOURS = env_int("STEAMKB_PRICE_REFRESH_HOURS", 24, minimum=24)
SCHEDULER_CHECK_SECONDS = env_int("STEAMKB_SCHEDULER_CHECK_SECONDS", 60, minimum=5)
CRAWLER_LEASE_SECONDS = env_int("STEAMKB_CRAWLER_LEASE_SECONDS", 120, minimum=30)
CRAWLER_HEARTBEAT_SECONDS = env_int("STEAMKB_CRAWLER_HEARTBEAT_SECONDS", 20, minimum=5)
DAILY_REFRESH_TIMEZONE = os.getenv("STEAMKB_DAILY_REFRESH_TIMEZONE", "Asia/Shanghai").strip() or "Asia/Shanghai"
try:
    DAILY_REFRESH_TZINFO = ZoneInfo(DAILY_REFRESH_TIMEZONE)
except ZoneInfoNotFoundError as exc:
    raise ValueError(f"STEAMKB_DAILY_REFRESH_TIMEZONE is invalid: {DAILY_REFRESH_TIMEZONE!r}") from exc
HISTORICAL_LOW_TOLERANCE_CNY = env_float("STEAMKB_HISTORICAL_LOW_TOLERANCE_CNY", 0.5, minimum=0)

HOTLIST_TARGET = env_int("STEAMKB_HOTLIST_TARGET", 100, minimum=100)
HOTLIST_CONCURRENCY = env_int("STEAMKB_HOTLIST_CONCURRENCY", 8, minimum=1, maximum=10)
HOTLIST_BATCH_SIZE = env_int("STEAMKB_HOTLIST_BATCH_SIZE", 200, minimum=50)
HOTLIST_REFRESH_HOURS = env_int("STEAMKB_HOTLIST_REFRESH_HOURS", 24, minimum=24)
HOT_METADATA_CONCURRENCY = env_int("STEAMKB_HOT_METADATA_CONCURRENCY", 2, minimum=1, maximum=4)
HOT_PREVIEW_TOP_LIMIT = env_int("STEAMKB_HOT_PREVIEW_TOP_LIMIT", 200, minimum=50)
HOT_PREVIEW_BATCH_LIMIT = env_int("STEAMKB_HOT_PREVIEW_BATCH_LIMIT", 100, minimum=20)
HOT_FULL_METADATA_TOP_LIMIT = env_int("STEAMKB_HOT_FULL_METADATA_TOP_LIMIT", 50, minimum=10)
HOT_METADATA_BATCH_LIMIT = env_int("STEAMKB_HOT_METADATA_BATCH_LIMIT", 50, minimum=10)

NICHE_POOL_BATCH_LIMIT = env_int("STEAMKB_NICHE_POOL_BATCH_LIMIT", 30, minimum=10)
NICHE_POOL_REFRESH_MINUTES = env_int("STEAMKB_NICHE_POOL_REFRESH_MINUTES", 1440, minimum=30)
NICHE_POOL_DISPLAY_LIMIT = 20
NICHE_POOL_BOOTSTRAP_REFRESH_MINUTES = env_int("STEAMKB_NICHE_POOL_BOOTSTRAP_REFRESH_MINUTES", 180, minimum=60)
NICHE_POOL_LIMIT = env_int("STEAMKB_NICHE_POOL_LIMIT", 500, minimum=50)
NICHE_MAX_REVIEWS = env_int("STEAMKB_NICHE_MAX_REVIEWS", 50000, minimum=1)

STEAM_CATALOG_LIMIT = env_int("STEAMKB_CATALOG_LIMIT", 0, minimum=0)
CATALOG_SCAN_BATCH_LIMIT = env_int("STEAMKB_CATALOG_SCAN_BATCH_LIMIT", STEAM_CATALOG_LIMIT or 10000, minimum=500)
CATALOG_RESCAN_DAYS = env_int("STEAMKB_CATALOG_RESCAN_DAYS", 7, minimum=1)
CATALOG_ENRICH_DAILY_LIMIT = env_int("STEAMKB_CATALOG_ENRICH_DAILY_LIMIT", 1500, minimum=100)
CATALOG_ENRICH_BATCH_LIMIT = env_int("STEAMKB_CATALOG_ENRICH_BATCH_LIMIT", 50, minimum=20)

HOME_RECOMMENDATION_REPEAT_DAYS = env_int("STEAMKB_HOME_REPEAT_DAYS", 7, minimum=1)
HOME_POPULAR_MIN_REVIEWS = env_int("STEAMKB_HOME_POPULAR_MIN_REVIEWS", 10000, minimum=1)
HOME_POPULAR_MIN_PLAYERS = env_int("STEAMKB_HOME_POPULAR_MIN_PLAYERS", 2000, minimum=1)
TRACKED_REFRESH_BATCH_LIMIT = env_int("STEAMKB_TRACKED_REFRESH_BATCH_LIMIT", 1, minimum=1)
ITAD_HISTORYLOW_BATCH_LIMIT = env_int("STEAMKB_ITAD_HISTORYLOW_BATCH_LIMIT", 50, minimum=1)
ITAD_HISTORYLOW_REFRESH_DAYS = env_int("STEAMKB_ITAD_HISTORYLOW_REFRESH_DAYS", 30, minimum=7)

STORE_REQUEST_DELAY_MIN_SECONDS = env_float("STEAMKB_STORE_DELAY_MIN_SECONDS", 1.5, minimum=0)
STORE_REQUEST_DELAY_MAX_SECONDS = max(
    STORE_REQUEST_DELAY_MIN_SECONDS,
    env_float("STEAMKB_STORE_DELAY_MAX_SECONDS", 4.0, minimum=0),
)
STEAM_TIMEOUT_SECONDS = env_float("STEAMKB_HTTP_TIMEOUT_SECONDS", 15, minimum=1)
STEAM_MAX_RETRIES = env_int("STEAMKB_HTTP_MAX_RETRIES", 2, minimum=0)
DIRECT_COOLDOWN_MINUTES = env_int("STEAMKB_DIRECT_COOLDOWN_MINUTES", 5, minimum=1)
START_COOLDOWN_SECONDS = env_int("STEAMKB_START_COOLDOWN_SECONDS", 0, minimum=0)

ITAD_API_KEY = os.getenv("ITAD_API_KEY", "")
STEAM_API_KEY = os.getenv("STEAM_API_KEY", "").strip()
STEAM_PROXY_URL = os.getenv("STEAMKB_PROXY_URL", "").strip()
USE_PROXY = env_bool("USE_PROXY", fallback="UNE_PROXY")
STEAM_PROXY_VERIFY_TLS = env_bool("STEAMKB_PROXY_VERIFY_TLS", True)
STEAM_USER_AGENT = "Steam-KaKaBase/1.0 (+local personal dashboard)"
STEAM_RETRY_STATUSES = {429, 500, 502, 503}

LOG_RETENTION_DAYS = env_int("STEAMKB_LOG_RETENTION_DAYS", 30, minimum=7)
PRICE_RETENTION_DAYS = env_int("STEAMKB_PRICE_RETENTION_DAYS", 730, minimum=30)
RECOMMENDATION_RETENTION_DAYS = env_int("STEAMKB_RECOMMENDATION_RETENTION_DAYS", 730, minimum=30)
CRAWL_TASK_RETENTION_DAYS = env_int("STEAMKB_CRAWL_TASK_RETENTION_DAYS", 60, minimum=7)
IMAGE_CACHE_MAX_BYTES = env_int("STEAMKB_IMAGE_CACHE_MAX_BYTES", 512 * 1024 * 1024, minimum=16 * 1024 * 1024)
IMAGE_CACHE_RETENTION_DAYS = env_int("STEAMKB_IMAGE_CACHE_RETENTION_DAYS", 30, minimum=1)
IMAGE_CACHE_MAX_FILE_BYTES = env_int("STEAMKB_IMAGE_CACHE_MAX_FILE_BYTES", 2 * 1024 * 1024, minimum=256 * 1024)
SEARCH_CACHE_TTL_SECONDS = env_int("STEAMKB_SEARCH_CACHE_TTL_SECONDS", 900, minimum=30)
SEARCH_CACHE_EMPTY_TTL_SECONDS = env_int("STEAMKB_SEARCH_EMPTY_CACHE_TTL_SECONDS", 30, minimum=5)
SEARCH_CACHE_MAX_ENTRIES = env_int("STEAMKB_SEARCH_CACHE_MAX_ENTRIES", 512, minimum=32)
PUBLIC_DETAIL_QUEUE_LIMIT = env_int("STEAMKB_PUBLIC_DETAIL_QUEUE_LIMIT", 60, minimum=10, maximum=200)
AUTH_RATE_LIMIT = env_int("STEAMKB_AUTH_RATE_LIMIT", 10, minimum=1, maximum=1000)
AUTH_RATE_WINDOW_SECONDS = env_int("STEAMKB_AUTH_RATE_WINDOW_SECONDS", 300, minimum=1, maximum=3600)
FAVORITES_RATE_LIMIT = env_int("STEAMKB_FAVORITES_RATE_LIMIT", 60, minimum=1, maximum=5000)
FAVORITES_RATE_WINDOW_SECONDS = env_int("STEAMKB_FAVORITES_RATE_WINDOW_SECONDS", 60, minimum=1, maximum=3600)
APP_NAME_REFRESH_HOURS = env_int("STEAMKB_APP_NAME_REFRESH_HOURS", 24, minimum=24)

APP_VERSION = "0.5.0"
ITAD_MISSING_GAME_ID = "__itad_missing__"
EXTERNAL_SERVICES = ("steam_api", "steam_store", "itad", "image_cdn")
