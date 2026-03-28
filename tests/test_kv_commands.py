# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Tests for Key-Value Commands (MVP v0.2 spec §6.2)."""

import pytest
from testcore.commands import (
    handle_set, handle_get, handle_kmget, handle_mset, handle_del,
    handle_exists, handle_keys, handle_dbsize, handle_flushdb,
)
from testcore.store import get_store
from testcore.protocol import RESPParser


@pytest.fixture(autouse=True)
def reset_store():
    """Reset store before each test."""
    store = get_store()
    store._data.clear()


class TestSetCommand:
    """Tests for SET command."""

    @pytest.mark.asyncio
    async def test_set_basic(self):
        """Test basic SET command."""
        response = await handle_set(['key1', 'value1'])
        assert response == b'+OK\r\n'

        store = get_store()
        assert store.get('key1') == 'value1'

    @pytest.mark.asyncio
    async def test_set_nx_not_exists(self):
        """Test SET NX when key doesn't exist."""
        response = await handle_set(['key1', 'value1', 'NX'])
        assert response == b'+OK\r\n'

    @pytest.mark.asyncio
    async def test_set_nx_exists(self):
        """Test SET NX when key exists returns nil."""
        await handle_set(['key1', 'value1'])
        response = await handle_set(['key1', 'value2', 'NX'])
        # Parse response
        parser = RESPParser()
        messages = parser.feed(response)
        assert messages[0] is None  # Nil

    @pytest.mark.asyncio
    async def test_set_xx_exists(self):
        """Test SET XX when key exists."""
        await handle_set(['key1', 'value1'])
        response = await handle_set(['key1', 'value2', 'XX'])
        assert response == b'+OK\r\n'

    @pytest.mark.asyncio
    async def test_set_xx_not_exists(self):
        """Test SET XX when key doesn't exist returns nil."""
        response = await handle_set(['key1', 'value1', 'XX'])
        # Parse response
        parser = RESPParser()
        messages = parser.feed(response)
        assert messages[0] is None  # Nil

    @pytest.mark.asyncio
    async def test_set_reserved_prefix_error(self):
        """Test SET on reserved prefix returns error."""
        response = await handle_set(['_sys:foo', 'bar'])
        assert response.startswith(b'-')
        assert b'READONLY' in response

    @pytest.mark.asyncio
    async def test_set_wrong_args(self):
        """Test SET with wrong number of arguments."""
        response = await handle_set(['key1'])
        assert response.startswith(b'-ERR')


class TestGetCommand:
    """Tests for GET command."""

    @pytest.mark.asyncio
    async def test_get_existing_key(self):
        """Test GET on existing key."""
        await handle_set(['key1', 'value1'])
        response = await handle_get(['key1'])
        assert response == b'$6\r\nvalue1\r\n'

    @pytest.mark.asyncio
    async def test_get_missing_key(self):
        """Test GET on missing key returns nil."""
        response = await handle_get(['missing'])
        # Parse response
        parser = RESPParser()
        messages = parser.feed(response)
        assert messages[0] is None  # Nil

    @pytest.mark.asyncio
    async def test_get_wrong_args(self):
        """Test GET with no arguments."""
        response = await handle_get([])
        assert response.startswith(b'-ERR')


class TestMGetCommand:
    """Tests for MGET command."""

    @pytest.mark.asyncio
    async def test_mget_multiple_keys(self):
        """Test MGET with multiple keys."""
        await handle_set(['k1', 'v1'])
        await handle_set(['k2', 'v2'])
        await handle_set(['k3', 'v3'])

        response = await handle_kmget(['k1', 'k2', 'k3'])
        # Parse response
        parser = RESPParser()
        messages = parser.feed(response)
        result = messages[0]

        assert result == ['v1', 'v2', 'v3']

    @pytest.mark.asyncio
    async def test_mget_missing_keys(self):
        """Test MGET with some missing keys."""
        await handle_set(['k1', 'v1'])

        response = await handle_kmget(['k1', 'missing', 'k3'])
        parser = RESPParser()
        messages = parser.feed(response)
        result = messages[0]

        assert result == ['v1', None, None]

    @pytest.mark.asyncio
    async def test_mget_wrong_args(self):
        """Test MGET with no arguments."""
        response = await handle_kmget([])
        assert response.startswith(b'-ERR')


class TestMSetCommand:
    """Tests for MSET command."""

    @pytest.mark.asyncio
    async def test_mset_multiple_pairs(self):
        """Test MSET with multiple key-value pairs."""
        response = await handle_mset(['k1', 'v1', 'k2', 'v2', 'k3', 'v3'])
        assert response == b'+OK\r\n'

        store = get_store()
        assert store.get('k1') == 'v1'
        assert store.get('k2') == 'v2'
        assert store.get('k3') == 'v3'

    @pytest.mark.asyncio
    async def test_mset_reserved_prefix_error(self):
        """Test MSET with reserved prefix returns error."""
        response = await handle_mset(['k1', 'v1', '_sys:foo', 'bar'])
        assert response.startswith(b'-')
        assert b'READONLY' in response

        # Verify atomic failure - no keys set
        store = get_store()
        assert store.get('k1') is None

    @pytest.mark.asyncio
    async def test_mset_wrong_args_odd(self):
        """Test MSET with odd number of arguments."""
        response = await handle_mset(['k1', 'v1', 'k2'])
        assert response.startswith(b'-ERR')

    @pytest.mark.asyncio
    async def test_mset_wrong_args_empty(self):
        """Test MSET with no arguments."""
        response = await handle_mset([])
        assert response.startswith(b'-ERR')


