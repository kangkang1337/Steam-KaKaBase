import json
import os
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from backend.server import create_app


SYSTEM_BROWSERS = (
    Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
    Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
)


@pytest.fixture
def browser():
    with sync_playwright() as playwright:
        configured = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        executable = configured or next((str(path) for path in SYSTEM_BROWSERS if path.is_file()), None)
        launched = playwright.chromium.launch(headless=True, executable_path=executable)
        try:
            yield launched
        finally:
            launched.close()


@pytest.fixture
def frontend_server(isolated_runtime):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = uvicorn.Config(create_app(start_background=False), host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="playwright-uvicorn")
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=2)
        pytest.fail("Playwright test server did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def mock_frontend_api(page):
    state = {"tracked": False, "track_calls": 0, "untrack_calls": 0}
    hot_games = [
        {
            "appid": 11,
            "rank": 1,
            "name": "Free Alpha",
            "current_players": 900,
            "review_score": 70,
            "is_free": True,
            "is_paid": False,
            "cn_price": "免费",
            "header_image": "",
            "fetched_at": "2026-09-07T10:00:00+00:00",
        },
        {
            "appid": 22,
            "rank": 2,
            "name": "Paid Bravo",
            "current_players": 500,
            "review_score": 95,
            "is_free": False,
            "is_paid": True,
            "cn_price": "¥ 68.00",
            "cn_price_final": 6800,
            "header_image": "",
            "fetched_at": "2026-09-07T10:00:00+00:00",
        },
    ]
    detail = {
        "game": {
            "appid": 4242,
            "name": "Test Quest",
            "tracked": False,
            "header_image": "",
            "short_description": "A deterministic browser-test game.",
            "release_date": "2025-01-02",
            "site_peak_players": 120,
            "site_peak_recorded_since": "2026-01-01T00:00:00+00:00",
        },
        "prices": [],
        "priceHistory": [],
        "players": [{"player_count": 42, "fetched_at": "2026-09-07T10:00:00+00:00"}],
        "reviews": {"review_score": 90, "total_reviews": 1000, "fetched_at": "2026-09-07T10:00:00+00:00"},
    }

    def reply(route, payload, status=200):
        route.fulfill(
            status=status,
            content_type="application/json; charset=utf-8",
            body=json.dumps(payload, ensure_ascii=False),
        )

    def handle(route):
        request = route.request
        path = urlparse(request.url).path
        if path == "/api/status":
            return reply(route, {
                "player_refresh_minutes": 30,
                "price_refresh_hours": 24,
                "service_cooldowns": {},
                "direct_service_cooldowns": {},
                "proxy": {},
                "task_progress": {},
                "niche_max_reviews": 50000,
                "steam_catalog_count": 30000,
                "steam_catalog_game_count": 4000,
                "steam_catalog_excluded_count": 1000,
                "steam_catalog_enriched_count": 5000,
                "catalog_enrich_daily_limit": 1500,
            })
        if path == "/api/games" and request.method == "GET":
            games = [{**detail["game"], "tracked": True}] if state["tracked"] else []
            return reply(route, {"games": games})
        if path == "/api/home-picks":
            return reply(route, {"refresh_key": "2026-09-07", "historical_low": None, "niche": None, "meme": None})
        if path == "/api/hot-games":
            return reply(route, {"games": hot_games, "count": 2, "version": "test-v1", "queued": False})
        if path == "/api/hot-games/ensure":
            return reply(route, {"queued": False, "count": 2, "target": 100, "preview_queued": 0})
        if path == "/api/niche-pool":
            niche = [{**hot_games[1], "rank": 1, "peak_players": 700, "weighted_score": 0.91}]
            return reply(route, {"games": niche, "count": 1, "pool_count": 20, "selection_mode": "all"})
        if path == "/api/search":
            return reply(route, {"items": [{"appid": 4242, "name": "Test Quest", "tiny_image": ""}]})
        if path == "/api/games/4242":
            return reply(route, {**detail, "game": {**detail["game"], "tracked": state["tracked"]}})
        if path == "/api/track" and request.method == "POST":
            state["tracked"] = True
            state["track_calls"] += 1
            return reply(route, {"ok": True, "appid": 4242, "queued": False})
        if path == "/api/untrack" and request.method == "POST":
            state["tracked"] = False
            state["untrack_calls"] += 1
            return reply(route, {"ok": True, "appid": 4242, "tracked": False})
        return reply(route, {"error": f"unexpected mocked endpoint: {path}"}, status=404)

    page.route("**/api/**", handle)
    return state


def open_test_page(browser, frontend_server, *, viewport=None):
    page = browser.new_page(viewport=viewport or {"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    state = mock_frontend_api(page)
    page.goto(frontend_server, wait_until="domcontentloaded")
    expect(page.locator(".brand")).to_contain_text("Steam-KaKaBase", timeout=15000)
    return page, state, errors


def test_navigation_hot_filters_and_niche_pool(browser, frontend_server):
    page, _, errors = open_test_page(browser, frontend_server)
    try:
        expect(page.locator(".home-lines")).to_contain_text("Welcome to SteamKaKaBase!")
        expect(page.locator(".home-monitor span")).to_contain_text("目录已收录 30,000")
        page.get_by_role("button", name="打开导航菜单").click()
        page.locator(".app-menu").get_by_role("button", name="热门榜", exact=True).click()
        expect(page.locator(".hot-row")).to_have_count(2)

        page.get_by_text("仅查看付费游戏", exact=True).click()
        expect(page.locator(".hot-row")).to_have_count(1)
        expect(page.locator(".hot-row")).to_contain_text("Paid Bravo")

        page.get_by_text("仅查看付费游戏", exact=True).click()
        page.get_by_text("按好评率排序", exact=True).click()
        expect(page.locator(".hot-row").first).to_contain_text("Paid Bravo")

        page.get_by_role("button", name="打开导航菜单").click()
        page.locator(".app-menu").get_by_role("button", name="小众池", exact=True).click()
        expect(page.locator(".hot-row")).to_have_count(1)
        expect(page.locator(".hot-row")).to_contain_text("Paid Bravo")
        expect(page.locator(".panel-head .muted")).to_contain_text("当前池 20 条，展示全部")
        expect(page.locator(".panel-head .muted")).not_to_contain_text("未满")
        assert errors == []
    finally:
        page.close()


def test_search_detail_favorite_round_trip(browser, frontend_server):
    page, state, errors = open_test_page(browser, frontend_server, viewport={"width": 390, "height": 844})
    try:
        search = page.get_by_role("textbox", name="搜索 Steam 游戏")
        search.fill("Test Quest")
        expect(page.locator(".suggestion")).to_contain_text("Test Quest", timeout=5000)
        page.locator(".suggestion").click()

        expect(page.locator(".hero h1")).to_have_text("Test Quest")
        favorite = page.locator(".favorite-btn")
        expect(favorite).to_have_text("收藏")
        favorite.click()
        expect(favorite).to_have_text("已收藏")
        assert state["track_calls"] == 1

        favorite.click()
        expect(favorite).to_have_text("收藏")
        expect(page.locator(".status")).to_contain_text("已取消收藏：Test Quest")
        assert state["untrack_calls"] == 1
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        assert errors == []
    finally:
        page.close()
