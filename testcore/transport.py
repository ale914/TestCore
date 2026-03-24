# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Transport layer for instrument communication.

TestCore manages transport on behalf of drivers. The driver receives
a ready-to-use transport object with send()/query()/close() methods.

Address routing:
    TCPIP0::192.168.1.50::inst0::INSTR  → VisaTransport
    GPIB0::22::INSTR                    → VisaTransport
    USB0::...::INSTR                    → VisaTransport
    TCPIP::192.168.1.50::5025           → SocketTransport (raw SCPI)
    COM3                                → SerialTransport
    (none)                              → None (dryrun, no transport)
"""

from __future__ import annotations
import socket
from abc import ABC, abstractmethod
from .base_driver import DriverError


class Transport(ABC):
    """Base interface for all instrument transports."""

    @abstractmethod
    def send(self, command: str) -> None:
        """Send a command (no response expected)."""

    @abstractmethod
    def query(self, command: str) -> str:
        """Send a command and return the response."""

    @abstractmethod
    def close(self) -> None:
        """Close the connection."""


class VisaTransport(Transport):
    """VISA transport via pyvisa (GPIB, USB, LAN, serial)."""

    def __init__(self, address: str, timeout: int = 5000, **_kwargs):
        try:
            import pyvisa
        except ImportError:
            raise DriverError(
                "pyvisa not installed — run: pip install pyvisa pyvisa-py")

        try:
            rm = pyvisa.ResourceManager()
            self._inst = rm.open_resource(address)
            self._inst.timeout = timeout
        except Exception as e:
            raise DriverError(f"VISA open failed for '{address}': {e}")

    def send(self, command: str) -> None:
        self._inst.write(command)

    def query(self, command: str) -> str:
        return self._inst.query(command)

    def close(self) -> None:
        try:
            self._inst.close()
        except Exception:
            pass


class SocketTransport(Transport):
    """Raw SCPI over TCP socket (e.g. LXI port 5025)."""

    def __init__(self, host: str, port: int = 5025, timeout: float = 5.0,
                 **_kwargs):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(timeout)
            self._sock.connect((host, port))
        except Exception as e:
            raise DriverError(f"TCP connect failed for {host}:{port}: {e}")

    def send(self, command: str) -> None:
        self._sock.sendall((command + "\n").encode())

    def query(self, command: str) -> str:
        # Flush any stale data in the receive buffer before sending
        self._sock.setblocking(False)
        try:
            while True:
                self._sock.recv(4096)
        except BlockingIOError:
            pass  # buffer is clean
        finally:
            self._sock.setblocking(True)

        self.send(command)

        # Read until newline terminator (standard SCPI response termination)
        data = b""
        while True:
            chunk = self._sock.recv(4096)
            if not chunk:
                break
            data += chunk
            # Take only the first complete line (up to \n)
            if b"\n" in data:
                # Return first line, discard any trailing data
                line, _, _ = data.partition(b"\n")
                return line.decode().strip()
        return data.decode().strip()

    def close(self) -> None:
        try:
            self._sock.close()
        except Exception:
            pass


class SerialTransport(Transport):
    """Serial port transport via pyserial."""

    def __init__(self, port: str, baudrate: int = 9600, timeout: float = 5.0,
                 bytesize: int = 8, parity: str = "N", stopbits: int = 1,
                 **_kwargs):
        try:
            import serial
        except ImportError:
            raise DriverError(
                "pyserial not installed — run: pip install pyserial")

        try:
            self._ser = serial.Serial(
                port=port, baudrate=baudrate, timeout=timeout,
                bytesize=bytesize, parity=parity, stopbits=stopbits)
        except Exception as e:
            raise DriverError(f"serial open failed for {port}: {e}")

    def send(self, command: str) -> None:
        self._ser.write((command + "\n").encode())

    def query(self, command: str) -> str:
        self.send(command)
        return self._ser.readline().decode().strip()

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:
            pass


def resolve_transport(address: str, **kwargs) -> Transport:
    """Create the right transport from an address string.

    Optional kwargs override transport defaults:
        timeout     — timeout in ms (VISA) or seconds (socket/serial)
        baudrate    — serial baud rate (default 9600)
        parity      — serial parity (default none)
        stopbits    — serial stop bits (default 1)
        bytesize    — serial byte size (default 8)

    Routing rules:
        *::INSTR or *::SOCKET   → VisaTransport
        GPIB*                   → VisaTransport
        USB*                    → VisaTransport
        COM*                    → SerialTransport
        host::port (no INSTR)   → SocketTransport (raw TCP)
    """
    upper = address.upper()

    # VISA resource strings
    if upper.endswith("::INSTR") or upper.endswith("::SOCKET"):
        return VisaTransport(address, **kwargs)
    if upper.startswith("GPIB") or upper.startswith("USB"):
        return VisaTransport(address, **kwargs)

    # Serial port
    if upper.startswith("COM") or upper.startswith("/DEV/TTY"):
        return SerialTransport(address, **kwargs)

    # Raw TCP: "host::port" or "host:port"
    # TCPIP::host::port format (without ::INSTR)
    if upper.startswith("TCPIP"):
        # Parse TCPIP::host::port
        parts = address.split("::")
        if len(parts) >= 3:
            host = parts[1]
            try:
                port = int(parts[2])
                return SocketTransport(host, port, **kwargs)
            except ValueError:
                pass
        # Fall through to VISA for non-standard TCPIP strings
        return VisaTransport(address, **kwargs)

    # Plain host:port
    if ":" in address:
        host, _, port_str = address.rpartition(":")
        try:
            return SocketTransport(host, int(port_str), **kwargs)
        except ValueError:
            pass

    # Default: try VISA
    return VisaTransport(address, **kwargs)
