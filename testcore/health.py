# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Health monitoring system — periodic ping for connected instruments.

Activated via IADD health=N parameter. The monitor runs a background
asyncio task per instrument that sends *IDN? at the configured interval,
but only when the instrument has been idle longer than the interval.

After 3 consecutive ping failures, the instrument transitions to
UNRESPONSIVE (same state as driver timeout).
"""

from __future__ import annotations

import asyncio
import logging
import time

from .instruments import InstrumentState

logger = logging.getLogger(__name__)

HEALTH_MAX_FAILURES = 3


class HealthMonitor:
    """Singleton managing per-instrument health ping tasks."""

    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}

    def start(self, inst) -> None:
        """Start health monitoring for an instrument."""
        if inst.name in self._tasks:
            self._tasks[inst.name].cancel()
        task = asyncio.create_task(
            self._health_loop(inst),
            name=f"health:{inst.name}")
        self._tasks[inst.name] = task

    def stop(self, name: str) -> None:
        """Stop health monitoring for an instrument."""
        task = self._tasks.pop(name, None)
        if task:
            task.cancel()

    def stop_all(self) -> None:
        """Cancel all health tasks (server shutdown)."""
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()

    def is_monitored(self, name: str) -> bool:
        return name in self._tasks

    async def _health_loop(self, inst) -> None:
        """Per-instrument health check loop."""
        from .instruments import get_registry
        from .events import publish_instrument_event

        interval = inst.health_interval
        registry = get_registry()

        try:
            while True:
                await asyncio.sleep(interval)

                # Skip if already in error state (resumes after IRESET)
                if inst.state in (InstrumentState.FAULT,
                                  InstrumentState.UNRESPONSIVE):
                    continue

                # Skip if instrument is busy with a driver call
                if inst._busy:
                    continue

                # Skip if instrument was active recently
                idle_time = time.monotonic() - inst.last_call_ok
                if idle_time < interval:
                    continue

                # Perform ping
                try:
                    inst._busy = True
                    await registry._call_driver(
                        inst, inst.driver.passthrough, "*IDN?")
                    inst.last_call_ok = time.monotonic()
                    inst.health_failures = 0
                except Exception:
                    inst.health_failures += 1
                    if inst.health_failures >= HEALTH_MAX_FAILURES:
                        if inst.state != InstrumentState.UNRESPONSIVE:
                            inst.state = InstrumentState.UNRESPONSIVE
                            logger.error(
                                f"Health: {inst.name} UNRESPONSIVE after "
                                f"{HEALTH_MAX_FAILURES} consecutive failures")
                            await publish_instrument_event(
                                "UNRESPONSIVE", inst.name,
                                reason="health_ping_failed")
                finally:
                    inst._busy = False

        except asyncio.CancelledError:
            pass


# Global singleton
_health_monitor: HealthMonitor | None = None


def get_health_monitor() -> HealthMonitor:
    global _health_monitor
    if _health_monitor is None:
        _health_monitor = HealthMonitor()
    return _health_monitor
