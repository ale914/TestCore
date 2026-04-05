# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Tests for IWATCH/IUNWATCH/IWATCHES guard system."""

import asyncio
import pytest

from testcore.commands import (
    handle_instrument_add, handle_instrument_remove,
    handle_instrument_init, handle_lock, handle_unlock,
    handle_watch, handle_unwatch, handle_watches,
    handle_write, handle_read,
)
from testcore.instruments import get_registry, InstrumentState
from testcore.store import get_store
from testcore.watch import get_watch_manager, WatchManager

DRYRUN = "dryrun"
CTX = {"session_id": 1}


@pytest.fixture(autouse=True)
def reset_all():
    """Reset registry, store and watch manager before each test."""
    registry = get_registry()
    for name in list(registry._instruments.keys()):
        try:
            registry.remove(name)
        except Exception:
            pass
    registry._instruments.clear()
    registry._driver_modules.clear()

    store = get_store()
    store._data.clear()
    store._meas.clear()

    wm = get_watch_manager()
    wm.stop_all()
    wm._watches.clear()


async def _setup_ready_instrument(name="sim"):
    """Helper: IADD + ILOCK + IINIT → READY."""
    await handle_instrument_add([name, DRYRUN], CTX)
    await handle_lock([name], CTX)
    await handle_instrument_init([name], CTX)
    return get_registry().get(name)


# ---------------------------------------------------------------------------
# IWATCH argument validation
# ---------------------------------------------------------------------------

class TestIWatchValidation:

    @pytest.mark.asyncio
    async def test_requires_three_args(self):
        r = await handle_watch(["sim", "VOUT"], CTX)
        assert b"wrong number" in r

    @pytest.mark.asyncio
    async def test_interval_not_integer(self):
        await _setup_ready_instrument()
        r = await handle_watch(["sim", "VOUT", "abc"], CTX)
        assert b"integer" in r

    @pytest.mark.asyncio
    async def test_interval_too_short(self):
        await _setup_ready_instrument()
        r = await handle_watch(["sim", "VOUT", "50"], CTX)
        assert b"interval too short" in r

    @pytest.mark.asyncio
    async def test_instrument_not_found(self):
        r = await handle_watch(["noexist", "VOUT", "500"], CTX)
        assert b"-" in r[:1]

    @pytest.mark.asyncio
    async def test_instrument_not_ready(self):
        await handle_instrument_add(["sim", DRYRUN], CTX)
        await handle_lock(["sim"], CTX)
        # LOCKED but not IINIT — not READY
        r = await handle_watch(["sim", "VOUT", "500"], CTX)
        assert b"NOTINIT" in r or b"not READY" in r

    @pytest.mark.asyncio
    async def test_requires_lock_owner(self):
        await _setup_ready_instrument()
        other_ctx = {"session_id": 99}
        r = await handle_watch(["sim", "VOUT", "500"], other_ctx)
        assert b"LOCKED" in r or b"IDLE" in r

    @pytest.mark.asyncio
    async def test_unknown_resource(self):
        await _setup_ready_instrument()
        r = await handle_watch(["sim", "NOSUCHRES", "500"], CTX)
        assert b"NORESOURCE" in r

    @pytest.mark.asyncio
    async def test_invalid_min_threshold(self):
        await _setup_ready_instrument()
        r = await handle_watch(["sim", "VOUT", "500", "MIN=abc"], CTX)
        assert b"invalid threshold" in r

    @pytest.mark.asyncio
    async def test_invalid_max_threshold(self):
        await _setup_ready_instrument()
        r = await handle_watch(["sim", "VOUT", "500", "MAX=xyz"], CTX)
        assert b"invalid threshold" in r

    @pytest.mark.asyncio
    async def test_valid_registration(self):
        await _setup_ready_instrument()
        r = await handle_watch(["sim", "VOUT", "500", "MAX=10.0"], CTX)
        assert r == b"+OK\r\n"
        assert get_watch_manager().has_watch("sim", "VOUT")

    @pytest.mark.asyncio
    async def test_valid_no_thresholds(self):
        await _setup_ready_instrument()
        r = await handle_watch(["sim", "VOUT", "200"], CTX)
        assert r == b"+OK\r\n"

    @pytest.mark.asyncio
    async def test_duplicate_watch_replaced(self):
        await _setup_ready_instrument()
        await handle_watch(["sim", "VOUT", "500", "MAX=10.0"], CTX)
        old_task = get_watch_manager()._watches["sim"]["VOUT"].task
        await handle_watch(["sim", "VOUT", "1000", "MAX=20.0"], CTX)
        new_entry = get_watch_manager()._watches["sim"]["VOUT"]
        assert new_entry.interval_ms == 1000
        assert new_entry.max_val == 20.0
        assert new_entry.task is not old_task


