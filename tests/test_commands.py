# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Unit tests for command dispatch."""

import pytest
from testcore.commands import (
    CommandDispatcher,
    handle_ping,
    handle_command,
    dispatcher
)
from testcore.protocol import RESPSerializer, RESPParser


class TestCommandHandlers:
    """Test individual command handlers."""

    @pytest.mark.asyncio
    async def test_handle_ping_no_args(self):
        """Test PING command with no arguments."""
        result = await handle_ping([])
        assert result == RESPSerializer.simple_string("PONG")

    @pytest.mark.asyncio
    async def test_handle_ping_with_args(self):
        """Test PING command echoes message argument (spec §6.1)."""
        result = await handle_ping(["hello"])
        assert result == RESPSerializer.bulk_string("hello")

    @pytest.mark.asyncio
    async def test_handle_ping_multiword(self):
        """Test PING echoes first argument only."""
        result = await handle_ping(["hello", "world"])
        assert result == RESPSerializer.bulk_string("hello")

    @pytest.mark.asyncio
    async def test_handle_cmdlist(self):
        """Test CMDLIST returns list of available commands."""
        result = await handle_command([])
        # Parse the array response
        parser = RESPParser()
        messages = parser.feed(result)

        assert len(messages) == 1
        commands = messages[0]
        assert isinstance(commands, list)
        assert "PING" in commands
        assert "COMMAND LIST" in commands

    @pytest.mark.asyncio
    async def test_handle_cmdlist_sorted(self):
        """Test CMDLIST returns commands in alphabetical order."""
        result = await handle_command([])
        parser = RESPParser()
        messages = parser.feed(result)
        commands = messages[0]

        # Check if sorted
        assert commands == sorted(commands)

    @pytest.mark.asyncio
    async def test_handle_cmdlist_pattern(self):
        """Test CMDLIST with pattern filtering (spec §6.1)."""
        # Filter for commands starting with P
        result = await handle_command(["P*"])
        parser = RESPParser()
        messages = parser.feed(result)
        commands = messages[0]

        assert "PING" in commands
        assert "COMMAND LIST" not in commands  # Doesn't match P*

    @pytest.mark.asyncio
    async def test_handle_cmdlist_pattern_no_match(self):
        """Test CMDLIST pattern with no matches."""
        result = await handle_command(["NONEXISTENT*"])
        parser = RESPParser()
        messages = parser.feed(result)
        commands = messages[0]

        assert commands == []  # Empty list


class TestCommandDispatcher:
    """Test command dispatcher."""

    @pytest.mark.asyncio
    async def test_dispatch_ping(self):
        """Test dispatching PING command."""
        result = await dispatcher.dispatch(["PING"])
        assert result == RESPSerializer.simple_string("PONG")

    @pytest.mark.asyncio
    async def test_dispatch_case_insensitive(self):
        """Test commands are case-insensitive."""
        result1 = await dispatcher.dispatch(["ping"])
        result2 = await dispatcher.dispatch(["PING"])
        result3 = await dispatcher.dispatch(["PiNg"])

        assert result1 == result2 == result3

    @pytest.mark.asyncio
    async def test_dispatch_unknown_command(self):
        """Test unknown command returns error."""
        result = await dispatcher.dispatch(["UNKNOWN"])
        assert result.startswith(b"-ERR unknown command")

    @pytest.mark.asyncio
    async def test_dispatch_empty_command(self):
        """Test empty command returns error."""
        result = await dispatcher.dispatch([])
        assert result.startswith(b"-ERR")

    @pytest.mark.asyncio
    async def test_custom_handler_registration(self):
        """Test registering custom handler."""
        custom_dispatcher = CommandDispatcher()

        async def custom_handler(args, context=None):
            return RESPSerializer.simple_string("CUSTOM")

        custom_dispatcher.register("TEST", custom_handler)
        result = await custom_dispatcher.dispatch(["TEST"])
        assert result == RESPSerializer.simple_string("CUSTOM")

    @pytest.mark.asyncio
    async def test_multiple_commands(self):
        """Test multiple different commands."""
        results = []
        for _ in range(5):
            result = await dispatcher.dispatch(["PING"])
            results.append(result)

        expected = RESPSerializer.simple_string("PONG")
        assert all(r == expected for r in results)