class TestDelCommand:
    """Tests for DEL command."""

    @pytest.mark.asyncio
    async def test_del_single_key(self):
        """Test DEL with single key."""
        await handle_set(['k1', 'v1'])
        response = await handle_del(['k1'])
        assert response == b':1\r\n'

        store = get_store()
        assert store.get('k1') is None

    @pytest.mark.asyncio
    async def test_del_multiple_keys(self):
        """Test DEL with multiple keys."""
        await handle_set(['k1', 'v1'])
        await handle_set(['k2', 'v2'])
        await handle_set(['k3', 'v3'])

        response = await handle_del(['k1', 'k2'])
        assert response == b':2\r\n'

    @pytest.mark.asyncio
    async def test_del_missing_key(self):
        """Test DEL on missing key."""
        response = await handle_del(['missing'])
        assert response == b':0\r\n'

    @pytest.mark.asyncio
    async def test_del_reserved_prefix_error(self):
        """Test DEL on reserved prefix returns error."""
        response = await handle_del(['_sys:foo'])
        assert response.startswith(b'-')
        assert b'READONLY' in response


class TestExistsCommand:
    """Tests for EXISTS command."""

    @pytest.mark.asyncio
    async def test_exists_single_key(self):
        """Test EXISTS with single key."""
        await handle_set(['k1', 'v1'])
        response = await handle_exists(['k1'])
        assert response == b':1\r\n'

    @pytest.mark.asyncio
    async def test_exists_missing_key(self):
        """Test EXISTS on missing key."""
        response = await handle_exists(['missing'])
        assert response == b':0\r\n'

    @pytest.mark.asyncio
    async def test_exists_multiple_keys(self):
        """Test EXISTS with multiple keys."""
        await handle_set(['k1', 'v1'])
        await handle_set(['k2', 'v2'])

        response = await handle_exists(['k1', 'k2', 'missing'])
        assert response == b':2\r\n'


class TestKeysCommand:
    """Tests for KEYS command."""

    @pytest.mark.asyncio
    async def test_keys_all(self):
        """Test KEYS * returns all client keys."""
        await handle_set(['k1', 'v1'])
        await handle_set(['k2', 'v2'])

        # Add reserved key manually
        store = get_store()
        store._data['_sys:version'] = '0.2.0'

        response = await handle_keys(['*'])
        parser = RESPParser()
        messages = parser.feed(response)
        result = messages[0]

        assert set(result) == {'k1', 'k2'}  # Excludes reserved

    @pytest.mark.asyncio
    async def test_keys_pattern_glob(self):
        """Test KEYS with glob pattern."""
        await handle_set(['meas:900MHz:pk2pk', '0.0034'])
        await handle_set(['meas:900MHz:acpr', '-42.1'])
        await handle_set(['meas:1800MHz:evm', '1.23'])
        await handle_set(['config:freq', '900e6'])

        response = await handle_keys(['meas:900MHz:*'])
        parser = RESPParser()
        messages = parser.feed(response)
        result = messages[0]

        assert set(result) == {'meas:900MHz:pk2pk', 'meas:900MHz:acpr'}

    @pytest.mark.asyncio
    async def test_keys_no_match(self):
        """Test KEYS pattern with no matches."""
        await handle_set(['k1', 'v1'])

        response = await handle_keys(['nomatch:*'])
        parser = RESPParser()
        messages = parser.feed(response)
        result = messages[0]

        assert result == []

    @pytest.mark.asyncio
    async def test_keys_default_all(self):
        """Test KEYS without arguments defaults to *."""
        await handle_set(['k1', 'v1'])
        await handle_set(['k2', 'v2'])

        response = await handle_keys([])
        parser = RESPParser()
        messages = parser.feed(response)
        result = messages[0]

        assert set(result) == {'k1', 'k2'}


class TestDbSizeCommand:
    """Tests for DBSIZE command."""

    @pytest.mark.asyncio
    async def test_dbsize_empty(self):
        """Test DBSIZE on empty store."""
        response = await handle_dbsize([])
        assert response == b':0\r\n'

    @pytest.mark.asyncio
    async def test_dbsize_with_keys(self):
        """Test DBSIZE counts client keys only."""
        await handle_set(['k1', 'v1'])
        await handle_set(['k2', 'v2'])

        # Add reserved keys
        store = get_store()
        store._data['_sys:version'] = '0.2.0'
        store._data['_drv:vsg:status'] = 'OK'

        response = await handle_dbsize([])
        assert response == b':2\r\n'  # Excludes reserved


class TestFlushDbCommand:
    """Tests for FLUSHDB command."""

    @pytest.mark.asyncio
    async def test_flushdb_basic(self):
        """Test FLUSHDB removes client keys."""
        await handle_set(['k1', 'v1'])
        await handle_set(['k2', 'v2'])

        response = await handle_flushdb([])
        assert response == b'+OK\r\n'

        store = get_store()
        assert store.get('k1') is None
        assert store.dbsize() == 0

    @pytest.mark.asyncio
    async def test_flushdb_preserves_reserved(self):
        """Test FLUSHDB preserves reserved prefixes."""
        await handle_set(['k1', 'v1'])

        store = get_store()
        store._data['_sys:version'] = '0.2.0'
        store._data['_drv:vsg:status'] = 'OK'

        await handle_flushdb([])

        assert store.get('k1') is None
        assert store.get('_sys:version') == '0.2.0'
        assert store.get('_drv:vsg:status') == 'OK'


