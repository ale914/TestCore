# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Tests for Agilent 33500 driver and ScpiDriver base class."""

from __future__ import annotations
import pytest
from testcore.base_driver import ScpiDriver, DriverError


# -- Mock transport for testing --

class MockTransport:
    """Records SCPI commands and returns canned responses."""

    def __init__(self, responses: dict[str, str] | None = None):
        self.sent: list[str] = []
        self.queries: list[str] = []
        self._responses = responses or {}
        self._responses.setdefault(
            "*IDN?", "Agilent Technologies,33521A,MY12345678,2.05")
        self._responses.setdefault("*TST?", "0")
        self._responses.setdefault("*OPC?", "1")
        self._responses.setdefault("SYST:ERR?", "+0,\"No error\"")

    def send(self, command: str):
        self.sent.append(command)

    def query(self, command: str) -> str:
        self.queries.append(command)
        return self._responses.get(command, "0")

    def close(self):
        pass


def make_config(**kwargs) -> dict:
    """Create a driver config with mock transport."""
    transport = kwargs.pop("transport_obj", None) or MockTransport(
        kwargs.pop("responses", None))
    config = {"transport": transport}
    config.update(kwargs)
    return config


# ===== ScpiDriver Tests =====

class ConcreteScpiDriver(ScpiDriver):
    """Minimal concrete ScpiDriver for testing base class methods."""

    def configure(self, config_path): pass
    def discover(self): return []
    def read(self, resource): return ""
    def write(self, resource, value): pass


class TestScpiDriver:
    """Tests for ScpiDriver IEEE 488.2 common commands."""

    def _make(self, responses=None):
        t = MockTransport(responses)
        d = ConcreteScpiDriver()
        d.connect({"transport": t})
        return d, t

    def test_idn(self):
        d, t = self._make()
        result = d.idn()
        assert "*IDN?" in t.queries
        assert "Agilent" in result

    def test_rst(self):
        d, t = self._make()
        d.rst()
        assert "*RST" in t.sent

    def test_cls(self):
        d, t = self._make()
        d.cls()
        assert "*CLS" in t.sent

    def test_opc(self):
        d, _ = self._make()
        assert d.opc() == "1"

    def test_tst(self):
        d, _ = self._make()
        assert d.tst() == "0"

    def test_error(self):
        d, _ = self._make()
        assert "No error" in d.error()

    def test_init_calls_rst_cls(self):
        d, t = self._make()
        d.init()
        assert "*RST" in t.sent
        assert "*CLS" in t.sent
        assert "*TST?" not in t.queries

    def test_init_with_selftest(self):
        d, t = self._make()
        d.init(selftest=True)
        assert "*RST" in t.sent
        assert "*CLS" in t.sent
        assert "*TST?" in t.queries

    def test_init_fails_on_bad_selftest(self):
        d, _ = self._make({"*TST?": "1"})
        with pytest.raises(DriverError, match="self-test failed"):
            d.init(selftest=True)

    def test_passthrough_query(self):
        d, _ = self._make({"MEAS:FREQ?": "1000.0"})
        assert d.passthrough("MEAS:FREQ?") == "1000.0"

    def test_passthrough_command(self):
        d, t = self._make()
        assert d.passthrough("OUTP ON") == "OK"
        assert "OUTP ON" in t.sent

    def test_info_parses_idn(self):
        d, _ = self._make()
        info = d.info()
        assert info["vendor"] == "Agilent Technologies"
        assert info["model"] == "33521A"
        assert info["serial"] == "MY12345678"
        assert info["version"] == "2.05"

    def test_info_handles_error(self):
        d, t = self._make()
        t.query = lambda cmd: (_ for _ in ()).throw(Exception("fail"))
        info = d.info()
        assert info["vendor"] == "unknown"

    def test_safe_state_calls_rst(self):
        d, t = self._make()
        d.safe_state()
        assert "*RST" in t.sent

    def test_send_without_connect_raises(self):
        d = ConcreteScpiDriver()
        with pytest.raises(DriverError, match="not connected"):
            d._send("*RST")

    def test_disconnect(self):
        d, _ = self._make()
        d.disconnect()
        assert d._connected is False
        assert d._transport is None


# ===== Agilent33500Driver Tests =====

from testcore.drivers.agilent33500 import Agilent33500Driver, _RESOURCES


class TestAgilent33500Connect:
    """Tests for connect/disconnect lifecycle."""

    def test_connect_basic(self):
        d = Agilent33500Driver()
        d.connect(make_config())
        assert d._connected is True
        assert d._channels == 1

    def test_connect_detects_2ch(self):
        t = MockTransport({"*IDN?": "Agilent Technologies,33522A,SN,1.0"})
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        assert d._channels == 2


