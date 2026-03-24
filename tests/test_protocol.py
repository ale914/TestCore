# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Unit tests for RESP protocol parsing and serialization."""

import pytest
from testcore.protocol import (
    RESPParser,
    RESPSerializer,
    RESPProtocolError
)


class TestRESPSerializer:
    """Test RESP serialization."""

    def test_simple_string(self):
        """Test simple string encoding."""
        result = RESPSerializer.simple_string("PONG")
        assert result == b"+PONG\r\n"

    def test_bulk_string(self):
        """Test bulk string encoding."""
        result = RESPSerializer.bulk_string("PING")
        assert result == b"$4\r\nPING\r\n"

    def test_bulk_string_empty(self):
        """Test empty bulk string."""
        result = RESPSerializer.bulk_string("")
        assert result == b"$0\r\n\r\n"

    def test_array_single_element(self):
        """Test array with single element."""
        result = RESPSerializer.array(["PING"])
        assert result == b"*1\r\n$4\r\nPING\r\n"

    def test_array_multiple_elements(self):
        """Test array with multiple elements."""
        result = RESPSerializer.array(["SET", "key", "value"])
        expected = b"*3\r\n$3\r\nSET\r\n$3\r\nkey\r\n$5\r\nvalue\r\n"
        assert result == expected

    def test_error(self):
        """Test error encoding."""
        result = RESPSerializer.error("unknown command")
        assert result == b"-ERR unknown command\r\n"

    def test_integer(self):
        """Test integer encoding."""
        result = RESPSerializer.integer(1000)
        assert result == b":1000\r\n"


class TestRESPParser:
    """Test RESP parsing."""

    def test_simple_string(self):
        """Test simple string parsing."""
        parser = RESPParser()
        messages = parser.feed(b"+PONG\r\n")
        assert messages == ["PONG"]

    def test_bulk_string(self):
        """Test bulk string parsing."""
        parser = RESPParser()
        messages = parser.feed(b"$4\r\nPING\r\n")
        assert messages == ["PING"]

    def test_array_single_element(self):
        """Test array with single element."""
        parser = RESPParser()
        messages = parser.feed(b"*1\r\n$4\r\nPING\r\n")
        assert messages == [["PING"]]

    def test_array_multiple_elements(self):
        """Test array with multiple elements."""
        parser = RESPParser()
        messages = parser.feed(b"*3\r\n$3\r\nSET\r\n$3\r\nkey\r\n$5\r\nvalue\r\n")
        assert messages == [["SET", "key", "value"]]

    def test_incomplete_data(self):
        """Test handling of incomplete data."""
        parser = RESPParser()

        # Feed partial simple string
        messages = parser.feed(b"+PON")
        assert messages == []

        # Complete the message
        messages = parser.feed(b"G\r\n")
        assert messages == ["PONG"]

    def test_incomplete_bulk_string(self):
        """Test incomplete bulk string."""
        parser = RESPParser()

        # Feed length only
        messages = parser.feed(b"$4\r\n")
        assert messages == []

        # Feed partial content
        messages = parser.feed(b"PI")
        assert messages == []

        # Complete
        messages = parser.feed(b"NG\r\n")
        assert messages == ["PING"]

    def test_multiple_messages(self):
        """Test multiple messages in single feed."""
        parser = RESPParser()
        messages = parser.feed(b"+PONG\r\n+OK\r\n")
        assert messages == ["PONG", "OK"]

    def test_invalid_type_byte(self):
        """Test that non-RESP data is parsed as inline command."""
        parser = RESPParser()
        # This is now valid - treated as inline command
        messages = parser.feed(b"INVALID\r\n")
        assert messages == [["INVALID"]]

    def test_ping_command_realistic(self):
        """Test realistic PING command from client."""
        parser = RESPParser()
        # This is what a real Redis client sends
        messages = parser.feed(b"*1\r\n$4\r\nPING\r\n")
        assert messages == [["PING"]]

    def test_empty_bulk_string(self):
        """Test empty bulk string."""
        parser = RESPParser()
        messages = parser.feed(b"$0\r\n\r\n")
        assert messages == [""]

    def test_error_message(self):
        """Test error message parsing."""
        parser = RESPParser()
        messages = parser.feed(b"-ERR unknown command\r\n")
        assert messages == ["ERR_ERR unknown command"]

    def test_incomplete_array(self):
        """Test incomplete array."""
        parser = RESPParser()

        # Feed array header and first element
        messages = parser.feed(b"*2\r\n$4\r\nPING\r\n")
        assert messages == []

        # Complete second element
        messages = parser.feed(b"$4\r\ntest\r\n")
        assert messages == [["PING", "test"]]


class TestInlineCommands:
    """Test inline command support (Redis-compatible)."""

    def test_inline_ping(self):
        """Test inline PING command."""
        parser = RESPParser()
        messages = parser.feed(b"PING\r\n")
        assert messages == [["PING"]]

    def test_inline_ping_lf_only(self):
        """Test inline PING with LF only."""
        parser = RESPParser()
        messages = parser.feed(b"PING\n")
        assert messages == [["PING"]]

    def test_inline_get(self):
        """Test inline GET command with argument."""
        parser = RESPParser()
        messages = parser.feed(b"GET mykey\r\n")
        assert messages == [["GET", "mykey"]]

    def test_inline_set(self):
        """Test inline SET command with multiple arguments."""
        parser = RESPParser()
        messages = parser.feed(b"SET mykey myvalue\r\n")
        assert messages == [["SET", "mykey", "myvalue"]]

    def test_inline_lowercase(self):
        """Test inline commands are case-preserved."""
        parser = RESPParser()
        messages = parser.feed(b"ping\r\n")
        assert messages == [["ping"]]

    def test_inline_mixed_case(self):
        """Test inline commands with mixed case."""
        parser = RESPParser()
        messages = parser.feed(b"GeT MyKey\r\n")
        assert messages == [["GeT", "MyKey"]]

    def test_inline_multiple_commands(self):
        """Test multiple inline commands."""
        parser = RESPParser()
        messages = parser.feed(b"PING\r\nGET key\r\n")
        assert messages == [["PING"], ["GET", "key"]]

    def test_inline_empty_line(self):
        """Test empty lines are handled gracefully."""
        parser = RESPParser()
        messages = parser.feed(b"\r\n")
        assert messages == [[]]

    def test_inline_whitespace_only(self):
        """Test whitespace-only lines."""
        parser = RESPParser()
        messages = parser.feed(b"   \r\n")
        assert messages == [[]]

    def test_inline_extra_spaces(self):
        """Test inline command with extra spaces."""
        parser = RESPParser()
        messages = parser.feed(b"GET   key   \r\n")
        assert messages == [["GET", "key"]]

    def test_inline_incomplete(self):
        """Test incomplete inline command."""
        parser = RESPParser()
        messages = parser.feed(b"PING")
        assert messages == []

        # Complete it
        messages = parser.feed(b"\r\n")
        assert messages == [["PING"]]

    def test_mixed_resp_and_inline(self):
        """Test mixing RESP and inline commands."""
        parser = RESPParser()

        # Inline command
        messages = parser.feed(b"PING\r\n")
        assert messages == [["PING"]]

        # RESP command
        messages = parser.feed(b"*1\r\n$4\r\nPING\r\n")
        assert messages == [["PING"]]

        # Both should be treated the same
        assert True  # Just verify no errors
