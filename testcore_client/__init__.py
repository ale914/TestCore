# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""TestCore Python Client Library.

Synchronous client for TestCore Server via TCP/RESP2.
Equivalent of redis-py for Redis. Zero external dependencies.

Usage:
    from testcore_client import TestCore

    tc = TestCore()
    tc.ping()
    tc.kset("key", "value")
    val = tc.kget("key")
"""

from testcore_client.client import TestCore, Pipeline
from testcore_client.exceptions import (
    TestCoreError,
    CommandError,
    ReadOnlyError,
    NoAliasError,
    InstrumentError,
    IdleError,
    LockedError,
    NotInitError,
    FaultError,
    DriverError,
    ProtocolError,
)

__version__ = "0.1.0"

__all__ = [
    "TestCore",
    "Pipeline",
    "TestCoreError",
    "CommandError",
    "ReadOnlyError",
    "NoAliasError",
    "InstrumentError",
    "IdleError",
    "LockedError",
    "NotInitError",
    "FaultError",
    "DriverError",
    "ProtocolError",
]
