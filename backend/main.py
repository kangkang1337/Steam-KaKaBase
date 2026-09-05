"""Steam-KaKaBase process entry point."""

import threading

from . import config
from .crawler import scheduler_loop, startup_prewarm_async
from .db import init_db
from .server import create_server
from .steam_client import probe_proxy
from ._runtime import cleanup_image_cache_once


def main():
    init_db()
    probe_proxy()
    cleanup_image_cache_once()
    startup_prewarm_async()
    threading.Thread(target=scheduler_loop, daemon=True, name="steamkb-scheduler").start()
    server = create_server(port=config.PORT)
    print(f"Steam-KaKaBase running at http://127.0.0.1:{config.PORT}")
    print(f"SQLite database: {config.DB_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    main()
