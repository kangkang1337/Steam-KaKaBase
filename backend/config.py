"""Environment-backed configuration exposed to backend modules.

The compatibility runtime remains the single source of values during the
staged split, so every module observes the same locks, paths and settings.
"""

from . import _runtime

ROOT = _runtime.ROOT
DATA_DIR = _runtime.DATA_DIR
IMAGE_CACHE_DIR = _runtime.IMAGE_CACHE_DIR
BADGE_ASSETS_DIR = _runtime.BADGE_ASSETS_DIR
DB_PATH = _runtime.DB_PATH
LOG_PATH = _runtime.LOG_PATH
PORT = _runtime.PORT
DB_TIMEOUT_SECONDS = _runtime.DB_TIMEOUT_SECONDS
APP_VERSION = _runtime.APP_VERSION

PLAYER_REFRESH_MINUTES = _runtime.PLAYER_REFRESH_MINUTES
PRICE_REFRESH_HOURS = _runtime.PRICE_REFRESH_HOURS
HOTLIST_TARGET = _runtime.HOTLIST_TARGET
HOTLIST_REFRESH_HOURS = _runtime.HOTLIST_REFRESH_HOURS
HOT_PREVIEW_TOP_LIMIT = _runtime.HOT_PREVIEW_TOP_LIMIT
HOT_FULL_METADATA_TOP_LIMIT = _runtime.HOT_FULL_METADATA_TOP_LIMIT
NICHE_POOL_DISPLAY_LIMIT = _runtime.NICHE_POOL_DISPLAY_LIMIT


def __getattr__(name):
    """Keep less common settings available without duplicating their values."""
    if name.isupper() and hasattr(_runtime, name):
        return getattr(_runtime, name)
    raise AttributeError(name)
