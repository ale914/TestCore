# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""TestCore client exceptions.

Hierarchy mapped 1:1 to RESP error prefixes from the server.
"""


class TestCoreError(Exception):
    """Base exception for all TestCore client errors."""


class ProtocolError(TestCoreError):
    """RESP protocol parsing error (malformed response)."""


class CommandError(TestCoreError):
    """Server returned -ERR (generic command error)."""


class ReadOnlyError(CommandError):
    """Server returned -READONLY (reserved key prefix)."""


class NoAliasError(CommandError):
    """Server returned -NOALIAS (alias not found)."""


class InstrumentError(TestCoreError):
    """Base for instrument-related errors."""


class IdleError(InstrumentError):
    """Server returned -IDLE (instrument not locked)."""


class LockedError(InstrumentError):
    """Server returned -LOCKED (instrument locked by another session)."""


class NotInitError(InstrumentError):
    """Server returned -NOTINIT (instrument not initialized)."""


class FaultError(InstrumentError):
    """Server returned -FAULT or -UNRESPONSIVE."""


class DriverError(InstrumentError):
    """Server returned -DRIVER (driver-level error)."""


# Prefix → exception class mapping
_ERROR_MAP = {
    "READONLY": ReadOnlyError,
    "NOALIAS": NoAliasError,
    "IDLE": IdleError,
    "LOCKED": LockedError,
    "NOTINIT": NotInitError,
    "FAULT": FaultError,
    "UNRESPONSIVE": FaultError,
    "DRIVER": DriverError,
}


def raise_for_error(error_str: str):
    """Parse RESP error string and raise the appropriate exception.

    Error formats from server:
      "ERR message"           → CommandError
      "ERR READONLY ..."      → ReadOnlyError (prefix in message)
      "IDLE ..."              → IdleError
      "LOCKED ..."            → LockedError
      etc.
    """
    parts = error_str.split(" ", 1)
    prefix = parts[0]
    message = parts[1] if len(parts) > 1 else prefix

    # Direct prefix match (e.g. -IDLE, -LOCKED, -FAULT)
    if prefix in _ERROR_MAP:
        raise _ERROR_MAP[prefix](message)

    # ERR with known sub-prefix (e.g. -ERR READONLY ...)
    if prefix == "ERR" and message:
        sub_parts = message.split(" ", 1)
        sub_prefix = sub_parts[0]
        if sub_prefix in _ERROR_MAP:
            sub_message = sub_parts[1] if len(sub_parts) > 1 else message
            raise _ERROR_MAP[sub_prefix](sub_message)

    raise CommandError(error_str)
