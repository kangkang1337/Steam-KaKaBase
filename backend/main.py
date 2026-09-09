"""Steam-KaKaBase Uvicorn process entry point."""

import uvicorn

from . import config


def main():
    print(f"Steam-KaKaBase running at http://{config.HOST}:{config.PORT}")
    print(f"SQLite database: {config.DB_PATH}")
    uvicorn.run(
        "backend.server:app",
        host=config.HOST,
        port=config.PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
