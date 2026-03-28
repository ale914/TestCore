# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Tests for Event Notification System (spec §5.3)."""

import pytest
from unittest.mock import MagicMock, AsyncMock
from testcore.events import EventBus, get_event_bus, VALID_CHANNELS
from testcore.commands import (
    handle_subscribe, handle_unsubscribe, dispatcher
)
from testcore.protocol import RESPParser
from testcore.server import TestCoreServer


def make_mock_handler(client_id=1):
    """Create a mock ClientHandler with required attributes."""
    handler = MagicMock()
    handler.client_id = client_id
    handler.address = f"127.0.0.1:{50000 + client_id}"
    handler.name = None
    handler.cmd_count = 0
    handler.running = True
    handler.monitoring = False
    handler.subscribing = False
    handler._write = AsyncMock()
    return handler


def ctx(session_id, handler=None):
    c = {"session_id": session_id}
    if handler is not None:
        c["client_handler"] = handler
    return c


@pytest.fixture(autouse=True)
def reset_event_bus():
    """Reset global event bus before each test."""
    import testcore.events as events_mod
    events_mod._event_bus = None
    yield
    events_mod._event_bus = None


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def mock_server():
    server = TestCoreServer("127.0.0.1", 6399)
    yield server


class TestEventBus:
    """Tests for EventBus core functionality."""

    def test_subscribe(self, bus):
        handler = make_mock_handler(1)
        assert bus.subscribe(handler, "instrument") is True

    def test_subscribe_duplicate(self, bus):
        handler = make_mock_handler(1)
        bus.subscribe(handler, "instrument")
        assert bus.subscribe(handler, "instrument") is False

    def test_subscribe_invalid_channel(self, bus):
        handler = make_mock_handler(1)
        assert bus.subscribe(handler, "invalid_channel") is False

    def test_unsubscribe(self, bus):
        handler = make_mock_handler(1)
        bus.subscribe(handler, "instrument")
        assert bus.unsubscribe(handler, "instrument") is True

    def test_unsubscribe_not_subscribed(self, bus):
        handler = make_mock_handler(1)
        assert bus.unsubscribe(handler, "instrument") is False

    def test_unsubscribe_all(self, bus):
        handler = make_mock_handler(1)
        bus.subscribe(handler, "instrument")
        bus.subscribe(handler, "lock")
        removed = bus.unsubscribe_all(handler)
        assert sorted(removed) == ["instrument", "lock"]
        assert bus.subscriber_channels(handler) == []

    def test_subscriber_channels(self, bus):
        handler = make_mock_handler(1)
        bus.subscribe(handler, "instrument")
        bus.subscribe(handler, "session")
        channels = bus.subscriber_channels(handler)
        assert sorted(channels) == ["instrument", "session"]

    def test_subscriber_count(self, bus):
        h1 = make_mock_handler(1)
        h2 = make_mock_handler(2)
        bus.subscribe(h1, "instrument")
        bus.subscribe(h2, "instrument")
        assert bus.subscriber_count("instrument") == 2

    @pytest.mark.asyncio
    async def test_publish_delivers_to_subscribers(self, bus):
        h1 = make_mock_handler(1)
        h2 = make_mock_handler(2)
        bus.subscribe(h1, "instrument")
        bus.subscribe(h2, "instrument")

        count = await bus.publish("instrument",
                                  {"type": "ADD", "instrument": "vsg"})
        assert count == 2
        h1._write.assert_called_once()
        h2._write.assert_called_once()

    @pytest.mark.asyncio
    async def test_publish_message_format(self, bus):
        handler = make_mock_handler(1)
        bus.subscribe(handler, "lock")

        await bus.publish("lock",
                          {"type": "acquired", "instrument": "vsg"})

        data = handler._write.call_args[0][0]
        parser = RESPParser()
        messages = parser.feed(data)
        result = messages[0]

        assert isinstance(result, list)
        assert result[0] == "event"
        assert result[1] == "lock"
        # Third element is JSON payload
        import json
        payload = json.loads(result[2])
        assert payload["type"] == "acquired"
        assert payload["instrument"] == "vsg"

    @pytest.mark.asyncio
    async def test_publish_no_subscribers(self, bus):
        count = await bus.publish("instrument",
                                  {"type": "ADD"})
        assert count == 0

    @pytest.mark.asyncio
    async def test_publish_removes_dead_subscribers(self, bus):
        alive = make_mock_handler(1)
        dead = make_mock_handler(2)
        dead._write.side_effect = Exception("connection lost")

        bus.subscribe(alive, "instrument")
        bus.subscribe(dead, "instrument")

        count = await bus.publish("instrument",
                                  {"type": "ADD"})
        assert count == 1
        assert bus.subscriber_count("instrument") == 1

    @pytest.mark.asyncio
    async def test_publish_only_matching_channel(self, bus):
        h1 = make_mock_handler(1)
        h2 = make_mock_handler(2)
        bus.subscribe(h1, "instrument")
        bus.subscribe(h2, "lock")

        await bus.publish("instrument", {"type": "ADD"})
        h1._write.assert_called_once()
        h2._write.assert_not_called()


