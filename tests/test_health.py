# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Tests for Health monitoring system."""

import asyncio
import time
import pytest
from testcore.commands import (
    handle_instrument_add, handle_instrument_remove,
    handle_instrument_info,
)
from testcore.instruments import get_registry, InstrumentState
from testcore.health import get_health_monitor, HealthMonitor
from testcore.protocol import RESPParser

DRYRUN_PATH = "dryrun"


@pytest.fixture(autouse=True)
def reset_all():
    """Reset registry and health monitor before each test."""
    registry = get_registry()
    for name in list(registry._instruments.keys()):
        try:
            registry.remove(name)
        except Exception:
            pass
    registry._instruments.clear()
    registry._driver_modules.clear()

    hm = get_health_monitor()
    hm.stop_all()


class TestHealthIADD:
    """Tests for health=N parameter in IADD."""

    @pytest.mark.asyncio
    async def test_iadd_with_health(self):
        """IADD with health=N stores interval and starts monitoring."""
        response = await handle_instrument_add(
            ["vsg", DRYRUN_PATH, "health=5"])
        assert response == b'+OK\r\n'

        inst = get_registry().get("vsg")
        assert inst.health_interval == 5.0
        assert get_health_monitor().is_monitored("vsg")

    @pytest.mark.asyncio
    async def test_iadd_without_health(self):
        """IADD without health=N does not start monitoring."""
        response = await handle_instrument_add(["vsg", DRYRUN_PATH])
        assert response == b'+OK\r\n'

        inst = get_registry().get("vsg")
        assert inst.health_interval is None
        assert not get_health_monitor().is_monitored("vsg")

    @pytest.mark.asyncio
    async def test_iadd_health_too_low(self):
        """IADD with health=0 returns error."""
        response = await handle_instrument_add(
            ["vsg", DRYRUN_PATH, "health=0"])
        assert response.startswith(b'-')
        assert b'1-100' in response

    @pytest.mark.asyncio
    async def test_iadd_health_too_high(self):
        """IADD with health=101 returns error."""
        response = await handle_instrument_add(
            ["vsg", DRYRUN_PATH, "health=101"])
        assert response.startswith(b'-')
        assert b'1-100' in response

    @pytest.mark.asyncio
    async def test_iadd_health_negative(self):
        """IADD with health=-1 returns error."""
        response = await handle_instrument_add(
            ["vsg", DRYRUN_PATH, "health=-1"])
        assert response.startswith(b'-')

    @pytest.mark.asyncio
    async def test_iadd_health_valid_boundaries(self):
        """IADD with health=1 and health=100 are valid."""
        response = await handle_instrument_add(
            ["vsg1", DRYRUN_PATH, "health=1"])
        assert response == b'+OK\r\n'
        assert get_registry().get("vsg1").health_interval == 1.0

        response = await handle_instrument_add(
            ["vsg2", DRYRUN_PATH, "health=100"])
        assert response == b'+OK\r\n'
        assert get_registry().get("vsg2").health_interval == 100.0


class TestHealthIREMOVE:
    """Tests for health cleanup on IREMOVE."""

    @pytest.mark.asyncio
    async def test_iremove_stops_health(self):
        """IREMOVE stops health monitoring."""
        await handle_instrument_add(["vsg", DRYRUN_PATH, "health=5"])
        assert get_health_monitor().is_monitored("vsg")

        await handle_instrument_remove(["vsg"])
        assert not get_health_monitor().is_monitored("vsg")

    @pytest.mark.asyncio
    async def test_iremove_without_health_ok(self):
        """IREMOVE on non-monitored instrument doesn't error."""
        await handle_instrument_add(["vsg", DRYRUN_PATH])
        response = await handle_instrument_remove(["vsg"])
        assert response == b'+OK\r\n'


