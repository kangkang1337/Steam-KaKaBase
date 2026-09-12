"""FastAPI transport layer; business operations live in services."""

import mimetypes
import secrets
import hashlib
import hmac
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config, services
from .db import init_db
from .logging_utils import log_event
from .schemas import AdminUserRequest, DeleteAccountRequest, LoginRequest, TrackRequest, UntrackRequest
from . import auth
from .rate_limit import IpRateLimiter


def _file_response(path: Path, *, media_type=None, max_age=3600):
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(
        path,
        media_type=media_type or mimetypes.guess_type(str(path))[0],
        headers={"Cache-Control": f"public, max-age={int(max_age)}"},
    )


def _validate_security_config():
    if config.ENVIRONMENT not in {"development", "test", "production"}:
        raise RuntimeError("STEAMKB_ENV must be development, test, or production")
    if config.IS_PRODUCTION and len(config.ADMIN_TOKEN) < 32:
        raise RuntimeError("STEAMKB_ADMIN_TOKEN must contain at least 32 characters in production")
    if config.IS_PRODUCTION and (not config.ALLOWED_HOSTS or "*" in config.ALLOWED_HOSTS):
        raise RuntimeError("STEAMKB_ALLOWED_HOSTS must be an explicit non-wildcard list in production")
    if config.IS_PRODUCTION and "*" in config.CORS_ALLOWED_ORIGINS:
        raise RuntimeError("wildcard CORS is not allowed in production")


def _admin_token_from_request(request: Request):
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.headers.get("X-Admin-Token", "").strip()