class TestSubscribeCommand:
    """Tests for SUBSCRIBE command handler."""

    @pytest.mark.asyncio
    async def test_subscribe_single_channel(self, mock_server):
        handler = make_mock_handler(1)
        response = await handle_subscribe(
            ["instrument"], ctx(1, handler))
        parser = RESPParser()
        messages = parser.feed(response)
        # Redis format: ["subscribe", channel, count]
        assert messages[0] == ["subscribe", "instrument", 1]
        assert handler.subscribing is True

    @pytest.mark.asyncio
    async def test_subscribe_multiple_channels(self, mock_server):
        handler = make_mock_handler(1)
        response = await handle_subscribe(
            ["instrument", "lock"], ctx(1, handler))
        parser = RESPParser()
        messages = parser.feed(response)
        assert messages[0] == ["subscribe", "instrument", 1]
        assert messages[1] == ["subscribe", "lock", 2]

    @pytest.mark.asyncio
    async def test_subscribe_invalid_channel(self, mock_server):
        handler = make_mock_handler(1)
        response = await handle_subscribe(
            ["bogus_channel"], ctx(1, handler))
        assert b"-ERR" in response
        assert b"invalid channel" in response

    @pytest.mark.asyncio
    async def test_subscribe_no_args(self, mock_server):
        handler = make_mock_handler(1)
        response = await handle_subscribe([], ctx(1, handler))
        assert b"-ERR" in response

    @pytest.mark.asyncio
    async def test_subscribe_no_session(self):
        response = await handle_subscribe(["instrument"])
        assert b"no session" in response

    @pytest.mark.asyncio
    async def test_subscriber_mode_blocks_commands(self, mock_server):
        """Client in subscriber mode can only use SUBSCRIBE/UNSUBSCRIBE/PING."""
        handler = make_mock_handler(1)
        handler.subscribing = True

        response = await dispatcher.dispatch(
            ["KSET", "key", "val"], ctx(1, handler))
        assert b"-ERR" in response
        assert b"subscriber mode" in response

    @pytest.mark.asyncio
    async def test_subscriber_mode_allows_ping(self, mock_server):
        handler = make_mock_handler(1)
        handler.subscribing = True

        response = await dispatcher.dispatch(["PING"], ctx(1, handler))
        assert response == b"+PONG\r\n"

    @pytest.mark.asyncio
    async def test_subscriber_mode_allows_unsubscribe(self, mock_server):
        handler = make_mock_handler(1)
        # First subscribe
        await handle_subscribe(["instrument"], ctx(1, handler))
        assert handler.subscribing is True

        # Unsubscribe via dispatch should work
        response = await dispatcher.dispatch(
            ["UNSUBSCRIBE"], ctx(1, handler))
        assert b"unsubscribe" in response


