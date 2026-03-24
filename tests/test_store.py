# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Tests for Key-Value Store (MVP v0.2 spec §5)."""

import pytest
from testcore.store import KeyValueStore, RESERVED_PREFIXES


class TestKeyValueStore:
    """Unit tests for KeyValueStore."""

    def setup_method(self):
        """Create fresh store for each test."""
        self.store = KeyValueStore()

    # SET/GET tests

    def test_set_get_basic(self):
        """Test basic SET and GET."""
        assert self.store.set('key1', 'value1') is True
        assert self.store.get('key1') == 'value1'

    def test_get_missing_key(self):
        """Test GET on non-existent key returns None."""
        assert self.store.get('missing') is None

    def test_set_nx_not_exists(self):
        """Test SET NX succeeds when key doesn't exist."""
        assert self.store.set('key1', 'value1', nx=True) is True
        assert self.store.get('key1') == 'value1'

    def test_set_nx_exists(self):
        """Test SET NX fails when key exists."""
        self.store.set('key1', 'value1')
        assert self.store.set('key1', 'value2', nx=True) is False
        assert self.store.get('key1') == 'value1'  # Unchanged

    def test_set_xx_exists(self):
        """Test SET XX succeeds when key exists."""
        self.store.set('key1', 'value1')
        assert self.store.set('key1', 'value2', xx=True) is True
        assert self.store.get('key1') == 'value2'

    def test_set_xx_not_exists(self):
        """Test SET XX fails when key doesn't exist."""
        assert self.store.set('key1', 'value1', xx=True) is False
        assert self.store.get('key1') is None

    def test_set_reserved_prefix_error(self):
        """Test SET on reserved prefix raises error."""
        for prefix in RESERVED_PREFIXES:
            with pytest.raises(ValueError, match="READONLY"):
                self.store.set(f'{prefix}foo', 'value')

    def test_get_reserved_prefix_allowed(self):
        """Test GET on reserved prefix is allowed (spec §5.1)."""
        # Manually insert reserved key
        self.store._data['_sys:version'] = '0.2.0'
        assert self.store.get('_sys:version') == '0.2.0'

    # MGET/MSET tests

    def test_mget_basic(self):
        """Test MGET with multiple keys."""
        self.store.set('k1', 'v1')
        self.store.set('k2', 'v2')
        self.store.set('k3', 'v3')

        values = self.store.mget(['k1', 'k2', 'k3'])
        assert values == ['v1', 'v2', 'v3']

    def test_mget_missing_keys(self):
        """Test MGET returns None for missing keys."""
        self.store.set('k1', 'v1')
        values = self.store.mget(['k1', 'missing', 'k3'])
        assert values == ['v1', None, None]

    def test_mset_basic(self):
        """Test MSET with multiple pairs."""
        self.store.mset([('k1', 'v1'), ('k2', 'v2'), ('k3', 'v3')])
        assert self.store.get('k1') == 'v1'
        assert self.store.get('k2') == 'v2'
        assert self.store.get('k3') == 'v3'

    def test_mset_reserved_prefix_error(self):
        """Test MSET rejects reserved prefixes."""
        with pytest.raises(ValueError, match="READONLY"):
            self.store.mset([('k1', 'v1'), ('_sys:foo', 'bar')])

        # Verify no keys were set (atomic failure)
        assert self.store.get('k1') is None

    # DEL tests

    def test_del_single_key(self):
        """Test DEL with single key."""
        self.store.set('k1', 'v1')
        count = self.store.delete(['k1'])
        assert count == 1
        assert self.store.get('k1') is None

    def test_del_multiple_keys(self):
        """Test DEL with multiple keys."""
        self.store.set('k1', 'v1')
        self.store.set('k2', 'v2')
        self.store.set('k3', 'v3')

        count = self.store.delete(['k1', 'k2'])
        assert count == 2
        assert self.store.get('k1') is None
        assert self.store.get('k2') is None
        assert self.store.get('k3') == 'v3'

    def test_del_missing_key(self):
        """Test DEL on missing key returns 0."""
        count = self.store.delete(['missing'])
        assert count == 0

    def test_del_reserved_prefix_error(self):
        """Test DEL rejects reserved prefixes."""
        with pytest.raises(ValueError, match="READONLY"):
            self.store.delete(['_sys:foo'])

    # EXISTS tests

    def test_exists_single_key(self):
        """Test EXISTS with single key."""
        self.store.set('k1', 'v1')
        assert self.store.exists(['k1']) == 1
        assert self.store.exists(['missing']) == 0

    def test_exists_multiple_keys(self):
        """Test EXISTS with multiple keys."""
        self.store.set('k1', 'v1')
        self.store.set('k2', 'v2')
        assert self.store.exists(['k1', 'k2', 'missing']) == 2

    # KEYS tests

    def test_keys_all(self):
        """Test KEYS * returns all client keys."""
        self.store.set('k1', 'v1')
        self.store.set('k2', 'v2')
        self.store._data['_sys:version'] = '0.2.0'  # Reserved key

        keys = self.store.keys('*')
        assert set(keys) == {'k1', 'k2'}  # Excludes reserved

    def test_keys_pattern_glob(self):
        """Test KEYS with glob pattern."""
        self.store.set('meas:900MHz:pk2pk', '0.0034')
        self.store.set('meas:900MHz:acpr', '-42.1')
        self.store.set('meas:1800MHz:evm', '1.23')
        self.store.set('config:freq', '900e6')

        keys = self.store.keys('meas:900MHz:*')
        assert set(keys) == {'meas:900MHz:pk2pk', 'meas:900MHz:acpr'}

    def test_keys_pattern_no_match(self):
        """Test KEYS pattern with no matches."""
        self.store.set('k1', 'v1')
        keys = self.store.keys('nomatch:*')
        assert keys == []

    # DBSIZE tests

    def test_dbsize_empty(self):
        """Test DBSIZE on empty store."""
        assert self.store.dbsize() == 0

    def test_dbsize_with_keys(self):
        """Test DBSIZE counts client keys only."""
        self.store.set('k1', 'v1')
        self.store.set('k2', 'v2')
        self.store._data['_sys:version'] = '0.2.0'
        self.store._data['_drv:vsg:status'] = 'OK'

        assert self.store.dbsize() == 2  # Excludes reserved

    # FLUSHDB tests

    def test_flushdb_basic(self):
        """Test FLUSHDB removes client keys."""
        self.store.set('k1', 'v1')
        self.store.set('k2', 'v2')
        self.store.flushdb()

        assert self.store.get('k1') is None
        assert self.store.get('k2') is None
        assert self.store.dbsize() == 0

    def test_flushdb_preserves_reserved(self):
        """Test FLUSHDB preserves reserved prefixes."""
        self.store.set('k1', 'v1')
        self.store._data['_sys:version'] = '0.2.0'
        self.store._data['_drv:vsg:status'] = 'OK'

        self.store.flushdb()

        assert self.store.get('k1') is None
        assert self.store.get('_sys:version') == '0.2.0'
        assert self.store.get('_drv:vsg:status') == 'OK'


