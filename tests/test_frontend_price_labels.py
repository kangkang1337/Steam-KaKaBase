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
