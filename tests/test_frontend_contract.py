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

    assert "activePage === 'monitor'" in source
    assert "monitoring.crawler.queue.active_total" in source
    assert "monitoring.database.storage.database_bytes" in source


def test_admin_controls_use_a_real_boolean_disabled_state():
    source = FRONTEND.read_text(encoding="utf-8")

    assert "controlBusy: null" in source
    assert "|| !!controlBusy" in source
    assert "this.controlBusy=null" in source
    assert "controlBusy: ''" not in source
    assert "controlConfirmAction: null" in source
    assert "@click=\"confirmControl\"" in source
    assert "confirm(`${this.controlLabel(action)}？`)" not in source


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
    assert "typeof value === 'string' && /^\\d+$/.test(value)" in source


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


def test_navigation_uses_history_and_cancels_stale_detail_updates():
    source = FRONTEND.read_text(encoding="utf-8")

    assert "window.addEventListener('popstate', this.restoreRouteFromUrl)" in source
    assert "window.history.pushState(state, '', url)" in source
    assert "cancelDetailNavigation()" in source
    assert "navigate: false" in source


def test_api_errors_are_status_aware_and_never_show_proxy_html():
    source = FRONTEND.read_text(encoding="utf-8")

    assert "res.headers.get('content-type')" in source
    assert "contentType.includes('application/json')" in source
    assert "if (res.status === 429)" in source
    assert "this.t('rateLimited')" in source
    assert "[502, 503, 504].includes(res.status)" in source
    assert "this.t('requestFailed', {status: res.status})" in source
    assert "jsonError:" not in source


def test_auth_form_validates_input_and_prevents_duplicate_submissions():
    source = FRONTEND.read_text(encoding="utf-8")

    assert 'minlength="3" maxlength="40"' in source
    assert 'minlength="8" maxlength="128"' in source
    assert ':disabled="authBusy"' in source
    assert "authBusy: false" in source
    assert "if (this.authBusy) return; this.authBusy=true" in source
    assert "finally { this.authBusy=false; }" in source
    assert "apiErrorMessage(data)" in source
    assert "Array.isArray(detail)" in source
