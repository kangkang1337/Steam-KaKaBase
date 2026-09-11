import json
import os
import re
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
CHROMIUM_UNSAFE_PORTS = {
    1, 7, 9, 11, 13, 15, 17, 19, 20, 21, 22, 23, 25, 37, 42, 43, 53,
    69, 77, 79, 87, 95, 101, 102, 103, 104, 109, 110, 111, 113, 115, 117,
    119, 123, 135, 137, 139, 143, 161, 179, 389, 427, 465, 512, 513, 514,
    515, 526, 530, 531, 532, 540, 548, 554, 556, 563, 587, 601, 636, 989,
    990, 993, 995, 1719, 1720, 1723, 2049, 3659, 4045, 5060, 5061, 6000,
    6566, 6665, 6666, 6667, 6668, 6669, 6697, 10080,
}


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
    port = None
    while port is None or port in CHROMIUM_UNSAFE_PORTS:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
    config = uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error")
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
    state = {"tracked": False, "track_calls": 0, "untrack_calls": 0, "detail_calls": 0}
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
            "name_zh": "测试任务",
            "name_en": "Test Quest",
            "tracked": False,
            "header_image": "",
            "short_description": "A deterministic browser-test game.",
            "release_date": "2025-01-02",
            "site_peak_players": 120,
            "site_peak_recorded_since": "2026-01-01T00:00:00+00:00",
        },
        "prices": [],
        "priceHistory": [],
        "players": [
            {"player_count": 30, "fetched_at": "2026-09-07T08:00:00+00:00"},
            {"player_count": 42, "fetched_at": "2026-09-07T10:00:00+00:00"},
            {"player_count": 36, "fetched_at": "2026-09-07T12:00:00+00:00"},
        ],
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
            if "scroll" in request.url:
                return reply(route, {"items": [
                    {"appid": 5000 + index, "name": f"Scroll Result {index + 1}", "tiny_image": ""}
                    for index in range(12)
                ]})
            return reply(route, {"items": [{"appid": 4242, "name": "Test Quest", "name_zh": "测试任务", "name_en": "Test Quest", "tiny_image": ""}]})
        if path == "/api/games/4242":
            state["detail_calls"] += 1
            if state["detail_calls"] == 1:
                return reply(route, {
                    **detail,
                    "game": {**detail["game"], "tracked": state["tracked"]},
                    "refresh_pending": True,
                    "pending_fields": ["prices"],
                })
            cn_price = {
                "region": "CN", "final_formatted": "¥ 20.00", "final": 2000,
                "currency": "CNY", "discount_percent": 0,
                "fetched_at": "2026-09-07T10:01:00+00:00",
            }
            us_price = {
                "region": "US", "final_formatted": "$ 10.00", "final": 1000,
                "currency": "USD", "discount_percent": 0,
                "fetched_at": "2026-09-07T10:01:00+00:00",
            }
            return reply(route, {
                **detail,
                "game": {**detail["game"], "tracked": state["tracked"]},
                "prices": [cn_price, us_price],
                "priceHistory": [cn_price, us_price],
                "refresh_pending": False,
                "pending_fields": [],
            })
        if path == "/api/games/4242/interest" and request.method == "POST":
            return reply(route, {"ok": True, "appid": 4242, "queued": True, "reason": None})
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
    page.on("pageerror", lambda error: errors.append(error.stack or str(error)))
    state = mock_frontend_api(page)
    page.goto(frontend_server, wait_until="domcontentloaded")
    expect(page.locator(".brand")).to_contain_text("Steam-KaKaBase", timeout=15000)
    return page, state, errors


