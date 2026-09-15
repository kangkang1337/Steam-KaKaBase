"""Steam and ITAD HTTP transport, retries, proxy fallback and endpoint parsing."""

import asyncio
import hashlib
import json
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config
from .external_errors import ExternalDataUnavailable, SteamRateLimited
from .logging_utils import log_event, safe_log_url
from .pricing import amount_int_to_cny
from .utils import now_iso, retry_delay


ALLOWED_IMAGE_HOSTS = {
    "shared.akamai.steamstatic.com", "shared.cloudflare.steamstatic.com",
    "cdn.akamai.steamstatic.com", "cdn.cloudflare.steamstatic.com",
    "steamcdn-a.akamaihd.net",
}
START_COOLDOWN_UNTIL = time.time() + config.START_COOLDOWN_SECONDS
SERVICE_COOLDOWN_UNTIL = {
    "steam_api": START_COOLDOWN_UNTIL, "steam_store": START_COOLDOWN_UNTIL,
    "itad": 0.0, "image_cdn": 0.0,
}
SERVICE_COOLDOWN_LOCK = threading.Lock()
SERVICE_RATE_LIMIT_STATUS = {
    service: {"count": 0, "last_at": None} for service in config.EXTERNAL_SERVICES
}
PROXY_STATUS = {
    "configured": bool(config.STEAM_PROXY_URL), "enabled": config.USE_PROXY,
    "reachable": None, "tls_verify": config.STEAM_PROXY_VERIFY_TLS,
    "fallback_successes": 0, "fallback_failures": 0,
    "message": "直连优先" if not config.USE_PROXY else "待检测",
}
PROXY_FALLBACK_LOCK = threading.Lock()
PROXY_FALLBACK_LOGGED_AT = {}
DIRECT_COOLDOWN_LOCK = threading.Lock()
DIRECT_COOLDOWN_UNTIL = {service: 0.0 for service in config.EXTERNAL_SERVICES}
DIRECT_FAILURE_COUNT = {service: 0 for service in config.EXTERNAL_SERVICES}


def external_service_for_url(url):
    host = (urllib.parse.urlsplit(str(url)).hostname or "").lower()
    if host == "api.isthereanydeal.com" or host.endswith(".isthereanydeal.com"):
        return "itad"
    if host == "store.steampowered.com":
        return "steam_store"
    if host in ALLOWED_IMAGE_HOSTS or host == "steamstatic.com" or host.endswith(".steamstatic.com"):
        return "image_cdn"
    return "steam_api"


def service_cooldown_remaining_seconds(service):
    with SERVICE_COOLDOWN_LOCK:
        return max(0, int(SERVICE_COOLDOWN_UNTIL.get(service, 0) - time.time()))


def steam_cooldown_remaining_seconds():
    return max(service_cooldown_remaining_seconds("steam_api"), service_cooldown_remaining_seconds("steam_store"))


def probe_proxy():
    if not config.USE_PROXY:
        PROXY_STATUS.update(message="直连优先，代理回退已关闭")
        return
    if not config.STEAM_PROXY_URL:
        PROXY_STATUS.update(reachable=False, message="代理回退已启用，但未配置 STEAMKB_PROXY_URL")
        return
    parsed = urllib.parse.urlparse(config.STEAM_PROXY_URL)
    host, port = parsed.hostname, parsed.port
    if not host or not port:
        PROXY_STATUS.update(reachable=False, message="代理地址格式无效")
        log_event("proxy unavailable: invalid STEAMKB_PROXY_URL")
        return
    try:
        with socket.create_connection((host, port), timeout=2):
            pass
        PROXY_STATUS.update(reachable=True, message="代理可连接")
        log_event(f"proxy reachable: {host}:{port}")
    except OSError as exc:
        PROXY_STATUS.update(reachable=False, message=f"代理不可连接: {exc}")
        log_event(f"proxy unavailable: {exc}; Steam requests will use direct connection")