# ---------------------------------------------------------------------------
# IUNWATCH
# ---------------------------------------------------------------------------

class TestIUnwatch:

    @pytest.mark.asyncio
    async def test_unwatch_specific(self):
        await _setup_ready_instrument()
        await handle_watch(["sim", "VOUT", "500"], CTX)
        r = await handle_unwatch(["sim", "VOUT"], CTX)
        assert r == b"+OK\r\n"
        assert not get_watch_manager().has_watch("sim", "VOUT")

    @pytest.mark.asyncio
    async def test_unwatch_not_found(self):
        await _setup_ready_instrument()
        r = await handle_unwatch(["sim", "VOUT"], CTX)
        assert b"watch not found" in r

    @pytest.mark.asyncio
    async def test_unwatch_instrument_all(self):
        await _setup_ready_instrument()
        await handle_watch(["sim", "VOUT", "500"], CTX)
        await handle_watch(["sim", "FREQ", "500"], CTX)
        r = await handle_unwatch(["sim", "ALL"], CTX)
        assert r == b"+OK\r\n"
        assert not get_watch_manager().has_watch("sim", "VOUT")
        assert not get_watch_manager().has_watch("sim", "FREQ")

    @pytest.mark.asyncio
    async def test_unwatch_marks_meas_stale(self):
        inst = await _setup_ready_instrument()
        # Write a MEAS entry first
        get_store().write_meas("sim", "VOUT", "3.3", "OK")
        await handle_watch(["sim", "VOUT", "500"], CTX)
        await handle_unwatch(["sim", "VOUT"], CTX)
        meas = get_store().get_meas("sim", "VOUT")
        assert meas.status == "STALE"

    @pytest.mark.asyncio
    async def test_iunwatch_all_session(self):
        await _setup_ready_instrument("a")
        await _setup_ready_instrument("b")
        # a is owned by session 1, b also
        await handle_watch(["a", "VOUT", "500"], CTX)
        await handle_watch(["b", "VOUT", "500"], CTX)
        r = await handle_unwatch(["ALL"], CTX)
        assert r == b"+OK\r\n"
        assert not get_watch_manager().has_watch("a", "VOUT")
        assert not get_watch_manager().has_watch("b", "VOUT")


# ---------------------------------------------------------------------------
# IWATCHES
# ---------------------------------------------------------------------------

class TestIWatches:

    @pytest.mark.asyncio
    async def test_empty(self):
        r = await handle_watches([], CTX)
        assert r == b"*0\r\n"

    @pytest.mark.asyncio
    async def test_lists_registered(self):
        await _setup_ready_instrument()
        await handle_watch(["sim", "VOUT", "500", "MAX=10.0"], CTX)
        r = await handle_watches([], CTX)
        assert b"sim" in r
        assert b"VOUT" in r
        assert b"500" in r
        assert b"MAX=10.0" in r

    @pytest.mark.asyncio
    async def test_filter_by_instrument(self):
        await _setup_ready_instrument("a")
        await _setup_ready_instrument("b")
        await handle_watch(["a", "VOUT", "500"], CTX)
        await handle_watch(["b", "VOUT", "500"], CTX)
        r = await handle_watches(["a"], CTX)
        assert b"a:VOUT" in r
        assert b"b:VOUT" not in r


# ---------------------------------------------------------------------------
# Watch polling — MEAS updated autonomously
# ---------------------------------------------------------------------------

class TestWatchPolling:

    @pytest.mark.asyncio
    async def test_meas_updated_by_watch(self):
        """Watch loop reads hardware and writes MEAS automatically."""
        inst = await _setup_ready_instrument()
        # Set a known value in dryrun
        inst.driver._state["VOUT"] = "5.0"

        await handle_watch(["sim", "VOUT", "100"], CTX)  # 100ms interval
        await asyncio.sleep(0.35)  # wait for at least 2 cycles

        meas = get_store().get_meas("sim", "VOUT")
        assert meas is not None
        assert meas.value == "5.0"
        assert meas.status == "OK"

    @pytest.mark.asyncio
    async def test_meas_reflects_updated_value(self):
        """Watch picks up value changes set by the client."""
        inst = await _setup_ready_instrument()
        inst.driver._state["VOUT"] = "1.0"

        await handle_watch(["sim", "VOUT", "100"], CTX)
        await asyncio.sleep(0.25)

        meas1 = get_store().get_meas("sim", "VOUT")
        assert meas1.value == "1.0"

        inst.driver._state["VOUT"] = "2.5"
        await asyncio.sleep(0.25)

        meas2 = get_store().get_meas("sim", "VOUT")
        assert meas2.value == "2.5"