def test_navigation_hot_filters_and_niche_pool(browser, frontend_server):
    page, _, errors = open_test_page(browser, frontend_server)
    try:
        expect(page.locator(".home-lines")).to_contain_text("欢迎来到 SteamKaKaBase！")
        expect(page.locator(".home-monitor span")).to_contain_text("目录已收录 30,000")
        page.get_by_role("button", name="EN", exact=True).click()
        expect(page.locator(".home-lines")).to_contain_text("Welcome to SteamKaKaBase!")
        expect(page.get_by_role("textbox", name="Search Steam games")).to_be_visible()
        page.get_by_role("button", name="中文", exact=True).click()
        page.get_by_role("button", name="打开导航菜单").click()
        page.locator(".app-menu").get_by_role("button", name="热门榜", exact=True).click()
        expect(page.locator(".hot-row")).to_have_count(2)
        expect(page).to_have_url(re.compile(r"#page=hot$"))
        page.go_back()
        expect(page.locator(".home-lines")).to_contain_text("欢迎来到 SteamKaKaBase！")

        page.get_by_role("button", name="打开导航菜单").click()
        page.locator(".app-menu").get_by_role("button", name="热门榜", exact=True).click()
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
        expect(page.locator(".suggestion")).to_contain_text("测试任务", timeout=5000)
        page.locator(".suggestion").click()

        expect(page.locator(".hero h1")).to_have_text("测试任务")
        expect(page.locator(".hero")).to_contain_text("¥ 20.00", timeout=15000)
        chart_axes = page.evaluate("""
          () => [...document.querySelectorAll('.chart')].map(element => {
            const chart = window.echarts?.getInstanceByDom(element);
            return {
                  axis: chart?.getOption()?.xAxis?.[0]?.type,
                  tooltipTrigger: chart?.getOption()?.tooltip?.[0]?.trigger,
                  tooltipTriggerOn: chart?.getOption()?.tooltip?.[0]?.triggerOn,
                      tooltipShow: chart?.getOption()?.tooltip?.[0]?.show,
                      barWidth: chart?.getOption()?.series?.[0]?.barWidth,
                      labelCount: (chart?.getOption()?.xAxis?.[0]?.data || []).filter((value, index) => (
                        // ECharts passes category values to the formatter as strings.
                        chart.getOption().xAxis[0].axisLabel.formatter(String(value), index) !== ''
                      )).length,
                  width: chart?.getWidth(),
              height: chart?.getHeight(),
              points: chart?.getOption()?.series?.reduce((total, series) => total + (series.data || []).length, 0)
            };
          })
        """)
        assert [chart["axis"] for chart in chart_axes] == ["time", "category"]
        assert chart_axes[1]["tooltipShow"] is False
        assert chart_axes[1]["tooltipTriggerOn"] == "none"
        assert chart_axes[1]["barWidth"] == "60%"
        assert 1 <= chart_axes[1]["labelCount"] <= 7
        assert all(chart["width"] > 0 and chart["height"] > 0 and chart["points"] > 0 for chart in chart_axes)
        assert page.evaluate("document.querySelector('.chart-hover-surface').__steamkbTooltipBound === true")
        page.locator(".chart").nth(1).scroll_into_view_if_needed()
        hover_point = page.evaluate("""
          () => {
            const element = document.querySelectorAll('.chart')[1];
            const chart = window.echarts.getInstanceByDom(element);
            const bars = chart.getZr().storage.getDisplayList().filter(item => item.style?.fill === '#66c0f4');
            const bar = bars[Math.floor(bars.length / 2)];
            const shape = bar.shape;
            const canvasPoint = bar.transformCoordToGlobal(
              shape.x + shape.width / 2,
              shape.y + shape.height / 2
            );
            const rect = element.getBoundingClientRect();
            return {
              x: rect.left + canvasPoint[0],
              y: rect.top + canvasPoint[1]
            };
          }
        """)
        page.mouse.move(hover_point["x"], hover_point["y"])
        assert errors == []
        expect(page.locator("body")).to_contain_text("在线人数:", timeout=3000)
        assert state["detail_calls"] >= 2

        page.get_by_label("选择价格地区").select_option("US")
        expect(page.locator(".hero")).to_contain_text("$ 10.00")
        price_view = page.evaluate("""
          () => {
            const chart = window.echarts?.getInstanceByDom(document.querySelector('.chart'));
            const option = chart?.getOption();
            return {
              seriesName: option?.series?.[0]?.name,
              price: option?.series?.[0]?.data?.[0]?.value?.[1],
              firstRegion: document.querySelector('table tbody tr.selected')?.textContent
            };
          }
        """)
        assert price_view["seriesName"] == "美国"
        assert price_view["price"] == 10
        assert price_view["firstRegion"].startswith("US")

        page.get_by_role("button", name="EN", exact=True).click()
        expect(page.locator(".hero h1")).to_have_text("Test Quest")
        expect(page.locator(".hero")).to_contain_text("$ 10.00")
        assert page.get_by_label("Select price region").input_value() == "US"
        page.get_by_role("button", name="中文", exact=True).click()

        favorite = page.locator(".favorite-btn")
        expect(favorite).to_have_text("收藏")
        favorite.click()
        expect(favorite).to_have_text("已收藏")
        assert state["track_calls"] == 0

        favorite.click()
        expect(favorite).to_have_text("收藏")
        expect(page.locator(".status")).to_contain_text("已取消收藏：Test Quest")
        assert state["untrack_calls"] == 0
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        assert errors == []
    finally:
        page.close()


