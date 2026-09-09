import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _load_offsite_module():
    spec = importlib.util.spec_from_file_location(
        "offsite_backup", ROOT / "scripts" / "offsite_backup.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frontend_dependencies_are_same_origin_and_pinned():
    html = (ROOT / "steamkb.html").read_text(encoding="utf-8")
    assert 'src="/assets/vendor/vue-3.5.13.global.prod.js"' in html
    assert 'src="/assets/vendor/echarts-5.6.0.min.js"' in html
    assert "unpkg.com" not in html
    assert "cdn.jsdelivr.net/npm" not in html


def test_nginx_limits_public_search_and_admin_routes():
    site = (ROOT / "deploy/nginx/steam-kakabase.conf").read_text(encoding="utf-8")
    zones = (ROOT / "deploy/nginx/steam-kakabase-rate-limits.conf").read_text(
        encoding="utf-8"
    )
    assert "zone=steamkb_search" in zones
    assert "zone=steamkb_admin" in zones
    assert "limit_req zone=steamkb_search" in site
    assert "limit_req zone=steamkb_admin" in site
    assert "proxy_pass http://127.0.0.1:8765" in site
    assert "include /etc/nginx/proxy_params;" in site
    assert "include proxy_params;" not in site


def test_linux_nginx_validation_uses_writable_runtime_files():
    validator = (ROOT / "deploy/validate_linux.sh").read_text(encoding="utf-8")
    assert "pid ${TEMP_DIR}/nginx.pid;" in validator
    assert "access_log ${TEMP_DIR}/access.log;" in validator
    assert "error_log stderr;" in validator
    assert "s|listen 80;|listen 18080;|g" in validator
    assert "s|listen \\[::\\]:80;|listen [::]:18080;|g" in validator
    assert "client_body_temp_path ${TEMP_DIR}/client_body;" in validator
    assert "proxy_temp_path ${TEMP_DIR}/proxy;" in validator
    assert "proxy_headers_hash_max_size 1024;" in validator
    assert "proxy_headers_hash_bucket_size 128;" in validator


def test_systemd_services_are_separate_and_sandboxed():
    web = (ROOT / "deploy/systemd/steam-kakabase-web.service").read_text(encoding="utf-8")
    crawler = (ROOT / "deploy/systemd/steam-kakabase-crawler.service").read_text(
        encoding="utf-8"
    )
    assert "python -m backend.main" in web
    assert "python -m backend.crawler_main" in crawler
    for unit in (web, crawler):
        assert "NoNewPrivileges=true" in unit
        assert "ProtectSystem=strict" in unit
        assert "ReadWritePaths=@@APP_DIR@@/data" in unit


def test_offsite_upload_uses_scoped_remote_and_retention(tmp_path):
    module = _load_offsite_module()
    backup = tmp_path / "steamkb-daily.sqlite3"
    backup.write_bytes(b"test")
    calls = []

    def runner(command, check):
        calls.append((command, check))

    target = module.upload_backup(
        backup,
        "vultr:private/steamkb",
        hostname="host-a",
        retention_days=30,
        runner=runner,
    )

    assert target.endswith("/host-a/steamkb-daily.sqlite3")
    assert calls[0][0][:2] == ["rclone", "copyto"]
    assert calls[1][0][:2] == ["rclone", "delete"]
    assert "30d" in calls[1][0]
