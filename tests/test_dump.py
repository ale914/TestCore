# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Tests for DUMP command."""

import json
import pytest
from unittest.mock import MagicMock
from testcore.commands import handle_dump, dispatcher
from testcore.store import get_store
from testcore.protocol import RESPParser
from testcore.server import TestCoreServer, ClientHandler

DRYRUN_PATH = "dryrun"


@pytest.fixture(autouse=True)
def reset_state():
    """Reset store and registry before each test."""
    store = get_store()
    store._data.clear()
    from testcore.instruments import get_registry
    registry = get_registry()
    for name in list(registry._instruments.keys()):
        try:
            registry.remove(name)
        except Exception:
            pass
    registry._instruments.clear()


def make_handler(monitoring=False, subscribing=False):
    handler = MagicMock(spec=ClientHandler)
    handler.monitoring = monitoring
    handler.subscribing = subscribing
    return handler


def parse_bulk(response: bytes) -> str:
    """Parse RESP bulk string response to string."""
    parser = RESPParser()
    messages = parser.feed(response)
    return messages[0]


class TestDumpCommand:

    @pytest.mark.asyncio
    async def test_dump_returns_json(self):
        response = await handle_dump([], {"session_id": 1})
        text = parse_bulk(response)
        data = json.loads(text)
        assert "version" in data
        assert "timestamp" in data
        assert "kv" in data
        assert "instruments" in data
        assert "locks" in data
        assert "sessions" in data

    @pytest.mark.asyncio
    async def test_dump_includes_kv(self):
        store = get_store()
        store._data["mykey"] = "myval"
        store._data["other"] = "123"
        response = await handle_dump([], {"session_id": 1})
        data = json.loads(parse_bulk(response))
        assert data["kv"]["mykey"] == "myval"
        assert data["kv"]["other"] == "123"

    @pytest.mark.asyncio
    async def test_dump_excludes_reserved_keys(self):
        store = get_store()
        store._data["mykey"] = "val"
        store._data["_sys:version"] = "0.8.1"
        store._data["_inst:awg:state"] = "READY"
        response = await handle_dump([], {"session_id": 1})
        data = json.loads(parse_bulk(response))
        assert "mykey" in data["kv"]
        assert "_sys:version" not in data["kv"]
        assert "_inst:awg:state" not in data["kv"]

    @pytest.mark.asyncio
    async def test_dump_includes_instruments(self):
        from testcore.instruments import get_registry

        registry = get_registry()
        registry.add("sim1", DRYRUN_PATH)
        response = await handle_dump([], {"session_id": 1})
        data = json.loads(parse_bulk(response))
        assert "sim1" in data["instruments"]
        assert data["instruments"]["sim1"]["state"] == "IDLE"
        assert data["instruments"]["sim1"]["driver"] == "DryRunDriver"

    @pytest.mark.asyncio
    async def test_dump_blocked_in_monitor_mode(self):
        handler = make_handler(monitoring=True)
        ctx = {"session_id": 1, "client_handler": handler}
        response = await handle_dump([], ctx)
        assert b"-" in response
        assert b"monitor" in response.lower()

    @pytest.mark.asyncio
    async def test_dump_blocked_in_subscriber_mode(self):
        handler = make_handler(subscribing=True)
        ctx = {"session_id": 1, "client_handler": handler}
        response = await handle_dump([], ctx)
        assert b"-" in response
        assert b"subscriber" in response.lower()

    @pytest.mark.asyncio
    async def test_dump_allowed_normal_client(self):
        handler = make_handler()
        ctx = {"session_id": 1, "client_handler": handler}
        response = await handle_dump([], ctx)
        text = parse_bulk(response)
        data = json.loads(text)
        assert "version" in data

    @pytest.mark.asyncio
    async def test_dump_registered(self):
        assert "DUMP" in dispatcher._handlers

    @pytest.mark.asyncio
    async def test_dump_locks_section(self):
        from testcore.instruments import get_registry

        registry = get_registry()
        registry.add("sim1", DRYRUN_PATH)
        inst = registry.get("sim1")
        inst.lock_owner = 42

        response = await handle_dump([], {"session_id": 1})
        data = json.loads(parse_bulk(response))
        assert data["locks"]["sim1"] == 42
