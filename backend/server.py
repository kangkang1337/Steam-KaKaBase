"""HTTP transport only; business operations live in services."""

import json
import mimetypes
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config, services
from .logging_utils import log_event


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, content_type=None, max_age=3600):
        if not path.is_file():
            self.send_response(404)
            self.send_cors_headers()
            self.end_headers()
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_cors_headers()
        self.send_header("Content-Type", content_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        self.send_header("Cache-Control", f"public, max-age={int(max_age)}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self):
        length = int(self.headers.get("Content-Length") or "0")
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/favicon.ico":
                return self.send_file(config.ROOT / "assets" / "favicon.png", "image/png")
            if path.startswith("/assets/"):
                relative = Path(urllib.parse.unquote(path.removeprefix("/assets/")))
                if relative.is_absolute() or ".." in relative.parts:
                    return self.send_json({"error": "not found"}, 404)
                return self.send_file(config.ROOT / "assets" / relative)
            if path == "/api/image-cache":
                url = (query.get("url") or [""])[0].strip()
                if not url:
                    return self.send_json({"error": "missing url"}, 400)
                try:
                    image_path = services.cache_remote_image(url)
                    return self.send_file(image_path, mimetypes.guess_type(str(image_path))[0] or "image/jpeg", 604800)
                except Exception as exc:
                    log_event(f"image cache failed url={url}: {exc}")
                    self.send_response(302)
                    self.send_cors_headers()
                    self.send_header("Location", url)
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    return
            if path == "/api/games":
                return self.send_json({"games": services.list_games()})
            if path == "/api/hot-games/ensure":
                return self.send_json(services.ensure_hot_games((query.get("target") or ["100"])[0]))
            if path == "/api/hot-games":
                return self.send_json(services.list_hot_games((query.get("limit") or ["100"])[0]))
            if path == "/api/hot-games/version":
                return self.send_json(services.hot_games_version())
            if path == "/api/niche-pool":
                return self.send_json(services.list_niche_pool())
            if path == "/api/home-picks":
                return self.send_json(services.get_home_picks())
            if path == "/api/status":
                return self.send_json(services.get_status())
            if path == "/api/search":
                return self.send_json(services.search((query.get("q") or [""])[0].strip()))
            if path.startswith("/api/games/"):
                payload = services.get_game(path.rsplit("/", 1)[-1], (query.get("history_limit") or ["500"])[0])
                return self.send_json(payload or {"error": "not found"}, 200 if payload else 404)
            if path in ("/", "/steamkb.html"):
                return self.send_file(config.ROOT / "steamkb.html", "text/html; charset=utf-8", 0)
            self.send_json({"error": "not found"}, 404)
        except (TypeError, ValueError):
            self.send_json({"error": "invalid request"}, 400)
        except Exception as exc:
            log_event(f"http GET failed path={path}: {exc}")
            self.send_json({"error": str(exc)}, 500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/api/track":
                body = self.read_json_body()
                return self.send_json(services.track_game(body.get("appid"), body.get("name"), body.get("header_image") or body.get("tiny_image")))
            if parsed.path == "/api/untrack":
                return self.send_json(services.untrack_game(self.read_json_body().get("appid")))
            if parsed.path == "/api/refresh-all":
                return self.send_json(services.refresh_all())
            if parsed.path.startswith("/api/games/") and parsed.path.endswith("/refresh"):
                return self.send_json(services.refresh_game(parsed.path.split("/")[3]))
            self.send_json({"error": "not found"}, 404)
        except (TypeError, ValueError):
            self.send_json({"error": "invalid request"}, 400)
        except Exception as exc:
            log_event(f"http POST failed path={parsed.path}: {exc}")
            self.send_json({"error": str(exc)}, 500)


def create_server(host="127.0.0.1", port=None):
    selected_port = config.PORT if port is None else int(port)
    return ThreadingHTTPServer((host, selected_port), Handler)
