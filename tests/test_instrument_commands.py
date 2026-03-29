# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Tests for Instrument Commands (spec §6.3)."""

import json
import pytest
from testcore.commands import (
    handle_instrument_add, handle_instrument_remove, handle_instrument_init,
    handle_instrument_info, handle_instrument_list, handle_instrument_resources,
    handle_instrument_ping, handle_instrument_wait, handle_instrument_reset,
    handle_align, handle_driver_list
)
from testcore.instruments import get_registry, InstrumentState
from testcore.protocol import RESPParser

# Bundled driver short name
DRYRUN_PATH = "dryrun"

SESSION_1 = 1


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


async def add_and_lock(name, resources=None):
    """Helper: add instrument via registry and lock it for SESSION_1."""
    registry = get_registry()
    config = {"resources": resources} if resources else None
    registry.add(name, DRYRUN_PATH, config=config)
    registry.lock(name, SESSION_1)


class TestInstrumentAddCommand:
    """Tests for IADD command. State → IDLE."""

    @pytest.mark.asyncio
    async def test_add_basic(self):
        response = await handle_instrument_add(["vsg", DRYRUN_PATH])
        assert response == b'+OK\r\n'
        inst = get_registry().get("vsg")
        assert inst.state == InstrumentState.IDLE

    @pytest.mark.asyncio
    async def test_add_no_config(self):
        response = await handle_instrument_add(["vsg", DRYRUN_PATH])
        assert response == b'+OK\r\n'

    @pytest.mark.asyncio
    async def test_add_duplicate_error(self):
        await handle_instrument_add(["vsg", DRYRUN_PATH])
        response = await handle_instrument_add(["vsg", DRYRUN_PATH])
        assert response.startswith(b'-')
        assert b'already exists' in response

    @pytest.mark.asyncio
    async def test_add_wrong_args(self):
        response = await handle_instrument_add(["vsg"])
        assert response.startswith(b'-ERR')


class TestInstrumentRemoveCommand:
    """Tests for IREMOVE command."""

    @pytest.mark.asyncio
    async def test_remove_basic(self):
        await handle_instrument_add(["vsg", DRYRUN_PATH])
        response = await handle_instrument_remove(["vsg"])
        assert response == b'+OK\r\n'

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self):
        response = await handle_instrument_remove(["missing"])
        assert response.startswith(b'-')


class TestInstrumentInitCommand:
    """Tests for IINIT command. Requires LOCKED state."""

    @pytest.mark.asyncio
    async def test_init_basic(self):
        await add_and_lock("vsg", ["FREQ", "POWER"])
        response = await handle_instrument_init(["vsg"])
        assert response == b'+OK\r\n'

        inst = get_registry().get("vsg")
        assert inst.state == InstrumentState.READY

    @pytest.mark.asyncio
    async def test_init_with_config_file(self, tmp_path):
        config_file = tmp_path / "test.cfg"
        config_file.write_text("test config")

        await add_and_lock("vsg", ["FREQ"])
        response = await handle_instrument_init(["vsg", str(config_file)])
        assert response == b'+OK\r\n'

    @pytest.mark.asyncio
    async def test_init_on_idle_returns_error(self):
        """INIT on IDLE instrument returns -IDLE error."""
        await handle_instrument_add(["vsg", DRYRUN_PATH])
        response = await handle_instrument_init(["vsg"])
        assert b'-IDLE' in response

    @pytest.mark.asyncio
    async def test_init_wrong_args(self):
        response = await handle_instrument_init([])
        assert response.startswith(b'-ERR')


class TestInstrumentInfoCommand:
    """Tests for IINFO command."""

    @pytest.mark.asyncio
    async def test_info_basic(self):
        await add_and_lock("vsg", ["FREQ"])
        await handle_instrument_init(["vsg"])
        response = await handle_instrument_info(["vsg"])

        parser = RESPParser()
        messages = parser.feed(response)
        result = messages[0]

        assert "name:vsg" in result
        assert "state:READY" in result
        assert "vendor:TestCore" in result

    @pytest.mark.asyncio
    async def test_info_idle_state(self):
        """INFO shows IDLE state before lock."""
        await handle_instrument_add(["vsg", DRYRUN_PATH])
        response = await handle_instrument_info(["vsg"])

        parser = RESPParser()
        messages = parser.feed(response)
        result = messages[0]
        assert "state:IDLE" in result

    @pytest.mark.asyncio
    async def test_info_nonexistent(self):
        response = await handle_instrument_info(["missing"])
        assert response.startswith(b'-')


