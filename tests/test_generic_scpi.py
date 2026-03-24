# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Tests for Generic SCPI driver."""

import pytest
from testcore.base_driver import ScpiDriver, DriverError


# -- Mock transport (same pattern as test_agilent33500.py) --

class MockTransport:
    def __init__(self, responses=None):
        self.sent = []
        self.queries = []
        self._responses = responses or {}
        self._responses.setdefault(
            "*IDN?", "Keysight,34465A,MY12345678,1.02")
        self._responses.setdefault("*TST?", "0")
        self._responses.setdefault("*OPC?", "1")
        self._responses.setdefault("SYST:ERR?", '+0,"No error"')

    def send(self, command):
        self.sent.append(command)

    def query(self, command):
        self.queries.append(command)
        return self._responses.get(command, "0")

    def close(self):
        pass


def make_config(**kwargs):
    transport = kwargs.pop("transport_obj", None) or MockTransport(
        kwargs.pop("responses", None))
    config = {"transport": transport}
    config.update(kwargs)
    return config


# -- Import the driver --

from testcore.drivers.generic_scpi import GenericScpiDriver


class TestGenericScpiConnect:

    def test_connect(self):
        d = GenericScpiDriver()
        d.connect(make_config())
        assert d._connected is True

    def test_disconnect(self):
        d = GenericScpiDriver()
        d.connect(make_config())
        d.disconnect()
        assert d._connected is False


class TestGenericScpiInit:

    def test_init_runs_rst_cls_tst(self):
        t = MockTransport()
        d = GenericScpiDriver()
        d.connect(make_config(transport_obj=t))
        d.init()
        assert "*RST" in t.sent
        assert "*CLS" in t.sent
        assert "*TST?" in t.queries

    def test_init_fails_on_bad_selftest(self):
        t = MockTransport({"*TST?": "1"})
        d = GenericScpiDriver()
        d.connect(make_config(transport_obj=t))
        with pytest.raises(DriverError, match="self-test failed"):
            d.init()


class TestGenericScpiDiscover:

    def test_discover_returns_empty(self):
        d = GenericScpiDriver()
        d.connect(make_config())
        assert d.discover() == []


class TestGenericScpiReadWrite:

    def test_read_raises(self):
        d = GenericScpiDriver()
        d.connect(make_config())
        with pytest.raises(DriverError, match="unknown resource"):
            d.read("CH1:FREQ")

    def test_write_raises(self):
        d = GenericScpiDriver()
        d.connect(make_config())
        with pytest.raises(DriverError, match="unknown resource"):
            d.write("CH1:FREQ", "1000")

    def test_error_suggests_iraw(self):
        d = GenericScpiDriver()
        d.connect(make_config())
        with pytest.raises(DriverError, match="IRAW"):
            d.read("VOLT")


class TestGenericScpiPassthrough:

    def test_passthrough_query(self):
        t = MockTransport({"MEAS:VOLT:DC?": "1.23456"})
        d = GenericScpiDriver()
        d.connect(make_config(transport_obj=t))
        assert d.passthrough("MEAS:VOLT:DC?") == "1.23456"

    def test_passthrough_command(self):
        t = MockTransport()
        d = GenericScpiDriver()
        d.connect(make_config(transport_obj=t))
        result = d.passthrough("CONF:VOLT:DC 10")
        assert result == "OK"
        assert "CONF:VOLT:DC 10" in t.sent


class TestGenericScpiSafeState:

    def test_safe_state_sends_rst(self):
        t = MockTransport()
        d = GenericScpiDriver()
        d.connect(make_config(transport_obj=t))
        d.safe_state()
        assert "*RST" in t.sent


class TestGenericScpiInfo:

    def test_info_parses_idn(self):
        d = GenericScpiDriver()
        d.connect(make_config())
        info = d.info()
        assert info["vendor"] == "Keysight"
        assert info["model"] == "34465A"
        assert info["serial"] == "MY12345678"
        assert info["version"] == "1.02"


class TestGenericScpiConfigure:

    def test_configure_sends_lines(self, tmp_path):
        config = tmp_path / "setup.cfg"
        config.write_text("# Setup\nCONF:VOLT:DC 10\nTRIG:SOUR IMM\n")

        t = MockTransport()
        d = GenericScpiDriver()
        d.connect(make_config(transport_obj=t))
        d.configure(str(config))

        assert "CONF:VOLT:DC 10" in t.sent
        assert "TRIG:SOUR IMM" in t.sent
        assert len(t.sent) == 2

    def test_configure_file_not_found(self):
        d = GenericScpiDriver()
        d.connect(make_config())
        with pytest.raises(DriverError, match="not found"):
            d.configure("/nonexistent/file.cfg")


class TestGenericScpiLoadSave:

    def test_load_not_supported(self):
        d = GenericScpiDriver()
        d.connect(make_config())
        with pytest.raises(DriverError, match="not supported"):
            d.load("target", "file.csv")

    def test_save_not_supported(self):
        d = GenericScpiDriver()
        d.connect(make_config())
        with pytest.raises(DriverError, match="not supported"):
            d.save("target", "file.csv")
