"""Steam and ITAD HTTP transport, retries, proxy fallback and endpoint parsing."""

import asyncio
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from . import _runtime as runtime


SteamRateLimited = runtime.SteamRateLimited
ExternalDataUnavailable = runtime.ExternalDataUnavailable
probe_proxy = runtime.probe_proxy
proxy_fallback_enabled = runtime.proxy_fallback_enabled
service_cooldown_remaining_seconds = runtime.service_cooldown_remaining_seconds
steam_cooldown_remaining_seconds = runtime.steam_cooldown_remaining_seconds
direct_cooldown_remaining_seconds = runtime.direct_cooldown_remaining_seconds
set_service_cooldown = runtime.set_service_cooldown
set_steam_cooldown = runtime.set_steam_cooldown
check_service_cooldown = runtime.check_service_cooldown
check_steam_cooldown = runtime.check_steam_cooldown
external_service_for_url = runtime.external_service_for_url
cache_image = runtime.cache_image
fetch_players_for_appids_async = runtime.fetch_players_for_appids_async


def request_json(url, timeout=None, headers=None, missing_statuses=None, max_retries=None, service=None):
    timeout = runtime.STEAM_TIMEOUT_SECONDS if timeout is None else timeout
    service = service or runtime.external_service_for_url(url)
    runtime.check_service_cooldown(service)
    missing_statuses = set(missing_statuses or [])
    base_headers = {
        "User-Agent": runtime.STEAM_USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
    }
    if headers:
        base_headers.update(headers)
    last_exc = None
    retries = runtime.STEAM_MAX_RETRIES if max_retries is None else max(0, int(max_retries))
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=base_headers)
        try:
            try_direct = runtime.reserve_direct_attempt(service)
            if try_direct:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                try:
                    res = opener.open(req, timeout=timeout)
                    runtime.record_direct_success(service)
                except urllib.error.HTTPError as direct_http_exc:
                    runtime.record_direct_success(service)
                    if runtime.proxy_fallback_enabled() and (
                        direct_http_exc.code >= 500 or direct_http_exc.code == 403
                    ):
                        runtime.log_proxy_fallback_once(url, f"HTTP {direct_http_exc.code}")
                        try_direct = False
                    else:
                        raise
                except (urllib.error.URLError, TimeoutError, OSError) as direct_exc:
                    if not runtime.proxy_fallback_enabled():
                        raise
                    runtime.set_direct_cooldown(service, type(direct_exc).__name__)
                    runtime.log_proxy_fallback_once(url, type(direct_exc).__name__)
                    try_direct = False
            if not try_direct:
                proxy_handler = urllib.request.ProxyHandler(
                    {"http": runtime.STEAM_PROXY_URL, "https": runtime.STEAM_PROXY_URL}
                )
                handlers = [proxy_handler]
                if not runtime.STEAM_PROXY_VERIFY_TLS:
                    handlers.append(
                        urllib.request.HTTPSHandler(context=ssl._create_unverified_context())
                    )
                try:
                    res = urllib.request.build_opener(*handlers).open(req, timeout=timeout)
                    runtime.record_proxy_fallback(True)
                except urllib.error.HTTPError:
                    runtime.record_proxy_fallback(True)
                    raise
                except Exception as proxy_exc:
                    runtime.record_proxy_fallback(False, proxy_exc)
                    raise
            with res:
                charset = res.headers.get_content_charset() or "utf-8"
                return json.loads(res.read().decode(charset, errors="replace"))
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code in missing_statuses:
                raise ExternalDataUnavailable(f"HTTP {exc.code}: external data unavailable")
            if exc.code == 429:
                runtime.set_service_cooldown(service, 10)
                raise SteamRateLimited(f"{service} HTTP 429", service)
            if exc.code not in runtime.STEAM_RETRY_STATUSES or attempt >= retries:
                runtime.log_event(f"steam request failed status={exc.code} url={url}: {exc}")
                raise
            runtime.log_event(
                f"steam request retry status={exc.code} attempt={attempt + 1} url={url}"
            )
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_exc = exc
            if attempt >= retries:
                runtime.log_event(f"steam request failed url={url}: {exc}")
                raise
            runtime.log_event(f"steam request retry attempt={attempt + 1} url={url}: {exc}")
        time.sleep(runtime.retry_delay(attempt))
    raise last_exc