class TestAgilent33500Init:
    """Tests for init and configure."""

    def test_init_resets_and_disables_output(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.init()
        assert "*RST" in t.sent
        assert "OUTP1 OFF" in t.sent

    def test_init_2ch_disables_both(self):
        t = MockTransport({"*IDN?": "Agilent Technologies,33522A,SN,1.0"})
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.init()
        assert "OUTP1 OFF" in t.sent
        assert "OUTP2 OFF" in t.sent

    def test_configure_file_not_found(self):
        d = Agilent33500Driver()
        d.connect(make_config())
        with pytest.raises(DriverError, match="not found"):
            d.configure("/nonexistent/path.txt")

    def test_configure_reads_scpi_lines(self, tmp_path):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))

        config = tmp_path / "awg.cfg"
        config.write_text("# Comment\nFUNC SIN\nFREQ 1000\n")
        d.configure(str(config))

        assert "FUNC SIN" in t.sent
        assert "FREQ 1000" in t.sent
        assert len(t.sent) == 2


class TestAgilent33500Discover:
    """Tests for resource discovery."""

    def test_discover_1ch(self):
        d = Agilent33500Driver()
        d.connect(make_config())
        resources = d.discover()
        assert all(r.startswith("CH1:") for r in resources)
        assert len(resources) == len(_RESOURCES)

    def test_discover_2ch(self):
        t = MockTransport({"*IDN?": "Agilent Technologies,33522A,SN,1.0"})
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        resources = d.discover()
        # Compact format: CH[1|2]:NAME
        assert all(r.startswith("CH[1|2]:") for r in resources)
        assert len(resources) == len(_RESOURCES)  # 48 compact, not 96

    def test_discover_sorted(self):
        d = Agilent33500Driver()
        d.connect(make_config())
        resources = d.discover()
        assert resources == sorted(resources)

    def test_resource_count(self):
        """Verify we have ~48 resources (40%+ of ~100 instrument commands)."""
        assert len(_RESOURCES) >= 40

    def test_resource_names_are_flat(self):
        """No colons in resource names — they're flat, not hierarchical."""
        for name in _RESOURCES:
            assert ":" not in name, f"resource '{name}' should be flat"


class TestAgilent33500Read:
    """Tests for reading resource values."""

    def test_read_frequency(self):
        t = MockTransport({"SOUR1:FREQ?": "1000.0"})
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        assert d.read("CH1:FREQ") == "1000.0"

    def test_read_output_function(self):
        t = MockTransport({"SOUR1:FUNC?": "SIN"})
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        assert d.read("CH1:FUNC") == "SIN"

    def test_read_amplitude(self):
        t = MockTransport({"SOUR1:VOLT?": "2.500"})
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        assert d.read("CH1:AMPL") == "2.500"

    def test_read_offset(self):
        t = MockTransport({"SOUR1:VOLT:OFFS?": "0.500"})
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        assert d.read("CH1:OFFSET") == "0.500"

    def test_read_ch2(self):
        t = MockTransport({
            "*IDN?": "Agilent Technologies,33522A,SN,1.0",
            "SOUR2:FREQ?": "5000.0",
        })
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        assert d.read("CH2:FREQ") == "5000.0"

    def test_read_unknown_resource(self):
        d = Agilent33500Driver()
        d.connect(make_config())
        with pytest.raises(DriverError, match="unknown resource"):
            d.read("CH1:NONEXISTENT")

    def test_read_invalid_channel_format(self):
        d = Agilent33500Driver()
        d.connect(make_config())
        with pytest.raises(DriverError, match="invalid channel"):
            d.read("X1:FREQ")

    def test_read_channel_out_of_range(self):
        d = Agilent33500Driver()
        d.connect(make_config())  # 1-ch instrument
        with pytest.raises(DriverError, match="not available"):
            d.read("CH2:FREQ")

    def test_read_no_colon_raises(self):
        d = Agilent33500Driver()
        d.connect(make_config())
        with pytest.raises(DriverError, match="invalid resource"):
            d.read("FREQ")


