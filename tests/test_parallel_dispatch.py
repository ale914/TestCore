# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Tests for IMREAD parallel dispatch behavior."""

import asyncio
import pytest
from testcore.commands import dispatcher, handle_instrument_add
from testcore.instruments import get_registry, InstrumentState

DRYRUN_PATH = "dryrun"
SESSION_1 = 1


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

    dispatcher._dispatch_lock = None

    from testcore.health import get_health_monitor
    get_health_monitor().stop_all()

    from testcore.watch import get_watch_manager
    get_watch_manager().stop_all()
    get_watch_manager()._watches.clear()


async def _setup_ready(name):
    ctx = {"session_id": SESSION_1}
    await dispatcher.dispatch(["IADD", name, DRYRUN_PATH], ctx)
    await dispatcher.dispatch(["ILOCK", name], ctx)
    await dispatcher.dispatch(["IINIT", name], ctx)


class TestSerialDispatch:
    """Dispatcher uses a single global asyncio.Lock by default."""

    @pytest.mark.asyncio
    async def test_ping_works(self):
        response = await dispatcher.dispatch(["PING"], {})
        assert response == b'+PONG\r\n'

    @pytest.mark.asyncio
    async def test_dispatch_lock_created_on_first_call(self):
        """Dispatch lock is lazily created on first dispatch."""
        dispatcher._dispatch_lock = None
        await dispatcher.dispatch(["PING"], {})
        assert dispatcher._dispatch_lock is not None

    @pytest.mark.asyncio
    async def test_kset_runs_serially(self):
        response = await dispatcher.dispatch(
            ["KSET", "key", "val"], {"session_id": 1})
        assert response == b'+OK\r\n'

    @pytest.mark.asyncio
    async def test_iadd_works(self):
        response = await dispatcher.dispatch(
            ["IADD", "vsg", DRYRUN_PATH], {"session_id": SESSION_1})
        assert response == b'+OK\r\n'
        assert "vsg" in get_registry()._instruments


class TestIReadParallel:
    """IMREAD always reads all resources concurrently via asyncio.gather."""

    @pytest.mark.asyncio
    async def test_imread_single(self):
        """IMREAD with one target works."""
        await _setup_ready("inst1")
        ctx = {"session_id": SESSION_1}
        r = await dispatcher.dispatch(["IMREAD", "inst1:VOUT"], ctx)
        assert not r.startswith(b'-')

    @pytest.mark.asyncio
    async def test_imread_multiple_same_instrument(self):
        """IMREAD with multiple resources on one instrument works."""
        await _setup_ready("inst1")
        ctx = {"session_id": SESSION_1}
        r = await dispatcher.dispatch(["IMREAD", "inst1:VOUT", "inst1:FREQ"], ctx)
        assert not r.startswith(b'-')
        # Returns RESP array
        assert r.startswith(b'*')

    @pytest.mark.asyncio
    async def test_imread_multiple_instruments(self):
        """IMREAD on different instruments executes concurrently."""
        await _setup_ready("inst1")
        await _setup_ready("inst2")
        ctx = {"session_id": SESSION_1}
        r = await dispatcher.dispatch(
            ["IMREAD", "inst1:VOUT", "inst2:VOUT"], ctx)
        assert not r.startswith(b'-')
        assert r.startswith(b'*')

    @pytest.mark.asyncio
    async def test_imread_returns_array(self):
        """IMREAD returns RESP array with one element per resource."""
        await _setup_ready("inst1")
        ctx = {"session_id": SESSION_1}
        r = await dispatcher.dispatch(
            ["IMREAD", "inst1:VOUT", "inst1:FREQ"], ctx)
        # *2\r\n... — array of 2 elements
        assert r.startswith(b'*2\r\n')

    @pytest.mark.asyncio
    async def test_imread_requires_lock(self):
        """IMREAD returns error if caller doesn't hold the lock."""
        await _setup_ready("inst1")
        other_ctx = {"session_id": 99}
        r = await dispatcher.dispatch(["IMREAD", "inst1:VOUT"], other_ctx)
        assert r.startswith(b'-')

    @pytest.mark.asyncio
    async def test_imread_unknown_instrument(self):
        """IMREAD returns error for unknown instrument."""
        ctx = {"session_id": SESSION_1}
        r = await dispatcher.dispatch(["IMREAD", "noexist:VOUT"], ctx)
        assert r.startswith(b'-')

    @pytest.mark.asyncio
    async def test_imread_no_args_error(self):
        """IMREAD with no args returns error."""
        ctx = {"session_id": SESSION_1}
        r = await dispatcher.dispatch(["IMREAD"], ctx)
        assert r.startswith(b'-')
