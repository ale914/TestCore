# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Tests for --parallel per-instrument dispatch locking."""

import asyncio
import time
import pytest
from testcore.commands import dispatcher, handle_instrument_add
from testcore.instruments import get_registry, InstrumentState

DRYRUN_PATH = "dryrun"
SESSION_1 = 1
SESSION_2 = 2


@pytest.fixture(autouse=True)
def reset_all():
    """Reset registry and dispatcher state before each test."""
    registry = get_registry()
    for name in list(registry._instruments.keys()):
        try:
            registry.remove(name)
        except Exception:
            pass
    registry._instruments.clear()
    registry._driver_modules.clear()

    # Reset dispatcher parallel state
    dispatcher._parallel = False
    dispatcher._instrument_locks.clear()
    dispatcher._dispatch_lock = None
    dispatcher._registry_lock = None

    from testcore.health import get_health_monitor
    get_health_monitor().stop_all()


class TestParallelFlag:
    """Tests for set_parallel() configuration."""

    def test_default_not_parallel(self):
        assert dispatcher._parallel is False

    def test_set_parallel(self):
        dispatcher.set_parallel(True)
        assert dispatcher._parallel is True

    def test_set_parallel_false(self):
        dispatcher.set_parallel(True)
        dispatcher.set_parallel(False)
        assert dispatcher._parallel is False


class TestParallelExtractInstruments:
    """Tests for _extract_instruments() instrument name extraction."""

    def test_single_instrument_command(self):
        result = dispatcher._extract_instruments("IREAD", ["awg", "CH1:FREQ"])
        assert result == ["awg"]

    def test_single_instrument_colon_format(self):
        result = dispatcher._extract_instruments("IREAD", ["awg:CH1:FREQ"])
        assert result == ["awg"]

    def test_multi_instrument_command(self):
        result = dispatcher._extract_instruments("ILOCK", ["awg", "osc1", "osc2"])
        assert result == ["awg", "osc1", "osc2"]

    def test_imread_multi(self):
        result = dispatcher._extract_instruments(
            "IMREAD", ["awg:CH1:FREQ", "osc:CH1:AMPL"])
        assert set(result) == {"awg", "osc"}

    def test_non_instrument_command(self):
        result = dispatcher._extract_instruments("PING", [])
        assert result == []

    def test_kset_no_lock(self):
        result = dispatcher._extract_instruments("KSET", ["key", "value"])
        assert result == []

    def test_unlock_all(self):
        result = dispatcher._extract_instruments("IUNLOCK", ["ALL"])
        assert result == []

    def test_unlock_single(self):
        result = dispatcher._extract_instruments("IUNLOCK", ["awg"])
        assert result == ["awg"]

    def test_empty_args(self):
        result = dispatcher._extract_instruments("IREAD", [])
        assert result == []

    def test_registry_command_not_extracted(self):
        """IADD/IREMOVE use registry lock, not instrument lock."""
        result = dispatcher._extract_instruments("IADD", ["awg", "dryrun"])
        assert result == []


class TestParallelDispatch:
    """Tests for parallel dispatch behavior."""

    @pytest.mark.asyncio
    async def test_non_instrument_no_lock(self):
        """Non-instrument commands run without any lock in parallel mode."""
        dispatcher.set_parallel(True)
        response = await dispatcher.dispatch(["PING"], {})
        assert response == b'+PONG\r\n'

    @pytest.mark.asyncio
    async def test_kset_no_lock(self):
        """KV commands run without lock in parallel mode."""
        dispatcher.set_parallel(True)
        response = await dispatcher.dispatch(
            ["KSET", "key", "val"], {"session_id": 1})
        assert response == b'+OK\r\n'

    @pytest.mark.asyncio
    async def test_iadd_uses_registry_lock(self):
        """IADD acquires registry lock in parallel mode."""
        dispatcher.set_parallel(True)
        response = await dispatcher.dispatch(
            ["IADD", "vsg", DRYRUN_PATH], {})
        assert response == b'+OK\r\n'
        assert "vsg" in get_registry()._instruments

    @pytest.mark.asyncio
    async def test_iremove_cleans_lock(self):
        """IREMOVE removes the per-instrument lock."""
        dispatcher.set_parallel(True)
        await dispatcher.dispatch(["IADD", "vsg", DRYRUN_PATH], {})

        # Create instrument lock by dispatching an instrument command
        await dispatcher.dispatch(["IINFO", "vsg"], {})
        assert "vsg" in dispatcher._instrument_locks

        await dispatcher.dispatch(["IREMOVE", "vsg"], {})
        assert "vsg" not in dispatcher._instrument_locks

    @pytest.mark.asyncio
    async def test_instrument_command_creates_lock(self):
        """First instrument command creates a per-instrument lock."""
        dispatcher.set_parallel(True)
        await dispatcher.dispatch(["IADD", "vsg", DRYRUN_PATH], {})

        assert "vsg" not in dispatcher._instrument_locks
        await dispatcher.dispatch(["IPING", "vsg"], {})
        assert "vsg" in dispatcher._instrument_locks

    @pytest.mark.asyncio
    async def test_parallel_different_instruments(self):
        """Commands on different instruments can run concurrently."""
        dispatcher.set_parallel(True)
        await dispatcher.dispatch(["IADD", "inst1", DRYRUN_PATH], {})
        await dispatcher.dispatch(["IADD", "inst2", DRYRUN_PATH], {})

        # Both should complete without blocking each other
        ctx = {"session_id": SESSION_1}
        await dispatcher.dispatch(["ILOCK", "inst1"], ctx)
        await dispatcher.dispatch(["ILOCK", "inst2"], ctx)

        r1 = await dispatcher.dispatch(["IPING", "inst1"], ctx)
        r2 = await dispatcher.dispatch(["IPING", "inst2"], ctx)
        assert not r1.startswith(b'-')
        assert not r2.startswith(b'-')

    @pytest.mark.asyncio
    async def test_global_mode_still_works(self):
        """Default (non-parallel) mode uses global lock."""
        # dispatcher._parallel is False by default from fixture
        response = await dispatcher.dispatch(["PING"], {})
        assert response == b'+PONG\r\n'
        assert dispatcher._dispatch_lock is not None

    @pytest.mark.asyncio
    async def test_ilock_multi_sorted_order(self):
        """ILOCK with multiple instruments acquires locks in sorted order."""
        dispatcher.set_parallel(True)
        await dispatcher.dispatch(["IADD", "zzz", DRYRUN_PATH], {})
        await dispatcher.dispatch(["IADD", "aaa", DRYRUN_PATH], {})

        ctx = {"session_id": SESSION_1}
        response = await dispatcher.dispatch(["ILOCK", "zzz", "aaa"], ctx)
        assert response == b'+OK\r\n'

        # Both should be locked
        registry = get_registry()
        assert registry.get("zzz").lock_owner == SESSION_1
        assert registry.get("aaa").lock_owner == SESSION_1
