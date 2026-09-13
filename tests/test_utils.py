from datetime import datetime, timezone

from pathlib import Path

from backend import _runtime, pricing, utils


def test_runtime_reexports_pure_helpers_for_compatibility():
    assert _runtime.clean_name("  Portal  ") == utils.clean_name("  Portal  ")
    assert _runtime.amount_int_to_cny(1000, "USD") == pricing.amount_int_to_cny(1000, "USD")


def test_foundational_modules_do_not_depend_on_runtime():
    backend_dir = Path(__file__).resolve().parents[1] / "backend"

    for module_name in ("utils.py", "pricing.py", "logging_utils.py"):
        assert "_runtime" not in (backend_dir / module_name).read_text(encoding="utf-8")


def test_transport_module_owns_shared_external_failures_without_runtime_import():
    backend_dir = Path(__file__).resolve().parents[1] / "backend"
    source = (backend_dir / "steam_client.py").read_text(encoding="utf-8")

    assert "from . import _runtime" not in source
    assert "runtime." not in source
    from backend import steam_client
    assert _runtime.SteamRateLimited is steam_client.SteamRateLimited
    assert _runtime.ExternalDataUnavailable is steam_client.ExternalDataUnavailable


def test_safe_log_url_redacts_all_supported_secret_parameter_names():
    value = utils.safe_log_url(
        "https://example.test/path?key=one&api_key=two&apikey=three&token=four&access_token=five&keep=ok"
    )

    assert "one" not in value and "two" not in value and "three" not in value
    assert "four" not in value and "five" not in value
    assert "keep=ok" in value
    assert value.count("%2A%2A%2A") == 5


def test_release_date_parser_and_daily_key_remain_timezone_aware():
    chinese_date = "2025\u5e74 2\u6708 3\u65e5"
    assert utils.parse_release_date(chinese_date) == datetime(2025, 2, 3, tzinfo=timezone.utc)
    assert utils.parse_release_date("not a release date") is None
    assert utils.daily_refresh_key(datetime(2026, 9, 6, 0, 9, 59)) == "2026-09-05"


def test_cache_cleanup_removes_expired_files_and_bounds_remaining_size(tmp_path, monkeypatch):
    old = tmp_path / "old.jpg"
    old.write_bytes(b"old")
    recent_a = tmp_path / "recent-a.jpg"
    recent_a.write_bytes(b"a" * 6)
    recent_b = tmp_path / "recent-b.jpg"
    recent_b.write_bytes(b"b" * 6)
    now = 10_000_000
    monkeypatch.setattr(utils.time, "time", lambda: now)
    old_stat_time = now - 3 * 86400
    import os
    os.utime(old, (old_stat_time, old_stat_time))
    os.utime(recent_a, (now - 20, now - 20))
    os.utime(recent_b, (now - 10, now - 10))

    utils.cleanup_image_cache(tmp_path, retention_days=1, max_bytes=8)

    assert not old.exists()
    assert not recent_a.exists()
    assert recent_b.exists()
