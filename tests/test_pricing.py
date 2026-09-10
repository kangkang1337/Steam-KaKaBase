from datetime import datetime, timedelta, timezone
import sqlite3

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


def _insert_cn_price(runtime, appid, final, discount, fetched_at):
    with runtime.database_connection() as conn:
        conn.execute(
            """
            INSERT INTO price_snapshots(
                appid, region, currency, initial, final, discount_percent,
                final_formatted, source, fetched_at
            ) VALUES (?, 'CN', 'CNY', 2000, ?, ?, ?, 'steam', ?)
            """,
            (appid, final, discount, f"¥ {final / 100:.2f}", fetched_at),
        )


def test_single_observed_price_is_not_promoted_to_historical_low(isolated_runtime, insert_game):
    appid = insert_game(6101, "Newly Observed")
    _insert_cn_price(isolated_runtime, appid, 1000, 50, "2026-09-08T00:00:00+00:00")

    with isolated_runtime.database_connection() as conn:
        conn.row_factory = sqlite3.Row
        prices = isolated_runtime.latest_by_region(conn, appid)

    assert prices[0]["historical_low_source"] == "site_observed"
    assert prices[0]["observed_low_cny"] == 10.0
    assert prices[0]["historical_low"] is False


def test_discounted_observed_low_uses_site_history_after_multiple_snapshots(
    isolated_runtime, insert_game
):
    appid = insert_game(6102, "Observed Low")
    _insert_cn_price(isolated_runtime, appid, 2000, 0, "2026-09-07T00:00:00+00:00")
    _insert_cn_price(isolated_runtime, appid, 1000, 50, "2026-09-08T00:00:00+00:00")

    with isolated_runtime.database_connection() as conn:
        conn.row_factory = sqlite3.Row
        price = isolated_runtime.latest_by_region(conn, appid)[0]

    assert price["historical_low_cny"] == 10.0
    assert price["historical_low_source"] == "site_observed"
    assert price["observed_snapshot_count"] == 2
    assert price["historical_low"] is True


