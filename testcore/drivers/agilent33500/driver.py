# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Agilent 33500 Series Waveform Generator driver.

Supports: 33521A (1-ch), 33522A (2-ch)
Reference: Agilent 33500 Series User's Guide (33520-90001)

Resources are CHn:NAME — the driver hides SCPI complexity:
    IREAD  awg:CH1:FREQ          → "1000.0"
    IWRITE awg:CH1:AMPL 2.5
    IWRITE awg:CH1:OUTPUT ON
    IRAW   awg MEAS:FREQ?        → passthrough for unmapped commands
"""

from __future__ import annotations
from testcore import ScpiDriver, DriverError
from .resources import RESOURCES


class Agilent33500Driver(ScpiDriver):
    """Driver for Agilent 33500 Series waveform generators.

    Resources use flat, human-readable names:
        CH1:FUNC           - waveform function (SIN, SQU, RAMP, PULS, ARB, etc.)
        CH1:FREQ           - frequency in Hz
        CH1:AMPL           - amplitude (Vpp by default)
        CH1:OFFSET         - DC offset voltage
        CH1:OUTPUT         - output on/off
        CH1:SWEEP          - sweep on/off
        CH1:BURST_CYCLES   - burst cycle count
        CH1:ARB_FUNC       - select active arbitrary waveform by name
        CH1:ARB_SRATE      - arbitrary waveform sample rate (Sa/s)

    ARB data/file operations (via driver methods or IRAW):
        arb_load(ch, name, data)   - load float data into volatile memory
        arb_clear()                - clear volatile memory
        arb_catalog()              - list volatile waveforms
        mem_load(filename)         - load .arb file from instrument storage
        mem_store(ch, filename)    - store arb to instrument file
        mem_catalog([dir])         - list files on instrument
        mem_delete(filename)       - delete file from instrument
    """

    def __init__(self):
        super().__init__()
        self._channels: int = 1

    # -- BaseDriver contract --

    def connect(self, config: dict) -> None:
        """Connect and detect channel count from *IDN? response."""
        super().connect(config)

        # Detect channel count from model string
        idn = self.idn()
        if "33522" in idn:
            self._channels = 2

    def init(self, selftest: bool = False) -> None:
        """Reset to factory defaults, clear errors. Self-test if requested."""
        super().init(selftest)
        self._send("OUTP1 OFF")
        if self._channels == 2:
            self._send("OUTP2 OFF")

    def configure(self, config_path: str) -> None:
        """Apply a config file (line-by-line SCPI commands)."""
        try:
            with open(config_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        self._send(line)
        except FileNotFoundError:
            raise DriverError(f"config file not found: {config_path}")
        except Exception as e:
            raise DriverError(f"config error: {e}")

    def discover(self) -> list[str]:
        """Return available resources in compact form.

        Single channel:  CH1:FUNC, CH1:FREQ, ...
        Multi channel:   CH[1|2]:FUNC, CH[1|2]:FREQ, ...
        """
        if self._channels == 1:
            return sorted(f"CH1:{name}" for name in RESOURCES)

        channels = "|".join(str(ch) for ch in range(1, self._channels + 1))
        prefix = f"CH[{channels}]"
        return sorted(f"{prefix}:{name}" for name in RESOURCES)

    def read(self, resource: str) -> str:
        """Read resource value. Example: read('CH1:FREQ') → '1000.0'"""
        channel, name = self._parse_resource(resource)
        if name not in RESOURCES:
            raise DriverError(f"unknown resource: {resource}")

        entry = RESOURCES[name]
        prefix = entry[2] if len(entry) > 2 else "SOUR"
        return self._query(f"{prefix}{channel}:{entry[0]}").strip()

    def write(self, resource: str, value: str) -> None:
        """Write resource value. Example: write('CH1:FREQ', '1000')"""
        channel, name = self._parse_resource(resource)
        if name not in RESOURCES:
            raise DriverError(f"unknown resource: {resource}")

        entry = RESOURCES[name]
        prefix = entry[2] if len(entry) > 2 else "SOUR"
        self._send(f"{prefix}{channel}:{entry[1].format(value=value)}")

    def load(self, target: str, file_path: str) -> str:
        """Load CSV waveform data into instrument as arb waveform.

        Target format: 'CH1:WaveName' — channel and waveform name.
        CSV file: one float value per line (normalized -1.0 to +1.0),
        or comma-separated on a single line. Lines starting with '#' are skipped.

        Example:
            ILOAD awg CH1:MyPulse /path/to/pulse.csv
        """
        channel, name = self._parse_resource(target)

        try:
            with open(file_path, "r") as f:
                values = []
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    for token in line.split(","):
                        token = token.strip()
                        if token:
                            values.append(float(token))
        except FileNotFoundError:
            raise DriverError(f"file not found: {file_path}")
        except ValueError as e:
            raise DriverError(f"invalid data in CSV: {e}")

        if len(values) < 8:
            raise DriverError(
                f"waveform too short ({len(values)} points), minimum 8")

        self.arb_load(channel, name, values)
        return f"{len(values)} points loaded"

    def safe_state(self) -> None:
        """Turn off all outputs (safe shutdown)."""
        try:
            self._send("OUTP1 OFF")
            if self._channels == 2:
                self._send("OUTP2 OFF")
        except Exception:
            pass

    # -- Convenience: APPLy shortcut --

    def apply(self, channel: int, function: str,
              frequency: str = "DEF", amplitude: str = "DEF",
              offset: str = "DEF") -> None:
        """APPLy shortcut: set function/freq/amplitude/offset in one command."""
        self._send(
            f"SOUR{channel}:APPL:{function} {frequency},{amplitude},{offset}")

    # -- Arbitrary waveform operations --

    def arb_load(self, channel: int, name: str, data: list[float]) -> None:
        """Load float data points into volatile memory as a named waveform.

        Data values must be in range -1.0 to +1.0 (normalized DAC values).
        Minimum 8 points, maximum 1M points (33521A) or 16M (33522A).
        """
        csv = ",".join(str(v) for v in data)
        self._send(f"SOUR{channel}:DATA:ARB {name},{csv}")

    def arb_clear(self) -> None:
        """Clear all waveforms from volatile memory."""
        self._send("DATA:VOL:CLE")

    def arb_catalog(self) -> str:
        """List available waveforms in volatile memory.

        Returns comma-separated list of waveform names.
        """
        return self._query("DATA:VOL:CAT?").strip()

    def mem_load(self, filename: str) -> None:
        """Load .arb/.barb file from instrument storage into volatile memory."""
        self._send(f'MMEM:LOAD:DATA "{filename}"')

    def mem_store(self, channel: int, filename: str) -> None:
        """Store current channel arb waveform to instrument file."""
        self._send(f'MMEM:STOR:DATA{channel} "{filename}"')

    def mem_catalog(self, directory: str = "") -> str:
        """List files on instrument storage.

        Returns comma-separated catalog of files.
        """
        if directory:
            return self._query(f'MMEM:CAT? "{directory}"').strip()
        return self._query("MMEM:CAT?").strip()

    def mem_delete(self, filename: str) -> None:
        """Delete file from instrument storage."""
        self._send(f'MMEM:DEL "{filename}"')

    # -- Helpers --

    def _parse_resource(self, resource: str) -> tuple[int, str]:
        """Parse 'CH1:FREQ' → (1, 'FREQ'). Raises DriverError."""
        parts = resource.split(":", 1)
        if len(parts) != 2:
            raise DriverError(
                f"invalid resource '{resource}', expected CHn:NAME")

        ch_str, name = parts
        if not ch_str.startswith("CH") or not ch_str[2:].isdigit():
            raise DriverError(
                f"invalid channel '{ch_str}', expected CH1 or CH2")

        channel = int(ch_str[2:])
        if channel < 1 or channel > self._channels:
            raise DriverError(
                f"channel {channel} not available "
                f"(instrument has {self._channels} channel(s))")

        return channel, name
