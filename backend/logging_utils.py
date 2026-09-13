"""Application logging, safe URL formatting, and filesystem rotation helpers."""

import threading
import time
from datetime import datetime

from . import config
from .utils import now_iso, safe_log_url


LOG_LOCK = threading.Lock()


def log_event(message):
    config.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{now_iso()}] {message}"
    print(line)
    try:
        with LOG_LOCK:
            with config.LOG_PATH.open("a", encoding="utf-8") as fp:
                fp.write(line + "\n")
    except OSError as exc:
        print(f"[log] {exc}")


def rotate_log_file_once(*, today=None):
    """Rotate the active log file and prune old archives without touching SQLite."""
    stamp = today or datetime.now().strftime("%Y-%m-%d")
    config.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_LOCK:
        if config.LOG_PATH.exists() and config.LOG_PATH.stat().st_size > 0:
            archive = config.LOG_PATH.with_name(f"{config.LOG_PATH.name}.{stamp}")
            if archive.exists():
                archive = config.LOG_PATH.with_name(f"{config.LOG_PATH.name}.{stamp}.{int(time.time())}")
            config.LOG_PATH.replace(archive)
        for archive in config.LOG_PATH.parent.glob(f"{config.LOG_PATH.name}.*"):
            try:
                if (time.time() - archive.stat().st_mtime) / 86400 > config.LOG_RETENTION_DAYS:
                    archive.unlink()
            except OSError:
                continue


__all__ = ["log_event", "rotate_log_file_once", "safe_log_url"]
