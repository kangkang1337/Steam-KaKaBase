"""FastAPI transport layer; business operations live in services."""

import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from . import config, services
from .db import init_db
from .logging_utils import log_event
from .schemas import TrackRequest, UntrackRequest


def _file_response(path: Path, *, media_type=None, max_age=3600):
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(
        path,
        media_type=media_type or mimetypes.guess_type(str(path))[0],
        headers={"Cache-Control": f"public, max-age={int(max_age)}"},
    )


def create_app():
    """Build the cache-only web application."""

    @asynccontextmanager
    async def lifespan(_app):
        init_db()
        yield

    application = FastAPI(
        title="Steam-KaKaBase API",
        version=config.APP_VERSION,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    @application.middleware("http")
    async def cache_policy(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/") or request.url.path in {"/", "/steamkb.html", "/health", "/ready"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(Exception)
    async def unhandled_exception(request, exc):
        log_event(f"http request failed path={request.url.path}: {exc}")
        return JSONResponse({"error": str(exc)}, status_code=500)

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

    @application.get("/assets/{asset_path:path}")
    def asset(asset_path: str):
        assets_root = (config.ROOT / "assets").resolve()
        requested = (assets_root / asset_path).resolve()
        if requested != assets_root and assets_root not in requested.parents:
            raise HTTPException(status_code=404, detail="not found")
        return _file_response(requested)

    @application.get("/api/image-cache")
    def image_cache(url: str = Query(min_length=1, max_length=2048)):
        try:
            image_path = services.cached_remote_image(url)
            if image_path:
                return _file_response(image_path, max_age=604800)
            if services.is_allowed_image_url(url):
                return RedirectResponse(url=url, status_code=302, headers={"Cache-Control": "no-store"})
            raise ValueError("unsupported image URL")
        except Exception as exc:
            log_event(f"image cache failed url={url}: {exc}")
            raise HTTPException(status_code=400, detail="unsupported image URL") from exc

    @application.get("/api/games")
    def games():
        return {"games": services.list_games()}

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

    @application.get("/api/hot-games/ensure")
    def ensure_hot_games(target: int = Query(default=100, ge=1)):
        return services.ensure_hot_games(target)

    @application.get("/api/niche-pool")
    def niche_pool():
        return services.list_niche_pool()

    @application.get("/api/home-picks")
    def home_picks():
        return services.get_home_picks()

    @application.get("/api/status")
    def status():
        return services.get_status()

    @application.get("/api/search")
    def search(
        q: str = Query(default="", max_length=300),
        limit: int = Query(default=12, ge=1, le=50),
        offset: int = Query(default=0, ge=0, le=10000),
    ):
        return services.search(q.strip(), limit, offset)

    @application.post("/api/track")
    def track(body: TrackRequest):
        return services.track_game(body.appid, body.name, body.header_image or body.tiny_image)

    @application.post("/api/untrack")
    def untrack(body: UntrackRequest):
        return services.untrack_game(body.appid)

    @application.post("/api/refresh-all")
    def refresh_all():
        return services.refresh_all()

    @application.post("/api/games/{appid}/refresh")
    def refresh_game(appid: int):
        if appid <= 0:
            raise HTTPException(status_code=422, detail="invalid appid")
        return services.refresh_game(appid)

    return application


app = create_app()
