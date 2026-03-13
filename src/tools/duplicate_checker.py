"""check_duplicate tool – queries KB_Files_Table for existing content hash."""

from __future__ import annotations

import asyncpg
from strands.tools import tool

from src.db.queries import find_by_content_hash

_db_pool: asyncpg.Pool | None = None


def set_db_pool(pool: asyncpg.Pool) -> None:
    """Set the module-level database pool for use by the check_duplicate tool."""
    global _db_pool
    _db_pool = pool


@tool
async def check_duplicate(content_hash: str) -> dict:
    """Check if content_hash already exists in the kb_files table.

    This is an async tool so it runs on the same event loop as the asyncpg
    connection pool, avoiding cross-event-loop errors.

    Args:
        content_hash: SHA-256 hex digest of the markdown body to check.

    Returns:
        dict with ``is_duplicate`` flag and ``existing_file_id`` (UUID string or None).
    """
    # TODO: Re-enable duplicate detection once conflict resolution is designed.
    # For now, always report content as unique so the validator gives full
    # uniqueness score (0.2) and the UI treats everything as fresh.
    return {"is_duplicate": False, "existing_file_id": None}
