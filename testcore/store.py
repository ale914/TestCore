# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Key-Value Store - In-memory dict with reserved prefixes.

Spec §5: Python dict, string keys/values, reserved prefixes.
Performance: O(1) dict operations, minimal overhead.
"""
from __future__ import annotations

import fnmatch
import time
from collections import deque
from typing import Any


# Reserved key prefixes (spec §5.1)
RESERVED_PREFIXES = ('_sys:', '_drv:', '_inst:', '_sess:', '_lock:', '_watch:')


class KeyValueStore:
    """In-memory KV store with reserved prefix protection."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def _is_reserved(self, key: str) -> bool:
        """Check if key uses reserved prefix."""
        return key.startswith(RESERVED_PREFIXES)

    def set(self, key: str, value: str, nx: bool = False, xx: bool = False) -> bool:
        """SET key value [NX|XX]. Returns True if set, False if condition failed."""
        if self._is_reserved(key):
            raise ValueError(f"READONLY cannot SET {key}")

        exists = key in self._data

        if nx and exists:
            return False
        if xx and not exists:
            return False

        self._data[key] = value
        return True

    def get(self, key: str) -> str | None:
        """GET key. Returns value or None."""
        return self._data.get(key)

    def mget(self, keys: list[str]) -> list[str | None]:
        """MGET key [key ...]. Returns list of values (None for missing)."""
        return [self._data.get(key) for key in keys]

    def mset(self, pairs: list[tuple[str, str]]) -> None:
        """MSET key val [key val ...]. Atomic multi-set."""
        # Check all keys first
        for key, _ in pairs:
            if self._is_reserved(key):
                raise ValueError(f"READONLY cannot SET {key}")

        # Set all keys
        for key, value in pairs:
            self._data[key] = value

    def delete(self, keys: list[str]) -> int:
        """DEL key [key ...]. Returns count of deleted keys."""
        # Check for reserved keys first
        for key in keys:
            if self._is_reserved(key):
                raise ValueError(f"READONLY cannot DEL {key}")

        count = 0
        for key in keys:
            if key in self._data:
                del self._data[key]
                count += 1
        return count

    def exists(self, keys: list[str]) -> int:
        """EXISTS key [key ...]. Returns count of existing keys."""
        return sum(1 for key in keys if key in self._data)

    def keys(self, pattern: str = '*') -> list[str]:
        """KEYS pattern. Returns matching keys (excludes reserved prefixes)."""
        _is_reserved = self._is_reserved
        if pattern == '*':
            return [k for k in self._data if not _is_reserved(k)]
        # Fast path: "prefix*" → startswith (avoids fnmatch overhead)
        if pattern.endswith('*') and '*' not in pattern[:-1] \
                and '?' not in pattern and '[' not in pattern:
            prefix = pattern[:-1]
            return [k for k in self._data
                    if not _is_reserved(k) and k.startswith(prefix)]
        _match = fnmatch.fnmatch
        return [k for k in self._data
                if not _is_reserved(k) and _match(k, pattern)]

    def dbsize(self) -> int:
        """DBSIZE. Returns count of client keys (excludes reserved prefixes)."""
        _is_reserved = self._is_reserved
        return sum(1 for k in self._data if not _is_reserved(k))

    def flushdb(self) -> None:
        """FLUSHDB. Removes all client keys (preserves reserved prefixes)."""
        _is_reserved = self._is_reserved
        user_keys = [k for k in self._data if not _is_reserved(k)]
        for k in user_keys:
            del self._data[k]



# Global store instance
_store: KeyValueStore | None = None


def get_store() -> KeyValueStore:
    """Get global KV store instance."""
    global _store
    if _store is None:
        _store = KeyValueStore()
    return _store
