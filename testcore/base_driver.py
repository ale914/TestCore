# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""BaseDriver ABC and DriverError (spec §7).

BaseDriver defines the TestCore driver contract.
ScpiDriver extends it with common IEEE 488.2 commands (*IDN?, *RST, etc.)
that all SCPI-compliant instruments share.
"""

from __future__ import annotations
from abc import ABC, abstractmethod


class DriverError(Exception):
    """Driver-raised error. Server returns -DRIVER <message>."""
    pass


class BaseDriver(ABC):
    """Abstract base class for all TestCore drivers.

    A driver is a stateless Python module that can be reused across
    multiple instrument instances of the same type/family.
    """

    @abstractmethod
    def connect(self, config: dict) -> None:
        """Thin init: open VISA/TCP connection only.

        Called on INSTRUMENT.ADD. config = parsed JSON (addr, port, etc.).
        Raise DriverError on failure.
        """

    @abstractmethod
    def disconnect(self) -> None:
        """Close connection. Called on INSTRUMENT.REMOVE or server shutdown.

        Must not raise.
        """

    @abstractmethod
    def init(self, selftest: bool = False) -> None:
        """Full instrument initialization (reset to known state).

        Called on INSTRUMENT.INIT. Runs self-test only if selftest=True.
        Raise DriverError on failure.
        """

    @abstractmethod
    def configure(self, config_path: str) -> None:
        """Apply proprietary config file to instrument.

        Called on INSTRUMENT.INIT with config file path.
        Driver reads and interprets the file in its own format.
        Raise DriverError on failure.
        """

    @abstractmethod
    def discover(self) -> list[str]:
        """Return list of available resource names.

        Called after init/configure and on INSTRUMENT.RESOURCES.
        """

    @abstractmethod
    def read(self, resource: str) -> str:
        """Read current value from resource. Return as string.

        Raise DriverError if unknown or hardware error.
        """

    @abstractmethod
    def write(self, resource: str, value: str) -> None:
        """Write value to resource.

        Raise DriverError on failure.
        """

    @abstractmethod
    def passthrough(self, command: str) -> str:
        """Send raw command string to instrument and return response.

        This is the SCPI tunneling escape hatch.
        """

    @abstractmethod
    def load(self, target: str, file_path: str) -> str:
        """Load data from a local file into the instrument.

        Called on ILOAD. The driver reads the file, interprets its format,
        and transfers the data to the instrument. What 'load' means is
        driver-specific:
          - Waveform generator: load CSV of data points as arb waveform
          - Spectrum analyzer: load correction table
          - Power meter: load calibration data

        Args:
            target: Driver-specific destination (e.g. 'CH1:MyWave')
            file_path: Path to the local file to load

        Returns:
            Status string (e.g. 'OK', '1024 points loaded')

        Raise DriverError on failure.
        """

    @abstractmethod
    def save(self, target: str, file_path: str) -> str:
        """Save data from the instrument to a local file.

        Called on ISAVE. The driver retrieves data from the instrument
        and writes it to file_path. What 'save' means is driver-specific:
          - Oscilloscope: save screenshot (PNG/BMP)
          - Spectrum analyzer: save trace data (CSV)
          - Any instrument: save measurement results, calibration, state

        Args:
            target: Driver-specific source (e.g. 'SCREEN', 'TRACE1',
                    'CH1:DATA'). The driver decides which targets it supports.
            file_path: Path to the local file to write

        Returns:
            Status string (e.g. 'OK', '1024 bytes saved', 'screenshot saved')

        Raise DriverError on failure or unsupported target.
        """

    @abstractmethod
    def safe_state(self) -> None:
        """Put instrument in safe/idle state.

        Called on UNLOCK, disconnect, before INSTRUMENT.REMOVE.
        Must not raise.
        """

    @abstractmethod
    def info(self) -> dict:
        """Return instrument metadata.

        Required keys: vendor, model, serial, version.
        """


class ScpiDriver(BaseDriver):
    """Base class for SCPI-compliant instrument drivers.

    Provides IEEE 488.2 common commands (*IDN?, *RST, *TST?, *OPC?, *CLS).
    Transport (send/query) is provided by TestCore via config['transport']
    in connect(). Drivers just call self._send() / self._query().
    """

    def __init__(self):
        self._connected = False
        self._transport = None

    def connect(self, config: dict) -> None:
        """Connect using transport provided by TestCore.

        Raises DriverError if no transport is provided — SCPI drivers
        require a connection (VISA, TCP, or serial).
        """
        self._transport = config.get("transport")
        if self._transport is None:
            raise DriverError(
                "no transport: IADD requires an address for this driver")
        self._connected = True

    def disconnect(self) -> None:
        """Close transport connection."""
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:
                pass
            self._transport = None
        self._connected = False

    def _send(self, command: str) -> None:
        """Send a SCPI command (no response expected)."""
        if self._transport is None:
            raise DriverError("not connected")
        self._transport.send(command)

    def _query(self, command: str) -> str:
        """Send a SCPI query and return the response string."""
        if self._transport is None:
            raise DriverError("not connected")
        return self._transport.query(command)

    # -- IEEE 488.2 Common Commands --

    def idn(self) -> str:
        """*IDN? — Query instrument identification string."""
        return self._query("*IDN?")

    def rst(self) -> None:
        """*RST — Reset instrument to factory default state."""
        self._send("*RST")

    def cls(self) -> None:
        """*CLS — Clear status registers and error queue."""
        self._send("*CLS")

    def opc(self) -> str:
        """*OPC? — Query operation complete (returns '1' when done)."""
        return self._query("*OPC?")

    def tst(self) -> str:
        """*TST? — Run self-test and return result ('0' = pass)."""
        return self._query("*TST?")

    def error(self) -> str:
        """SYST:ERR? — Query next error from the error queue."""
        return self._query("SYST:ERR?")

    # -- BaseDriver contract: default SCPI implementations --

    def load(self, target: str, file_path: str) -> str:
        """Default: not supported. Override in drivers that support file loading."""
        raise DriverError("load not supported by this driver")

    def save(self, target: str, file_path: str) -> str:
        """Default: not supported. Override in drivers that support file saving."""
        raise DriverError("save not supported by this driver")

    def init(self, selftest: bool = False) -> None:
        """Reset instrument and clear errors. Optionally run self-test."""
        self.rst()
        self.cls()
        if selftest:
            result = self.tst()
            if result.strip() != "0":
                raise DriverError(f"self-test failed: {result}")

    def passthrough(self, command: str) -> str:
        """SCPI tunneling: send raw command, return response if query."""
        if command.rstrip().endswith("?"):
            return self._query(command)
        self._send(command)
        return "OK"

    def safe_state(self) -> None:
        """Reset instrument to safe defaults."""
        try:
            self.rst()
        except Exception:
            pass

    def info(self) -> dict:
        """Parse *IDN? response into vendor/model/serial/version."""
        try:
            idn = self.idn()
            parts = [p.strip() for p in idn.split(",")]
            return {
                "vendor": parts[0] if len(parts) > 0 else "unknown",
                "model": parts[1] if len(parts) > 1 else "unknown",
                "serial": parts[2] if len(parts) > 2 else "unknown",
                "version": parts[3] if len(parts) > 3 else "unknown",
            }
        except Exception:
            return {
                "vendor": "unknown", "model": "unknown",
                "serial": "unknown", "version": "unknown",
            }
