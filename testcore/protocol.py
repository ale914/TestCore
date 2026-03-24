# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""RESP (Redis Serialization Protocol) parser and serializer."""

from __future__ import annotations


class RESPProtocolError(Exception):
    """RESP protocol parsing error."""
    pass


class RESPParser:
    """
    Stateful RESP protocol parser with inline command support.

    Supports:
    - RESP Protocol:
      - Simple Strings (+OK\r\n)
      - Bulk Strings ($4\r\nPING\r\n)
      - Arrays (*1\r\n$4\r\nPING\r\n)
      - Errors (-ERR message\r\n)
    - Inline Commands (Redis-compatible):
      - Plain text commands (PING, GET key, SET key value)
    """

    # 1 MB default buffer limit — protects against malformed/malicious clients
    MAX_BUFFER_SIZE = 1024 * 1024

    def __init__(self, max_buffer_size: int | None = None):
        self.buffer = bytearray()
        self._max_buffer_size = max_buffer_size or self.MAX_BUFFER_SIZE

    def feed(self, data: bytes) -> list[object]:
        """
        Feed data and return list of complete parsed messages.

        Args:
            data: Raw bytes from socket

        Returns:
            List of parsed messages

        Raises:
            RESPProtocolError: On protocol violations or buffer overflow
        """
        self.buffer.extend(data)

        if len(self.buffer) > self._max_buffer_size:
            self.buffer.clear()
            raise RESPProtocolError(
                f"buffer overflow ({self._max_buffer_size} bytes)")

        messages = []

        while True:
            result, consumed = self._parse_message()
            if consumed == 0:
                break  # Need more data
            messages.append(result)
            del self.buffer[:consumed]

        return messages

    def _parse_message(self) -> tuple[object | None, int]:
        """
        Parse single message (RESP or inline command).

        Returns:
            (message, bytes_consumed) or (None, 0) if incomplete
        """
        if not self.buffer:
            return (None, 0)

        type_byte = self.buffer[0]

        # RESP protocol - starts with type markers (compare raw bytes)
        if type_byte == 42:    # b'*'
            return self._parse_array()
        elif type_byte == 36:  # b'$'
            return self._parse_bulk_string()
        elif type_byte == 43:  # b'+'
            return self._parse_simple_string()
        elif type_byte == 45:  # b'-'
            return self._parse_error()
        elif type_byte == 58:  # b':'
            return self._parse_integer()
        else:
            # Not a RESP type marker - treat as inline command
            return self._parse_inline_command()

    def _find_crlf(self, start: int = 0) -> int | None:
        """Find next CRLF, return index or None."""
        idx = self.buffer.find(b'\r\n', start)
        return idx if idx != -1 else None

    def _find_newline(self, start: int = 0) -> tuple[int | None, int]:
        """
        Find next newline (\r\n or \n).
        Returns (index, length) where length is 1 for \n or 2 for \r\n.
        """
        # Try CRLF first
        idx = self.buffer.find(b'\r\n', start)
        if idx != -1:
            return (idx, 2)

        # Try LF alone
        idx = self.buffer.find(b'\n', start)
        if idx != -1:
            return (idx, 1)

        return (None, 0)

    def _parse_inline_command(self) -> tuple[list[str] | None, int]:
        """
        Parse inline command (Redis-compatible).
        Format: PING or GET key or SET key "hello world"
        Terminated by \r\n or \n
        Supports double-quoted arguments to preserve spaces.
        Returns array of command parts (like RESP array).
        """
        newline_idx, newline_len = self._find_newline()
        if newline_idx is None:
            return (None, 0)

        # Extract command line
        command_line = self.buffer[:newline_idx].decode('utf-8').strip()

        if not command_line:
            # Empty line - skip it
            return ([], newline_idx + newline_len)

        # Split respecting double-quoted strings
        parts = []
        i = 0
        n = len(command_line)
        while i < n:
            # Skip whitespace
            while i < n and command_line[i] in ' \t':
                i += 1
            if i >= n:
                break

            if command_line[i] == '"':
                # Quoted argument — collect until closing quote
                i += 1  # skip opening quote
                start = i
                while i < n and command_line[i] != '"':
                    i += 1
                parts.append(command_line[start:i])
                if i < n:
                    i += 1  # skip closing quote
            else:
                # Unquoted argument — collect until whitespace
                start = i
                while i < n and command_line[i] not in ' \t':
                    i += 1
                parts.append(command_line[start:i])

        return (parts, newline_idx + newline_len)

    def _parse_integer(self) -> tuple[int | None, int]:
        """Parse :1000\r\n format."""
        crlf = self._find_crlf()
        if crlf is None:
            return (None, 0)

        try:
            value = int(self.buffer[1:crlf])
        except ValueError as e:
            raise RESPProtocolError(f"Invalid integer: {e}")

        return (value, crlf + 2)

    def _parse_simple_string(self) -> tuple[str | None, int]:
        """Parse +OK\r\n format."""
        crlf = self._find_crlf()
        if crlf is None:
            return (None, 0)

        value = self.buffer[1:crlf].decode('utf-8')
        return (value, crlf + 2)

    def _parse_error(self) -> tuple[str | None, int]:
        """Parse -ERR message\r\n format."""
        crlf = self._find_crlf()
        if crlf is None:
            return (None, 0)

        # Return error as string (prefix with ERR_ to distinguish from simple strings)
        value = self.buffer[1:crlf].decode('utf-8')
        return (f"ERR_{value}", crlf + 2)

    def _parse_bulk_string(self) -> tuple[str | None, int]:
        """Parse $4\r\nPING\r\n format."""
        # Parse length line
        crlf = self._find_crlf()
        if crlf is None:
            return (None, 0)

        try:
            length = int(self.buffer[1:crlf])
        except ValueError as e:
            raise RESPProtocolError(f"Invalid bulk string length: {e}")

        if length < 0:
            # Null bulk string
            return (None, crlf + 2)

        # Check if we have full string
        str_start = crlf + 2
        str_end = str_start + length
        if len(self.buffer) < str_end + 2:  # +2 for trailing \r\n
            return (None, 0)

        # Verify trailing CRLF
        if self.buffer[str_end:str_end + 2] != b'\r\n':
            raise RESPProtocolError("Missing CRLF after bulk string")

        value = self.buffer[str_start:str_end].decode('utf-8')
        return (value, str_end + 2)

    def _parse_array(self) -> tuple[list | None, int]:
        """Parse *1\r\n$4\r\nPING\r\n format."""
        buf = self.buffer
        # Parse count line
        crlf = buf.find(b'\r\n')
        if crlf == -1:
            return (None, 0)

        try:
            count = int(buf[1:crlf])
        except ValueError as e:
            raise RESPProtocolError(f"Invalid array count: {e}")

        if count < 0:
            return (None, crlf + 2)

        pos = crlf + 2
        items = []
        buf_len = len(buf)

        for _ in range(count):
            if pos >= buf_len:
                return (None, 0)

            tb = buf[pos]

            if tb == 36:  # '$' bulk string
                cr = buf.find(b'\r\n', pos + 1)
                if cr == -1:
                    return (None, 0)
                try:
                    length = int(buf[pos + 1:cr])
                except ValueError as e:
                    raise RESPProtocolError(f"Invalid bulk string length: {e}")
                if length < 0:
                    items.append(None)
                    pos = cr + 2
                    continue
                str_start = cr + 2
                str_end = str_start + length
                if buf_len < str_end + 2:
                    return (None, 0)
                if buf[str_end:str_end + 2] != b'\r\n':
                    raise RESPProtocolError("Missing CRLF after bulk string")
                items.append(buf[str_start:str_end].decode('utf-8'))
                pos = str_end + 2

            elif tb == 58:  # ':' integer
                cr = buf.find(b'\r\n', pos + 1)
                if cr == -1:
                    return (None, 0)
                try:
                    items.append(int(buf[pos + 1:cr]))
                except ValueError as e:
                    raise RESPProtocolError(f"Invalid integer: {e}")
                pos = cr + 2

            elif tb == 43:  # '+' simple string
                cr = buf.find(b'\r\n', pos + 1)
                if cr == -1:
                    return (None, 0)
                items.append(buf[pos + 1:cr].decode('utf-8'))
                pos = cr + 2

            elif tb == 45:  # '-' error
                cr = buf.find(b'\r\n', pos + 1)
                if cr == -1:
                    return (None, 0)
                items.append(f"ERR_{buf[pos + 1:cr].decode('utf-8')}")
                pos = cr + 2

            elif tb == 42:  # '*' nested array — fallback to recursive
                saved = self.buffer
                self.buffer = buf[pos:]
                item, consumed = self._parse_array()
                self.buffer = saved
                if consumed == 0:
                    return (None, 0)
                items.append(item)
                pos += consumed

            else:
                raise RESPProtocolError(
                    f"Unexpected type byte in array: {chr(tb)}")

        return (items, pos)


