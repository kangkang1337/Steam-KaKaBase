from datetime import datetime, timedelta, timezone

import pytest

from backend import _runtime


@pytest.mark.parametrize(
    ("current", "historical_low", "expected"),
    [
        (50.0, 50.0, True),
        (50.5, 50.0, True),
        (49.5, 50.0, True),
        (50.51, 50.0, False),
        (None, 50.0, False),
        (50.0, None, False),
    ],
)
def test_historical_low_tolerance(current, historical_low, expected):
    assert _runtime.compare_historical_low(current, historical_low) is expected


@pytest.mark.parametrize(
    ("amount", "currency", "expected"),
    [
        (1000, "CNY", 10.0),
        (1000, "USD", 72.0),
        (1000, "JPY", 0.49),
        (None, "CNY", None),
    ],
)
def test_amount_to_cny(amount, currency, expected):
    assert _runtime.amount_int_to_cny(amount, currency) == expected


def test_fresh_timestamp_is_not_due():
    assert _runtime.is_due(_runtime.now_iso(), 30) is False


def test_old_or_missing_timestamp_is_due():
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=0).isoformat()
    assert _runtime.is_due(old, 30) is True
    assert _runtime.is_due(None, 30) is True


def test_recent_release_filter():
    current_year = datetime.now(timezone.utc).year
    assert _runtime.is_recent_release(str(current_year)) is True
    assert _runtime.is_recent_release(str(current_year - 7)) is True
    assert _runtime.is_recent_release(str(current_year - 9)) is False
    assert _runtime.is_recent_release("1 Jan, 2010") is False
    assert _runtime.is_recent_release(None) is False


def test_release_recency_factor_uses_age_bands():
    current_year = datetime.now(timezone.utc).year
    assert _runtime.release_recency_factor(str(current_year - 2)) == 1.0
    assert _runtime.release_recency_factor(str(current_year - 4)) == 0.95
    assert _runtime.release_recency_factor(str(current_year - 7)) == 0.85
    assert _runtime.release_recency_factor(str(current_year - 9)) == 0.0


def test_daily_homepage_boundary_is_0010_local_time():
    assert _runtime.daily_refresh_key(datetime(2026, 9, 6, 0, 9, 59)) == "2026-09-05"
    assert _runtime.daily_refresh_key(datetime(2026, 9, 6, 0, 10, 0)) == "2026-09-06"


def test_daily_home_snapshot_keeps_the_same_available_picks(isolated_runtime):
    first_low = {"appid": 10}
    later_top_low = {"appid": 20}
    first_key, selected_low, selected_meme = isolated_runtime.ensure_daily_home_snapshot(
        [first_low],
        ["/assets/memes/a.gif"],
    )
    second_key, selected_low_again, selected_meme_again = isolated_runtime.ensure_daily_home_snapshot(
        [later_top_low, first_low],
        ["/assets/memes/new.webp", "/assets/memes/a.gif"],
    )

    assert second_key == first_key
    assert selected_low_again["appid"] == selected_low["appid"] == 10
    assert selected_meme_again == selected_meme == "/assets/memes/a.gif"


def test_meme_extensions_cover_common_browser_formats():
    assert {".gif", ".webp", ".png", ".apng", ".jpg", ".jpeg", ".jfif", ".avif", ".bmp"} <= _runtime.MEME_EXTENSIONS


def test_niche_score_rewards_reviews_and_quality():
    baseline = {
        "review_score": 85,
        "total_reviews": 500,
        "peak_players": 1500,
        "current_players": 400,
        "release_date": str(datetime.now(timezone.utc).year - 2),
    }
    stronger = {**baseline, "review_score": 95, "total_reviews": 5000}
    assert _runtime.niche_weighted_score(stronger) > _runtime.niche_weighted_score(baseline)
