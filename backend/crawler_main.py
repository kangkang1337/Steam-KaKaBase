"""Standalone Steam-KaKaBase crawler process."""

import json
import os
import signal
import socket
import threading
from uuid import uuid4

from . import config, crawler
from . import _runtime as runtime
from .db import (
    acquire_process_lease,
    get_process_lease,
    init_db,
    recover_abandoned_crawl_tasks,
    release_process_lease,
    renew_process_lease,
    retire_obsolete_crawl_tasks,
    set_crawl_state,
    transaction,
)
from .steam_client import probe_proxy


LEASE_NAME = "crawler"


def _runtime_status():
    with runtime.STATUS_LOCK:
        refresh = dict(runtime.REFRESH_STATUS)
    return {
        "refresh": refresh,
        "service_cooldowns": {
            service: runtime.service_cooldown_remaining_seconds(service)
            for service in config.EXTERNAL_SERVICES
        },
        "direct_service_cooldowns": {
            service: runtime.direct_cooldown_remaining_seconds(service)
            for service in config.EXTERNAL_SERVICES
        },
        "rate_limits": {
            service: dict(runtime.SERVICE_RATE_LIMIT_STATUS.get(service, {}))
            for service in config.EXTERNAL_SERVICES
        },
        "proxy": dict(runtime.PROXY_STATUS),
    }


def publish_crawler_status(state, *, error=None):
    payload = _runtime_status()
    payload["state"] = state
    payload["error"] = str(error)[:500] if error else None
    payload["updated_at"] = runtime.now_iso()
    with transaction() as conn:
        set_crawl_state(conn, "crawler_runtime_status", json.dumps(payload, ensure_ascii=False))
        set_crawl_state(conn, "crawler_last_heartbeat_at", payload["updated_at"])
        if state == "running":
            set_crawl_state(conn, "crawler_last_error", "")
        elif error:
            set_crawl_state(conn, "crawler_last_error", payload["error"])


def run(*, stop_event=None, owner_id=None, prewarm=True):
    """Run one crawler until stopped; return 2 when another instance owns the lease."""
    init_db()
    stop_event = stop_event or threading.Event()
    owner_id = owner_id or uuid4().hex
    pid = os.getpid()
    hostname = socket.gethostname()
    acquired = acquire_process_lease(
        LEASE_NAME,
        owner_id,
        pid=pid,
        hostname=hostname,
        lease_seconds=config.CRAWLER_LEASE_SECONDS,
    )
    if not acquired:
        lease = get_process_lease(LEASE_NAME) or {}
        print(
            "Steam-KaKaBase crawler is already running "
            f"(pid={lease.get('pid')}, host={lease.get('hostname')})."
        )
        return 2

    heartbeat_failed = threading.Event()

    def heartbeat():
        while not stop_event.wait(config.CRAWLER_HEARTBEAT_SECONDS):
            try:
                if not renew_process_lease(
                    LEASE_NAME,
                    owner_id,
                    lease_seconds=config.CRAWLER_LEASE_SECONDS,
                ):
                    heartbeat_failed.set()
                    stop_event.set()
                    runtime.log_event("crawler lease lost; stopping scheduler")
                    return
                publish_crawler_status("running")
            except Exception as exc:
                runtime.log_event(f"crawler heartbeat failed: {exc}")

    heartbeat_thread = threading.Thread(
        target=heartbeat,
        daemon=True,
        name="steamkb-crawler-heartbeat",
    )
    try:
        recovery = recover_abandoned_crawl_tasks()
        retired = retire_obsolete_crawl_tasks()
        with transaction() as conn:
            set_crawl_state(conn, "crawler_started_at", runtime.now_iso())
            set_crawl_state(conn, "crawler_pid", str(pid))
            set_crawl_state(conn, "crawler_last_recovery", json.dumps(recovery))
            set_crawl_state(conn, "crawler_last_retired_obsolete_count", str(retired))
        if recovery["count"]:
            runtime.log_event(
                f"crawler recovered abandoned tasks count={recovery['count']} "
                f"by_type={recovery['by_type']}"
            )
        if retired:
            runtime.log_event(f"crawler retired obsolete tasks count={retired}")
        probe_proxy()
        runtime.cleanup_image_cache_once()
        publish_crawler_status("running")
        heartbeat_thread.start()
        if prewarm:
            crawler.run_startup_prewarm()
            publish_crawler_status("running")
        while not stop_event.wait(config.SCHEDULER_CHECK_SECONDS):
            retire_obsolete_crawl_tasks()
            crawler.run_scheduler_cycle()
            with transaction() as conn:
                set_crawl_state(conn, "crawler_last_cycle_at", runtime.now_iso())
            publish_crawler_status("running")
        return 3 if heartbeat_failed.is_set() else 0
    except Exception as exc:
        publish_crawler_status("failed", error=exc)
        runtime.log_event(f"crawler process failed: {exc}")
        raise
    finally:
        stop_event.set()
        # Prevent a heartbeat already in flight from publishing "running"
        # after the final shutdown state has been persisted.
        if heartbeat_thread.is_alive():
            heartbeat_thread.join()
        lease = get_process_lease(LEASE_NAME)
        owns_lease = bool(lease and lease.get("owner_id") == owner_id)
        try:
            if owns_lease:
                publish_crawler_status("stopped")
                with transaction() as conn:
                    set_crawl_state(conn, "crawler_stopped_at", runtime.now_iso())
        except Exception as exc:
            runtime.log_event(f"crawler shutdown status failed: {exc}")
        finally:
            # Publish before releasing ownership so an old process cannot
            # overwrite the status of a replacement crawler.
            if owns_lease:
                release_process_lease(LEASE_NAME, owner_id)


def main():
    stop_event = threading.Event()

    def stop(_signum, _frame):
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, stop)
    print(f"Steam-KaKaBase crawler using SQLite database: {config.DB_PATH}")
    raise SystemExit(run(stop_event=stop_event))


if __name__ == "__main__":
    main()
