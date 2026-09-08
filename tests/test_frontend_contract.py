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


def test_charts_use_deduplicated_time_axes_with_overlap_protection():
    source = FRONTEND.read_text(encoding="utf-8")

    assert source.count("type: 'time'") >= 2
    assert source.count("hideOverlap: true") >= 2
    assert "new Map()).values()].sort" in source
    assert "chartAxisTime(value, span = 0)" in source
