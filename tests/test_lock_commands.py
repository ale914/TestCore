# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Tests for Lock Commands (spec §6.5)."""

import pytest
from testcore.commands import handle_lock, handle_unlock, handle_locks
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


def add_instrument(name, config=None):
    """Helper: add instrument via registry."""
    registry = get_registry()
    cfg = {}
    if config:
        import json
        cfg = json.loads(config)
    registry.add(name, DRYRUN_PATH, config=cfg)


class TestLockCommand:
    """Tests for LOCK command."""

    @pytest.mark.asyncio
    async def test_lock_basic(self):
        add_instrument("vsg")
        response = await handle_lock(["vsg"], ctx(SESSION_1))
        assert response == b'+OK\r\n'

        inst = get_registry().get("vsg")
        assert inst.state == InstrumentState.LOCKED
        assert inst.lock_owner == SESSION_1

    @pytest.mark.asyncio
    async def test_lock_multiple(self):
        add_instrument("vsg")
        add_instrument("sa")
        response = await handle_lock(["vsg", "sa"], ctx(SESSION_1))
        assert response == b'+OK\r\n'

        registry = get_registry()
        assert registry.get("vsg").lock_owner == SESSION_1
        assert registry.get("sa").lock_owner == SESSION_1

    @pytest.mark.asyncio
    async def test_lock_already_locked_other_session(self):
        add_instrument("vsg")
        await handle_lock(["vsg"], ctx(SESSION_1))

        response = await handle_lock(["vsg"], ctx(SESSION_2))
        assert b'-LOCKED' in response
        assert b'session 1' in response

    @pytest.mark.asyncio
    async def test_lock_idempotent_same_session(self):
        add_instrument("vsg")
        await handle_lock(["vsg"], ctx(SESSION_1))
        response = await handle_lock(["vsg"], ctx(SESSION_1))
        assert response == b'+OK\r\n'

    @pytest.mark.asyncio
    async def test_lock_atomic_failure(self):
        """If one instrument is locked by other, none get locked."""
        add_instrument("vsg")
        add_instrument("sa")
        await handle_lock(["vsg"], ctx(SESSION_2))

        response = await handle_lock(["vsg", "sa"], ctx(SESSION_1))
        assert b'-LOCKED' in response

        # sa should NOT have been locked
        assert get_registry().get("sa").state == InstrumentState.IDLE

    @pytest.mark.asyncio
    async def test_lock_nonexistent(self):
        response = await handle_lock(["missing"], ctx(SESSION_1))
        assert b'-DRIVER' in response

    @pytest.mark.asyncio
    async def test_lock_wrong_args(self):
        response = await handle_lock([], ctx(SESSION_1))
        assert response.startswith(b'-ERR')

    @pytest.mark.asyncio
    async def test_lock_no_session(self):
        add_instrument("vsg")
        response = await handle_lock(["vsg"])
        assert b'no session' in response


class TestUnlockCommand:
    """Tests for UNLOCK command."""

    @pytest.mark.asyncio
    async def test_unlock_basic(self):
        add_instrument("vsg")
        await handle_lock(["vsg"], ctx(SESSION_1))
        response = await handle_unlock(["vsg"], ctx(SESSION_1))
        assert response == b'+OK\r\n'

        inst = get_registry().get("vsg")
        assert inst.state == InstrumentState.IDLE
        assert inst.lock_owner is None

    @pytest.mark.asyncio
    async def test_unlock_wrong_owner(self):
        add_instrument("vsg")
        await handle_lock(["vsg"], ctx(SESSION_1))
        response = await handle_unlock(["vsg"], ctx(SESSION_2))
        assert b'-LOCKED' in response

    @pytest.mark.asyncio
    async def test_unlock_not_locked(self):
        add_instrument("vsg")
        response = await handle_unlock(["vsg"], ctx(SESSION_1))
        assert b'-IDLE' in response

    @pytest.mark.asyncio
    async def test_unlock_wrong_args(self):
        response = await handle_unlock([], ctx(SESSION_1))
        assert response.startswith(b'-ERR')

    @pytest.mark.asyncio
    async def test_unlock_multiple(self):
        add_instrument("vsg")
        add_instrument("sa")
        await handle_lock(["vsg", "sa"], ctx(SESSION_1))
        response = await handle_unlock(["vsg", "sa"], ctx(SESSION_1))
        assert response == b'+OK\r\n'

        registry = get_registry()
        assert registry.get("vsg").state == InstrumentState.IDLE
        assert registry.get("sa").state == InstrumentState.IDLE


class TestUnlockAllCommand:
    """Tests for UNLOCK ALL command."""

    @pytest.mark.asyncio
    async def test_unlock_all(self):
        add_instrument("vsg")
        add_instrument("sa")
        await handle_lock(["vsg", "sa"], ctx(SESSION_1))

        response = await handle_unlock(["ALL"], ctx(SESSION_1))
        assert response == b'+OK\r\n'

        registry = get_registry()
        assert registry.get("vsg").state == InstrumentState.IDLE
        assert registry.get("sa").state == InstrumentState.IDLE

    @pytest.mark.asyncio
    async def test_unlock_all_only_own_locks(self):
        """UNLOCK ALL only releases locks held by calling session."""
        add_instrument("vsg")
        add_instrument("sa")
        await handle_lock(["vsg"], ctx(SESSION_1))
        await handle_lock(["sa"], ctx(SESSION_2))

        await handle_unlock(["ALL"], ctx(SESSION_1))

        registry = get_registry()
        assert registry.get("vsg").state == InstrumentState.IDLE
        assert registry.get("sa").lock_owner == SESSION_2

    @pytest.mark.asyncio
    async def test_unlock_all_no_locks(self):
        add_instrument("vsg")
        response = await handle_unlock(["ALL"], ctx(SESSION_1))
        assert response == b'+OK\r\n'


class TestLocksCommand:
    """Tests for LOCKS command."""

    @pytest.mark.asyncio
    async def test_locks_empty(self):
        response = await handle_locks([])
        parser = RESPParser()
        messages = parser.feed(response)
        assert messages[0] == []

    @pytest.mark.asyncio
    async def test_locks_with_locks(self):
        add_instrument("vsg")
        add_instrument("sa")
        await handle_lock(["vsg"], ctx(SESSION_1))
        await handle_lock(["sa"], ctx(SESSION_2))

        response = await handle_locks([])
        parser = RESPParser()
        messages = parser.feed(response)
        result = set(messages[0])
        assert "sa:session:2" in result
        assert "vsg:session:1" in result

    @pytest.mark.asyncio
    async def test_locks_after_unlock(self):
        add_instrument("vsg")
        await handle_lock(["vsg"], ctx(SESSION_1))
        await handle_unlock(["vsg"], ctx(SESSION_1))

        response = await handle_locks([])
        parser = RESPParser()
        messages = parser.feed(response)
        assert messages[0] == []
