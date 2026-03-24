# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Tests for KGETALL command."""

import pytest
from testcore.commands import handle_getall, dispatcher
from testcore.store import get_store
from testcore.protocol import RESPParser


@pytest.fixture(autouse=True)
def reset_store():
    """Reset store before each test."""
    store = get_store()
    store._data.clear()


def parse_array(response: bytes) -> list:
    parser = RESPParser()
    return parser.feed(response)[0]


class TestKgetallCommand:

    @pytest.mark.asyncio
    async def test_getall_empty(self):
        result = parse_array(await handle_getall([], {}))
        assert result == []

    @pytest.mark.asyncio
    async def test_getall_basic(self):
        store = get_store()
        store._data["key1"] = "val1"
        store._data["key2"] = "val2"
        result = parse_array(await handle_getall([], {}))
        # Flat array: [k1, v1, k2, v2]
        assert len(result) == 4
        # Convert to dict for easier assertion
        pairs = dict(zip(result[::2], result[1::2]))
        assert pairs["key1"] == "val1"
        assert pairs["key2"] == "val2"

    @pytest.mark.asyncio
    async def test_getall_excludes_reserved(self):
        store = get_store()
        store._data["mykey"] = "val"
        store._data["_sys:version"] = "1.0"
        store._data["_inst:awg:state"] = "READY"
        result = parse_array(await handle_getall([], {}))
        keys = result[::2]
        assert "mykey" in keys
        assert "_sys:version" not in keys
        assert "_inst:awg:state" not in keys

    @pytest.mark.asyncio
    async def test_getall_with_prefix(self):
        store = get_store()
        store._data["alert:temp"] = "85"
        store._data["alert:power"] = "36"
        store._data["result:freq"] = "1000"
        result = parse_array(await handle_getall(["alert:"], {}))
        assert len(result) == 4  # 2 keys × 2
        keys = result[::2]
        assert "alert:temp" in keys
        assert "alert:power" in keys
        assert "result:freq" not in keys

    @pytest.mark.asyncio
    async def test_getall_prefix_no_match(self):
        store = get_store()
        store._data["key1"] = "val1"
        result = parse_array(await handle_getall(["nomatch:"], {}))
        assert result == []

    @pytest.mark.asyncio
    async def test_getall_prefix_reserved_still_excluded(self):
        store = get_store()
        store._data["_sys:foo"] = "bar"
        result = parse_array(await handle_getall(["_sys:"], {}))
        assert result == []

    @pytest.mark.asyncio
    async def test_getall_registered(self):
        assert "KGETALL" in dispatcher._handlers

    @pytest.mark.asyncio
    async def test_getall_single_key(self):
        store = get_store()
        store._data["only"] = "one"
        result = parse_array(await handle_getall([], {}))
        assert result == ["only", "one"]
