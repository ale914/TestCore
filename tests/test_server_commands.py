# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Tests for Server Introspection Commands (INFO, TIME, CLIENTID, CLIENTLIST, CLIENTNAME, MONITOR)."""

import asyncio
import time
from unittest.mock import MagicMock, AsyncMock
import pytest
from testcore.commands import (
    handle_info, handle_time, handle_clientid, handle_clientlist,
    handle_clientname, handle_monitor, dispatcher
)
from testcore.store import get_store
from testcore.protocol import RESPParser
from testcore.server import TestCoreServer, ClientHandler, get_server


def ctx(session_id, handler=None):
    c = {"session_id": session_id}
    if handler is not None:
        c["client_handler"] = handler
    return c


def make_mock_handler(client_id=1, address="127.0.0.1:50000", name=None,
                      cmd_count=0, connect_time=None):
    """Create a mock ClientHandler with required attributes."""
    handler = MagicMock(spec=ClientHandler)
    handler.client_id = client_id
    handler.address = address
    handler.name = name
    handler.cmd_count = cmd_count
    handler.connect_time = connect_time or time.time()
    handler.running = True
    handler.monitoring = False
    handler._write = AsyncMock()
    return handler


@pytest.fixture(autouse=True)
def reset_store():
    """Reset store and instrument registry before each test."""
    store = get_store()
    store._data.clear()
    from testcore.instruments import get_registry
    registry = get_registry()
    for name in list(registry._instruments.keys()):
        try:
            registry.remove(name)
        except Exception:
            pass
    registry._instruments.clear()


@pytest.fixture
def mock_server():
    """Create a TestCoreServer instance (sets global singleton)."""
    server = TestCoreServer("127.0.0.1", 6399)
    yield server


class TestInfoCommand:
    """Tests for INFO command."""

    @pytest.mark.asyncio
    async def test_info_all_sections(self, mock_server):
        response = await handle_info([])
        parser = RESPParser()
        messages = parser.feed(response)
        result = messages[0]

        assert "# Server" in result
        assert "# Clients" in result
        assert "# Store" in result
        assert "# Instruments" in result
        assert "version:" in result

    @pytest.mark.asyncio
    async def test_info_server_section(self, mock_server):
        response = await handle_info(["server"])
        parser = RESPParser()
        messages = parser.feed(response)
        result = messages[0]

        assert "# Server" in result
        from testcore import __version__
        assert f"version:{__version__}" in result
        assert "uptime_seconds:" in result
        assert "host:127.0.0.1" in result
        assert "port:6399" in result
        # Should NOT contain other sections
        assert "# Store" not in result

    @pytest.mark.asyncio
    async def test_info_store_section(self, mock_server):
        store = get_store()
        store.set("k1", "v1")
        store.set("k2", "v2")

        response = await handle_info(["store"])
        parser = RESPParser()
        messages = parser.feed(response)
        result = messages[0]

        assert "# Store" in result
        assert "keys:2" in result
        assert "monitor_clients:" in result

    @pytest.mark.asyncio
    async def test_info_instruments_section(self, mock_server):
        response = await handle_info(["instruments"])
        parser = RESPParser()
        messages = parser.feed(response)
        result = messages[0]

        assert "# Instruments" in result
        assert "instrument_count:0" in result
        assert "locked_count:0" in result

    @pytest.mark.asyncio
    async def test_info_invalid_section(self, mock_server):
        response = await handle_info(["nonexistent"])
        assert response.startswith(b"-ERR")
        assert b"invalid INFO section" in response

    @pytest.mark.asyncio
    async def test_info_clients_with_connections(self, mock_server):
        h1 = make_mock_handler(1, cmd_count=5)
        h2 = make_mock_handler(2, cmd_count=10)
        mock_server.client_handlers = {1: h1, 2: h2}
        mock_server.total_commands = 15

        response = await handle_info(["clients"])
        parser = RESPParser()
        messages = parser.feed(response)
        result = messages[0]

        assert "connected_clients:2" in result
        assert "total_commands_processed:15" in result


