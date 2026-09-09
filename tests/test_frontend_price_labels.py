from pathlib import Path


HTML = (Path(__file__).parents[1] / "steamkb.html").read_text(encoding="utf-8")


def test_detail_price_card_renders_one_historical_low_note():
    assert HTML.count("historicalLowLabel('CN')") == 2
    assert "historicalLowNote('CN')" not in HTML
    assert "historicalLowStatus('CN')" not in HTML


def test_discount_card_does_not_hide_historical_low_note():
    assert ".stat.price-discount .stat-note {\n      display: none;" not in HTML
    assert "ITAD 历史最低" in HTML
    assert "本站观测最低" in HTML


def test_game_images_retry_before_using_the_local_placeholder():
    assert HTML.count('@error="retryImage"') == 6
    assert "cachedImage(game)" in HTML
    assert "/api/image-cache?appid=${appid}" in HTML
    assert "cachedImage(url)" not in HTML
    assert "retryImage(event)" in HTML
    assert "attempts >= 2" in HTML