async def async_request_direct_then_proxy(client, method, url, params=None, json_body=None):
    service = runtime.external_service_for_url(url)
    response = None
    fallback_reason = None
    try_direct = runtime.reserve_direct_attempt(service)
    if try_direct:
        try:
            response = await client.request(method, url, params=params, json=json_body)
            if response.status_code < 500 and response.status_code != 403:
                runtime.record_direct_success(service)
                return response
            fallback_reason = f"HTTP {response.status_code}"
        except Exception as direct_exc:
            if not runtime.proxy_fallback_enabled():
                raise direct_exc
            fallback_reason = type(direct_exc).__name__
            runtime.set_direct_cooldown(service, fallback_reason)
    else:
        fallback_reason = "direct cooldown active"
    if not runtime.proxy_fallback_enabled():
        return response
    if try_direct:
        runtime.log_proxy_fallback_once(url, fallback_reason or "unavailable")
    httpx = runtime.require_httpx()
    try:
        async with httpx.AsyncClient(
            timeout=runtime.STEAM_TIMEOUT_SECONDS,
            headers={"User-Agent": runtime.STEAM_USER_AGENT},
            follow_redirects=True,
            **runtime.proxy_httpx_options(),
        ) as proxy_client:
            proxied_response = await proxy_client.request(
                method, url, params=params, json=json_body
            )
        runtime.record_proxy_fallback(
            proxied_response.status_code < 500, f"HTTP {proxied_response.status_code}"
        )
        return proxied_response
    except Exception as proxy_exc:
        runtime.record_proxy_fallback(False, proxy_exc)
        raise


async def async_get_json(client, semaphore, url, params=None):
    service = runtime.external_service_for_url(url)
    runtime.check_service_cooldown(service)
    async with semaphore:
        for attempt in range(runtime.STEAM_MAX_RETRIES + 1):
            try:
                runtime.check_service_cooldown(service)
                response = await runtime.async_request_direct_then_proxy(
                    client, "GET", url, params=params
                )
                response.raise_for_status()
                return response.json()
            except SteamRateLimited:
                raise
            except Exception as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                if status_code == 429:
                    runtime.set_service_cooldown(service, 10)
                    raise SteamRateLimited(f"{service} HTTP 429", service)
                if status_code == 404:
                    raise ExternalDataUnavailable(f"{service} resource unavailable (HTTP 404)")
                retryable = status_code in runtime.STEAM_RETRY_STATUSES or status_code is None
                if not retryable or attempt >= runtime.STEAM_MAX_RETRIES:
                    runtime.log_event(
                        f"http async request failed status={status_code} "
                        f"url={runtime.safe_log_url(url)}: {exc}"
                    )
                    raise
                runtime.log_event(
                    f"http async request retry status={status_code} attempt={attempt + 1} "
                    f"url={runtime.safe_log_url(url)}: {exc}"
                )
                await asyncio.sleep(runtime.retry_delay(attempt))


async def async_post_json(client, semaphore, url, params=None, json_body=None):
    service = runtime.external_service_for_url(url)
    runtime.check_service_cooldown(service)
    async with semaphore:
        last_exc = None
        for attempt in range(runtime.STEAM_MAX_RETRIES + 1):
            try:
                runtime.check_service_cooldown(service)
                response = await runtime.async_request_direct_then_proxy(
                    client, "POST", url, params=params, json_body=json_body
                )
                response.raise_for_status()
                return response.json()
            except SteamRateLimited:
                raise
            except Exception as exc:
                last_exc = exc
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                if status_code == 429:
                    runtime.set_service_cooldown(service, 10)
                    raise SteamRateLimited(f"{service} HTTP 429", service)
                if status_code == 404:
                    raise ExternalDataUnavailable(f"{service} resource unavailable (HTTP 404)")
                retryable = status_code in runtime.STEAM_RETRY_STATUSES or status_code is None
                if not retryable or attempt >= runtime.STEAM_MAX_RETRIES:
                    runtime.log_event(
                        f"itad async post failed status={status_code} "
                        f"url={runtime.safe_log_url(url)}: {exc}"
                    )
                    raise
                runtime.log_event(
                    f"itad async post retry status={status_code} attempt={attempt + 1} "
                    f"url={runtime.safe_log_url(url)}: {exc}"
                )
                await asyncio.sleep(runtime.retry_delay(attempt))
        raise last_exc


