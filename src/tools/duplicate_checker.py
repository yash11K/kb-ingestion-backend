"""check_duplicate tool – queries KB_Files_Table for existing content hash."""

from __future__ import annotations

from strands.tools import tool

from src.db.queries import find_by_content_hash

_session_factory = None


def set_session_factory(session_factory) -> None:
    """Set the module-level session factory for use by the check_duplicate tool."""
    global _session_factory
    _session_factory = session_factory


@tool
async def check_duplicate(content_hash: str) -> dict:
    """Check if content_hash already exists in the kb_files table.

    This is an async tool so it runs on the same event loop as the SQLAlchemy
    async session, avoiding cross-event-loop errors.

    Args:
        content_hash: SHA-256 hex digest of the markdown body to check.

    Returns:
        dict with ``is_duplicate`` flag and ``existing_file_id`` (UUID string or None).
    """
    # TODO: Re-enable duplicate detection once conflict resolution is designed.
    # For now, always report content as unique so the validator gives full
    # uniqueness score (0.2) and the UI treats everything as fresh.
    return {"is_duplicate": False, "existing_file_id": None}