class TestInstrumentListCommand:
    """Tests for ILIST command."""

    @pytest.mark.asyncio
    async def test_list_empty(self):
        response = await handle_instrument_list([])
        parser = RESPParser()
        messages = parser.feed(response)
        assert messages[0] == []

    @pytest.mark.asyncio
    async def test_list_with_instruments(self):
        await handle_instrument_add(["vsg", DRYRUN_PATH])
        await handle_instrument_add(["sa", DRYRUN_PATH])

        response = await handle_instrument_list([])
        parser = RESPParser()
        messages = parser.feed(response)
        assert set(messages[0]) == {"sa", "vsg"}


class TestInstrumentResourcesCommand:
    """Tests for IRESOURCES command."""

    @pytest.mark.asyncio
    async def test_resources_after_init(self):
        await add_and_lock("vsg", ["FREQ", "POWER"])
        await handle_instrument_init(["vsg"])

        response = await handle_instrument_resources(["vsg"])
        parser = RESPParser()
        messages = parser.feed(response)
        assert set(messages[0]) == {"FREQ", "POWER"}

    @pytest.mark.asyncio
    async def test_resources_before_init(self):
        """IRESOURCES calls discover() directly even on IDLE."""
        registry = get_registry()
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})

        response = await handle_instrument_resources(["vsg"])
        parser = RESPParser()
        messages = parser.feed(response)
        assert messages[0] == ["FREQ"]


class TestInstrumentResetCommand:
    """Tests for IRESET command. FAULT → LOCKED."""

    @pytest.mark.asyncio
    async def test_reset_from_fault(self):
        await add_and_lock("vsg", ["FREQ"])
        inst = get_registry().get("vsg")
        inst.state = InstrumentState.FAULT

        response = await handle_instrument_reset(["vsg"])
        assert response == b'+OK\r\n'
        assert inst.state == InstrumentState.LOCKED
        assert inst.lock_owner == SESSION_1

    @pytest.mark.asyncio
    async def test_reset_from_ready_error(self):
        await add_and_lock("vsg", ["FREQ"])
        await handle_instrument_init(["vsg"])

        response = await handle_instrument_reset(["vsg"])
        assert response.startswith(b'-')


class TestAlignCommand:
    """Tests for IALIGN command. Requires LOCKED state."""

    @pytest.mark.asyncio
    async def test_align_basic(self):
        await add_and_lock("vsg", ["FREQ"])
        response = await handle_align(["vsg"])
        assert response == b'+OK\r\n'

        inst = get_registry().get("vsg")
        assert inst.state == InstrumentState.READY

    @pytest.mark.asyncio
    async def test_align_on_idle_returns_error(self):
        """ALIGN on IDLE returns -IDLE error."""
        await handle_instrument_add(["vsg", DRYRUN_PATH])
        response = await handle_align(["vsg"])
        assert b'-IDLE' in response

    @pytest.mark.asyncio
    async def test_align_multiple(self):
        await add_and_lock("vsg", ["FREQ"])
        await add_and_lock("sa", ["SPAN"])

        response = await handle_align(["vsg", "sa"])
        assert response == b'+OK\r\n'

        registry = get_registry()
        assert registry.get("vsg").state == InstrumentState.READY
        assert registry.get("sa").state == InstrumentState.READY

    @pytest.mark.asyncio
    async def test_align_wrong_args(self):
        response = await handle_align([])
        assert response.startswith(b'-ERR')


class TestDriverListCommand:
    """Tests for IDRIVERS command."""

    @pytest.mark.asyncio
    async def test_driver_list(self):
        await handle_instrument_add(["pm1", DRYRUN_PATH])
        await handle_instrument_add(["pm2", DRYRUN_PATH])

        response = await handle_driver_list([])
        parser = RESPParser()
        messages = parser.feed(response)
        assert len(messages[0]) == 1


