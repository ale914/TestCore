# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Watch guard system — server-side safety monitoring for instrument resources.

IWATCH registers a background task that periodically reads a resource and
checks its value against optional MIN/MAX thresholds. On violation, calls
safe_state() and marks the instrument FAULT.

Pattern identical to health.py: asyncio Tasks, fire-and-forget cancel,
_call_driver() for serialized transport access.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from .instruments import InstrumentState

logger = logging.getLogger(__name__)


@dataclass
class WatchEntry:
    """A single active guard."""
    instrument: str
    resource: str
    interval_ms: int
    min_val: float | None
    max_val: float | None
    task: asyncio.Task


class WatchManager:
    """Singleton managing per-resource watch guard tasks."""

    def __init__(self):
        # {instrument_name: {resource_name: WatchEntry}}
        self._watches: dict[str, dict[str, WatchEntry]] = {}

    def start(self, inst, resource: str, interval_ms: int,
              min_val: float | None, max_val: float | None) -> None:
        """Start (or replace) a guard for instrument:resource."""
        # Cancel existing watch on same resource
        self.stop(inst.name, resource)

        task = asyncio.create_task(
            self._watch_loop(inst, resource, interval_ms, min_val, max_val),
            name=f"watch:{inst.name}:{resource}")

        if inst.name not in self._watches:
            self._watches[inst.name] = {}
        self._watches[inst.name][resource] = WatchEntry(
            instrument=inst.name,
            resource=resource,
            interval_ms=interval_ms,
            min_val=min_val,
            max_val=max_val,
            task=task,
        )

    def stop(self, instrument: str, resource: str) -> bool:
        """Stop a single guard. Returns True if it existed."""
        entry = self._watches.get(instrument, {}).pop(resource, None)
        if entry:
            entry.task.cancel()
            if not self._watches.get(instrument):
                self._watches.pop(instrument, None)
            return True
        return False

    def stop_instrument(self, instrument: str) -> None:
        """Stop all guards on an instrument (called before IUNLOCK/IREMOVE)."""
        entries = self._watches.pop(instrument, {})
        for entry in entries.values():
            entry.task.cancel()

    def stop_all(self) -> None:
        """Cancel all guard tasks (server shutdown)."""
        for entries in self._watches.values():
            for entry in entries.values():
                entry.task.cancel()
        self._watches.clear()

    def has_watch(self, instrument: str, resource: str) -> bool:
        return resource in self._watches.get(instrument, {})

    def list_watches(self, instrument: str | None = None) -> list[str]:
        """Return list of 'inst:res:interval_ms[:MIN=val][:MAX=val]' strings."""
        result = []
        for inst_name, entries in self._watches.items():
            if instrument and inst_name != instrument:
                continue
            for res, entry in entries.items():
                parts = [inst_name, res, str(entry.interval_ms)]
                if entry.min_val is not None:
                    parts.append(f"MIN={entry.min_val}")
                if entry.max_val is not None:
                    parts.append(f"MAX={entry.max_val}")
                result.append(":".join(parts))
        return result

    # ------------------------------------------------------------------

    async def _watch_loop(self, inst, resource: str, interval_ms: int,
                          min_val: float | None, max_val: float | None) -> None:
        """Per-resource guard loop."""
        from .instruments import get_registry, DriverError
        from .store import get_store
        from .events import publish_meas_event

        registry = get_registry()
        store = get_store()

        try:
            while True:
                await asyncio.sleep(interval_ms / 1000)

                # Skip if instrument not operational
                if inst.state != InstrumentState.READY:
                    continue

                # Read via _call_driver — serialized by inst._lock
                try:
                    value = await registry._call_driver(
                        inst, inst.driver.read, resource)
                    inst.total_calls += 1
                except DriverError:
                    # Timeout already set inst → UNRESPONSIVE via _call_driver
                    store.write_meas(inst.name, resource, None, "ERROR")
                    await publish_meas_event(
                        inst.name, resource, None, time.time(), "ERROR")
                    continue

                # Write MEAS
                store.write_meas(inst.name, resource, value, "OK")
                await publish_meas_event(
                    inst.name, resource, value, time.time(), "OK")

                # Check thresholds
                if min_val is not None or max_val is not None:
                    try:
                        fval = float(value)
                        violated = (
                            (min_val is not None and fval < min_val) or
                            (max_val is not None and fval > max_val)
                        )
                        if violated:
                            await self._guard_trip(
                                inst, resource, value, min_val, max_val)
                    except ValueError:
                        pass  # non-numeric value: thresholds ignored

        except asyncio.CancelledError:
            pass

    async def _guard_trip(self, inst, resource: str, value: str,
                          min_val: float | None, max_val: float | None) -> None:
        """Execute safety action on threshold violation."""
        from .instruments import InstrumentState
        from .store import get_store
        from .events import publish_meas_event, publish_instrument_event

        logger.error(
            f"Guard trip: {inst.name}:{resource} = {value} "
            f"(min={min_val}, max={max_val}) → safe_state + FAULT")

        try:
            inst.driver.safe_state()
        except Exception as e:
            logger.error(f"Guard: safe_state failed on {inst.name}: {e}")

        inst.state = InstrumentState.FAULT

        store = get_store()
        store.write_meas(inst.name, resource, value, "GUARD_FAULT")
        await publish_meas_event(
            inst.name, resource, value, time.time(), "GUARD_FAULT")

        await publish_instrument_event(
            "GUARD_FAULT", inst.name,
            resource=resource,
            value=str(value),
            min=str(min_val) if min_val is not None else None,
            max=str(max_val) if max_val is not None else None,
        )


# Global singleton
_watch_manager: WatchManager | None = None


def get_watch_manager() -> WatchManager:
    global _watch_manager
    if _watch_manager is None:
        _watch_manager = WatchManager()
    return _watch_manager