class TestHealthIINFO:
    """Tests for health fields in IINFO output."""

    @pytest.mark.asyncio
    async def test_iinfo_shows_health(self):
        """IINFO includes health fields when health is active."""
        await handle_instrument_add(["vsg", DRYRUN_PATH, "health=10"])
        response = await handle_instrument_info(["vsg"])
        parser = RESPParser()
        result = parser.feed(response)[0]
        assert "health_interval:10s" in result
        assert "health_failures:0" in result
        assert "health_last_ok:" in result

    @pytest.mark.asyncio
    async def test_iinfo_no_health_fields(self):
        """IINFO without health does not show health fields."""
        await handle_instrument_add(["vsg", DRYRUN_PATH])
        response = await handle_instrument_info(["vsg"])
        parser = RESPParser()
        result = parser.feed(response)[0]
        assert "health_interval" not in result


class TestHealthMonitor:
    """Tests for HealthMonitor class."""

    def test_stop_all(self):
        """stop_all clears all tasks."""
        hm = get_health_monitor()
        assert len(hm._tasks) == 0
        hm.stop_all()
        assert len(hm._tasks) == 0

    @pytest.mark.asyncio
    async def test_health_skips_fault_state(self):
        """Health loop skips ping when instrument is FAULT."""
        await handle_instrument_add(["vsg", DRYRUN_PATH, "health=1"])
        inst = get_registry().get("vsg")
        inst.state = InstrumentState.FAULT
        inst.last_call_ok = 0  # force idle

        # Let health loop run one cycle
        await asyncio.sleep(1.5)

        # Should still be FAULT (health skipped, didn't try to ping)
        assert inst.state == InstrumentState.FAULT
        assert inst.health_failures == 0

    @pytest.mark.asyncio
    async def test_health_skips_busy(self):
        """Health loop skips ping when instrument is busy."""
        await handle_instrument_add(["vsg", DRYRUN_PATH, "health=1"])
        inst = get_registry().get("vsg")
        inst._busy = True
        inst.last_call_ok = 0  # force idle

        await asyncio.sleep(1.5)

        # Should not have pinged (was busy)
        inst._busy = False

    @pytest.mark.asyncio
    async def test_health_skips_recent_activity(self):
        """Health loop skips ping when instrument was active recently."""
        await handle_instrument_add(["vsg", DRYRUN_PATH, "health=2"])
        inst = get_registry().get("vsg")
        inst.last_call_ok = time.monotonic()  # just active

        await asyncio.sleep(1.5)  # less than health interval

        # No ping should have occurred (instrument active recently)
        assert inst.health_failures == 0

    @pytest.mark.asyncio
    async def test_health_ping_success_resets_failures(self):
        """Successful health ping resets failure counter."""
        await handle_instrument_add(["vsg", DRYRUN_PATH, "health=1"])
        inst = get_registry().get("vsg")
        inst.health_failures = 2
        inst.last_call_ok = 0  # force idle

        await asyncio.sleep(1.5)

        # DryRun always succeeds → failures reset to 0
        assert inst.health_failures == 0


class TestInstrumentBusyFlag:
    """Tests for _busy flag in _call_driver."""

    @pytest.mark.asyncio
    async def test_busy_flag_set_during_call(self):
        """_busy is True during driver call and False after."""
        registry = get_registry()
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", 1)
        await registry.init_instrument("vsg")

        inst = registry.get("vsg")
        assert inst._busy is False

        await registry.read("vsg", "FREQ")
        assert inst._busy is False  # reset after call

    @pytest.mark.asyncio
    async def test_last_call_ok_updated(self):
        """last_call_ok is updated after successful driver call."""
        registry = get_registry()
        registry.add("vsg", DRYRUN_PATH, config={"resources": ["FREQ"]})
        registry.lock("vsg", 1)

        inst = registry.get("vsg")
        before = inst.last_call_ok

        await asyncio.sleep(0.01)
        await registry.init_instrument("vsg")

        assert inst.last_call_ok > before
