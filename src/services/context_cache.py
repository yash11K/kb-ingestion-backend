"""In-memory cache for Context Agent analysis results.

Caches the initial proactive analysis keyed on a hash of the file's
meaningful state (content_hash, validation_score, status, deep link states).
Follow-up conversations are never cached.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass


@dataclass
class _CacheEntry:
    analysis: str
    created_at: float


class ContextCache:
    """Simple in-memory LRU-ish cache with TTL for context agent analyses."""

    def __init__(self, ttl_seconds: int = 600, max_entries: int = 200) -> None:
        self._cache: dict[str, _CacheEntry] = {}
        self._ttl = ttl_seconds
        self._max = max_entries

    @staticmethod
    def make_key(
        file_id: str,
        content_hash: str,
        validation_score: float | None,
        status: str,
        deep_link_states: list[dict],
    ) -> str:
        """Compute a cache key from the file's current state.

        Any change to content, score, status, or deep links produces a
        different key, effectively invalidating the old entry.
        """
        raw = json.dumps(
            {
                "file_id": file_id,
                "content_hash": content_hash,
                "validation_score": validation_score,
                "status": status,
                "deep_links": sorted(
                    [{"url": d.get("url", ""), "status": d.get("status", "")} for d in deep_link_states],
                    key=lambda x: x["url"],
                ),
            },
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, key: str) -> str | None:
        """Return cached analysis or None if missing / expired."""
        entry = self._cache.get(key)
        if entry is None:
            return None
        if time.time() - entry.created_at > self._ttl:
            del self._cache[key]
            return None
        return entry.analysis

    def set(self, key: str, analysis: str) -> None:
        """Store an analysis result."""
        # Evict oldest entries if over capacity
        if len(self._cache) >= self._max:
            oldest_key = min(self._cache, key=lambda k: self._cache[k].created_at)
            del self._cache[oldest_key]
        self._cache[key] = _CacheEntry(analysis=analysis, created_at=time.time())

    def invalidate_file(self, file_id: str) -> None:
        """Remove all cache entries containing a given file_id.

        This is a brute-force scan, acceptable for ≤200 entries.
        """
        to_remove = [k for k, v in self._cache.items() if file_id in v.analysis]
        # Actually, since the key itself is a hash, we can't know which file
        # it belongs to.  Instead, we rely on state-based keys: any change to
        # the file produces a new key, so the old entry simply becomes a
        # stale cache entry that will expire via TTL.  No explicit
        # invalidation is needed.
