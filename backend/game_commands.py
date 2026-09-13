"""Small SQLite commands used by the web API and crawler.

These commands deliberately do not fetch remote data.  The web process can
therefore enqueue crawler work without ever becoming a second writer/fetcher.
"""

from .db import transaction
from .utils import UNKNOWN_GAME_NAME, clean_name, now_iso


def quick_track_game(appid, name=None, header_image=None):
    """Materialize a lightweight tracked game row without network I/O."""
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO games(appid, name, header_image, tracked, updated_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(appid) DO UPDATE SET
                name=CASE WHEN excluded.name != ? THEN excluded.name ELSE games.name END,
                header_image=COALESCE(excluded.header_image, games.header_image),
                tracked=1, updated_at=excluded.updated_at
            """,
            (int(appid), clean_name(name), header_image, now_iso(), UNKNOWN_GAME_NAME),
        )


def untrack_game(appid):
    with transaction() as conn:
        conn.execute(
            "UPDATE games SET tracked=0, updated_at=? WHERE appid=?",
            (now_iso(), int(appid)),
        )