class TestUnsubscribeCommand:
    """Tests for UNSUBSCRIBE command handler."""

    @pytest.mark.asyncio
    async def test_unsubscribe_specific(self, mock_server):
        handler = make_mock_handler(1)
        await handle_subscribe(
            ["instrument", "lock"], ctx(1, handler))

        response = await handle_unsubscribe(
            ["instrument"], ctx(1, handler))
        parser = RESPParser()
        messages = parser.feed(response)
        assert messages[0] == ["unsubscribe", "instrument", 1]
        # Still subscribed to lock, so still in subscriber mode
        assert handler.subscribing is True

    @pytest.mark.asyncio
    async def test_unsubscribe_all(self, mock_server):
        handler = make_mock_handler(1)
        await handle_subscribe(
            ["instrument", "lock"], ctx(1, handler))

        response = await handle_unsubscribe([], ctx(1, handler))
        parser = RESPParser()
        messages = parser.feed(response)
        assert len(messages) == 2
        # Should exit subscriber mode
        assert handler.subscribing is False

    @pytest.mark.asyncio
    async def test_unsubscribe_exits_subscriber_mode(self, mock_server):
        handler = make_mock_handler(1)
        await handle_subscribe(["instrument"], ctx(1, handler))
        assert handler.subscribing is True

        await handle_unsubscribe(["instrument"], ctx(1, handler))
        assert handler.subscribing is False

    @pytest.mark.asyncio
    async def test_unsubscribe_no_session(self):
        response = await handle_unsubscribe([])
        assert b"no session" in response

    @pytest.mark.asyncio
    async def test_unsubscribe_when_not_subscribed(self, mock_server):
        handler = make_mock_handler(1)
        response = await handle_unsubscribe([], ctx(1, handler))
        parser = RESPParser()
        messages = parser.feed(response)
        # Should return ["unsubscribe", None, 0]
        assert messages[0][0] == "unsubscribe"
        assert messages[0][2] == 0


