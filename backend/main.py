"""Steam-KaKaBase Uvicorn process entry point."""

import uvicorn

from . import config


def main():
    print(f"Steam-KaKaBase running at http://127.0.0.1:{config.PORT}")
    print(f"SQLite database: {config.DB_PATH}")
    uvicorn.run(
        "backend.server:app",
        host="127.0.0.1",
        port=config.PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
