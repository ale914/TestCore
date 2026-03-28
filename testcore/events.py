# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Event notification system (spec §5.3).

Minimal pub/sub for server-generated events only (no client-to-client).
Clients subscribe via SUBSCRIBE command; the connection enters subscriber
mode and can only receive events + issue SUBSCRIBE/UNSUBSCRIBE/PING.

Event channels:
    watch       — Watch guard triggers
    instrument  — State changes (ADD, REMOVE, INIT, FAULT, etc.)
    lock        — Lock acquired / released / force-released
    session     — Client connect / disconnect
    kv          — KV store changes (KSET), supports glob key filter
                  e.g. kv:alert:* receives only keys matching alert:*
    meas        — MEAS updates, supports instrument:resource filter
                  e.g. meas:vsg:CH1:FREQ
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
    "watch",
    "instrument",
    "lock",
    "session",
    "kv",
    "meas",
})


def _parse_channel(channel: str) -> tuple[str, str | None]:
    """Parse channel into (base_channel, filter_pattern).

    For any valid channel, a suffix after the base becomes a glob filter:
        'kv'             → ('kv', None)
        'kv:alert:*'     → ('kv', 'alert:*')
        'meas:sensor1:*' → ('meas', 'sensor1:*')
        'instrument'     → ('instrument', None)
    """
    for base in VALID_CHANNELS:
        if channel == base:
            return base, None
        if channel.startswith(base + ":"):
            return base, channel[len(base) + 1:]
    return channel, None


def is_valid_channel(channel: str) -> bool:
    """Check if channel (with optional kv filter) is valid."""
    base, _ = _parse_channel(channel)
    return base in VALID_CHANNELS


class _Subscription:
    """A single subscription: handler + optional key filter."""
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

    kv supports optional glob key filters:
        kv           — all KSET events
        kv:alert:*   — only keys matching alert:*
        kv:result    — only the exact key "result"
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
                      filter_key: str | None = None) -> int:
        """Publish event to all subscribers of channel.

        Message format (RESP array): ["event", "<channel>", "<json_payload>"]

        Pass filter_key to enable glob filtering on channels that support it.
        Subscribers with a pattern only receive events where the key matches.

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
            # Apply filter
            if sub.pattern is not None and filter_key is not None:
                if not fnmatch.fnmatch(filter_key, sub.pattern):
                    continue

            try:
                await sub.handler._write(message)
                delivered += 1
            except Exception:
                dead.append(sub)

        # Clean up dead subscribers
        if dead:
            subs.difference_update(dead)

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
    await bus.publish("instrument", payload)


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
    await bus.publish("lock", payload)


async def publish_session_event(event_type: str, session_id: int, **kwargs):
    """Publish a session event."""
    bus = get_event_bus()
    payload = {
        "type": event_type,
        "session_id": session_id,
        "timestamp": int(time.time() * 1_000_000),
        **kwargs,
    }
    await bus.publish("session", payload)


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
    await bus.publish("kv", payload, filter_key=key)


async def publish_meas_event(instrument: str, resource: str,
                              value: str | None, ts: float, status: str):
    """Publish a MEAS event on meas channel with instrument:resource filtering."""
    bus = get_event_bus()
    payload = {
        "type": "meas",
        "instrument": instrument,
        "resource": resource,
        "value": value,
        "ts": ts,
        "status": status,
    }
    await bus.publish("meas", payload,
                      filter_key=f"{instrument}:{resource}")
