# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Tests for Alias Commands (spec §6.6)."""

import pytest
from testcore.commands import (
    handle_alias_set, handle_alias_get, handle_alias_del, handle_alias_list,
    handle_aread, handle_awrite, handle_lock, handle_instrument_add,
    _aliases
)
from testcore.instruments import get_registry, InstrumentState
from testcore.protocol import RESPParser

DRYRUN_PATH = "dryrun"
SESSION_1 = 1
SESSION_2 = 2


def ctx(session_id):
    return {"session_id": session_id}


@pytest.fixture(autouse=True)
def reset_all():
    """Reset registry and aliases before each test."""
    _aliases.clear()
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


class TestAliasSet:
    """Tests for ALIAS.SET command."""

    @pytest.mark.asyncio
    async def test_set_sub_alias(self):
        response = await handle_alias_set(
            ["rf_power", "SUB", "pm1:POWER"])
        assert response == b"+OK\r\n"
        assert _aliases["rf_power"] == ("SUB", "pm1:POWER")

    @pytest.mark.asyncio
    async def test_set_raw_alias(self):
        response = await handle_alias_set(
            ["sa_pk2pk", "RAW", "sa::CALC:MARK1:Y?"])
        assert response == b"+OK\r\n"
        assert _aliases["sa_pk2pk"] == ("RAW", "sa::CALC:MARK1:Y?")

    @pytest.mark.asyncio
    async def test_set_invalid_type(self):
        response = await handle_alias_set(
            ["test", "INVALID", "foo:bar"])
        assert b"-ERR" in response
        assert b"must be SUB or RAW" in response

    @pytest.mark.asyncio
    async def test_set_sub_invalid_target(self):
        response = await handle_alias_set(
            ["test", "SUB", "no_colon_here"])
        assert b"-ERR" in response
        assert b"instrument:resource" in response

    @pytest.mark.asyncio
    async def test_set_raw_invalid_target(self):
        response = await handle_alias_set(
            ["test", "RAW", "no_double_colon"])
        assert b"-ERR" in response
        assert b"instrument::scpi_command" in response

    @pytest.mark.asyncio
    async def test_set_overwrites(self):
        await handle_alias_set(["rf", "SUB", "pm1:POWER"])
        await handle_alias_set(["rf", "SUB", "pm2:POWER"])
        assert _aliases["rf"] == ("SUB", "pm2:POWER")

    @pytest.mark.asyncio
    async def test_set_wrong_args(self):
        response = await handle_alias_set(["only_name"])
        assert b"-ERR" in response


class TestAliasGet:
    """Tests for ALIAS.GET command."""

    @pytest.mark.asyncio
    async def test_get_existing(self):
        _aliases["rf_power"] = ("SUB", "pm1:POWER")
        response = await handle_alias_get(["rf_power"])
        parser = RESPParser()
        messages = parser.feed(response)
        assert messages[0] == ["SUB", "pm1:POWER"]

    @pytest.mark.asyncio
    async def test_get_nonexistent(self):
        response = await handle_alias_get(["nope"])
        assert b"-NOALIAS" in response

    @pytest.mark.asyncio
    async def test_get_no_args(self):
        response = await handle_alias_get([])
        assert b"-ERR" in response


class TestAliasDel:
    """Tests for ALIAS.DEL command."""

    @pytest.mark.asyncio
    async def test_del_existing(self):
        _aliases["rf_power"] = ("SUB", "pm1:POWER")
        response = await handle_alias_del(["rf_power"])
        assert response == b"+OK\r\n"
        assert "rf_power" not in _aliases

    @pytest.mark.asyncio
    async def test_del_nonexistent(self):
        response = await handle_alias_del(["nope"])
        assert b"-NOALIAS" in response

    @pytest.mark.asyncio
    async def test_del_no_args(self):
        response = await handle_alias_del([])
        assert b"-ERR" in response


class TestAliasList:
    """Tests for ALIAS.LIST command."""

    @pytest.mark.asyncio
    async def test_list_empty(self):
        response = await handle_alias_list([])
        parser = RESPParser()
        messages = parser.feed(response)
        assert messages[0] == []

    @pytest.mark.asyncio
    async def test_list_sorted(self):
        _aliases["zeta"] = ("SUB", "z:Z")
        _aliases["alpha"] = ("SUB", "a:A")
        _aliases["mid"] = ("RAW", "m::CMD?")
        response = await handle_alias_list([])
        parser = RESPParser()
        messages = parser.feed(response)
        assert messages[0] == ["alpha", "mid", "zeta"]