def test_detail_poll_cannot_pull_user_back_from_hot_page(browser, frontend_server):
    page, _, errors = open_test_page(browser, frontend_server)
    try:
        search = page.get_by_role("textbox", name="搜索 Steam 游戏")
        search.fill("Test Quest")
        page.locator(".suggestion").click()
        expect(page.locator(".hero h1")).to_have_text("测试任务")

        page.get_by_role("button", name="打开导航菜单").click()
        page.locator(".app-menu").get_by_role("button", name="热门榜", exact=True).click()
        expect(page.locator(".hot-row")).to_have_count(2)
        # The initial detail response is deliberately incomplete and schedules
        # a ten-second cache poll. It must not force the view back to details.
        page.wait_for_timeout(10_500)
        expect(page.locator(".hot-row")).to_have_count(2)
        assert errors == []
    finally:
        page.close()


def test_search_results_scroll_inside_dropdown(browser, frontend_server):
    page, _, errors = open_test_page(browser, frontend_server)
    try:
        search = page.get_by_role("textbox", name="搜索 Steam 游戏")
        search.fill("scroll")
        dropdown = page.locator(".suggestions")
        expect(dropdown.locator(".suggestion")).to_have_count(12, timeout=5000)
        dimensions = dropdown.evaluate(
            "element => ({clientHeight: element.clientHeight, scrollHeight: element.scrollHeight})"
        )
        assert dimensions["scrollHeight"] > dimensions["clientHeight"]
        dropdown.evaluate("element => { element.scrollTop = element.scrollHeight; }")
        assert dropdown.evaluate(
            "element => element.scrollTop + element.clientHeight >= element.scrollHeight - 1"
        )
        assert errors == []
    finally:
        page.close()


def test_mobile_hot_rows_keep_columns_separate(browser, frontend_server):
    page, _, errors = open_test_page(
        browser, frontend_server, viewport={"width": 390, "height": 844}
    )
    try:
        page.get_by_role("button", name="打开导航菜单").click()
        page.locator(".app-menu").get_by_role("button", name="热门榜", exact=True).click()
        expect(page.locator(".hot-row")).to_have_count(2)
        layout = page.locator(".hot-row").first.evaluate(
            """row => {
              const box = selector => {
                const rect = row.querySelector(selector).getBoundingClientRect();
                return {left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom};
              };
              const rowRect = row.getBoundingClientRect();
              return {
                row: {left: rowRect.left, right: rowRect.right},
                image: box('img'), title: box('.hot-title'), review: box('.hot-review'),
                players: box('.hot-players'), price: box('.hot-price')
              };
            }"""
        )
        assert layout["image"]["right"] <= layout["title"]["left"]
        assert layout["title"]["bottom"] <= layout["review"]["top"]
        assert layout["review"]["right"] <= layout["players"]["left"]
        assert layout["players"]["right"] <= layout["price"]["left"]
        for key in ("image", "title", "review", "players", "price"):
            assert layout["row"]["left"] <= layout[key]["left"]
            assert layout[key]["right"] <= layout["row"]["right"]
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        assert errors == []
    finally:
        page.close()