class TestAgilent33500Write:
    """Tests for writing resource values."""

    def test_write_frequency(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.write("CH1:FREQ", "1000")
        assert "SOUR1:FREQ 1000" in t.sent

    def test_write_function(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.write("CH1:FUNC", "SQU")
        assert "SOUR1:FUNC SQU" in t.sent

    def test_write_amplitude(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.write("CH1:AMPL", "2.5")
        assert "SOUR1:VOLT 2.5" in t.sent

    def test_write_offset(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.write("CH1:OFFSET", "1.0")
        assert "SOUR1:VOLT:OFFS 1.0" in t.sent

    def test_write_output_on(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.write("CH1:OUTPUT", "ON")
        assert "OUTP1:STAT ON" in t.sent

    def test_write_pulse_width(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.write("CH1:PULSE_WIDTH", "0.001")
        assert "SOUR1:FUNC:PULS:WIDT 0.001" in t.sent

    def test_write_sweep(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.write("CH1:SWEEP", "ON")
        assert "SOUR1:SWE:STAT ON" in t.sent

    def test_write_burst_cycles(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.write("CH1:BURST_CYCLES", "5")
        assert "SOUR1:BURS:NCYC 5" in t.sent

    def test_write_lead_edge(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.write("CH1:LEAD_EDGE", "8e-6")
        assert "SOUR1:FUNC:PULS:TRAN:LEAD 8e-6" in t.sent

    def test_write_ch2(self):
        t = MockTransport({
            "*IDN?": "Agilent Technologies,33522A,SN,1.0"
        })
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.write("CH2:FREQ", "5000")
        assert "SOUR2:FREQ 5000" in t.sent

    def test_write_unknown_resource(self):
        d = Agilent33500Driver()
        d.connect(make_config())
        with pytest.raises(DriverError, match="unknown resource"):
            d.write("CH1:NONEXISTENT", "42")


class TestAgilent33500SafeState:
    """Tests for safe_state."""

    def test_safe_state_disables_output(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.safe_state()
        assert "OUTP1 OFF" in t.sent

    def test_safe_state_2ch(self):
        t = MockTransport({"*IDN?": "Agilent Technologies,33522A,SN,1.0"})
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.safe_state()
        assert "OUTP1 OFF" in t.sent
        assert "OUTP2 OFF" in t.sent


class TestAgilent33500Apply:
    """Tests for APPLy convenience method."""

    def test_apply_sine(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.apply(1, "SIN", "1000", "2.5", "0")
        assert "SOUR1:APPL:SIN 1000,2.5,0" in t.sent

    def test_apply_defaults(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.apply(1, "SQU")
        assert "SOUR1:APPL:SQU DEF,DEF,DEF" in t.sent


class TestAgilent33500Passthrough:
    """Tests for SCPI tunneling (passthrough)."""

    def test_passthrough_query(self):
        t = MockTransport({"MEAS:FREQ?": "1000.0"})
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        assert d.passthrough("MEAS:FREQ?") == "1000.0"

    def test_passthrough_command(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        result = d.passthrough("DATA:ARB mywave,0,0.5,1,0.5,0")
        assert result == "OK"
        assert "DATA:ARB mywave,0,0.5,1,0.5,0" in t.sent


class TestAgilent33500ArbResources:
    """Tests for arbitrary waveform per-channel resources."""

    def test_read_arb_func(self):
        t = MockTransport({"SOUR1:FUNC:ARB?": "TestArb"})
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        assert d.read("CH1:ARB_FUNC") == "TestArb"

    def test_write_arb_func(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.write("CH1:ARB_FUNC", "MyWave")
        assert "SOUR1:FUNC:ARB MyWave" in t.sent

    def test_read_arb_srate(self):
        t = MockTransport({"SOUR1:FUNC:ARB:SRAT?": "40000"})
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        assert d.read("CH1:ARB_SRATE") == "40000"

    def test_write_arb_srate(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.write("CH1:ARB_SRATE", "50000")
        assert "SOUR1:FUNC:ARB:SRAT 50000" in t.sent

    def test_read_arb_filter(self):
        t = MockTransport({"SOUR1:FUNC:ARB:FILT?": "NORM"})
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        assert d.read("CH1:ARB_FILTER") == "NORM"

    def test_write_arb_filter(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.write("CH1:ARB_FILTER", "STEP")
        assert "SOUR1:FUNC:ARB:FILT STEP" in t.sent

    def test_read_arb_ptpeak(self):
        t = MockTransport({"SOUR1:FUNC:ARB:PTP?": "2.000"})
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        assert d.read("CH1:ARB_PTPEAK") == "2.000"

    def test_write_arb_advance(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.write("CH1:ARB_ADVANCE", "TRIG")
        assert "SOUR1:FUNC:ARB:ADV TRIG" in t.sent


class TestAgilent33500ArbMethods:
    """Tests for arbitrary waveform data/file driver methods."""

    def test_arb_load(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.arb_load(1, "TestArb", [0.0, 0.5, 1.0, 0.5, 0.0, -0.5, -1.0, -0.5])
        assert t.sent[-1] == "SOUR1:DATA:ARB TestArb,0.0,0.5,1.0,0.5,0.0,-0.5,-1.0,-0.5"

    def test_arb_clear(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.arb_clear()
        assert "DATA:VOL:CLE" in t.sent

    def test_arb_catalog(self):
        t = MockTransport({"DATA:VOL:CAT?": '"TestArb","MyWave"'})
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        result = d.arb_catalog()
        assert "TestArb" in result
        assert "MyWave" in result

    def test_mem_load(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.mem_load("INT:\\BUILTIN\\SINC.ARB")
        assert 'MMEM:LOAD:DATA "INT:\\BUILTIN\\SINC.ARB"' in t.sent

    def test_mem_store(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.mem_store(1, "INT:\\MyWave.arb")
        assert 'MMEM:STOR:DATA1 "INT:\\MyWave.arb"' in t.sent

    def test_mem_catalog(self):
        t = MockTransport({"MMEM:CAT?": '0,0,"file1.arb","file2.arb"'})
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        result = d.mem_catalog()
        assert "file1.arb" in result

    def test_mem_catalog_with_directory(self):
        t = MockTransport({
            'MMEM:CAT? "INT:\\BUILTIN"': '0,0,"SINC.ARB","CARDIAC.ARB"'
        })
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        result = d.mem_catalog("INT:\\BUILTIN")
        assert "SINC.ARB" in result

    def test_mem_delete(self):
        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        d.mem_delete("INT:\\MyWave.arb")
        assert 'MMEM:DEL "INT:\\MyWave.arb"' in t.sent


class TestAgilent33500Load:
    """Tests for load() — CSV waveform file loading."""

    def test_load_csv_one_per_line(self, tmp_path):
        csv_file = tmp_path / "wave.csv"
        csv_file.write_text("0.0\n0.5\n1.0\n0.5\n0.0\n-0.5\n-1.0\n-0.5\n")

        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        result = d.load("CH1:MyPulse", str(csv_file))

        assert result == "8 points loaded"
        assert t.sent[-1] == "SOUR1:DATA:ARB MyPulse,0.0,0.5,1.0,0.5,0.0,-0.5,-1.0,-0.5"

    def test_load_csv_comma_separated(self, tmp_path):
        csv_file = tmp_path / "wave.csv"
        csv_file.write_text("0.0,0.25,0.5,0.75,1.0,0.75,0.5,0.25\n")

        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        result = d.load("CH1:TestWave", str(csv_file))
        assert result == "8 points loaded"

    def test_load_csv_with_comments(self, tmp_path):
        csv_file = tmp_path / "wave.csv"
        csv_file.write_text(
            "# My waveform\n0.0\n0.5\n1.0\n0.5\n0.0\n-0.5\n-1.0\n-0.5\n")

        t = MockTransport()
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        result = d.load("CH1:Wave", str(csv_file))
        assert result == "8 points loaded"

    def test_load_file_not_found(self):
        d = Agilent33500Driver()
        d.connect(make_config())
        with pytest.raises(DriverError, match="file not found"):
            d.load("CH1:Wave", "/nonexistent/file.csv")

    def test_load_too_few_points(self, tmp_path):
        csv_file = tmp_path / "short.csv"
        csv_file.write_text("0.0\n0.5\n1.0\n")

        d = Agilent33500Driver()
        d.connect(make_config())
        with pytest.raises(DriverError, match="too short"):
            d.load("CH1:Wave", str(csv_file))

    def test_load_invalid_data(self, tmp_path):
        csv_file = tmp_path / "bad.csv"
        csv_file.write_text("0.0\nnot_a_number\n0.5\n")

        d = Agilent33500Driver()
        d.connect(make_config())
        with pytest.raises(DriverError, match="invalid data"):
            d.load("CH1:Wave", str(csv_file))

    def test_load_ch2(self, tmp_path):
        csv_file = tmp_path / "wave.csv"
        csv_file.write_text("0,0.5,1,0.5,0,-0.5,-1,-0.5\n")

        t = MockTransport({"*IDN?": "Agilent Technologies,33522A,SN,1.0"})
        d = Agilent33500Driver()
        d.connect(make_config(transport_obj=t))
        result = d.load("CH2:Wave", str(csv_file))
        assert result == "8 points loaded"
        assert "SOUR2:DATA:ARB Wave" in t.sent[-1]


class TestAgilent33500ResourceMap:
    """Validate resource map structure."""

    def test_all_resources_have_query(self):
        for name, entry in _RESOURCES.items():
            assert entry[0].endswith("?"), f"{name} query missing '?': {entry[0]}"

    def test_all_resources_have_write_template(self):
        for name, entry in _RESOURCES.items():
            template = entry[1]
            assert template is not None, f"{name} has no write template"
            assert "{value}" in template, f"{name} template missing {{value}}"

    def test_resource_names_are_flat(self):
        """Resource names should not contain colons."""
        for name in _RESOURCES:
            assert ":" not in name, f"resource '{name}' should be flat"
