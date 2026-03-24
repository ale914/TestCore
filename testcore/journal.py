# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Command journal - ring buffer for command logging.

Spec §5.2, §6.1: Records recent commands for diagnostics.
Uses collections.deque(maxlen) for O(1) append with automatic eviction.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class JournalEntry:
    """Single journal entry."""
    timestamp: float
    session_id: int
    client_name: str
    command: str
    status: str  # "ok" or "error"

    def to_str(self) -> str:
        """Format as human-readable string."""
        ts = f"{self.timestamp:.6f}"
        name = self.client_name or f"session:{self.session_id}"
        return f"{ts} [{name}] {self.command} -> {self.status}"


class Journal:
    """Ring buffer of recent commands.

    Thread-safe by design: only accessed from the single-threaded
    asyncio dispatch loop (same as KV store).
    """

    # Commands excluded from logging (noise)
    _EXCLUDED: frozenset[str] = frozenset({"PING", "JOURNAL", "COMMAND"})

    def __init__(self, maxlen: int = 1000):
        self._entries: deque[JournalEntry] = deque(maxlen=maxlen)
        self._maxlen = maxlen

    @property
    def maxlen(self) -> int:
        return self._maxlen

    def record(self, session_id: int, client_name: str,
               command: list[str], status: str) -> None:
        """Record a command execution."""
        cmd_name = command[0].upper() if command else ""
        if cmd_name in self._EXCLUDED:
            return

        self._entries.append(JournalEntry(
            timestamp=time.time(),
            session_id=session_id,
            client_name=client_name or "",
            command=" ".join(command),
            status=status,
        ))

    def tail(self, count: int = 10) -> list[JournalEntry]:
        """Return last N entries (tail -n N)."""
        if count >= len(self._entries):
            return list(self._entries)
        return list(self._entries)[-count:]

    def head(self, offset: int, count: int | None = None) -> list[JournalEntry]:
        """Return entries from offset (tail -n +N style, 0-based).

        offset: 0-based start position
        count: max entries to return (None = all from offset)
        """
        entries = list(self._entries)
        if offset >= len(entries):
            return []
        result = entries[offset:]
        if count is not None:
            result = result[:count]
        return result

    def all(self) -> list[JournalEntry]:
        """Return all entries."""
        return list(self._entries)

    def clear(self) -> int:
        """Clear journal. Returns count of cleared entries."""
        count = len(self._entries)
        self._entries.clear()
        return count

    def __len__(self) -> int:
        return len(self._entries)


# Global journal instance
_journal: Journal | None = None


def get_journal(maxlen: int = 1000) -> Journal:
    """Get global journal instance."""
    global _journal
    if _journal is None:
        _journal = Journal(maxlen=maxlen)
    return _journal