def proxy_fallback_enabled():
    return config.USE_PROXY and PROXY_STATUS.get("reachable") is True


def direct_cooldown_remaining_seconds(service=None):
    with DIRECT_COOLDOWN_LOCK:
        if service:
            return max(0, int(DIRECT_COOLDOWN_UNTIL.get(service, 0) - time.time()))
        return max((max(0, int(value - time.time())) for value in DIRECT_COOLDOWN_UNTIL.values()), default=0)


def reserve_direct_attempt(service):
    if not proxy_fallback_enabled():
        return True
    now = time.time()
    with DIRECT_COOLDOWN_LOCK:
        if DIRECT_COOLDOWN_UNTIL.get(service, 0) > now:
            return False
        if DIRECT_FAILURE_COUNT.get(service, 0):
            DIRECT_COOLDOWN_UNTIL[service] = now + min(60, max(10, int(config.STEAM_TIMEOUT_SECONDS) + 5))
        return True


def record_direct_success(service):
    with DIRECT_COOLDOWN_LOCK:
        DIRECT_COOLDOWN_UNTIL[service] = 0.0
        DIRECT_FAILURE_COUNT[service] = 0


def set_direct_cooldown(service, reason=None):
    if not proxy_fallback_enabled():
        return
    now = time.time()
    until = now + (config.DIRECT_COOLDOWN_MINUTES * 60)
    with DIRECT_COOLDOWN_LOCK:
        was_active = DIRECT_COOLDOWN_UNTIL.get(service, 0) > now
        DIRECT_COOLDOWN_UNTIL[service] = max(DIRECT_COOLDOWN_UNTIL.get(service, 0), until)
        DIRECT_FAILURE_COUNT[service] = DIRECT_FAILURE_COUNT.get(service, 0) + 1
    if not was_active:
        suffix = f" reason={reason}" if reason else ""
        log_event(f"direct connection cooldown enabled service={service} for {config.DIRECT_COOLDOWN_MINUTES} minutes{suffix}")


def log_proxy_fallback_once(url, reason):
    parsed = urllib.parse.urlsplit(url)
    path = re.sub(r"/appreviews/\d+(?:/|$)", "/appreviews/{appid}", parsed.path)
    key = f"{parsed.scheme}://{parsed.netloc}{path}"
    now = time.time()
    with PROXY_FALLBACK_LOCK:
        if now - PROXY_FALLBACK_LOGGED_AT.get(key, 0) < 300:
            return
        PROXY_FALLBACK_LOGGED_AT[key] = now
    log_event(f"direct request failed; proxy fallback started url={key} reason={reason}")


def record_proxy_fallback(success, error=None):
    with PROXY_FALLBACK_LOCK:
        key = "fallback_successes" if success else "fallback_failures"
        PROXY_STATUS[key] = int(PROXY_STATUS.get(key) or 0) + 1
        if success and str(PROXY_STATUS.get("message") or "").startswith("代理回退失败"):
            PROXY_STATUS["message"] = "代理可连接，回退正常"
        elif not success and error:
            PROXY_STATUS["message"] = f"代理回退失败: {str(error)[:120]}"


def set_service_cooldown(service, minutes=10):
    now = time.time()
    until = now + (minutes * 60)
    with SERVICE_COOLDOWN_LOCK:
        previous = SERVICE_COOLDOWN_UNTIL.get(service, 0)
        was_active = previous > now
        SERVICE_COOLDOWN_UNTIL[service] = max(previous, until)
        if not was_active:
            metric = SERVICE_RATE_LIMIT_STATUS.setdefault(service, {"count": 0, "last_at": None})
            metric["count"] += 1
            metric["last_at"] = now_iso()
    if not was_active:
        log_event(f"external service cooldown enabled service={service} for {minutes} minutes")


def set_steam_cooldown(minutes=10, service="steam_api"):
    set_service_cooldown(service, minutes)