class RESPSerializer:
    """RESP protocol serializer."""

    # Pre-encoded constants
    _OK = b"+OK\r\n"
    _PONG = b"+PONG\r\n"
    _NULL = b"$-1\r\n"
    _CRLF = b"\r\n"
    _ZERO = b":0\r\n"
    _ONE = b":1\r\n"

    @staticmethod
    def simple_string(s: str) -> bytes:
        """Encode +OK\r\n format."""
        if s == "OK":
            return RESPSerializer._OK
        if s == "PONG":
            return RESPSerializer._PONG
        return b"+" + s.encode('utf-8') + b"\r\n"

    @staticmethod
    def bulk_string(s: str) -> bytes:
        """Encode $4\r\nPING\r\n format."""
        data = s.encode('utf-8')
        return f"${len(data)}\r\n".encode('utf-8') + data + b"\r\n"

    @staticmethod
    def array(items: list) -> bytes:
        """Encode array of items."""
        parts = [f"*{len(items)}\r\n".encode('utf-8')]
        _bs = RESPSerializer.bulk_string
        _int = RESPSerializer.integer
        _null = RESPSerializer._NULL
        for item in items:
            if item is None:
                parts.append(_null)
            elif isinstance(item, str):
                parts.append(_bs(item))
            elif isinstance(item, int):
                parts.append(_int(item))
            else:
                raise ValueError(f"Unsupported array item type: {type(item)}")
        return b"".join(parts)

    @staticmethod
    def error(msg: str) -> bytes:
        """Encode -ERR message\r\n format."""
        return f"-ERR {msg}\r\n".encode('utf-8')

    @staticmethod
    def integer(n: int) -> bytes:
        """Encode :1000\r\n format."""
        if n == 0:
            return RESPSerializer._ZERO
        if n == 1:
            return RESPSerializer._ONE
        return f":{n}\r\n".encode('utf-8')

    @staticmethod
    def null() -> bytes:
        """Encode $-1\r\n format (nil)."""
        return RESPSerializer._NULL
