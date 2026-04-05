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
RESERVED_PREFIXES = ('_sys:', '_drv:', '_inst:', '_sess:', '_lock:', '_watch:', '_meas:')


class MeasValue:
    """A single MEAS entry — structured measurement result."""
    __slots__ = ("value", "ts", "status")

    def __init__(self, value: str | None, ts: float, status: str):
        self.value = value
        self.ts = ts
        self.status = status


class KeyValueStore:
    """In-memory KV store with reserved prefix protection."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._owners: dict[str, int] = {}  # key → session_id (RO keys only)
        self._meas: dict[str, MeasValue] = {}  # _meas:<inst>:<resource> → MeasValue

    def _is_reserved(self, key: str) -> bool:
        """Check if key uses reserved prefix."""
        return key.startswith(RESERVED_PREFIXES)

    def _check_owner(self, key: str, session_id: int | None) -> None:
        """Raise ValueError if key is RO and session_id is not the owner."""
        if not self._owners:
            return
        owner = self._owners.get(key)
        if owner is not None and owner != session_id:
            raise ValueError(f"READONLY key '{key}' owned by session {owner}")

    def set(self, key: str, value: str, nx: bool = False, xx: bool = False,
            ro: bool = False, session_id: int | None = None) -> bool:
        """SET key value [NX|XX] [RO]. Returns True if set, False if condition failed."""
        if self._is_reserved(key):
            raise ValueError(f"READONLY cannot SET {key}")

        self._check_owner(key, session_id)

        exists = key in self._data

        if nx and exists:
            return False
        if xx and not exists:
            return False

        self._data[key] = value
        if ro and session_id is not None:
            self._owners[key] = session_id
        elif self._owners and not ro and session_id is not None:
            if self._owners.get(key) == session_id:
                del self._owners[key]
        return True

    def get(self, key: str) -> str | None:
        """GET key. Returns value or None."""
        return self._data.get(key)

    def mget(self, keys: list[str]) -> list[str | None]:
        """MGET key [key ...]. Returns list of values (None for missing)."""
        return [self._data.get(key) for key in keys]

    def mset(self, pairs: list[tuple[str, str]],
             session_id: int | None = None) -> None:
        """MSET key val [key val ...]. Atomic multi-set."""
        # Check all keys first
        for key, _ in pairs:
            if self._is_reserved(key):
                raise ValueError(f"READONLY cannot SET {key}")
            self._check_owner(key, session_id)

        # Set all keys
        for key, value in pairs:
            self._data[key] = value

    def delete(self, keys: list[str], session_id: int | None = None) -> int:
        """DEL key [key ...]. Returns count of deleted keys."""
        # Check for reserved keys first
        for key in keys:
            if self._is_reserved(key):
                raise ValueError(f"READONLY cannot DEL {key}")
            self._check_owner(key, session_id)

        count = 0
        has_owners = bool(self._owners)
        for key in keys:
            if key in self._data:
                del self._data[key]
                if has_owners:
                    self._owners.pop(key, None)
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

    def flushdb(self, session_id: int | None = None) -> None:
        """FLUSHDB. Removes client keys (preserves reserved and other sessions' RO keys)."""
        _is_reserved = self._is_reserved
        for k in [k for k in self._data if not _is_reserved(k)]:
            owner = self._owners.get(k)
            if owner is not None and owner != session_id:
                continue  # skip RO keys owned by other sessions
            del self._data[k]
            self._owners.pop(k, None)

    def release_owner(self, session_id: int) -> None:
        """Release all RO keys owned by session_id (called on disconnect)."""
        owned = [k for k, v in self._owners.items() if v == session_id]
        for k in owned:
            del self._owners[k]

    # ------------------------------------------------------------------
    # MEAS storage
    # ------------------------------------------------------------------

    @staticmethod
    def _meas_key(instrument: str, resource: str) -> str:
        return f"_meas:{instrument}:{resource}"

    def write_meas(self, instrument: str, resource: str,
                   value: str | None, status: str) -> None:
        """Write a MEAS entry atomically."""
        key = self._meas_key(instrument, resource)
        self._meas[key] = MeasValue(value, time.time(), status)

    def invalidate_meas(self, instrument: str) -> list[tuple[str, str, MeasValue]]:
        """Mark all MEAS for an instrument as STALE.

        Returns list of (instrument, resource, meas) for event publishing.
        """
        prefix = f"_meas:{instrument}:"
        invalidated = []
        for key, meas in self._meas.items():
            if key.startswith(prefix) and meas.status != "STALE":
                resource = key[len(prefix):]
                meas.ts = time.time()
                meas.status = "STALE"
                invalidated.append((instrument, resource, meas))
        return invalidated

    def get_meas(self, instrument: str, resource: str) -> MeasValue | None:
        """Get a single MEAS entry."""
        return self._meas.get(self._meas_key(instrument, resource))

    def get_all_meas(self, instrument: str | None = None) -> list[tuple[str, MeasValue]]:
        """Get all MEAS entries, optionally filtered by instrument.

        Returns list of (display_key, MeasValue) where display_key omits '_meas:'.
        """
        prefix = "_meas:"
        inst_prefix = f"_meas:{instrument}:" if instrument else None
        result = []
        for key, meas in self._meas.items():
            if inst_prefix and not key.startswith(inst_prefix):
                continue
            # Display key: "_meas:inst:res" → "inst res"
            raw = key[len(prefix):]
            inst, res = raw.split(":", 1)
            result.append((f"{inst} {res}", meas))
        return result

    def get_meas_keys(self, instrument: str | None = None) -> list[str]:
        """Get MEAS key names (without '_meas:' prefix)."""
        prefix = "_meas:"
        inst_prefix = f"_meas:{instrument}:" if instrument else None
        result = []
        for key in self._meas:
            if inst_prefix and not key.startswith(inst_prefix):
                continue
            raw = key[len(prefix):]
            inst, res = raw.split(":", 1)
            result.append(f"{inst} {res}")
        return result


# Global store instance
_store: KeyValueStore | None = None


def get_store() -> KeyValueStore:
    """Get global KV store instance."""
    global _store
    if _store is None:
        _store = KeyValueStore()
    return _store
