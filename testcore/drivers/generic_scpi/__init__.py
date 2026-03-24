# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Generic SCPI driver — connect any SCPI instrument without a custom driver.

No mapped resources. Use IRAW for all communication.
IEEE 488.2 commands (*IDN?, *RST, *CLS, *TST?, *OPC?) via ScpiDriver base.

Usage:
    IADD multimeter generic_scpi TCPIP::192.168.1.50::5025
    ILOCK multimeter
    IINIT multimeter
    IRAW multimeter MEAS:VOLT:DC?
    IRAW multimeter CONF:RES 1000
    IUNLOCK multimeter
"""

from testcore import ScpiDriver, DriverError


class GenericScpiDriver(ScpiDriver):
    """Passthrough-only SCPI driver.

    All communication goes through IRAW (passthrough).
    No resources are mapped — IREAD/IWRITE return 'unknown resource'.
    Useful for quick instrument access without writing a full driver.
    """

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
        """No mapped resources — returns empty list."""
        return []

    def read(self, resource: str) -> str:
        """Not supported — use IRAW instead."""
        raise DriverError(
            f"unknown resource '{resource}' (generic_scpi has no mapped "
            f"resources, use IRAW for direct SCPI commands)")

    def write(self, resource: str, value: str) -> None:
        """Not supported — use IRAW instead."""
        raise DriverError(
            f"unknown resource '{resource}' (generic_scpi has no mapped "
            f"resources, use IRAW for direct SCPI commands)")
