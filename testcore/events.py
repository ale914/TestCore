# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Event notification system (spec §5.3).

Minimal pub/sub for server-generated events only (no client-to-client).
Clients subscribe via SUBSCRIBE command; the connection enters subscriber
mode and can only receive events + issue SUBSCRIBE/UNSUBSCRIBE/PING.

Event channels:
    __event:watch       — Watch guard triggers
    __event:instrument  — State changes (ADD, REMOVE, INIT, FAULT, etc.)
    __event:lock        — Lock acquired / released / force-released
    __event:session     — Client connect / disconnect
    __event:kv          — KV store changes (KSET), supports glob key filter
                          e.g. __event:kv:alert:* receives only keys matching alert:*
"""
from __future__ import annotations

import fnmatch
import json
import logging
import time
from typing import TYPE_CHECKING

from .protocol import RESPSerializer

if TYPE_CHECKING:
    from .server import ClientHandler

logger = logging.getLogger(__name__)

# Valid event channel bases
VALID_CHANNELS = frozenset({
    "__event:watch",
    "__event:instrument",
    "__event:lock",
    "__event:session",
    "__event:kv",
})

# KV channel prefix — subscriptions starting with this can have a glob filter
_KV_CHANNEL_PREFIX = "__event:kv"


def _parse_channel(channel: str) -> tuple[str, str | None]:
    """Parse channel into (base_channel, filter_pattern).

    '__event:kv'          → ('__event:kv', None)       — all KSET
    '__event:kv:alert:*'  → ('__event:kv', 'alert:*')  — glob filter
    '__event:instrument'  → ('__event:instrument', None)
    """
    if channel.startswith(_KV_CHANNEL_PREFIX):
        suffix = channel[len(_KV_CHANNEL_PREFIX):]
        if suffix and suffix[0] == ":":
            return _KV_CHANNEL_PREFIX, suffix[1:]
        elif suffix == "":
            return _KV_CHANNEL_PREFIX, None
        # Invalid format
        return channel, None
    return channel, None


def is_valid_channel(channel: str) -> bool:
    """Check if channel (with optional kv filter) is valid."""
    base, _ = _parse_channel(channel)
    return base in VALID_CHANNELS


class _Subscription:
    """A single subscription: handler + optional key filter for __event:kv."""
    __slots__ = ("handler", "pattern", "channel_spec")

    def __init__(self, handler: ClientHandler, pattern: str | None,
                 channel_spec: str):
        self.handler = handler
        self.pattern = pattern  # glob filter for kv keys, None = all
        self.channel_spec = channel_spec  # original channel string

    def __eq__(self, other):
        if isinstance(other, _Subscription):
            return (self.handler is other.handler
                    and self.channel_spec == other.channel_spec)
        return NotImplemented

    def __hash__(self):
        return hash((id(self.handler), self.channel_spec))


class EventBus:
    """Server-side event pub/sub bus.

    Subscribers are ClientHandler instances. Each handler tracks which
    channels it is subscribed to. The bus maintains channel → set[_Subscription]
    mappings for efficient broadcast.

    __event:kv supports optional glob key filters:
        __event:kv           — all KSET events
        __event:kv:alert:*   — only keys matching alert:*
        __event:kv:result    — only the exact key "result"
    """

    def __init__(self):
        self._subscriptions: dict[str, set[_Subscription]] = {}

    def subscribe(self, handler: ClientHandler, channel: str) -> bool:
        """Subscribe handler to channel. Returns True if newly subscribed."""
        base, pattern = _parse_channel(channel)
        if base not in VALID_CHANNELS:
            return False
        if base not in self._subscriptions:
            self._subscriptions[base] = set()
        sub = _Subscription(handler, pattern, channel)
        subs = self._subscriptions[base]
        if sub in subs:
            return False
        subs.add(sub)
        return True

    def unsubscribe(self, handler: ClientHandler, channel: str) -> bool:
        """Unsubscribe handler from channel. Returns True if was subscribed."""
        base, pattern = _parse_channel(channel)
        subs = self._subscriptions.get(base)
        if not subs:
            return False
        sub = _Subscription(handler, pattern, channel)
        if sub in subs:
            subs.discard(sub)
            return True
        return False

    def unsubscribe_all(self, handler: ClientHandler) -> list[str]:
        """Unsubscribe handler from all channels. Returns list of channels removed."""
        removed = []
        for base, subs in self._subscriptions.items():
            to_remove = [s for s in subs if s.handler is handler]
            for s in to_remove:
                subs.discard(s)
                removed.append(s.channel_spec)
        return removed

    def subscriber_channels(self, handler: ClientHandler) -> list[str]:
        """Return list of channels handler is subscribed to."""
        result = []
        for base, subs in self._subscriptions.items():
            for s in subs:
                if s.handler is handler:
                    result.append(s.channel_spec)
        return result

    def subscriber_count(self, channel: str) -> int:
        """Return number of subscribers for a channel base."""
        base, _ = _parse_channel(channel)
        return len(self._subscriptions.get(base, set()))

    async def publish(self, channel: str, payload: dict,
                      kv_key: str | None = None) -> int:
        """Publish event to all subscribers of channel.

        Message format (RESP array): ["event", "<channel>", "<json_payload>"]

        For __event:kv, pass kv_key to enable glob filtering.
        Subscribers with a filter only receive events where the key matches.

        Returns number of clients that received the message.
        """
        subs = self._subscriptions.get(channel)
        if not subs:
            return 0

        json_payload = json.dumps(payload, separators=(',', ':'))
        message = RESPSerializer.array(["event", channel, json_payload])

        delivered = 0
        dead = []
        for sub in subs:
            # Apply kv key filter
            if sub.pattern is not None and kv_key is not None:
                if not fnmatch.fnmatch(kv_key, sub.pattern):
                    continue

            try:
                await sub.handler._write(message)
                delivered += 1
            except Exception:
                dead.append(sub)

        # Clean up dead subscribers
        for sub in dead:
            subs.discard(sub)
            logger.debug(
                f"Removed dead subscriber {sub.handler.client_id} "
                f"from {channel}")

        return delivered


# Global event bus singleton
_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get global event bus instance."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus


# Helper functions for publishing common events

async def publish_instrument_event(event_type: str, instrument: str, **kwargs):
    """Publish an instrument state change event."""
    bus = get_event_bus()
    payload = {
        "type": event_type,
        "instrument": instrument,
        "timestamp": int(time.time() * 1_000_000),
        **kwargs,
    }
    await bus.publish("__event:instrument", payload)


async def publish_lock_event(event_type: str, instrument: str, session_id: int, **kwargs):
    """Publish a lock event."""
    bus = get_event_bus()
    payload = {
        "type": event_type,
        "instrument": instrument,
        "session_id": session_id,
        "timestamp": int(time.time() * 1_000_000),
        **kwargs,
    }
    await bus.publish("__event:lock", payload)


async def publish_session_event(event_type: str, session_id: int, **kwargs):
    """Publish a session event."""
    bus = get_event_bus()
    payload = {
        "type": event_type,
        "session_id": session_id,
        "timestamp": int(time.time() * 1_000_000),
        **kwargs,
    }
    await bus.publish("__event:session", payload)


async def publish_kv_event(key: str, value: str, session_id: int | None = None):
    """Publish a KV set event with key-based filtering."""
    bus = get_event_bus()
    payload = {
        "type": "set",
        "key": key,
        "value": value,
        "timestamp": int(time.time() * 1_000_000),
    }
    if session_id is not None:
        payload["session_id"] = session_id
    await bus.publish("__event:kv", payload, kv_key=key)