class TestTimeCommand:
    """Tests for TIME command."""

    @pytest.mark.asyncio
    async def test_time_returns_two_elements(self):
        response = await handle_time([])
        parser = RESPParser()
        messages = parser.feed(response)
        result = messages[0]

        assert isinstance(result, list)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_time_values_reasonable(self):
        response = await handle_time([])
        parser = RESPParser()
        messages = parser.feed(response)
        result = messages[0]

        seconds = int(result[0])
        microseconds = int(result[1])

        assert seconds > 1_000_000_000  # After year 2001
        assert 0 <= microseconds < 1_000_000


class TestClientIdCommand:
    """Tests for CLIENTID command."""

    @pytest.mark.asyncio
    async def test_clientid_returns_session(self):
        response = await handle_clientid([], ctx(42))
        assert response == b':42\r\n'

    @pytest.mark.asyncio
    async def test_clientid_no_session(self):
        response = await handle_clientid([])
        assert b'no session' in response


class TestClientListCommand:
    """Tests for CLIENTLIST command."""

    @pytest.mark.asyncio
    async def test_clientlist_empty(self, mock_server):
        response = await handle_clientlist([])
        parser = RESPParser()
        messages = parser.feed(response)
        assert messages[0] == ""

    @pytest.mark.asyncio
    async def test_clientlist_with_clients(self, mock_server):
        h1 = make_mock_handler(1, "10.0.0.1:5000", cmd_count=3)
        h2 = make_mock_handler(2, "10.0.0.2:6000", name="tester", cmd_count=7)
        mock_server.client_handlers = {1: h1, 2: h2}

        response = await handle_clientlist([])
        parser = RESPParser()
        messages = parser.feed(response)
        result = messages[0]

        assert "id=1" in result
        assert "addr=10.0.0.1:5000" in result
        assert "cmd=3" in result
        assert "id=2" in result
        assert "name=tester" in result
        assert "cmd=7" in result

    @pytest.mark.asyncio
    async def test_clientlist_shows_age(self, mock_server):
        h1 = make_mock_handler(1, connect_time=time.time() - 10)
        mock_server.client_handlers = {1: h1}

        response = await handle_clientlist([])
        parser = RESPParser()
        messages = parser.feed(response)
        result = messages[0]

        assert "age=10" in result or "age=11" in result


class TestClientNameCommand:
    """Tests for CLIENTNAME command."""

    @pytest.mark.asyncio
    async def test_clientname_get_no_name(self):
        handler = make_mock_handler(1)
        handler.name = None
        response = await handle_clientname([], ctx(1, handler))
        parser = RESPParser()
        messages = parser.feed(response)
        assert messages[0] is None  # nil

    @pytest.mark.asyncio
    async def test_clientname_set(self):
        handler = make_mock_handler(1)
        handler.name = None
        response = await handle_clientname(["test_script"], ctx(1, handler))
        assert response == b'+OK\r\n'
        assert handler.name == "test_script"

    @pytest.mark.asyncio
    async def test_clientname_get_after_set(self):
        handler = make_mock_handler(1)
        handler.name = None
        await handle_clientname(["my_session"], ctx(1, handler))
        response = await handle_clientname([], ctx(1, handler))
        parser = RESPParser()
        messages = parser.feed(response)
        assert messages[0] == "my_session"

    @pytest.mark.asyncio
    async def test_clientname_no_session(self):
        response = await handle_clientname([])
        assert b'no session' in response


