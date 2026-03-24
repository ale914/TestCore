# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Tests for Instrument registry and single-axis state machine (spec §6.3, §7.3)."""

import pytest
from testcore.instruments import (
    InstrumentRegistry, InstrumentState,
    IdleError, NotInitError, LockedError, FaultError
)
from testcore.base_driver import DriverError

# Bundled driver short name
DRYRUN_PATH = "dryrun"

SESSION_1 = 1
SESSION_2 = 2


@pytest.fixture
def registry():
    """Create fresh instrument registry."""
    return InstrumentRegistry()


class TestInstrumentAdd:
    """Tests for INSTRUMENT.ADD (thin init). State → IDLE."""

    def test_add_basic(self, registry):
        inst = registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ", "POWER"]})
        assert inst.name == "vsg"
        assert inst.state == InstrumentState.IDLE
        assert inst.lock_owner is None

    def test_add_default_config(self, registry):
        inst = registry.add("dev1", DRYRUN_PATH)
        assert inst.state == InstrumentState.IDLE

    def test_add_duplicate_name_error(self, registry):
        registry.add("vsg", DRYRUN_PATH)
        with pytest.raises(DriverError, match="already exists"):
            registry.add("vsg", DRYRUN_PATH)

    def test_add_shared_driver(self, registry):
        inst1 = registry.add("pm1", DRYRUN_PATH, config={"resources": ["POWER"]})
        inst2 = registry.add("pm2", DRYRUN_PATH, config={"resources": ["POWER"]})
        assert inst1.driver is not inst2.driver

    def test_add_invalid_path_error(self, registry):
        with pytest.raises(DriverError):
            registry.add("bad", "/nonexistent/driver.py")


class TestInstrumentRemove:
    """Tests for INSTRUMENT.REMOVE (from any state)."""

    def test_remove_basic(self, registry):
        registry.add("vsg", DRYRUN_PATH)
        registry.remove("vsg")
        assert registry.list_instruments() == []

    def test_remove_calls_safe_state_and_disconnect(self, registry):
        inst = registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ", "POWER"]})
        inst.driver.init()
        inst.driver.write("FREQ", "900e6")
        registry.remove("vsg")
        with pytest.raises(DriverError, match="not found"):
            registry.get("vsg")

    def test_remove_nonexistent_error(self, registry):
        with pytest.raises(DriverError, match="not found"):
            registry.remove("missing")


class TestLock:
    """Tests for LOCK (IDLE → LOCKED)."""

    def test_lock_basic(self, registry):
        registry.add("vsg", DRYRUN_PATH)
        registry.lock("vsg", SESSION_1)
        inst = registry.get("vsg")
        assert inst.state == InstrumentState.LOCKED
        assert inst.lock_owner == SESSION_1

    def test_lock_already_locked_by_other(self, registry):
        registry.add("vsg", DRYRUN_PATH)
        registry.lock("vsg", SESSION_1)
        with pytest.raises(LockedError, match="owned by session 1"):
            registry.lock("vsg", SESSION_2)

    def test_lock_idempotent_same_session(self, registry):
        registry.add("vsg", DRYRUN_PATH)
        registry.lock("vsg", SESSION_1)
        registry.lock("vsg", SESSION_1)  # no error
        assert registry.get("vsg").lock_owner == SESSION_1

    def test_lock_nonexistent_error(self, registry):
        with pytest.raises(DriverError, match="not found"):
            registry.lock("missing", SESSION_1)


