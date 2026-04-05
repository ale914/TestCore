# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Tests for BENCH command and bench.cfg loading."""

import asyncio
import os
import tempfile
import pytest

from testcore.commands import handle_bench, handle_instrument_add, handle_lock, handle_instrument_init
from testcore.instruments import get_registry, InstrumentState
from testcore.store import get_store
from testcore.watch import get_watch_manager
from testcore.server import load_bench_config

DRYRUN = "dryrun"
CTX = {"session_id": 1}


@pytest.fixture(autouse=True)
def reset_all():
    registry = get_registry()
    for name in list(registry._instruments.keys()):
        try:
            registry.remove(name)
        except Exception:
            pass
    registry._instruments.clear()
    registry._driver_modules.clear()

    get_store()._data.clear()
    get_store()._meas.clear()

    wm = get_watch_manager()
    wm.stop_all()
    wm._watches.clear()


async def _setup_ready(name="sim"):
    await handle_instrument_add([name, DRYRUN], CTX)
    await handle_lock([name], CTX)
    await handle_instrument_init([name], CTX)


# ---------------------------------------------------------------------------
# BENCH command
# ---------------------------------------------------------------------------

class TestBenchCommand:

    @pytest.mark.asyncio
    async def test_empty_bench(self):
        r = await handle_bench([], CTX)
        assert r == b"*0\r\n"

    @pytest.mark.asyncio
    async def test_single_instrument_idle(self):
        await handle_instrument_add(["awg", DRYRUN], CTX)
        r = await handle_bench([], CTX)
        assert b"awg" in r
        assert b"IDLE" in r
        assert b"owner=-" in r
        assert b"watches=0" in r
        assert b"health=off" in r

    @pytest.mark.asyncio
    async def test_ready_instrument_shows_owner(self):
        await _setup_ready("awg")
        r = await handle_bench([], CTX)
        assert b"READY" in r
        assert b"owner=1" in r

    @pytest.mark.asyncio
    async def test_health_interval_shown(self):
        await handle_instrument_add(["psu", DRYRUN, "health=10"], CTX)
        r = await handle_bench([], CTX)
        assert b"health=10s" in r

    @pytest.mark.asyncio
    async def test_watch_count_shown(self):
        from testcore.commands import handle_watch
        await _setup_ready("sim")
        await handle_watch(["sim", "VOUT", "500"], CTX)
        r = await handle_bench([], CTX)
        assert b"watches=1" in r

    @pytest.mark.asyncio
    async def test_multiple_instruments(self):
        await handle_instrument_add(["a", DRYRUN], CTX)
        await handle_instrument_add(["b", DRYRUN], CTX)
        r = await handle_bench([], CTX)
        assert b"a " in r
        assert b"b " in r

    @pytest.mark.asyncio
    async def test_fault_state_shown(self):
        await _setup_ready("sim")
        inst = get_registry().get("sim")
        inst.state = InstrumentState.FAULT
        r = await handle_bench([], CTX)
        assert b"FAULT" in r


# ---------------------------------------------------------------------------
# bench.cfg loading
# ---------------------------------------------------------------------------

class TestBenchConfig:

    @pytest.mark.asyncio
    async def test_load_single_instrument(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".cfg",
                                         delete=False) as f:
            f.write("sim dryrun\n")
            path = f.name
        try:
            await load_bench_config(path)
            assert "sim" in get_registry()._instruments
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_load_multiple_instruments(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".cfg",
                                         delete=False) as f:
            f.write("awg dryrun\npsu dryrun\n")
            path = f.name
        try:
            await load_bench_config(path)
            assert "awg" in get_registry()._instruments
            assert "psu" in get_registry()._instruments
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_comments_and_blank_lines_skipped(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".cfg",
                                         delete=False) as f:
            f.write("# this is a comment\n\nsim dryrun\n\n")
            path = f.name
        try:
            await load_bench_config(path)
            assert "sim" in get_registry()._instruments
            assert len(get_registry()._instruments) == 1
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_bad_line_skipped_others_loaded(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".cfg",
                                         delete=False) as f:
            f.write("onlyone\nsim dryrun\n")  # first line has no driver
            path = f.name
        try:
            await load_bench_config(path)
            assert "sim" in get_registry()._instruments
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_missing_file_does_not_raise(self):
        # Should log error and return without raising
        await load_bench_config("/nonexistent/path/bench.cfg")

    @pytest.mark.asyncio
    async def test_health_kwarg_parsed(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".cfg",
                                         delete=False) as f:
            f.write("psu dryrun health=15\n")
            path = f.name
        try:
            await load_bench_config(path)
            inst = get_registry().get("psu")
            assert inst.health_interval == 15
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_instruments_start_idle(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".cfg",
                                         delete=False) as f:
            f.write("awg dryrun\n")
            path = f.name
        try:
            await load_bench_config(path)
            inst = get_registry().get("awg")
            assert inst.state == InstrumentState.IDLE
            assert inst.lock_owner is None
        finally:
            os.unlink(path)