class TestInstrumentPingCommand:
    """Tests for IPING command — connectivity check without lock."""

    @pytest.mark.asyncio
    async def test_ping_idle(self):
        """IPING works on IDLE instrument (no lock needed)."""
        await handle_instrument_add(["vsg", DRYRUN_PATH])
        response = await handle_instrument_ping(["vsg"])
        parser = RESPParser()
        messages = parser.feed(response)
        # DryRun driver returns IDN string via passthrough
        assert messages[0] is not None
        assert isinstance(messages[0], str)

    @pytest.mark.asyncio
    async def test_ping_locked(self):
        """IPING works on LOCKED instrument."""
        await add_and_lock("vsg", ["FREQ"])
        response = await handle_instrument_ping(["vsg"])
        assert not response.startswith(b'-')

    @pytest.mark.asyncio
    async def test_ping_ready(self):
        """IPING works on READY instrument."""
        await add_and_lock("vsg", ["FREQ"])
        await handle_instrument_init(["vsg"])
        response = await handle_instrument_ping(["vsg"])
        assert not response.startswith(b'-')

    @pytest.mark.asyncio
    async def test_ping_fault(self):
        """IPING on FAULT instrument returns error."""
        await add_and_lock("vsg", ["FREQ"])
        inst = get_registry().get("vsg")
        inst.state = InstrumentState.FAULT
        response = await handle_instrument_ping(["vsg"])
        assert b'-FAULT' in response

    @pytest.mark.asyncio
    async def test_ping_nonexistent(self):
        """IPING on unknown instrument returns error."""
        response = await handle_instrument_ping(["nonexistent"])
        assert response.startswith(b'-')

    @pytest.mark.asyncio
    async def test_ping_wrong_args(self):
        """IPING with no args returns error."""
        response = await handle_instrument_ping([])
        assert response.startswith(b'-ERR')


class TestInstrumentWaitCommand:
    """Tests for IWAIT command — wait for pending operations (*OPC?)."""

    @pytest.mark.asyncio
    async def test_wait_ready(self):
        """IWAIT works on READY instrument."""
        await add_and_lock("vsg", ["FREQ"])
        await handle_instrument_init(["vsg"])
        response = await handle_instrument_wait(
            ["vsg"], {"session_id": SESSION_1})
        assert response == b'+OK\r\n'

    @pytest.mark.asyncio
    async def test_wait_on_idle_returns_error(self):
        """IWAIT on IDLE instrument returns error."""
        await handle_instrument_add(["vsg", DRYRUN_PATH])
        response = await handle_instrument_wait(
            ["vsg"], {"session_id": SESSION_1})
        assert b'-IDLE' in response

    @pytest.mark.asyncio
    async def test_wait_on_locked_returns_error(self):
        """IWAIT on LOCKED (not initialized) returns NOTINIT error."""
        await add_and_lock("vsg", ["FREQ"])
        response = await handle_instrument_wait(
            ["vsg"], {"session_id": SESSION_1})
        assert b'-NOTINIT' in response

    @pytest.mark.asyncio
    async def test_wait_wrong_owner_returns_error(self):
        """IWAIT by non-owner returns LOCKED error."""
        await add_and_lock("vsg", ["FREQ"])
        await handle_instrument_init(["vsg"])
        response = await handle_instrument_wait(
            ["vsg"], {"session_id": 999})
        assert b'-LOCKED' in response

    @pytest.mark.asyncio
    async def test_wait_fault_returns_error(self):
        """IWAIT on FAULT instrument returns error."""
        await add_and_lock("vsg", ["FREQ"])
        inst = get_registry().get("vsg")
        inst.state = InstrumentState.FAULT
        response = await handle_instrument_wait(
            ["vsg"], {"session_id": SESSION_1})
        assert b'-FAULT' in response

    @pytest.mark.asyncio
    async def test_wait_nonexistent(self):
        """IWAIT on unknown instrument returns error."""
        response = await handle_instrument_wait(
            ["nonexistent"], {"session_id": SESSION_1})
        assert response.startswith(b'-')

    @pytest.mark.asyncio
    async def test_wait_wrong_args(self):
        """IWAIT with no args returns error."""
        response = await handle_instrument_wait([], {})
        assert response.startswith(b'-ERR')