async def lookup_itad_game_ids(appids):
    """Resolve Steam App IDs to ITAD IDs without touching the database."""
    if not runtime.ITAD_API_KEY:
        return {}
    httpx = runtime.require_httpx()
    semaphore = asyncio.Semaphore(min(5, runtime.HOTLIST_CONCURRENCY))
    url = "https://api.isthereanydeal.com/games/lookup/v1"
    found = {}
    async with httpx.AsyncClient(
        timeout=runtime.STEAM_TIMEOUT_SECONDS,
        headers=runtime.itad_headers(),
        follow_redirects=True,
        **runtime.steam_httpx_options(),
    ) as client:
        async def fetch_one(appid):
            try:
                payload = await async_get_json(client, semaphore, url, {"appid": int(appid)})
                game = payload.get("game") if payload.get("found") else None
                game_id = game.get("id") if isinstance(game, dict) else None
                if not game_id:
                    runtime.log_event(f"itad lookup unavailable appid={appid}")
                    return int(appid), runtime.ITAD_MISSING_GAME_ID
                return int(appid), game_id
            except Exception as exc:
                runtime.log_event(f"itad lookup skipped appid={appid}: {exc}")
                return int(appid), None

        for appid, game_id in await asyncio.gather(*(fetch_one(appid) for appid in appids)):
            if game_id:
                found[appid] = game_id
    return found


async def fetch_itad_history_low_rows(gid_to_appid, countries, stamp):
    """Fetch normalized ITAD low-price rows without persisting them."""
    if not gid_to_appid:
        return []
    httpx = runtime.require_httpx()
    semaphore = asyncio.Semaphore(2)
    url = "https://api.isthereanydeal.com/games/historylow/v1"
    all_rows = []
    async with httpx.AsyncClient(
        timeout=runtime.STEAM_TIMEOUT_SECONDS,
        headers=runtime.itad_headers(),
        follow_redirects=True,
        **runtime.steam_httpx_options(),
    ) as client:
        for country in countries:
            for gid_batch in runtime.chunks(list(gid_to_appid), 200):
                try:
                    payload = await async_post_json(
                        client, semaphore, url, {"country": country}, gid_batch
                    )
                except Exception as exc:
                    runtime.log_event(f"itad historylow skipped country={country}: {exc}")
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
                            runtime.amount_int_to_cny(amount_int, currency) if amount_int is not None else None,
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
    if runtime.STEAM_API_KEY:
        params["key"] = runtime.STEAM_API_KEY
    query = urllib.parse.urlencode(params)
    last_error = None
    for host in ("https://api.steampowered.com", "https://partner.steam-api.com"):
        try:
            return runtime.request_json(
                f"{host}/IStoreService/GetAppList/v1/?{query}",
                timeout=max(15, runtime.STEAM_TIMEOUT_SECONDS),
                max_retries=1,
            )
        except Exception as exc:
            last_error = exc
            runtime.log_event(f"store catalog endpoint failed host={host}: {exc}")
    raise ExternalDataUnavailable(str(last_error or "store catalog unavailable"))


def fetch_appdetails(appid, region="US"):
    query = urllib.parse.urlencode({"appids": appid, "cc": region, "l": "schinese"})
    payload = runtime.request_json(f"https://store.steampowered.com/api/appdetails?{query}")
    record = payload.get(str(appid)) or {}
    return (record.get("data") or {}) if record.get("success") else None


def fetch_players(appid):
    query = urllib.parse.urlencode({"appid": appid})
    payload = runtime.request_json(
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
    payload = runtime.request_json(f"https://store.steampowered.com/appreviews/{appid}?{query}")
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
    if not runtime.ITAD_API_KEY:
        return []
    query = urllib.parse.urlencode(
        {
            "key": runtime.ITAD_API_KEY,
            "shop": "steam",
            "ids": f"app/{appid}",
            "region": "us",
        }
    )
    try:
        payload = runtime.request_json(
            f"https://api.isthereanydeal.com/v01/game/prices/?{query}",
            missing_statuses={404},
        )
    except ExternalDataUnavailable:
        runtime.log_event(f"itad prices unavailable appid={appid}")
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
    httpx = runtime.require_httpx()
    semaphore = asyncio.Semaphore(1)
    async with httpx.AsyncClient(
        timeout=runtime.STEAM_TIMEOUT_SECONDS,
        headers={"User-Agent": runtime.STEAM_USER_AGENT},
        follow_redirects=True,
        **runtime.steam_httpx_options(),
    ) as client:
        urls = [
            "https://api.steampowered.com/ISteamChartsService/GetGamesByConcurrentPlayers/v1/",
            "https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/",
        ]
        for url in urls:
            try:
                payload = await runtime.async_get_json(client, semaphore, url)
                rows = runtime.parse_hot_chart(payload)
                if rows:
                    return rows
            except Exception as exc:
                runtime.log_event(f"hotlist endpoint failed url={url}: {exc}")
    return []


__all__ = [name for name in globals() if not name.startswith("_")]