# ---------------------------------------------------------------------------
# Guard trip — threshold violation
# ---------------------------------------------------------------------------

class TestGuardTrip:

    @pytest.mark.asyncio
    async def test_guard_trip_max(self):
        """Value above MAX triggers safe_state + FAULT."""
        inst = await _setup_ready_instrument()
        inst.driver._state["VOUT"] = "15.0"  # above MAX=10

        await handle_watch(["sim", "VOUT", "100", "MAX=10.0"], CTX)
        await asyncio.sleep(0.35)

        assert inst.state == InstrumentState.FAULT
        meas = get_store().get_meas("sim", "VOUT")
        assert meas.status == "GUARD_FAULT"

    @pytest.mark.asyncio
    async def test_guard_trip_min(self):
        """Value below MIN triggers safe_state + FAULT."""
        inst = await _setup_ready_instrument()
        inst.driver._state["VOUT"] = "1.0"  # below MIN=3.2

        await handle_watch(["sim", "VOUT", "100", "MIN=3.2"], CTX)
        await asyncio.sleep(0.35)

        assert inst.state == InstrumentState.FAULT

    @pytest.mark.asyncio
    async def test_no_trip_within_range(self):
        """Value within range does NOT trip."""
        inst = await _setup_ready_instrument()
        inst.driver._state["VOUT"] = "5.0"

        await handle_watch(["sim", "VOUT", "100", "MIN=3.0", "MAX=10.0"], CTX)
        await asyncio.sleep(0.35)

        assert inst.state == InstrumentState.READY

    @pytest.mark.asyncio
    async def test_no_trip_without_thresholds(self):
        """Watch without thresholds never trips."""
        inst = await _setup_ready_instrument()
        inst.driver._state["VOUT"] = "9999.0"

        await handle_watch(["sim", "VOUT", "100"], CTX)
        await asyncio.sleep(0.35)

        assert inst.state == InstrumentState.READY

    @pytest.mark.asyncio
    async def test_guard_resumes_after_ireset(self):
        """After IRESET + IINIT, guard resumes reading (no trip since value fixed)."""
        from testcore.commands import handle_instrument_reset, handle_instrument_init
        inst = await _setup_ready_instrument()
        inst.driver._state["VOUT"] = "15.0"

        await handle_watch(["sim", "VOUT", "100", "MAX=10.0"], CTX)
        await asyncio.sleep(0.35)

        assert inst.state == InstrumentState.FAULT

        # Reset, re-init, then fix value (reset reinitializes driver state)
        await handle_instrument_reset(["sim"], CTX)
        await handle_instrument_init(["sim"], CTX)
        inst.driver._state["VOUT"] = "5.0"

        assert inst.state == InstrumentState.READY

        await asyncio.sleep(0.35)
        meas = get_store().get_meas("sim", "VOUT")
        assert meas.value == "5.0"
        assert meas.status == "OK"


# ---------------------------------------------------------------------------
# Lifecycle — IUNLOCK and disconnect cleanup
# ---------------------------------------------------------------------------

class TestWatchLifecycle:

    @pytest.mark.asyncio
    async def test_iunlock_stops_watches(self):
        """IUNLOCK stops all watches on the instrument."""
        await _setup_ready_instrument()
        await handle_watch(["sim", "VOUT", "100"], CTX)
        assert get_watch_manager().has_watch("sim", "VOUT")

        await handle_unlock(["sim"], CTX)
        assert not get_watch_manager().has_watch("sim", "VOUT")

    @pytest.mark.asyncio
    async def test_iremove_stops_watches(self):
        """IREMOVE stops watches before disconnecting driver."""
        await _setup_ready_instrument()
        await handle_watch(["sim", "VOUT", "100"], CTX)
        assert get_watch_manager().has_watch("sim", "VOUT")

        await handle_instrument_remove(["sim"], CTX)
        assert not get_watch_manager().has_watch("sim", "VOUT")

    @pytest.mark.asyncio
    async def test_watch_skips_non_ready(self):
        """Watch loop skips reading when instrument is not READY."""
        inst = await _setup_ready_instrument()
        inst.driver._state["VOUT"] = "5.0"

        await handle_watch(["sim", "VOUT", "100"], CTX)
        await asyncio.sleep(0.15)
        first_meas = get_store().get_meas("sim", "VOUT")
        assert first_meas is not None

        # Force non-READY state
        inst.state = InstrumentState.LOCKED
        prev_ts = first_meas.ts
        await asyncio.sleep(0.25)

        # MEAS should not have been updated
        current_meas = get_store().get_meas("sim", "VOUT")
        assert current_meas.ts == prev_ts