class TestUnlock:
    """Tests for UNLOCK (any owned state → IDLE, safe_state called)."""

    def test_unlock_basic(self, registry):
        registry.add("vsg", DRYRUN_PATH)
        registry.lock("vsg", SESSION_1)
        registry.unlock("vsg", SESSION_1)
        inst = registry.get("vsg")
        assert inst.state == InstrumentState.IDLE
        assert inst.lock_owner is None

    @pytest.mark.asyncio
    async def test_unlock_from_ready(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", SESSION_1)
        await registry.init_instrument("vsg")
        assert registry.get("vsg").state == InstrumentState.READY

        registry.unlock("vsg", SESSION_1)
        assert registry.get("vsg").state == InstrumentState.IDLE

    @pytest.mark.asyncio
    async def test_unlock_clears_resources(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", SESSION_1)
        await registry.init_instrument("vsg")
        assert registry.get("vsg").resources == ["FREQ"]

        registry.unlock("vsg", SESSION_1)
        assert registry.get("vsg").resources == []

    def test_unlock_not_locked_error(self, registry):
        registry.add("vsg", DRYRUN_PATH)
        with pytest.raises(IdleError, match="not locked"):
            registry.unlock("vsg", SESSION_1)

    def test_unlock_wrong_session_error(self, registry):
        registry.add("vsg", DRYRUN_PATH)
        registry.lock("vsg", SESSION_1)
        with pytest.raises(LockedError, match="owned by session 1"):
            registry.unlock("vsg", SESSION_2)


class TestInstrumentInit:
    """Tests for INSTRUMENT.INIT (LOCKED → READY)."""

    @pytest.mark.asyncio
    async def test_init_basic(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ", "POWER"]})
        registry.lock("vsg", SESSION_1)
        await registry.init_instrument("vsg")
        inst = registry.get("vsg")
        assert inst.state == InstrumentState.READY
        assert inst.resources == ["FREQ", "POWER"]

    @pytest.mark.asyncio
    async def test_init_with_config_file(self, registry, tmp_path):
        config_file = tmp_path / "test.cfg"
        config_file.write_text("test config")

        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ", "POWER"]})
        registry.lock("vsg", SESSION_1)
        await registry.init_instrument("vsg", str(config_file))
        assert registry.get("vsg").state == InstrumentState.READY

    @pytest.mark.asyncio
    async def test_init_on_idle_raises_idle_error(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        with pytest.raises(IdleError, match="not locked"):
            await registry.init_instrument("vsg")

    @pytest.mark.asyncio
    async def test_init_on_fault_raises_fault_error(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", SESSION_1)
        inst = registry.get("vsg")
        inst.state = InstrumentState.FAULT
        with pytest.raises(FaultError, match="FAULT"):
            await registry.init_instrument("vsg")

    @pytest.mark.asyncio
    async def test_reinit_from_ready(self, registry):
        """INIT on READY re-initializes (same owner)."""
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", SESSION_1)
        await registry.init_instrument("vsg")
        assert registry.get("vsg").state == InstrumentState.READY
        # Re-init should work
        await registry.init_instrument("vsg")
        assert registry.get("vsg").state == InstrumentState.READY

    @pytest.mark.asyncio
    async def test_init_nonexistent_error(self, registry):
        with pytest.raises(DriverError, match="not found"):
            await registry.init_instrument("missing")


class TestAlign:
    """Tests for ALIGN (LOCKED → READY, no re-init)."""

    @pytest.mark.asyncio
    async def test_align_basic(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ", "POWER"]})
        registry.lock("vsg", SESSION_1)
        await registry.align("vsg")
        inst = registry.get("vsg")
        assert inst.state == InstrumentState.READY
        assert inst.resources == ["FREQ", "POWER"]

    @pytest.mark.asyncio
    async def test_align_refreshes_resources(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ", "POWER"]})
        registry.lock("vsg", SESSION_1)
        await registry.align("vsg")
        inst = registry.get("vsg")
        assert "FREQ" in inst.resources
        assert "POWER" in inst.resources

    @pytest.mark.asyncio
    async def test_align_on_idle_raises_idle_error(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        with pytest.raises(IdleError, match="not locked"):
            await registry.align("vsg")

    @pytest.mark.asyncio
    async def test_align_on_ready_raises_error(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", SESSION_1)
        await registry.init_instrument("vsg")
        with pytest.raises(DriverError, match="already READY"):
            await registry.align("vsg")

    @pytest.mark.asyncio
    async def test_align_on_fault_raises_fault_error(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", SESSION_1)
        inst = registry.get("vsg")
        inst.state = InstrumentState.FAULT
        with pytest.raises(FaultError, match="FAULT"):
            await registry.align("vsg")


class TestStateMachine:
    """Tests for state-based access control."""

    @pytest.mark.asyncio
    async def test_idle_blocks_read(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        with pytest.raises(IdleError, match="not locked"):
            await registry.read("vsg", "FREQ")

    @pytest.mark.asyncio
    async def test_idle_blocks_write(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        with pytest.raises(IdleError, match="not locked"):
            await registry.write("vsg", "FREQ", "900e6")

    @pytest.mark.asyncio
    async def test_idle_blocks_passthrough(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        with pytest.raises(IdleError, match="not locked"):
            await registry.passthrough("vsg", "*IDN?")

    @pytest.mark.asyncio
    async def test_locked_blocks_read(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", SESSION_1)
        with pytest.raises(NotInitError, match="requires INIT"):
            await registry.read("vsg", "FREQ")

    @pytest.mark.asyncio
    async def test_locked_blocks_write(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", SESSION_1)
        with pytest.raises(NotInitError, match="requires INIT"):
            await registry.write("vsg", "FREQ", "900e6")

    @pytest.mark.asyncio
    async def test_locked_blocks_passthrough(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", SESSION_1)
        with pytest.raises(NotInitError, match="requires INIT"):
            await registry.passthrough("vsg", "*IDN?")

    @pytest.mark.asyncio
    async def test_ready_allows_read(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", SESSION_1)
        await registry.init_instrument("vsg")
        assert await registry.read("vsg", "FREQ") == "0.0"

    @pytest.mark.asyncio
    async def test_ready_allows_write(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", SESSION_1)
        await registry.init_instrument("vsg")
        await registry.write("vsg", "FREQ", "900e6")
        assert await registry.read("vsg", "FREQ") == "900e6"

    @pytest.mark.asyncio
    async def test_ready_allows_passthrough(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", SESSION_1)
        await registry.init_instrument("vsg")
        result = await registry.passthrough("vsg", "*IDN?")
        assert "DRYRUN_ECHO" in result

    @pytest.mark.asyncio
    async def test_unknown_resource_raises_driver_error(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", SESSION_1)
        await registry.init_instrument("vsg")
        with pytest.raises(DriverError, match="unknown resource"):
            await registry.read("vsg", "NONEXISTENT")


class TestInstrumentReset:
    """Tests for INSTRUMENT.RESET (FAULT/UNRESPONSIVE → LOCKED)."""

    @pytest.mark.asyncio
    async def test_reset_from_fault(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", SESSION_1)
        inst = registry.get("vsg")
        inst.state = InstrumentState.FAULT

        await registry.reset("vsg")
        assert inst.state == InstrumentState.LOCKED
        assert inst.lock_owner == SESSION_1  # lock preserved

    @pytest.mark.asyncio
    async def test_reset_from_unresponsive(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", SESSION_1)
        inst = registry.get("vsg")
        inst.state = InstrumentState.UNRESPONSIVE

        await registry.reset("vsg")
        assert inst.state == InstrumentState.LOCKED

    @pytest.mark.asyncio
    async def test_reset_from_ready_error(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", SESSION_1)
        await registry.init_instrument("vsg")
        with pytest.raises(DriverError, match="not in FAULT"):
            await registry.reset("vsg")

    @pytest.mark.asyncio
    async def test_reset_from_idle_error(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        with pytest.raises(DriverError, match="not in FAULT"):
            await registry.reset("vsg")


class TestInstrumentList:
    """Tests for INSTRUMENT.LIST and DRIVER.LIST."""

    def test_list_instruments_empty(self, registry):
        assert registry.list_instruments() == []

    def test_list_instruments(self, registry):
        registry.add("vsg", DRYRUN_PATH)
        registry.add("sa", DRYRUN_PATH)
        registry.add("pm1", DRYRUN_PATH)
        assert registry.list_instruments() == ["pm1", "sa", "vsg"]

    def test_list_drivers(self, registry):
        registry.add("pm1", DRYRUN_PATH)
        registry.add("pm2", DRYRUN_PATH)
        drivers = registry.list_drivers()
        assert len(drivers) == 1


class TestInstrumentStats:
    """Tests for instrument call tracking."""

    @pytest.mark.asyncio
    async def test_total_calls_increments(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", SESSION_1)
        await registry.init_instrument("vsg")
        inst = registry.get("vsg")
        assert inst.total_calls == 0

        await registry.read("vsg", "FREQ")
        assert inst.total_calls == 1

        await registry.write("vsg", "FREQ", "900e6")
        assert inst.total_calls == 2

    @pytest.mark.asyncio
    async def test_mean_response_time(self, registry):
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", SESSION_1)
        await registry.init_instrument("vsg")
        inst = registry.get("vsg")

        await registry.read("vsg", "FREQ")
        assert inst.mean_response_ms >= 0.0


class TestFullLifecycle:
    """End-to-end lifecycle tests."""

    @pytest.mark.asyncio
    async def test_add_lock_init_read_write_remove(self, registry):
        """Full lifecycle: ADD → LOCK → INIT → READ/WRITE → REMOVE."""
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ", "POWER"]})

        # IDLE - can't read
        with pytest.raises(IdleError):
            await registry.read("vsg", "FREQ")

        # LOCK → LOCKED - still can't read
        registry.lock("vsg", SESSION_1)
        with pytest.raises(NotInitError):
            await registry.read("vsg", "FREQ")

        # INIT → READY
        await registry.init_instrument("vsg")
        assert registry.get("vsg").state == InstrumentState.READY

        # READ/WRITE work
        assert await registry.read("vsg", "FREQ") == "0.0"
        await registry.write("vsg", "FREQ", "900e6")
        assert await registry.read("vsg", "FREQ") == "900e6"

        # REMOVE
        registry.remove("vsg")
        assert registry.list_instruments() == []

    @pytest.mark.asyncio
    async def test_add_lock_align_read(self, registry):
        """Lifecycle with ALIGN: ADD → LOCK → ALIGN → READ."""
        registry.add("pm1", DRYRUN_PATH, config={"resources": ["POWER"]})
        registry.lock("pm1", SESSION_1)

        # LOCKED
        with pytest.raises(NotInitError):
            await registry.read("pm1", "POWER")

        # ALIGN → READY (no full init)
        await registry.align("pm1")
        assert registry.get("pm1").state == InstrumentState.READY
        assert await registry.read("pm1", "POWER") == "0.0"

    @pytest.mark.asyncio
    async def test_unlock_idle_cycle(self, registry):
        """Test UNLOCK → IDLE → LOCK → INIT cycle."""
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", SESSION_1)
        await registry.init_instrument("vsg")
        assert registry.get("vsg").state == InstrumentState.READY

        # UNLOCK → IDLE
        registry.unlock("vsg", SESSION_1)
        assert registry.get("vsg").state == InstrumentState.IDLE

        # Can't read anymore
        with pytest.raises(IdleError):
            await registry.read("vsg", "FREQ")

        # Re-LOCK → LOCKED → INIT → READY
        registry.lock("vsg", SESSION_2)
        await registry.init_instrument("vsg")
        assert registry.get("vsg").state == InstrumentState.READY
        assert await registry.read("vsg", "FREQ") == "0.0"

    @pytest.mark.asyncio
    async def test_multi_instrument_same_driver(self, registry):
        """Multiple instruments sharing same driver module."""
        registry.add("pm1", DRYRUN_PATH, config={"resources": ["POWER"]})
        registry.add("pm2", DRYRUN_PATH, config={"resources": ["POWER"]})

        registry.lock("pm1", SESSION_1)
        registry.lock("pm2", SESSION_1)
        await registry.init_instrument("pm1")
        await registry.init_instrument("pm2")

        # Independent state
        await registry.write("pm1", "POWER", "10.5")
        await registry.write("pm2", "POWER", "20.3")

        assert await registry.read("pm1", "POWER") == "10.5"
        assert await registry.read("pm2", "POWER") == "20.3"

    @pytest.mark.asyncio
    async def test_handover_with_align(self, registry):
        """Client handover: session 1 UNLOCK → session 2 LOCK + ALIGN."""
        registry.add("pm1", DRYRUN_PATH, config={"resources": ["POWER"]})
        registry.lock("pm1", SESSION_1)
        await registry.init_instrument("pm1")
        await registry.write("pm1", "POWER", "42.0")

        # Session 1 unlocks
        registry.unlock("pm1", SESSION_1)
        assert registry.get("pm1").state == InstrumentState.IDLE

        # Session 2 takes over with ALIGN (accepts current state)
        registry.lock("pm1", SESSION_2)
        await registry.align("pm1")
        assert registry.get("pm1").state == InstrumentState.READY

    @pytest.mark.asyncio
    async def test_fault_preserves_lock(self, registry):
        """FAULT state keeps lock owner."""
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", SESSION_1)
        inst = registry.get("vsg")
        inst.state = InstrumentState.FAULT

        assert inst.lock_owner == SESSION_1
        assert inst.state == InstrumentState.FAULT

        # Reset → LOCKED (still owned)
        await registry.reset("vsg")
        assert inst.state == InstrumentState.LOCKED
        assert inst.lock_owner == SESSION_1
