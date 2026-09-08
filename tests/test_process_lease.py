from datetime import datetime, timedelta, timezone

from backend import db


def test_process_lease_allows_only_one_active_owner(isolated_runtime):
    now = datetime(2026, 9, 8, tzinfo=timezone.utc)

    assert db.acquire_process_lease(
        "crawler", "owner-a", pid=100, hostname="host-a", lease_seconds=60, moment=now
    ) is True
    assert db.acquire_process_lease(
        "crawler", "owner-b", pid=200, hostname="host-b", lease_seconds=60, moment=now
    ) is False

    lease = db.get_process_lease("crawler", moment=now)
    assert lease["owner_id"] == "owner-a"
    assert lease["pid"] == 100
    assert lease["active"] is True


def test_expired_process_lease_can_be_taken_over(isolated_runtime):
    now = datetime(2026, 9, 8, tzinfo=timezone.utc)
    db.acquire_process_lease("crawler", "owner-a", lease_seconds=30, moment=now)

    assert db.acquire_process_lease(
        "crawler", "owner-b", lease_seconds=60, moment=now + timedelta(seconds=31)
    ) is True
    assert db.get_process_lease(
        "crawler", moment=now + timedelta(seconds=31)
    )["owner_id"] == "owner-b"


def test_process_lease_renewal_and_owner_checked_release(isolated_runtime):
    now = datetime(2026, 9, 8, tzinfo=timezone.utc)
    db.acquire_process_lease("crawler", "owner-a", lease_seconds=30, moment=now)

    assert db.renew_process_lease(
        "crawler", "owner-a", lease_seconds=60, moment=now + timedelta(seconds=20)
    ) is True
    assert db.release_process_lease("crawler", "owner-b") is False
    assert db.get_process_lease(
        "crawler", moment=now + timedelta(seconds=70)
    )["active"] is True
    assert db.release_process_lease("crawler", "owner-a") is True
    assert db.get_process_lease("crawler") is None


def test_supervisor_can_release_only_the_matching_process_pid(isolated_runtime):
    assert db.acquire_process_lease(
        "crawler", "owner-a", pid=1234, hostname="host-a", lease_seconds=60
    )

    assert db.release_process_lease_by_pid("crawler", 5678) is False
    assert db.get_process_lease("crawler")["pid"] == 1234
    assert db.release_process_lease_by_pid("crawler", 1234) is True
    assert db.get_process_lease("crawler") is None