def require_admin(request: Request):
    """Protect server-wide mutations without ever exposing the token to JavaScript."""
    if not config.ADMIN_TOKEN and not config.IS_PRODUCTION:
        return
    supplied = _admin_token_from_request(request)
    if not supplied or not secrets.compare_digest(supplied, config.ADMIN_TOKEN):
        raise HTTPException(
            status_code=401,
            detail="administrator authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )


def create_app():
    """Build the cache-only web application."""

    _validate_security_config()
    rate_limiter = IpRateLimiter()

    def request_ip(request: Request):
        # Nginx appends its peer address.  Taking the final value avoids trusting
        # a client-supplied leading X-Forwarded-For value.
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.rsplit(",", 1)[-1].strip() or "unknown"
        return request.client.host if request.client else "unknown"

    def enforce_ip_rate(request: Request, bucket: str, *, limit: int, window_seconds: int):
        retry_after = rate_limiter.check(bucket, request_ip(request), limit=limit, window_seconds=window_seconds)
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail="请求过于频繁，请稍后再试",
                headers={"Retry-After": str(retry_after)},
            )

    @asynccontextmanager
    async def lifespan(_app):
        init_db()
        yield

    application = FastAPI(
        title="Steam-KaKaBase API",
        version=config.APP_VERSION,
        lifespan=lifespan,
        docs_url=None if config.IS_PRODUCTION else "/docs",
        redoc_url=None if config.IS_PRODUCTION else "/redoc",
        openapi_url=None if config.IS_PRODUCTION else "/openapi.json",
    )
    if config.ALLOWED_HOSTS:
        application.add_middleware(TrustedHostMiddleware, allowed_hosts=list(config.ALLOWED_HOSTS))
    if config.CORS_ALLOWED_ORIGINS:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.CORS_ALLOWED_ORIGINS),
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization", "X-Admin-Token"],
            allow_credentials=False,
        )
    application.mount("/assets", StaticFiles(directory=config.ROOT / "assets"), name="assets")

    @application.middleware("http")
    async def cache_policy(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/") or request.url.path in {"/", "/steamkb.html", "/health", "/ready"}:
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if config.IS_PRODUCTION and request.headers.get("X-Forwarded-Proto", request.url.scheme) == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @application.middleware("http")
    async def traffic_metrics(request, call_next):
        response = await call_next(request)
        path = request.url.path
        if not path.startswith(("/assets/", "/api/admin/")) and path not in {"/health", "/ready", "/favicon.ico"}:
            token = request.cookies.get("steamkb_visitor") or (secrets.token_urlsafe(24) if request.method == "GET" else None)
            digest = hmac.new(config.VISITOR_METRICS_SECRET.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest() if token else None
            try:
                services.record_site_request(digest, response.status_code)
                if token and "steamkb_visitor" not in request.cookies:
                    response.set_cookie("steamkb_visitor", token, max_age=31536000, httponly=True, samesite="lax", secure=config.IS_PRODUCTION)
            except Exception as exc:
                log_event(f"traffic metrics failed: {exc}")
        return response

    @application.exception_handler(Exception)
    async def unhandled_exception(request, exc):
        log_event(f"http request failed path={request.url.path}: {exc}")
        return JSONResponse({"error": "Internal server error"}, status_code=500)

    @application.get("/health")
    def health():
        return {"status": "ok"}

    @application.get("/ready")
    def ready():
        try:
            return services.readiness()
        except Exception as exc:
            log_event(f"readiness check failed: {exc}")
            return JSONResponse({"ready": False, "database": "unavailable"}, status_code=503)

    @application.get("/")
    @application.get("/steamkb.html")
    def index():
        return _file_response(config.ROOT / "steamkb.html", media_type="text/html; charset=utf-8", max_age=0)

    @application.get("/favicon.ico")
    def favicon():
        return _file_response(config.ROOT / "assets" / "favicon.png", media_type="image/png")

    @application.get("/api/image-cache")
    def image_cache(
        appid: int = Query(ge=1),
        retry: int = Query(default=0, ge=0, le=2),
    ):
        try:
            url = services.header_image_url(appid)
            if not url:
                raise ValueError("header image unavailable")
            image_path = services.cached_remote_image(url)
            if image_path:
                return _file_response(image_path, max_age=604800)
            redirect_url = url
            if retry:
                separator = "&" if "?" in url else "?"
                redirect_url = f"{url}{separator}_steamkb_retry={retry}"
            return RedirectResponse(url=redirect_url, status_code=302, headers={"Cache-Control": "no-store"})
        except Exception as exc:
            log_event(f"image cache failed appid={appid}: {exc}")
            raise HTTPException(status_code=400, detail="unsupported image URL") from exc

    @application.get("/api/games")
    def games():
        return {"games": services.list_games()}

    def current_user(request: Request, *, csrf=False):
        user = auth.session(request.cookies.get(auth.SESSION_COOKIE))
        if not user:
            raise HTTPException(status_code=401, detail="请先登录")
        if csrf and not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), user["csrf_token"]):
            raise HTTPException(status_code=403, detail="会话校验失败，请重新登录")
        return user

    def dashboard_admin(request: Request, *, csrf=False, owner=False):
        user = current_user(request, csrf=csrf)
        allowed = auth.is_owner(user) if owner else auth.is_admin(user)
        if not allowed:
            raise HTTPException(status_code=403, detail="管理员权限不足")
        return user

    @application.get("/api/auth/me")
    def auth_me(request: Request):
        user = auth.session(request.cookies.get(auth.SESSION_COOKIE))
        return {"user": {"username": user["username"], "is_admin": auth.is_admin(user), "is_owner": auth.is_owner(user)} if user else None, "csrf_token": user["csrf_token"] if user else None}

    @application.post("/api/auth/register")
    def auth_register(body: LoginRequest, request: Request):
        enforce_ip_rate(request, "auth", limit=config.AUTH_RATE_LIMIT, window_seconds=config.AUTH_RATE_WINDOW_SECONDS)
        try: auth.register(body.username, body.password)
        except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @application.post("/api/auth/login")
    def auth_login(body: LoginRequest, request: Request):
        enforce_ip_rate(request, "auth", limit=config.AUTH_RATE_LIMIT, window_seconds=config.AUTH_RATE_WINDOW_SECONDS)
        try: token, csrf, user, expires = auth.login(body.username, body.password, body.remember)
        except ValueError as exc: raise HTTPException(status_code=401, detail=str(exc)) from exc
        response = JSONResponse({"ok": True, "user": {"username": user["username"], "is_admin": auth.is_admin(user), "is_owner": auth.is_owner(user)}, "csrf_token": csrf})
        response.set_cookie(auth.SESSION_COOKIE, token, httponly=True, samesite="lax", secure=config.IS_PRODUCTION, max_age=int((expires - __import__('datetime').datetime.now(__import__('datetime').timezone.utc)).total_seconds()) if body.remember else None)
        return response

    @application.post("/api/auth/logout")
    def auth_logout(request: Request):
        auth.logout(request.cookies.get(auth.SESSION_COOKIE))
        response = JSONResponse({"ok": True}); response.delete_cookie(auth.SESSION_COOKIE); return response

    @application.post("/api/auth/delete-account")
    def auth_delete_account(body: DeleteAccountRequest, request: Request):
        enforce_ip_rate(request, "auth", limit=config.AUTH_RATE_LIMIT, window_seconds=config.AUTH_RATE_WINDOW_SECONDS)
        user = current_user(request, csrf=True)
        try:
            auth.delete_account(user["id"], body.password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        response = JSONResponse({"ok": True})
        response.delete_cookie(auth.SESSION_COOKIE)
        return response

    @application.get("/api/favorites")
    def favorites(request: Request):
        user = current_user(request)
        return {"games": services.list_user_favorites(user["id"])}

    @application.post("/api/favorites")
    def add_favorite(body: TrackRequest, request: Request):
        enforce_ip_rate(request, "favorites", limit=config.FAVORITES_RATE_LIMIT, window_seconds=config.FAVORITES_RATE_WINDOW_SECONDS)
        user = current_user(request, csrf=True)
        return services.add_user_favorite(user["id"], body.appid, body.name, body.header_image or body.tiny_image)

    @application.post("/api/favorites/{appid}/remove")
    def remove_favorite(appid: int, request: Request):
        enforce_ip_rate(request, "favorites", limit=config.FAVORITES_RATE_LIMIT, window_seconds=config.FAVORITES_RATE_WINDOW_SECONDS)
        user = current_user(request, csrf=True)
        return services.remove_user_favorite(user["id"], appid)

    @application.get("/api/games/{appid}")
    def game(appid: int, history_limit: int = Query(default=500, ge=1, le=5000)):
        if appid <= 0:
            raise HTTPException(status_code=422, detail="invalid appid")
        payload = services.get_game(appid, history_limit)
        if payload is None:
            raise HTTPException(status_code=404, detail="not found")
        return payload

    @application.get("/api/hot-games")
    def hot_games(limit: int = Query(default=100, ge=1)):
        return services.list_hot_games(limit)

    @application.get("/api/hot-games/version")
    def hot_games_version():
        return services.hot_games_version()

    @application.get("/api/niche-pool")
    def niche_pool():
        return services.list_niche_pool()

    @application.get("/api/home-picks")
    def home_picks():
        return services.get_home_picks()

    @application.get("/api/status")
    def status(request: Request):
        # Detailed process, queue, quota, storage and third-party cooldown
        # data belongs exclusively to the administrator monitor.
        if not auth.is_admin(auth.session(request.cookies.get(auth.SESSION_COOKIE))):
            return {"status": "ok"}
        payload = services.get_status()
        payload["browser_write_actions_enabled"] = not bool(config.ADMIN_TOKEN)
        if config.IS_PRODUCTION:
            payload.pop("last_errors", None)
            payload.pop("hot_last_errors", None)
            crawler = payload.get("crawler") or {}
            crawler.pop("pid", None)
            crawler.pop("hostname", None)
            crawler.pop("last_error", None)
            proxy = payload.get("proxy") or {}
            if str(proxy.get("message") or "").startswith("代理回退失败"):
                proxy["message"] = "代理回退暂不可用"
        return payload

    @application.get("/api/admin/monitoring")
    def admin_monitoring(request: Request):
        dashboard_admin(request)
        return services.admin_monitoring()

    @application.get("/api/admin/users")
    def admin_users(request: Request):
        dashboard_admin(request)
        return {"admins": auth.list_admins()}

    @application.post("/api/admin/users")
    def add_admin(body: AdminUserRequest, request: Request):
        owner = dashboard_admin(request, csrf=True, owner=True)
        try:
            username = auth.grant_admin(body.username, owner["username"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "username": username}

    @application.post("/api/admin/users/{username}/remove")
    def remove_admin(username: str, request: Request):
        dashboard_admin(request, csrf=True, owner=True)
        if config.ADMIN_OWNER_USERNAME and username.casefold() == config.ADMIN_OWNER_USERNAME.casefold():
            raise HTTPException(status_code=400, detail="服主账号由服务器环境变量保护，不能在页面移除")
        auth.revoke_admin(username)
        return {"ok": True}

    @application.post("/api/games/{appid}/interest")
    def request_game_detail(appid: int):
        if appid <= 0:
            raise HTTPException(status_code=422, detail="invalid appid")
        result = services.request_game_detail(appid)
        if result["reason"] == "not_found":
            raise HTTPException(status_code=404, detail="not found")
        return result

    @application.get("/api/search")
    def search(
        q: str = Query(default="", max_length=300),
        limit: int = Query(default=12, ge=1, le=50),
        offset: int = Query(default=0, ge=0, le=10000),
    ):
        return services.search(q.strip(), limit, offset)

    @application.post("/api/track")
    def track(body: TrackRequest, _admin=Depends(require_admin)):
        return services.track_game(body.appid, body.name, body.header_image or body.tiny_image)

    @application.post("/api/untrack")
    def untrack(body: UntrackRequest, _admin=Depends(require_admin)):
        return services.untrack_game(body.appid)

    @application.post("/api/refresh-all")
    def refresh_all(_admin=Depends(require_admin)):
        return services.refresh_all()

    @application.post("/api/games/{appid}/refresh")
    def refresh_game(appid: int, _admin=Depends(require_admin)):
        if appid <= 0:
            raise HTTPException(status_code=422, detail="invalid appid")
        return services.refresh_game(appid)

    return application


app = create_app()