class TestEventIntegration:
    """Integration tests for event publishing."""

    @pytest.mark.asyncio
    async def test_subscribe_receives_published_events(self, mock_server):
        """Subscriber receives events published to their channel."""
        handler = make_mock_handler(1)
        await handle_subscribe(["instrument"], ctx(1, handler))

        # Reset write mock (subscribe itself sends a response)
        handler._write.reset_mock()

        # Publish an event
        bus = get_event_bus()
        await bus.publish("instrument",
                          {"type": "ADD", "instrument": "vsg"})

        handler._write.assert_called_once()
        data = handler._write.call_args[0][0]
        parser = RESPParser()
        messages = parser.feed(data)
        assert messages[0][0] == "event"
        assert messages[0][1] == "instrument"

    @pytest.mark.asyncio
    async def test_kv_subscribe(self, mock_server):
        """Subscribe to __event:kv channel."""
        handler = make_mock_handler(1)
        response = await handle_subscribe(
            ["kv"], ctx(1, handler))
        parser = RESPParser()
        messages = parser.feed(response)
        assert messages[0] == ["subscribe", "kv", 1]

    @pytest.mark.asyncio
    async def test_kset_publishes_kv_event(self, mock_server):
        """KSET publishes event to __event:kv subscribers."""
        from testcore.store import get_store
        store = get_store()
        store._data.clear()

        handler = make_mock_handler(1)
        await handle_subscribe(["kv"], ctx(1, handler))
        handler._write.reset_mock()

        # KSET from another session
        from testcore.commands import handle_set
        await handle_set(["meas:power", "23.4"], ctx(2))

        handler._write.assert_called_once()
        data = handler._write.call_args[0][0]
        parser = RESPParser()
        messages = parser.feed(data)
        assert messages[0][0] == "event"
        assert messages[0][1] == "kv"

        import json
        payload = json.loads(messages[0][2])
        assert payload["type"] == "set"
        assert payload["key"] == "meas:power"
        assert payload["value"] == "23.4"
        assert payload["session_id"] == 2

    @pytest.mark.asyncio
    async def test_kset_nx_fail_no_event(self, mock_server):
        """KSET with NX on existing key does not publish event."""
        from testcore.store import get_store
        store = get_store()
        store._data.clear()
        store._data["existing"] = "old"

        handler = make_mock_handler(1)
        await handle_subscribe(["kv"], ctx(1, handler))
        handler._write.reset_mock()

        from testcore.commands import handle_set
        await handle_set(["existing", "new", "NX"], ctx(2))

        # NX failed — no event should be published
        handler._write.assert_not_called()

    @pytest.mark.asyncio
    async def test_kv_filter_matches(self, mock_server):
        """Subscriber with __event:kv:alert:* only receives matching keys."""
        from testcore.store import get_store
        store = get_store()
        store._data.clear()

        handler = make_mock_handler(1)
        await handle_subscribe(["kv:alert:*"], ctx(1, handler))
        handler._write.reset_mock()

        from testcore.commands import handle_set
        # This should NOT be received (doesn't match alert:*)
        await handle_set(["meas:power", "23.4"], ctx(2))
        handler._write.assert_not_called()

        # This SHOULD be received
        await handle_set(["alert:emergency", "stop"], ctx(2))
        handler._write.assert_called_once()
        data = handler._write.call_args[0][0]
        parser = RESPParser()
        messages = parser.feed(data)
        import json
        payload = json.loads(messages[0][2])
        assert payload["key"] == "alert:emergency"
        assert payload["value"] == "stop"

    @pytest.mark.asyncio
    async def test_kv_filter_exact_key(self, mock_server):
        """Subscriber with __event:kv:result receives only exact key."""
        from testcore.store import get_store
        store = get_store()
        store._data.clear()

        handler = make_mock_handler(1)
        await handle_subscribe(["kv:result"], ctx(1, handler))
        handler._write.reset_mock()

        from testcore.commands import handle_set
        await handle_set(["results", "nope"], ctx(2))
        handler._write.assert_not_called()

        await handle_set(["result", "pass"], ctx(2))
        handler._write.assert_called_once()

    @pytest.mark.asyncio
    async def test_kv_no_filter_receives_all(self, mock_server):
        """Subscriber with __event:kv (no filter) receives all KSET."""
        from testcore.store import get_store
        store = get_store()
        store._data.clear()

        handler = make_mock_handler(1)
        await handle_subscribe(["kv"], ctx(1, handler))
        handler._write.reset_mock()

        from testcore.commands import handle_set
        await handle_set(["meas:power", "1"], ctx(2))
        await handle_set(["alert:stop", "2"], ctx(2))
        await handle_set(["anything", "3"], ctx(2))
        assert handler._write.call_count == 3

    @pytest.mark.asyncio
    async def test_kv_multiple_filters(self, mock_server):
        """Multiple subscribers with different filters see different keys."""
        from testcore.store import get_store
        store = get_store()
        store._data.clear()

        h_alert = make_mock_handler(1)
        h_meas = make_mock_handler(2)
        await handle_subscribe(["kv:alert:*"], ctx(1, h_alert))
        await handle_subscribe(["kv:meas:*"], ctx(2, h_meas))
        h_alert._write.reset_mock()
        h_meas._write.reset_mock()

        from testcore.commands import handle_set
        await handle_set(["alert:fire", "yes"], ctx(3))
        await handle_set(["meas:temp", "42"], ctx(3))
        await handle_set(["other:key", "val"], ctx(3))

        assert h_alert._write.call_count == 1
        assert h_meas._write.call_count == 1

    @pytest.mark.asyncio
    async def test_kv_wildcard_filter(self, mock_server):
        """Glob pattern *temp* matches keys containing 'temp'."""
        from testcore.store import get_store
        store = get_store()
        store._data.clear()

        handler = make_mock_handler(1)
        await handle_subscribe(["kv:*temp*"], ctx(1, handler))
        handler._write.reset_mock()

        from testcore.commands import handle_set
        await handle_set(["meas:temperature", "25"], ctx(2))
        await handle_set(["dut_temp", "30"], ctx(2))
        await handle_set(["voltage", "5.0"], ctx(2))

        assert handler._write.call_count == 2
