from pathlib import Path


FRONTEND = Path(__file__).resolve().parents[1] / "steamkb.html"


def test_frontend_consumes_cooldown_and_proxy_status():
    source = FRONTEND.read_text(encoding="utf-8")

    assert "status.service_cooldowns" in source
    assert "status.direct_service_cooldowns" in source
    assert "status.proxy || {}" in source
    assert "当前使用代理回退" not in source
    assert "直连冷却中" in source


def test_frontend_consumes_crawler_and_queue_monitoring():
    source = FRONTEND.read_text(encoding="utf-8")

    assert "status.crawler?.running" in source
    assert "status.task_monitor" in source
    assert "采集器未运行" in source


def test_charts_use_deduplicated_axes_with_overlap_protection():
    source = FRONTEND.read_text(encoding="utf-8")

    assert "type: 'time'" in source
    assert "type: 'category'" in source
    assert "playerSnapshotPoints()" in source
    assert "hideOverlap: true" in source
    assert "interval: 0" in source
    assert "formatter: (value, index) => playerLabelIndexes.has(index)" in source
    assert "new Map()).values()].sort" in source
    assert "chartAxisTime(value, span = 0)" in source


def test_language_switch_is_persistent_and_independent_from_price_region():
    source = FRONTEND.read_text(encoding="utf-8")

    assert "steamkb.locale" in source
    assert "steamkb.priceRegion" in source
    assert "setLocale('zh-CN')" in source
    assert "setLocale('en-US')" in source
    assert "document.documentElement.lang = locale" in source
    assert "this.selectedRegion =" not in source[source.index("async setLocale(locale)"):source.index("regionLabel(region)")]


def test_game_name_uses_localized_api_fields_with_fallbacks():
    source = FRONTEND.read_text(encoding="utf-8")

    assert "game?.name_en" in source
    assert "game?.name_zh" in source
    assert "preferred || game?.name" in source
