from pathlib import Path


FRONTEND = Path(__file__).resolve().parents[1] / "steamkb.html"


def test_frontend_consumes_cooldown_and_proxy_status():
    source = FRONTEND.read_text(encoding="utf-8")

    assert "status.service_cooldowns" in source
    assert "status.direct_service_cooldowns" in source
    assert "status.proxy || {}" in source
    assert "当前使用代理回退" in source
    assert "直连冷却中" in source
