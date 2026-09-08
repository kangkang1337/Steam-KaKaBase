import json
import threading

from backend import crawler_main, db


def test_crawler_process_rejects_second_active_owner(isolated_runtime, monkeypatch):
    assert db.acquire_process_lease("crawler", "first-owner", lease_seconds=60)
    monkeypatch.setattr(crawler_main, "init_db", lambda: None)

    assert crawler_main.run(owner_id="second-owner", prewarm=False) == 2


def test_crawler_process_publishes_heartbeat_and_releases_lease(
    isolated_runtime, monkeypatch
):
    stop_event = threading.Event()
    published = []
    original_publish = crawler_main.publish_crawler_status

    def capture_publish(state, *, error=None):
        published.append(state)
        original_publish(state, error=error)

    monkeypatch.setattr(crawler_main, "publish_crawler_status", capture_publish)
    monkeypatch.setattr(crawler_main, "probe_proxy", lambda: None)
    monkeypatch.setattr(crawler_main.runtime, "cleanup_image_cache_once", lambda: None)
    monkeypatch.setattr(crawler_main.crawler, "run_scheduler_cycle", lambda: stop_event.set())
    monkeypatch.setattr(crawler_main.config, "CRAWLER_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(crawler_main.config, "SCHEDULER_CHECK_SECONDS", 0.02)

    assert crawler_main.run(
        stop_event=stop_event, owner_id="test-owner", prewarm=False
    ) == 0

    assert published[0] == "running"
    assert published.count("running") >= 2
    assert published[-1] == "stopped"
    assert db.get_process_lease("crawler") is None
    with db.transaction(rows=True) as conn:
        payload = json.loads(db.get_crawl_state(conn, "crawler_runtime_status"))
    assert payload["state"] == "stopped"