class TestMonitorCommand:
    """Tests for MONITOR command."""

    @pytest.mark.asyncio
    async def test_monitor_returns_ok(self, mock_server):
        handler = make_mock_handler(1)
        response = await handle_monitor([], ctx(1, handler))
        assert response == b'+OK\r\n'

    @pytest.mark.asyncio
    async def test_monitor_adds_to_set(self, mock_server):
        """MONITOR sets _pending_monitor flag (actual registration is deferred
        to _process_message, after +OK is sent to the client)."""
        handler = make_mock_handler(1)
        await handle_monitor([], ctx(1, handler))
        # Registration is deferred — handler is NOT yet in monitors
        assert handler not in mock_server.monitors
        assert handler._pending_monitor is True

    @pytest.mark.asyncio
    async def test_monitor_no_session(self):
        response = await handle_monitor([])
        assert b'no session' in response

    @pytest.mark.asyncio
    async def test_monitor_receives_commands(self, mock_server):
        """MONITOR client receives streamed commands from dispatch."""
        monitor_handler = make_mock_handler(10, "10.0.0.1:9000")
        mock_server.monitors.add(monitor_handler)

        # Another client dispatches a command
        client_handler = make_mock_handler(1, "127.0.0.1:5000")
        await dispatcher.dispatch(
            ["PING"], ctx(1, client_handler))

        # Monitor should have received a write
        monitor_handler._write.assert_called_once()
        data = monitor_handler._write.call_args[0][0]
        text = data.decode()

        # TestCore MONITOR format: +timestamp [#id_or_name] "cmd" "arg"...
        assert text.startswith("+")
        assert "[#1]" in text
        assert '"PING"' in text
        assert text.endswith("\r\n")

    @pytest.mark.asyncio
    async def test_monitor_receives_args(self, mock_server):
        """MONITOR output includes command arguments."""
        monitor_handler = make_mock_handler(10)
        mock_server.monitors.add(monitor_handler)

        client_handler = make_mock_handler(1, "127.0.0.1:5000")
        await dispatcher.dispatch(
            ["KSET", "mykey", "myvalue"], ctx(1, client_handler))

        data = monitor_handler._write.call_args[0][0]
        text = data.decode()
        assert '"KSET" "mykey" "myvalue"' in text

    @pytest.mark.asyncio
    async def test_monitor_not_logged_for_monitor_cmd(self, mock_server):
        """MONITOR command itself should not be broadcast."""
        monitor_handler = make_mock_handler(10)
        mock_server.monitors.add(monitor_handler)

        client_handler = make_mock_handler(2)
        await dispatcher.dispatch(
            ["MONITOR"], ctx(2, client_handler))

        # The MONITOR command itself should NOT trigger a broadcast
        monitor_handler._write.assert_not_called()

    @pytest.mark.asyncio
    async def test_monitor_multiple_clients(self, mock_server):
        """Multiple MONITOR clients all receive broadcasts."""
        h1 = make_mock_handler(10)
        h2 = make_mock_handler(11)
        mock_server.monitors.add(h1)
        mock_server.monitors.add(h2)

        client_handler = make_mock_handler(1, "127.0.0.1:5000")
        await dispatcher.dispatch(["PING"], ctx(1, client_handler))

        h1._write.assert_called_once()
        h2._write.assert_called_once()

    @pytest.mark.asyncio
    async def test_monitor_dead_client_removed(self, mock_server):
        """Dead monitor clients get cleaned up on broadcast failure."""
        alive = make_mock_handler(10)
        dead = make_mock_handler(11)
        dead._write.side_effect = Exception("connection lost")
        mock_server.monitors.add(alive)
        mock_server.monitors.add(dead)

        client_handler = make_mock_handler(1, "127.0.0.1:5000")
        await dispatcher.dispatch(["PING"], ctx(1, client_handler))

        # Dead handler should be removed from monitors
        assert dead not in mock_server.monitors
        assert alive in mock_server.monitors

    @pytest.mark.asyncio
    async def test_monitor_blocked_in_subscriber_mode(self, mock_server):
        """MONITOR is rejected if client is in subscriber mode."""
        handler = make_mock_handler(1)
        handler.subscribing = True
        response = await handle_monitor([], ctx(1, handler))
        assert b'subscriber mode' in response

    @pytest.mark.asyncio
    async def test_subscribe_blocked_in_monitor_mode(self, mock_server):
        """SUBSCRIBE is rejected if client is in monitor mode."""
        handler = make_mock_handler(1)
        handler.monitoring = True
        from testcore.commands import handle_subscribe
        response = await handle_subscribe(["kv"], ctx(1, handler))
        assert b'monitor mode' in response