def test_itad_low_has_priority_when_it_is_lower(isolated_runtime, insert_game):
    appid = insert_game(6103, "ITAD Low")
    stamp = "2026-09-08T00:00:00+00:00"
    _insert_cn_price(isolated_runtime, appid, 1050, 40, stamp)
    with isolated_runtime.database_connection() as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            INSERT INTO historical_lows(
                appid, itad_game_id, country, currency, amount,
                amount_int, amount_cny, fetched_at
            ) VALUES (?, ?, 'CN', 'CNY', 10.0, 1000, 10.0, ?)
            """,
            (appid, f"itad-{appid}", stamp),
        )
        price = isolated_runtime.latest_by_region(conn, appid)[0]

    assert price["historical_low_source"] == "itad"
    assert price["historical_low_cny"] == 10.0
    assert price["historical_low"] is True


def test_itad_low_only_applies_to_its_matching_region(isolated_runtime, insert_game):
    appid = insert_game(6104, "Regional Low")
    stamp = "2026-09-08T00:00:00+00:00"
    with isolated_runtime.database_connection() as conn:
        conn.row_factory = sqlite3.Row
        conn.executemany(
            """
            INSERT INTO price_snapshots(
                appid, region, currency, initial, final, discount_percent,
                final_formatted, source, fetched_at
            ) VALUES (?, ?, ?, ?, ?, 0, ?, 'steam', ?)
            """,
            [
                (appid, "US", "USD", 3000, 2000, "$ 20.00", stamp),
                (appid, "JP", "JPY", 3000, 2000, "JPY 2000", stamp),
            ],
        )
        conn.execute(
            """
            INSERT INTO historical_lows(
                appid, itad_game_id, country, currency, amount,
                amount_int, amount_cny, fetched_at
            ) VALUES (?, ?, 'US', 'USD', 10.0, 1000, 72.0, ?)
            """,
            (appid, f"itad-{appid}", stamp),
        )
        prices = {row["region"]: row for row in isolated_runtime.latest_by_region(conn, appid)}

    assert prices["US"]["historical_low_cny"] == 72.0
    assert prices["JP"]["historical_low_source"] == "site_observed"
    assert prices["JP"]["itad_historical_low_cny"] is None


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


def test_daily_homepage_boundary_is_0010_in_configured_business_time_zone():
    assert _runtime.daily_refresh_key(datetime(2026, 9, 6, 0, 9, 59)) == "2026-09-05"
    assert _runtime.daily_refresh_key(datetime(2026, 9, 6, 0, 10, 0)) == "2026-09-06"
    assert _runtime.daily_refresh_key(datetime(2026, 9, 5, 16, 9, 59, tzinfo=timezone.utc)) == "2026-09-05"
    assert _runtime.daily_refresh_key(datetime(2026, 9, 5, 16, 10, 0, tzinfo=timezone.utc)) == "2026-09-06"


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


def test_daily_historical_low_avoids_recent_repeats(isolated_runtime):
    refresh_key = isolated_runtime.daily_refresh_key()
    previous_key = (
        datetime.strptime(refresh_key, "%Y-%m-%d") - timedelta(days=1)
    ).strftime("%Y-%m-%d")
    with isolated_runtime.database_connection() as conn:
        conn.execute(
            """
            INSERT INTO daily_home_snapshots(
                recommendation_date, historical_low_appid, meme_url, created_at
            ) VALUES (?, ?, NULL, ?)
            """,
            (previous_key, 10, isolated_runtime.now_iso()),
        )

    _, selected_low, _ = isolated_runtime.ensure_daily_home_snapshot(
        [{"appid": 10}, {"appid": 20}],
        [],
    )

    assert selected_low["appid"] == 20


def test_daily_historical_low_does_not_repeat_when_all_candidates_are_recent(isolated_runtime):
    refresh_key = isolated_runtime.daily_refresh_key()
    with isolated_runtime.database_connection() as conn:
        for days_ago, appid in enumerate((10, 20), 1):
            previous_key = (
                datetime.strptime(refresh_key, "%Y-%m-%d") - timedelta(days=days_ago)
            ).strftime("%Y-%m-%d")
            conn.execute(
                """
                INSERT INTO daily_home_snapshots(
                    recommendation_date, historical_low_appid, meme_url, created_at
                ) VALUES (?, ?, NULL, ?)
                """,
                (previous_key, appid, isolated_runtime.now_iso()),
            )

    _, selected_low, _ = isolated_runtime.ensure_daily_home_snapshot(
        [{"appid": 10}, {"appid": 20}],
        [],
    )

    assert selected_low is None


def _insert_historical_low_candidate(runtime, appid, *, reviews, players, current=5000, low=50.0):
    stamp = runtime.now_iso()
    with runtime.database_connection() as conn:
        conn.execute(
            """
            INSERT INTO games(appid, name, header_image, is_free, tracked, updated_at)
            VALUES (?, ?, ?, 0, 0, ?)
            """,
            (appid, f"Game {appid}", f"https://example.test/{appid}.jpg", stamp),
        )
        conn.execute(
            """
            INSERT INTO game_latest_state(
                appid, current_players, cn_price, cn_price_final,
                cn_price_currency, total_reviews, updated_at
            ) VALUES (?, ?, '¥ 50.00', ?, 'CNY', ?, ?)
            """,
            (appid, players, current, reviews, stamp),
        )
        conn.execute(
            """
            INSERT INTO historical_lows(
                appid, itad_game_id, country, currency, amount,
                amount_int, amount_cny, fetched_at
            ) VALUES (?, ?, 'CN', 'CNY', ?, ?, ?, ?)
            """,
            (appid, f"itad-{appid}", low, int(low * 100), low, stamp),
        )


def test_popular_historical_low_uses_site_database_not_hot_games(isolated_runtime):
    _insert_historical_low_candidate(
        isolated_runtime,
        8001,
        reviews=isolated_runtime.HOME_POPULAR_MIN_REVIEWS,
        players=0,
    )

    games = isolated_runtime.list_popular_historical_low_games()

    assert [game["appid"] for game in games] == [8001]
    with isolated_runtime.database_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM hot_games").fetchone()[0] == 0


def test_popular_historical_low_excludes_low_popularity_and_non_low_prices(isolated_runtime):
    _insert_historical_low_candidate(isolated_runtime, 8101, reviews=9999, players=1999)
    _insert_historical_low_candidate(isolated_runtime, 8102, reviews=10000, players=0, current=5050)
    _insert_historical_low_candidate(isolated_runtime, 8103, reviews=10000, players=0, current=5051)

    games = isolated_runtime.list_popular_historical_low_games()

    assert [game["appid"] for game in games] == [8102]


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


def test_refresh_game_skips_store_requests_during_existing_cooldown(
    isolated_runtime, monkeypatch, insert_game
):
    runtime = isolated_runtime
    appid = insert_game(7001, "Cooldown Game")
    monkeypatch.setattr(
        runtime,
        "service_cooldown_remaining_seconds",
        lambda service: 300 if service == "steam_store" else 0,
    )
    monkeypatch.setattr(runtime, "fetch_appdetails", lambda *_args: pytest.fail("store request attempted"))
    monkeypatch.setattr(runtime, "fetch_reviews", lambda *_args: pytest.fail("review request attempted"))
    monkeypatch.setattr(runtime, "fetch_itad_prices", lambda *_args: pytest.fail("ITAD request attempted"))
    monkeypatch.setattr(runtime, "refresh_itad_history_lows", lambda *_args: pytest.fail("ITAD request attempted"))
    monkeypatch.setattr(runtime, "fetch_players", lambda _appid: 42)

    result = runtime.refresh_game(appid, include_details=True, mark_tracked=False)

    assert result["store_deferred"] is True
    assert result["errors"] == []
    with runtime.database_connection() as conn:
        assert conn.execute(
            "SELECT player_count FROM player_snapshots WHERE appid=?", (appid,)
        ).fetchone()[0] == 42


def test_refresh_game_stops_all_regions_after_first_rate_limit(
    isolated_runtime, monkeypatch, insert_game
):
    runtime = isolated_runtime
    appid = insert_game(7002, "Rate Limited Game")
    regions = []
    logs = []

    def fetch_details(_appid, region):
        regions.append(region)
        raise runtime.SteamRateLimited("steam_store rate limited, retry after 300s", "steam_store")

    monkeypatch.setattr(runtime, "service_cooldown_remaining_seconds", lambda _service: 0)
    monkeypatch.setattr(runtime, "fetch_appdetails", fetch_details)
    monkeypatch.setattr(runtime, "fetch_players", lambda _appid: 0)
    monkeypatch.setattr(runtime, "log_event", logs.append)

    result = runtime.refresh_game(
        appid,
        include_details=True,
        include_prices=True,
        include_players=True,
        include_reviews=True,
        mark_tracked=False,
        price_regions=["US", "CN", "JP"],
    )

    assert regions == ["US"]
    assert result["store_deferred"] is True
    assert len(result["errors"]) == 1
    assert len([line for line in logs if "steam store work deferred" in line]) == 1