class TestAread:
    """Tests for AREAD command."""

    @pytest.mark.asyncio
    async def test_aread_sub_alias(self):
        await setup_ready_instrument("vsg", ["FREQ", "POWER"])
        _aliases["vsg_freq"] = ("SUB", "vsg:FREQ")

        response = await handle_aread(["vsg_freq"], ctx(SESSION_1))
        parser = RESPParser()
        messages = parser.feed(response)
        assert messages[0] is not None  # DryRun returns simulated value

    @pytest.mark.asyncio
    async def test_aread_raw_alias(self):
        await setup_ready_instrument("vsg", ["FREQ"])
        _aliases["raw_test"] = ("RAW", "vsg::*IDN?")

        response = await handle_aread(["raw_test"], ctx(SESSION_1))
        parser = RESPParser()
        messages = parser.feed(response)
        assert messages[0] is not None

    @pytest.mark.asyncio
    async def test_aread_nonexistent_alias(self):
        response = await handle_aread(["nope"], ctx(SESSION_1))
        assert b"-NOALIAS" in response

    @pytest.mark.asyncio
    async def test_aread_wrong_owner(self):
        await setup_ready_instrument("vsg", ["FREQ"])
        _aliases["vsg_freq"] = ("SUB", "vsg:FREQ")

        response = await handle_aread(["vsg_freq"], ctx(SESSION_2))
        assert b"-LOCKED" in response

    @pytest.mark.asyncio
    async def test_aread_not_locked(self):
        """AREAD on unlocked instrument returns IDLE error."""
        registry = get_registry()
        registry.add("vsg", DRYRUN_PATH,
                      config={"resources": ["FREQ"]})
        _aliases["vsg_freq"] = ("SUB", "vsg:FREQ")

        response = await handle_aread(["vsg_freq"], ctx(SESSION_1))
        assert b"-IDLE" in response

    @pytest.mark.asyncio
    async def test_aread_no_session(self):
        response = await handle_aread(["something"])
        assert b"no session" in response

    @pytest.mark.asyncio
    async def test_aread_no_args(self):
        response = await handle_aread([])
        assert b"-ERR" in response


class TestAwrite:
    """Tests for AWRITE command."""

    @pytest.mark.asyncio
    async def test_awrite_sub_alias(self):
        await setup_ready_instrument("vsg", ["FREQ", "POWER"])
        _aliases["vsg_freq"] = ("SUB", "vsg:FREQ")

        response = await handle_awrite(
            ["vsg_freq", "900e6"], ctx(SESSION_1))
        assert response == b"+OK\r\n"

    @pytest.mark.asyncio
    async def test_awrite_raw_alias_rejected(self):
        _aliases["raw_test"] = ("RAW", "sa::CALC:MARK1:Y?")
        response = await handle_awrite(
            ["raw_test", "val"], ctx(SESSION_1))
        assert b"-ERR" in response
        assert b"SUB" in response

    @pytest.mark.asyncio
    async def test_awrite_nonexistent_alias(self):
        response = await handle_awrite(["nope", "val"], ctx(SESSION_1))
        assert b"-NOALIAS" in response

    @pytest.mark.asyncio
    async def test_awrite_wrong_owner(self):
        await setup_ready_instrument("vsg", ["FREQ"])
        _aliases["vsg_freq"] = ("SUB", "vsg:FREQ")

        response = await handle_awrite(
            ["vsg_freq", "900e6"], ctx(SESSION_2))
        assert b"-LOCKED" in response

    @pytest.mark.asyncio
    async def test_awrite_no_session(self):
        response = await handle_awrite(["something", "val"])
        assert b"no session" in response

    @pytest.mark.asyncio
    async def test_awrite_no_args(self):
        response = await handle_awrite([])
        assert b"-ERR" in response


class TestAliasDispatch:
    """Tests for alias commands via dispatcher."""

    @pytest.mark.asyncio
    async def test_alias_set_via_dispatch(self):
        from testcore.commands import dispatcher
        response = await dispatcher.dispatch(
            ["ALIAS", "SET", "rf", "SUB", "pm1:POWER"])
        assert response == b"+OK\r\n"

    @pytest.mark.asyncio
    async def test_alias_list_via_dispatch(self):
        from testcore.commands import dispatcher
        _aliases["test"] = ("SUB", "a:B")
        response = await dispatcher.dispatch(["ALIAS", "LIST"])
        parser = RESPParser()
        messages = parser.feed(response)
        assert "test" in messages[0]

    @pytest.mark.asyncio
    async def test_aread_via_dispatch(self):
        from testcore.commands import dispatcher
        _aliases["nope"] = ("SUB", "vsg:FREQ")
        # Will fail with IDLE/not found, but dispatch routing works
        response = await dispatcher.dispatch(
            ["AREAD", "nope"], ctx(SESSION_1))
        # Should NOT be "unknown command"
        assert b"unknown command" not in response
