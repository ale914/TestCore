# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""TCP connection and RESP protocol handling for TestCore client."""

from __future__ import annotations

import json
import socket
from typing import Any

from .exceptions import ProtocolError, raise_for_error


class Connection:
    """Synchronous TCP connection with RESP2 protocol."""

    def __init__(self, host: str, port: int, timeout: float):
        self._host = host
        self._port = port
        self._timeout = timeout
        self._sock: socket.socket | None = None
        self._buffer = bytearray()

    def connect(self):
        """Open TCP connection."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(self._timeout)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock.connect((self._host, self._port))
        self._buffer.clear()

    def close(self):
        """Close TCP connection."""
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._buffer.clear()

    @property
    def connected(self) -> bool:
        return self._sock is not None

    # -- Send --

    def send_command(self, *args: str) -> Any:
        """Send a RESP array command and return the parsed response.

        Raises appropriate exceptions for RESP error responses.
        """
        self._send_packed(self._encode_command(args))
        return self._read_response()

    def send_pipeline(self, commands: list[tuple[str, ...]]) -> list[Any]:
        """Send multiple commands in one TCP write and read all responses.

        Returns list of results. Error responses are returned as
        TestCoreError instances (not raised) so callers can inspect each.
        """
        packed = b"".join(self._encode_command(cmd) for cmd in commands)
        self._send_packed(packed)
        results = []
        for _ in commands:
            try:
                results.append(self._read_response())
            except Exception as e:
                results.append(e)
        return results

    def _encode_command(self, args: tuple[str, ...]) -> bytes:
        """Encode command as RESP array."""
        parts = [f"*{len(args)}\r\n".encode()]
        for arg in args:
            s = arg if isinstance(arg, str) else str(arg)
            data = s.encode("utf-8")
            parts.append(f"${len(data)}\r\n".encode())
            parts.append(data)
            parts.append(b"\r\n")
        return b"".join(parts)

    def _send_packed(self, data: bytes):
        """Send raw bytes over socket."""
        if not self._sock:
            raise ConnectionError("Not connected")
        try:
            self._sock.sendall(data)
        except OSError as e:
            self.close()
            raise ConnectionError(f"Send failed: {e}") from e

    # -- Receive & Parse --

    def _read_response(self) -> Any:
        """Read and parse one RESP response. Raises on errors."""
        result = self._parse_one()
        # Check for error response
        if isinstance(result, str) and result.startswith("ERR_"):
            raise_for_error(result[4:])
        return result

    def _read_raw_response(self) -> Any:
        """Read and parse one RESP response without raising on errors."""
        return self._parse_one()

    def _recv_more(self):
        """Read more data from socket into buffer."""
        if not self._sock:
            raise ConnectionError("Not connected")
        try:
            data = self._sock.recv(4096)
        except socket.timeout:
            raise TimeoutError("Socket read timed out")
        except OSError as e:
            self.close()
            raise ConnectionError(f"Recv failed: {e}") from e
        if not data:
            self.close()
            raise ConnectionError("Connection closed by server")
        self._buffer.extend(data)

    def _parse_one(self) -> Any:
        """Parse a single RESP message, reading more data as needed."""
        while True:
            result, consumed = self._try_parse()
            if consumed > 0:
                del self._buffer[:consumed]
                return result
            self._recv_more()

    def _try_parse(self) -> tuple[Any, int]:
        """Try to parse a RESP message from buffer.

        Returns (result, bytes_consumed) or (None, 0) if incomplete.
        """
        buf = self._buffer
        if not buf:
            return (None, 0)

        tb = buf[0]

        if tb == 43:    # '+' simple string
            return self._parse_simple_string(buf)
        elif tb == 45:  # '-' error
            return self._parse_error(buf)
        elif tb == 58:  # ':' integer
            return self._parse_integer(buf)
        elif tb == 36:  # '$' bulk string
            return self._parse_bulk_string(buf)
        elif tb == 42:  # '*' array
            return self._parse_array(buf, 0)
        else:
            raise ProtocolError(f"Unknown RESP type byte: {chr(tb)}")

    @staticmethod
    def _find_crlf(buf: bytearray, start: int = 0) -> int:
        """Find CRLF in buffer. Returns index or -1."""
        return buf.find(b"\r\n", start)

    @staticmethod
    def _parse_simple_string(buf: bytearray, offset: int = 0) -> tuple[str | None, int]:
        cr = buf.find(b"\r\n", offset + 1)
        if cr == -1:
            return (None, 0)
        return (buf[offset + 1:cr].decode("utf-8"), cr + 2 - offset)

    @staticmethod
    def _parse_error(buf: bytearray, offset: int = 0) -> tuple[str | None, int]:
        cr = buf.find(b"\r\n", offset + 1)
        if cr == -1:
            return (None, 0)
        return (f"ERR_{buf[offset + 1:cr].decode('utf-8')}", cr + 2 - offset)

    @staticmethod
    def _parse_integer(buf: bytearray, offset: int = 0) -> tuple[int | None, int]:
        cr = buf.find(b"\r\n", offset + 1)
        if cr == -1:
            return (None, 0)
        try:
            return (int(buf[offset + 1:cr]), cr + 2 - offset)
        except ValueError as e:
            raise ProtocolError(f"Invalid integer: {e}")

    @staticmethod
    def _parse_bulk_string(buf: bytearray, offset: int = 0) -> tuple[str | None, int]:
        cr = buf.find(b"\r\n", offset + 1)
        if cr == -1:
            return (None, 0)
        try:
            length = int(buf[offset + 1:cr])
        except ValueError as e:
            raise ProtocolError(f"Invalid bulk string length: {e}")
        if length < 0:
            return (None, cr + 2 - offset)
        str_start = cr + 2
        str_end = str_start + length
        if len(buf) < str_end + 2:
            return (None, 0)
        if buf[str_end:str_end + 2] != b"\r\n":
            raise ProtocolError("Missing CRLF after bulk string")
        return (buf[str_start:str_end].decode("utf-8"), str_end + 2 - offset)

    @classmethod
    def _parse_array(cls, buf: bytearray, offset: int) -> tuple[list | None, int]:
        cr = buf.find(b"\r\n", offset + 1)
        if cr == -1:
            return (None, 0)
        try:
            count = int(buf[offset + 1:cr])
        except ValueError as e:
            raise ProtocolError(f"Invalid array count: {e}")
        if count < 0:
            return (None, cr + 2 - offset)

        pos = cr + 2
        items = []
        buf_len = len(buf)

        for _ in range(count):
            if pos >= buf_len:
                return (None, 0)

            tb = buf[pos]

            if tb == 36:  # '$'
                result, consumed = cls._parse_bulk_string(buf, pos)
            elif tb == 58:  # ':'
                result, consumed = cls._parse_integer(buf, pos)
            elif tb == 43:  # '+'
                result, consumed = cls._parse_simple_string(buf, pos)
            elif tb == 45:  # '-'
                result, consumed = cls._parse_error(buf, pos)
            elif tb == 42:  # '*' nested
                result, consumed = cls._parse_array(buf, pos)
            else:
                raise ProtocolError(f"Unexpected type byte in array: {chr(tb)}")

            if consumed == 0:
                return (None, 0)
            items.append(result)
            pos += consumed

        return (items, pos - offset)