def check_service_cooldown(service):
    with SERVICE_COOLDOWN_LOCK:
        remaining = SERVICE_COOLDOWN_UNTIL.get(service, 0) - time.time()
    if remaining > 0:
        raise SteamRateLimited(f"{service} rate limited, retry after {int(remaining)}s", service)


def check_steam_cooldown(service="steam_api"):
    check_service_cooldown(service)


def require_httpx():
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("httpx is required; run: python -m pip install httpx") from exc
    return httpx


def steam_httpx_options():
    return {"proxy": None, "trust_env": False}


def proxy_httpx_options():
    return {
        "proxy": config.STEAM_PROXY_URL if proxy_fallback_enabled() else None,
        "trust_env": False,
        "verify": config.STEAM_PROXY_VERIFY_TLS,
    }


def itad_headers():
    return {
        "User-Agent": config.STEAM_USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


class _NoImageRedirect(urllib.request.HTTPRedirectHandler):
    """Keep an allowed CDN URL from becoming a request to an arbitrary host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def cache_image(url):
    service = "image_cdn"
    check_service_cooldown(service)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in ALLOWED_IMAGE_HOSTS:
        raise ValueError("unsupported image host")
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        suffix = ".img"
    config.IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = config.IMAGE_CACHE_DIR / (hashlib.sha256(url.encode("utf-8")).hexdigest() + suffix)
    if cache_path.is_file() and cache_path.stat().st_size > 0:
        return cache_path
    req = urllib.request.Request(url, headers={
        "User-Agent": config.STEAM_USER_AGENT,
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    })
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoImageRedirect())
        with opener.open(req, timeout=config.STEAM_TIMEOUT_SECONDS) as res:
            content_type = res.headers.get_content_type()
            if not content_type.startswith("image/"):
                raise ValueError(f"unexpected content type: {content_type}")
            body = res.read(config.IMAGE_CACHE_MAX_FILE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            set_service_cooldown(service, 10)
            raise SteamRateLimited("image_cdn HTTP 429", service) from exc
        raise
    if len(body) > config.IMAGE_CACHE_MAX_FILE_BYTES:
        raise ValueError("image exceeds cache file limit")
    cache_path.write_bytes(body)
    return cache_path


class ItadLookupBatchError(ExternalDataUnavailable):
    def __init__(self, message, partial_results=None):
        super().__init__(message)
        self.partial_results = dict(partial_results or {})


def _http_status(exc):
    return getattr(getattr(exc, "response", None), "status_code", None)


def _chunks(rows, size):
    for index in range(0, len(rows), size):
        yield rows[index:index + size]


def parse_hot_chart(payload):
    """Normalize either official Steam chart response shape without persistence."""
    response = payload.get("response") if isinstance(payload, dict) else {}
    candidates = []

    def visit(value):
        if isinstance(value, dict):
            appid = value.get("appid") or value.get("app_id") or value.get("steam_appid")
            if appid:
                item = value.get("item") if isinstance(value.get("item"), dict) else {}
                assets = item.get("assets") if isinstance(item.get("assets"), dict) else {}
                candidates.append({
                    "appid": int(appid), "rank": value.get("rank"),
                    "name": value.get("name") or item.get("name"),
                    "current_players": value.get("concurrent_in_game") or value.get("current_players") or value.get("players"),
                    "peak_players": value.get("peak_in_game") or value.get("peak_players"),
                    "header_image": value.get("header_image") or assets.get("header") or assets.get("small_capsule"),
                    "source": "steam_charts",
                })
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(response)
    seen, rows = set(), []
    for index, row in enumerate(candidates, 1):
        appid = row["appid"]
        if appid in seen:
            continue
        seen.add(appid)
        row["rank"] = int(row.get("rank") or index)
        rows.append(row)
        if len(rows) >= config.HOTLIST_TARGET:
            break
    return rows


def request_json(url, timeout=None, headers=None, missing_statuses=None, max_retries=None, service=None):
    timeout = config.STEAM_TIMEOUT_SECONDS if timeout is None else timeout
    service = service or external_service_for_url(url)
    check_service_cooldown(service)
    missing_statuses = set(missing_statuses or [])
    base_headers = {
        "User-Agent": config.STEAM_USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
    }
    if headers:
        base_headers.update(headers)
    last_exc = None
    retries = config.STEAM_MAX_RETRIES if max_retries is None else max(0, int(max_retries))
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=base_headers)
        try:
            try_direct = reserve_direct_attempt(service)
            if try_direct:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                try:
                    res = opener.open(req, timeout=timeout)
                    record_direct_success(service)
                except urllib.error.HTTPError as direct_http_exc:
                    record_direct_success(service)
                    if proxy_fallback_enabled() and (
                        direct_http_exc.code >= 500 or direct_http_exc.code == 403
                    ):
                        log_proxy_fallback_once(url, f"HTTP {direct_http_exc.code}")
                        try_direct = False
                    else:
                        raise
                except (urllib.error.URLError, TimeoutError, OSError) as direct_exc:
                    if not proxy_fallback_enabled():
                        raise
                    set_direct_cooldown(service, type(direct_exc).__name__)
                    log_proxy_fallback_once(url, type(direct_exc).__name__)
                    try_direct = False
            if not try_direct:
                proxy_handler = urllib.request.ProxyHandler(
                    {"http": config.STEAM_PROXY_URL, "https": config.STEAM_PROXY_URL}
                )
                handlers = [proxy_handler]
                if not config.STEAM_PROXY_VERIFY_TLS:
                    handlers.append(
                        urllib.request.HTTPSHandler(context=ssl._create_unverified_context())
                    )
                try:
                    res = urllib.request.build_opener(*handlers).open(req, timeout=timeout)
                    record_proxy_fallback(True)
                except urllib.error.HTTPError:
                    record_proxy_fallback(True)
                    raise
                except Exception as proxy_exc:
                    record_proxy_fallback(False, proxy_exc)
                    raise
            with res:
                charset = res.headers.get_content_charset() or "utf-8"
                return json.loads(res.read().decode(charset, errors="replace"))
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code in missing_statuses:
                raise ExternalDataUnavailable(f"HTTP {exc.code}: external data unavailable")
            if exc.code == 429:
                set_service_cooldown(service, 10)
                raise SteamRateLimited(f"{service} HTTP 429", service)
            if exc.code not in config.STEAM_RETRY_STATUSES or attempt >= retries:
                log_event(
                    f"steam request failed status={exc.code} url={safe_log_url(url)}"
                )
                raise
            log_event(
                f"steam request retry status={exc.code} attempt={attempt + 1} "
                f"url={safe_log_url(url)}"
            )
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_exc = exc
            if attempt >= retries:
                log_event(
                    f"steam request failed url={safe_log_url(url)} "
                    f"error={type(exc).__name__}"
                )
                raise
            log_event(
                f"steam request retry attempt={attempt + 1} "
                f"url={safe_log_url(url)} error={type(exc).__name__}"
            )
        time.sleep(retry_delay(attempt))
    raise last_exc


async def async_request_direct_then_proxy(client, method, url, params=None, json_body=None):
    service = external_service_for_url(url)
    response = None
    fallback_reason = None
    try_direct = reserve_direct_attempt(service)
    if try_direct:
        try:
            response = await client.request(method, url, params=params, json=json_body)
            if response.status_code < 500 and response.status_code != 403:
                record_direct_success(service)
                return response
            fallback_reason = f"HTTP {response.status_code}"
        except Exception as direct_exc:
            if not proxy_fallback_enabled():
                raise direct_exc
            fallback_reason = type(direct_exc).__name__
            set_direct_cooldown(service, fallback_reason)
    else:
        fallback_reason = "direct cooldown active"
    if not proxy_fallback_enabled():
        return response
    if try_direct:
        log_proxy_fallback_once(url, fallback_reason or "unavailable")
    httpx = require_httpx()
    try:
        async with httpx.AsyncClient(
            timeout=config.STEAM_TIMEOUT_SECONDS,
            headers={"User-Agent": config.STEAM_USER_AGENT},
            follow_redirects=True,
            **proxy_httpx_options(),
        ) as proxy_client:
            proxied_response = await proxy_client.request(
                method, url, params=params, json=json_body
            )
        record_proxy_fallback(
            proxied_response.status_code < 500, f"HTTP {proxied_response.status_code}"
        )
        return proxied_response
    except Exception as proxy_exc:
        record_proxy_fallback(False, proxy_exc)
        raise


async def async_get_json(client, semaphore, url, params=None):
    service = external_service_for_url(url)
    check_service_cooldown(service)
    async with semaphore:
        for attempt in range(config.STEAM_MAX_RETRIES + 1):
            try:
                check_service_cooldown(service)
                response = await async_request_direct_then_proxy(
                    client, "GET", url, params=params
                )
                response.raise_for_status()
                return response.json()
            except SteamRateLimited:
                raise
            except Exception as exc:
                status_code = _http_status(exc)
                if status_code == 429:
                    set_service_cooldown(service, 10)
                    raise SteamRateLimited(f"{service} HTTP 429", service)
                if status_code == 404:
                    raise ExternalDataUnavailable(f"{service} resource unavailable (HTTP 404)")
                retryable = status_code in config.STEAM_RETRY_STATUSES or status_code is None
                if not retryable or attempt >= config.STEAM_MAX_RETRIES:
                    log_event(
                        f"http async request failed status={status_code} "
                        f"url={safe_log_url(url)} error={type(exc).__name__}"
                    )
                    raise
                log_event(
                    f"http async request retry status={status_code} attempt={attempt + 1} "
                    f"url={safe_log_url(url)} error={type(exc).__name__}"
                )
                await asyncio.sleep(retry_delay(attempt))


async def async_post_json(client, semaphore, url, params=None, json_body=None):
    service = external_service_for_url(url)
    check_service_cooldown(service)
    async with semaphore:
        last_exc = None
        for attempt in range(config.STEAM_MAX_RETRIES + 1):
            try:
                check_service_cooldown(service)
                response = await async_request_direct_then_proxy(
                    client, "POST", url, params=params, json_body=json_body
                )
                response.raise_for_status()
                return response.json()
            except SteamRateLimited:
                raise
            except Exception as exc:
                last_exc = exc
                status_code = _http_status(exc)
                if status_code == 429:
                    set_service_cooldown(service, 10)
                    raise SteamRateLimited(f"{service} HTTP 429", service)
                if status_code == 404:
                    raise ExternalDataUnavailable(f"{service} resource unavailable (HTTP 404)")
                retryable = status_code in config.STEAM_RETRY_STATUSES or status_code is None
                if not retryable or attempt >= config.STEAM_MAX_RETRIES:
                    log_event(
                        f"itad async post failed status={status_code} "
                        f"url={safe_log_url(url)} error={type(exc).__name__}"
                    )
                    raise
                log_event(
                    f"itad async post retry status={status_code} attempt={attempt + 1} "
                    f"url={safe_log_url(url)} error={type(exc).__name__}"
                )
                await asyncio.sleep(retry_delay(attempt))
        raise last_exc


async def lookup_itad_game_ids(appids):
    """Resolve Steam App IDs to ITAD IDs without touching the database."""
    if not config.ITAD_API_KEY:
        return {}
    httpx = require_httpx()
    semaphore = asyncio.Semaphore(min(5, config.HOTLIST_CONCURRENCY))
    url = "https://api.isthereanydeal.com/games/lookup/v1"
    found = {}
    async with httpx.AsyncClient(
        timeout=config.STEAM_TIMEOUT_SECONDS,
        headers=itad_headers(),
        follow_redirects=True,
        **steam_httpx_options(),
    ) as client:
        async def fetch_one(appid):
            payload = await async_get_json(
                client,
                semaphore,
                url,
                {"key": config.ITAD_API_KEY, "appid": int(appid)},
            )
            game = payload.get("game") if payload.get("found") else None
            game_id = game.get("id") if isinstance(game, dict) else None
            return int(appid), game_id or config.ITAD_MISSING_GAME_ID

        results = await asyncio.gather(
            *(fetch_one(appid) for appid in appids), return_exceptions=True
        )
        failures = [result for result in results if isinstance(result, BaseException)]
        for result in results:
            if isinstance(result, BaseException):
                continue
            appid, game_id = result
            if game_id:
                found[appid] = game_id
        if failures:
            statuses = {_http_status(exc) for exc in failures}
            if statuses & {401, 403}:
                set_service_cooldown("itad", 60)
                message = "ITAD authentication rejected; check ITAD_API_KEY (HTTP 401/403)"
            else:
                message = f"ITAD lookup batch incomplete failed={len(failures)}"
            raise ItadLookupBatchError(message, found)
    return found


async def fetch_itad_history_low_rows(gid_to_appid, countries, stamp):
    """Fetch normalized ITAD low-price rows without persisting them."""
    if not gid_to_appid:
        return []
    httpx = require_httpx()
    semaphore = asyncio.Semaphore(2)
    url = "https://api.isthereanydeal.com/games/historylow/v1"
    all_rows = []
    async with httpx.AsyncClient(
        timeout=config.STEAM_TIMEOUT_SECONDS,
        headers=itad_headers(),
        follow_redirects=True,
        **steam_httpx_options(),
    ) as client:
        for country in countries:
            for gid_batch in _chunks(list(gid_to_appid), 200):
                try:
                    payload = await async_post_json(
                        client,
                        semaphore,
                        url,
                        {"key": config.ITAD_API_KEY, "country": country},
                        gid_batch,
                    )
                except Exception as exc:
                    if _http_status(exc) in {401, 403}:
                        set_service_cooldown("itad", 60)
                        raise ExternalDataUnavailable(
                            "ITAD authentication rejected; check ITAD_API_KEY (HTTP 401/403)"
                        ) from exc
                    log_event(
                        f"itad historylow skipped country={country} "
                        f"error={type(exc).__name__}"
                    )
                    continue
                returned = {item.get("id"): item for item in (payload or [])}
                for game_id in gid_batch:
                    appid = gid_to_appid.get(game_id)
                    if not appid:
                        continue
                    item = returned.get(game_id) or {}
                    low = item.get("low") or {}
                    price = low.get("price") or {}
                    regular = low.get("regular") or {}
                    shop = low.get("shop") or {}
                    amount_int = price.get("amountInt")
                    currency = price.get("currency")
                    all_rows.append(
                        (
                            appid, game_id, country,
                            shop.get("id") if amount_int is not None else None,
                            shop.get("name") if amount_int is not None else None,
                            currency if amount_int is not None else None,
                            price.get("amount") if amount_int is not None else None,
                            amount_int,
                            amount_int_to_cny(amount_int, currency) if amount_int is not None else None,
                            regular.get("amountInt") if amount_int is not None else None,
                            low.get("cut") if amount_int is not None else None,
                            low.get("timestamp") if amount_int is not None else None,
                            stamp,
                        )
                    )
    return all_rows


def fetch_store_catalog_page(last_appid=0, max_results=500):
    params = {"max_results": max(1, min(500, int(max_results)))}
    if last_appid:
        params["last_appid"] = int(last_appid)
    if config.STEAM_API_KEY:
        params["key"] = config.STEAM_API_KEY
    query = urllib.parse.urlencode(params)
    last_error = None
    for host in ("https://api.steampowered.com", "https://partner.steam-api.com"):
        try:
            return request_json(
                f"{host}/IStoreService/GetAppList/v1/?{query}",
                timeout=max(15, config.STEAM_TIMEOUT_SECONDS),
                max_retries=1,
            )
        except Exception as exc:
            last_error = exc
            log_event(f"store catalog endpoint failed host={host}: {exc}")
    raise ExternalDataUnavailable(str(last_error or "store catalog unavailable"))


def fetch_appdetails(appid, region="US"):
    query = urllib.parse.urlencode({"appids": appid, "cc": region, "l": "schinese"})
    payload = request_json(f"https://store.steampowered.com/api/appdetails?{query}")
    record = payload.get(str(appid)) or {}
    return (record.get("data") or {}) if record.get("success") else None


def fetch_players(appid):
    query = urllib.parse.urlencode({"appid": appid})
    payload = request_json(
        "https://api.steampowered.com/ISteamUserStats/"
        f"GetNumberOfCurrentPlayers/v1/?{query}"
    )
    return int((payload.get("response") or {}).get("player_count") or 0)


def fetch_reviews(appid):
    query = urllib.parse.urlencode(
        {
            "json": 1,
            "language": "all",
            "purchase_type": "all",
            "num_per_page": 0,
            "filter": "summary",
        }
    )
    payload = request_json(f"https://store.steampowered.com/appreviews/{appid}?{query}")
    summary = payload.get("query_summary") or {}
    total_positive = int(summary.get("total_positive") or 0)
    total_negative = int(summary.get("total_negative") or 0)
    total = total_positive + total_negative
    return {
        "review_score": round((total_positive / total) * 100) if total else None,
        "review_score_desc": summary.get("review_score_desc"),
        "total_positive": total_positive,
        "total_negative": total_negative,
        "total_reviews": total,
    }


def fetch_itad_prices(appid):
    if not config.ITAD_API_KEY:
        return []
    query = urllib.parse.urlencode(
        {
            "key": config.ITAD_API_KEY,
            "shop": "steam",
            "ids": f"app/{appid}",
            "region": "us",
        }
    )
    try:
        payload = request_json(
            f"https://api.isthereanydeal.com/v01/game/prices/?{query}",
            missing_statuses={404},
        )
    except ExternalDataUnavailable:
        log_event(f"itad prices unavailable appid={appid}")
        return []
    rows = []
    for item in (payload.get("data") or {}).values():
        for deal in item.get("list") or []:
            price = deal.get("price_new")
            if price is not None:
                rows.append(
                    {
                        "region": "ITAD-US",
                        "currency": "USD",
                        "initial": int(float(deal.get("price_old") or price) * 100),
                        "final": int(float(price) * 100),
                        "discount_percent": int(deal.get("price_cut") or 0),
                        "final_formatted": f"${float(price):.2f}",
                        "source": "itad",
                    }
                )
    return rows


async def fetch_official_hotlist_async():
    httpx = require_httpx()
    semaphore = asyncio.Semaphore(1)
    async with httpx.AsyncClient(
        timeout=config.STEAM_TIMEOUT_SECONDS,
        headers={"User-Agent": config.STEAM_USER_AGENT},
        follow_redirects=True,
        **steam_httpx_options(),
    ) as client:
        urls = [
            f"https://api.steampowered.com/ISteamChartsService/GetGamesByConcurrentPlayers/v1/?count={config.HOTLIST_TARGET}",
            f"https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/?count={config.HOTLIST_TARGET}",
        ]
        for url in urls:
            try:
                payload = await async_get_json(client, semaphore, url)
                rows = parse_hot_chart(payload)
                if rows:
                    return rows
            except Exception as exc:
                log_event(f"hotlist endpoint failed url={url}: {exc}")
    return []


__all__ = [name for name in globals() if not name.startswith("_")]
