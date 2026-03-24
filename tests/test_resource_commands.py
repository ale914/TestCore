# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Tests for Resource Commands (spec §6.4)."""

import pytest
from testcore.commands import (
    handle_read, handle_write, handle_raw, handle_readmulti,
    handle_load, handle_lock, handle_instrument_add
)
from testcore.instruments import get_registry, InstrumentState
from testcore.protocol import RESPParser

# Bundled driver short name
DRYRUN_PATH = "dryrun"

SESSION_1 = 1
SESSION_2 = 2


def ctx(session_id):
    return {"session_id": session_id}


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset instrument registry before each test."""
    registry = get_registry()
    for name in list(registry._instruments.keys()):
        try:
            registry.remove(name)
        except Exception:
            pass
    registry._instruments.clear()
    registry._driver_modules.clear()


async def setup_ready_instrument(name, resources=None):
    """Helper: add, lock, and init instrument to READY state."""
    registry = get_registry()
    registry.add(name, DRYRUN_PATH,
                 config={"resources": resources or ["FREQ", "POWER"]})
    registry.lock(name, SESSION_1)
    await registry.init_instrument(name)


class TestReadCommand:
    """Tests for READ command."""

    @pytest.mark.asyncio
    async def test_read_basic(self):
        await setup_ready_instrument("vsg")
        response = await handle_read(["vsg:FREQ"], ctx(SESSION_1))
        parser = RESPParser()
        messages = parser.feed(response)
        # DryRun driver returns simulated values
        assert messages[0] is not None

    @pytest.mark.asyncio
    async def test_read_wrong_owner(self):
        await setup_ready_instrument("vsg")
        response = await handle_read(["vsg:FREQ"], ctx(SESSION_2))
        assert b'-LOCKED' in response
        assert b'session 1' in response

    @pytest.mark.asyncio
    async def test_read_on_idle(self):
        registry = get_registry()
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        response = await handle_read(["vsg:FREQ"], ctx(SESSION_1))
        assert b'-IDLE' in response

    @pytest.mark.asyncio
    async def test_read_on_locked_not_init(self):
        registry = get_registry()
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", SESSION_1)
        response = await handle_read(["vsg:FREQ"], ctx(SESSION_1))
        assert b'-NOTINIT' in response

    @pytest.mark.asyncio
    async def test_read_on_fault(self):
        await setup_ready_instrument("vsg")
        inst = get_registry().get("vsg")
        inst.state = InstrumentState.FAULT
        response = await handle_read(["vsg:FREQ"], ctx(SESSION_1))
        assert b'-FAULT' in response

    @pytest.mark.asyncio
    async def test_read_invalid_format(self):
        response = await handle_read(["nocolon"], ctx(SESSION_1))
        assert response.startswith(b'-ERR')
        assert b'invalid resource address' in response

    @pytest.mark.asyncio
    async def test_read_nonexistent_instrument(self):
        response = await handle_read(["missing:FREQ"], ctx(SESSION_1))
        assert b'-DRIVER' in response

    @pytest.mark.asyncio
    async def test_read_wrong_args(self):
        response = await handle_read([], ctx(SESSION_1))
        assert response.startswith(b'-ERR')

    @pytest.mark.asyncio
    async def test_read_no_session(self):
        await setup_ready_instrument("vsg")
        response = await handle_read(["vsg:FREQ"])
        assert b'no session' in response


class TestWriteCommand:
    """Tests for WRITE command."""

    @pytest.mark.asyncio
    async def test_write_basic(self):
        await setup_ready_instrument("vsg")
        response = await handle_write(["vsg:FREQ", "900e6"], ctx(SESSION_1))
        assert response == b'+OK\r\n'

    @pytest.mark.asyncio
    async def test_write_wrong_owner(self):
        await setup_ready_instrument("vsg")
        response = await handle_write(["vsg:FREQ", "900e6"], ctx(SESSION_2))
        assert b'-LOCKED' in response

    @pytest.mark.asyncio
    async def test_write_on_idle(self):
        registry = get_registry()
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        response = await handle_write(["vsg:FREQ", "900e6"], ctx(SESSION_1))
        assert b'-IDLE' in response

    @pytest.mark.asyncio
    async def test_write_wrong_args(self):
        response = await handle_write(["vsg:FREQ"], ctx(SESSION_1))
        assert response.startswith(b'-ERR')


class TestRawCommand:
    """Tests for RAW command."""

    @pytest.mark.asyncio
    async def test_raw_basic(self):
        await setup_ready_instrument("vsg")
        response = await handle_raw(["vsg", ":FREQ?"], ctx(SESSION_1))
        parser = RESPParser()
        messages = parser.feed(response)
        assert messages[0] is not None

    @pytest.mark.asyncio
    async def test_raw_wrong_owner(self):
        await setup_ready_instrument("vsg")
        response = await handle_raw(["vsg", ":FREQ?"], ctx(SESSION_2))
        assert b'-LOCKED' in response

    @pytest.mark.asyncio
    async def test_raw_on_idle(self):
        registry = get_registry()
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        response = await handle_raw(["vsg", ":FREQ?"], ctx(SESSION_1))
        assert b'-IDLE' in response

    @pytest.mark.asyncio
    async def test_raw_wrong_args(self):
        response = await handle_raw(["vsg"], ctx(SESSION_1))
        assert response.startswith(b'-ERR')

    @pytest.mark.asyncio
    async def test_raw_multi_word_command(self):
        await setup_ready_instrument("vsg")
        response = await handle_raw(
            ["vsg", ":CALC:MARK1:Y?"], ctx(SESSION_1))
        parser = RESPParser()
        messages = parser.feed(response)
        assert messages[0] is not None


class TestReadmultiCommand:
    """Tests for READMULTI command."""

    @pytest.mark.asyncio
    async def test_readmulti_basic(self):
        await setup_ready_instrument("vsg", ["FREQ", "POWER"])
        response = await handle_readmulti(
            ["vsg:FREQ", "vsg:POWER"], ctx(SESSION_1))
        parser = RESPParser()
        messages = parser.feed(response)
        assert isinstance(messages[0], list)
        assert len(messages[0]) == 2

    @pytest.mark.asyncio
    async def test_readmulti_cross_instrument(self):
        await setup_ready_instrument("vsg", ["FREQ"])
        registry = get_registry()
        registry.add("sa", DRYRUN_PATH, config={"resources": ["SPAN"]})
        registry.lock("sa", SESSION_1)
        await registry.init_instrument("sa")

        response = await handle_readmulti(
            ["vsg:FREQ", "sa:SPAN"], ctx(SESSION_1))
        parser = RESPParser()
        messages = parser.feed(response)
        assert isinstance(messages[0], list)
        assert len(messages[0]) == 2

    @pytest.mark.asyncio
    async def test_readmulti_wrong_owner(self):
        await setup_ready_instrument("vsg", ["FREQ"])
        response = await handle_readmulti(
            ["vsg:FREQ"], ctx(SESSION_2))
        assert b'-LOCKED' in response

    @pytest.mark.asyncio
    async def test_readmulti_invalid_format(self):
        response = await handle_readmulti(["nocolon"], ctx(SESSION_1))
        assert response.startswith(b'-ERR')

    @pytest.mark.asyncio
    async def test_readmulti_wrong_args(self):
        response = await handle_readmulti([], ctx(SESSION_1))
        assert response.startswith(b'-ERR')


class TestLoadCommand:
    """Tests for ILOAD command."""

    @pytest.mark.asyncio
    async def test_load_basic(self, tmp_path):
        csv_file = tmp_path / "wave.csv"
        csv_file.write_text("0.0\n0.5\n1.0\n0.5\n0.0\n-0.5\n-1.0\n-0.5\n")

        await setup_ready_instrument("vsg", ["FREQ", "POWER"])
        response = await handle_load(
            ["vsg", "wave1", str(csv_file)], ctx(SESSION_1))
        parser = RESPParser()
        messages = parser.feed(response)
        assert "8 points loaded" in messages[0]

    @pytest.mark.asyncio
    async def test_load_wrong_owner(self, tmp_path):
        csv_file = tmp_path / "wave.csv"
        csv_file.write_text("0.0\n0.5\n1.0\n0.5\n0.0\n-0.5\n-1.0\n-0.5\n")

        await setup_ready_instrument("vsg")
        response = await handle_load(
            ["vsg", "wave1", str(csv_file)], ctx(SESSION_2))
        assert b'-LOCKED' in response

    @pytest.mark.asyncio
    async def test_load_on_idle(self, tmp_path):
        csv_file = tmp_path / "wave.csv"
        csv_file.write_text("0.0\n")

        registry = get_registry()
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        response = await handle_load(
            ["vsg", "wave1", str(csv_file)], ctx(SESSION_1))
        assert b'-IDLE' in response

    @pytest.mark.asyncio
    async def test_load_file_not_found(self):
        await setup_ready_instrument("vsg")
        response = await handle_load(
            ["vsg", "wave1", "/nonexistent/file.csv"], ctx(SESSION_1))
        assert b'-DRIVER' in response

    @pytest.mark.asyncio
    async def test_load_wrong_args(self):
        response = await handle_load(["vsg", "wave1"], ctx(SESSION_1))
        assert response.startswith(b'-ERR')

    @pytest.mark.asyncio
    async def test_load_no_session(self, tmp_path):
        csv_file = tmp_path / "wave.csv"
        csv_file.write_text("0.0\n")

        await setup_ready_instrument("vsg")
        response = await handle_load(
            ["vsg", "wave1", str(csv_file)])
        assert b'no session' in response
