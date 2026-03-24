# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Command dispatch system."""

from __future__ import annotations
import asyncio
import json
import time
from typing import Callable, Awaitable
from .protocol import RESPSerializer
from .store import get_store
from .instruments import (
    get_registry, InstrumentState, IdleError, NotInitError, FaultError, LockedError
)
from .base_driver import DriverError
from .events import (
    publish_kv_event, publish_instrument_event, publish_lock_event,
    get_event_bus, VALID_CHANNELS, is_valid_channel,
)
from .journal import get_journal
from . import __version__ as VERSION

# Type alias for command handlers (context carries session_id etc.)
CommandHandler = Callable[[list[str], dict], Awaitable[bytes]]


class CommandDispatcher:
    """Scalable command dispatch table.

    Dispatch is serial (one command at a time) via a global asyncio lock,
    matching the Redis single-threaded model. Driver I/O is offloaded to
    threads with a watchdog timeout (spec §7.4) but the dispatch lock
    ensures only one command is in flight at any time.
    """

    def __init__(self):
        self._handlers: dict[str, CommandHandler] = {}
        self._dispatch_lock: asyncio.Lock | None = None

    def register(self, name: str, handler: CommandHandler):
        """Register a command handler."""
        self._handlers[name.upper()] = handler

    # Root words that take a subcommand (e.g. CLIENT ID, COMMAND LIST)
    _SUBCOMMAND_ROOTS: set[str] = {"CLIENT", "COMMAND", "DRIVER", "ALIAS"}

    # Commands allowed in subscriber mode (spec §5.3)
    _SUBSCRIBER_ALLOWED: frozenset = frozenset({"SUBSCRIBE", "UNSUBSCRIBE", "PING"})

    async def dispatch(self, command: list[str], context: dict | None = None) -> bytes:
        """
        Dispatch command to registered handler.

        Supports both single-word commands (PING, KSET) and multi-word
        subcommands (CLIENT ID, COMMAND LIST, DRIVER LIST).

        Args:
            command: List of command parts (e.g., ['PING'] or ['CLIENT', 'ID'])
            context: Session context (session_id, etc.)

        Returns:
            RESP-encoded response bytes
        """
        if not command:
            return RESPSerializer.error("empty command")

        if context is None:
            context = {}

        cmd_name = command[0].upper()
        args = command[1:]

        # Try multi-word subcommand (e.g. CLIENT ID → "CLIENT ID")
        if cmd_name in self._SUBCOMMAND_ROOTS and args:
            compound = f"{cmd_name} {args[0].upper()}"
            if compound in self._handlers:
                cmd_name = compound
                args = args[1:]

        handler = self._handlers.get(cmd_name)

        if handler is None:
            return RESPSerializer.error(f"unknown command '{cmd_name}'")

        # Subscriber mode check
        client_handler = context.get("client_handler")
        if (client_handler is not None
                and getattr(client_handler, "subscribing", False)
                and cmd_name not in self._SUBSCRIBER_ALLOWED):
            return RESPSerializer.error(
                "only SUBSCRIBE, UNSUBSCRIBE, PING allowed in subscriber mode")

        # Lazy init: asyncio.Lock() requires a running event loop
        if self._dispatch_lock is None:
            self._dispatch_lock = asyncio.Lock()

        async with self._dispatch_lock:
            try:
                response = await handler(args, context)
            except Exception as e:
                response = RESPSerializer.error(
                    f"command execution failed: {e}")

        # Record in journal (after execution, with result status)
        journal = get_journal()
        session_id = context.get("session_id", 0)
        client_name = ""
        client_handler = context.get("client_handler")
        if client_handler is not None:
            client_name = getattr(client_handler, "name", "") or ""
        status = "error" if response[:1] == b"-" else "ok"
        journal.record(session_id, client_name, command, status)

        # Broadcast to MONITOR clients (skip MONITOR itself, like Redis)
        if cmd_name != "MONITOR":
            from .server import get_server
            server = get_server()
            if server and server.monitors:
                sid = context.get("session_id", 0)
                cname = getattr(client_handler, "name", "") or ""
                label = f"{cname}#{sid}" if cname else f"#{sid}"
                ts = time.time()
                # TestCore MONITOR format: +timestamp [#id_or_name] "cmd" "arg1" ...
                parts = [b"+", f"{ts:.6f} [{label}]".encode()]
                for c in command:
                    parts.append(b' "')
                    parts.append(c.encode() if isinstance(c, str) else str(c).encode())
                    parts.append(b'"')
                parts.append(b"\r\n")
                await server.broadcast_monitors(b"".join(parts))

        return response


# Command Handlers

_OK_RESPONSE = b"+OK\r\n"
_PONG_RESPONSE = b"+PONG\r\n"


async def handle_ping(args: list[str], context: dict = None) -> bytes:
    """
    Handle PING [message] command (spec §6.1).

    PING           → +PONG\r\n
    PING hello     → $5\r\nhello\r\n
    """
    if args:
        # Echo the message as bulk string
        return RESPSerializer.bulk_string(args[0])
    return _PONG_RESPONSE


async def handle_command(args: list[str], context: dict = None) -> bytes:
    """
    Handle COMMAND LIST [pattern] command (spec §6.1).

    COMMAND LIST          → *N\r\n...
    COMMAND LIST I*       → Filtered list
    """
    import fnmatch

    pattern = args[0] if args else None

    # Get all commands
    commands = list(dispatcher._handlers.keys())

    # Apply pattern filter if provided
    if pattern:
        commands = [cmd for cmd in commands if fnmatch.fnmatch(cmd, pattern)]

    # Sort alphabetically
    commands.sort()

    return RESPSerializer.array(commands)


# Key-Value Command Handlers (spec §6.2)

async def handle_set(args: list[str], context: dict = None) -> bytes:
    """
    Handle SET key value [NX|XX] command.

    SET key value     → +OK\r\n
    SET key value NX  → +OK\r\n or $-1\r\n (if exists)
    SET key value XX  → +OK\r\n or $-1\r\n (if not exists)
    """
    if len(args) < 2:
        return RESPSerializer.error("wrong number of arguments for 'KSET' command")

    key, value = args[0], args[1]
    nx = len(args) > 2 and args[2].upper() == 'NX'
    xx = len(args) > 2 and args[2].upper() == 'XX'

    store = get_store()
    try:
        success = store.set(key, value, nx=nx, xx=xx)
        if success:
            session_id = (context or {}).get("session_id")
            await publish_kv_event(key, value, session_id)
            return _OK_RESPONSE
        return RESPSerializer.null()
    except ValueError as e:
        return RESPSerializer.error(str(e))


async def handle_get(args: list[str], context: dict = None) -> bytes:
    """
    Handle GET key command.

    GET key  → $N\r\nvalue\r\n or $-1\r\n (nil)
    """
    if len(args) < 1:
        return RESPSerializer.error("wrong number of arguments for 'KGET' command")

    key = args[0]
    store = get_store()
    value = store.get(key)

    if value is None:
        return RESPSerializer.null()
    return RESPSerializer.bulk_string(value)


async def handle_mget(args: list[str], context: dict = None) -> bytes:
    """
    Handle MGET key [key ...] command.

    MGET k1 k2 k3  → *3\r\n$V1\r\nval1\r\n$-1\r\n$V3\r\nval3\r\n
    """
    if len(args) < 1:
        return RESPSerializer.error("wrong number of arguments for 'KMGET' command")

    store = get_store()
    values = store.mget(args)
    return RESPSerializer.array(values)


async def handle_mset(args: list[str], context: dict = None) -> bytes:
    """
    Handle MSET key value [key value ...] command.

    MSET k1 v1 k2 v2  → +OK\r\n
    """
    if len(args) < 2 or len(args) % 2 != 0:
        return RESPSerializer.error("wrong number of arguments for 'KMSET' command")

    # Parse key-value pairs
    pairs = [(args[i], args[i+1]) for i in range(0, len(args), 2)]

    store = get_store()
    try:
        store.mset(pairs)
        return RESPSerializer.simple_string("OK")
    except ValueError as e:
        return RESPSerializer.error(str(e))


async def handle_del(args: list[str], context: dict = None) -> bytes:
    """
    Handle DEL key [key ...] command.

    DEL k1 k2 k3  → :2\r\n (count of deleted keys)
    """
    if len(args) < 1:
        return RESPSerializer.error("wrong number of arguments for 'KDEL' command")

    store = get_store()
    try:
        count = store.delete(args)
        return RESPSerializer.integer(count)
    except ValueError as e:
        return RESPSerializer.error(str(e))


async def handle_exists(args: list[str], context: dict = None) -> bytes:
    """
    Handle EXISTS key [key ...] command.

    EXISTS k1 k2 k3  → :2\r\n (count of existing keys)
    """
    if len(args) < 1:
        return RESPSerializer.error("wrong number of arguments for 'KEXISTS' command")

    store = get_store()
    count = store.exists(args)
    return RESPSerializer.integer(count)


async def handle_keys(args: list[str], context: dict = None) -> bytes:
    """
    Handle KEYS pattern command.

    KEYS *       → *N\r\n...
    KEYS meas:*  → *N\r\n...
    """
    pattern = args[0] if args else '*'

    store = get_store()
    keys = store.keys(pattern)
    return RESPSerializer.array(keys)


async def handle_dbsize(args: list[str], context: dict = None) -> bytes:
    """
    Handle DBSIZE command.

    DBSIZE  → :42\r\n (count of client keys)
    """
    store = get_store()
    size = store.dbsize()
    return RESPSerializer.integer(size)


async def handle_flushdb(args: list[str], context: dict = None) -> bytes:
    """
    Handle FLUSHDB command.

    FLUSHDB  → +OK\r\n
    """
    store = get_store()
    store.flushdb()
    return RESPSerializer.simple_string("OK")



# Instrument Command Handlers (spec §6.3)

async def handle_instrument_add(args: list[str], context: dict = None) -> bytes:
    """Handle IADD name driver [address] [key=value ...].

    IADD awg agilent33500 TCPIP0::192.168.1.50::inst0::INSTR
    IADD pm keysight_u2000 COM3 baudrate=115200 timeout=10000
    IADD sim dryrun
    """
    if len(args) < 2:
        return RESPSerializer.error(
            "wrong number of arguments for 'IADD' command")

    name, driver_name = args[0], args[1]
    address = None
    transport_opts = {}

    for arg in args[2:]:
        if "=" in arg:
            key, _, val = arg.partition("=")
            # Auto-convert numeric values
            try:
                val = int(val)
            except ValueError:
                try:
                    val = float(val)
                except ValueError:
                    pass
            transport_opts[key] = val
        elif address is None:
            address = arg
        else:
            return RESPSerializer.error(
                "unexpected argument, use key=value for transport options")

    registry = get_registry()
    try:
        registry.add(name, driver_name, address,
                     transport_opts=transport_opts or None)
        await publish_instrument_event("ADD", name, driver=driver_name)
        return RESPSerializer.simple_string("OK")
    except DriverError as e:
        return RESPSerializer.error(f"DRIVER {e}")


async def handle_instrument_remove(args: list[str], context: dict = None) -> bytes:
    """Handle INSTRUMENT.REMOVE name."""
    if len(args) < 1:
        return RESPSerializer.error(
            "wrong number of arguments for 'IREMOVE' command")

    registry = get_registry()
    try:
        registry.remove(args[0])
        await publish_instrument_event("REMOVE", args[0])
        return RESPSerializer.simple_string("OK")
    except DriverError as e:
        return RESPSerializer.error(f"DRIVER {e}")


async def handle_instrument_init(args: list[str], context: dict = None) -> bytes:
    """Handle INSTRUMENT.INIT name [config_file_path]."""
    if len(args) < 1:
        return RESPSerializer.error(
            "wrong number of arguments for 'IINIT' command")

    name = args[0]
    config_path = args[1] if len(args) > 1 else None

    registry = get_registry()
    try:
        await registry.init_instrument(name, config_path)
        await publish_instrument_event("INIT", name)
        return RESPSerializer.simple_string("OK")
    except (IdleError, NotInitError, FaultError, DriverError) as e:
        return _state_error(e)


async def handle_instrument_info(args: list[str], context: dict = None) -> bytes:
    """Handle INSTRUMENT.INFO name."""
    if len(args) < 1:
        return RESPSerializer.error(
            "wrong number of arguments for 'IINFO' command")

    registry = get_registry()
    try:
        inst = registry.get(args[0])
        try:
            driver_info = inst.driver.info()
        except Exception:
            driver_info = {}

        lines = [
            f"name:{inst.name}",
            f"driver:{inst.driver_path}",
            f"state:{inst.state.value}",
            f"resources:{len(inst.resources)}",
            f"lock_owner:{inst.lock_owner or 'none'}",
            f"total_calls:{inst.total_calls}",
            f"total_errors:{inst.total_errors}",
            f"mean_response_ms:{inst.mean_response_ms:.3f}",
        ]
        for k, v in driver_info.items():
            lines.append(f"{k}:{v}")

        return RESPSerializer.bulk_string("\r\n".join(lines))
    except DriverError as e:
        return RESPSerializer.error(f"NORESOURCE {e}")


async def handle_instrument_list(args: list[str], context: dict = None) -> bytes:
    """Handle INSTRUMENT.LIST."""
    registry = get_registry()
    instruments = registry.list_instruments()
    return RESPSerializer.array(instruments)


async def handle_instrument_resources(args: list[str], context: dict = None) -> bytes:
    """Handle INSTRUMENT.RESOURCES name."""
    if len(args) < 1:
        return RESPSerializer.error(
            "wrong number of arguments for 'IRESOURCES' command")

    registry = get_registry()
    try:
        inst = registry.get(args[0])
        # Always call discover() for fresh resource list (spec §6.3.2)
        resources = inst.driver.discover()
        return RESPSerializer.array(resources)
    except DriverError as e:
        return RESPSerializer.error(f"NORESOURCE {e}")


async def handle_instrument_reset(args: list[str], context: dict = None) -> bytes:
    """Handle INSTRUMENT.RESET name."""
    if len(args) < 1:
        return RESPSerializer.error(
            "wrong number of arguments for 'IRESET' command")

    registry = get_registry()
    try:
        await registry.reset(args[0])
        await publish_instrument_event("RESET", args[0])
        return RESPSerializer.simple_string("OK")
    except (FaultError, DriverError) as e:
        return _state_error(e)


async def handle_align(args: list[str], context: dict = None) -> bytes:
    """Handle ALIGN instrument [instrument ...]."""
    if len(args) < 1:
        return RESPSerializer.error(
            "wrong number of arguments for 'IALIGN' command")

    registry = get_registry()
    try:
        for name in args:
            await registry.align(name)
        return RESPSerializer.simple_string("OK")
    except (IdleError, FaultError, DriverError) as e:
        return _state_error(e)


async def handle_driver_list(args: list[str], context: dict = None) -> bytes:
    """Handle DRIVER.LIST (list registered driver modules)."""
    registry = get_registry()
    drivers = registry.list_drivers()
    return RESPSerializer.array(drivers)


# Helpers

def _parse_resource_address(addr: str) -> tuple[str, str]:
    """Parse 'instrument:resource' → (instrument, resource). Raises ValueError."""
    parts = addr.split(":", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"invalid resource address '{addr}', expected instrument:resource")
    return parts[0], parts[1]


def _state_error(exc: Exception) -> bytes:
    """Convert state exception to RESP error with proper prefix."""
    if isinstance(exc, IdleError):
        return f"-IDLE {exc}\r\n".encode()
    if isinstance(exc, NotInitError):
        return f"-NOTINIT {exc}\r\n".encode()
    if isinstance(exc, LockedError):
        return f"-LOCKED {exc}\r\n".encode()
    if isinstance(exc, FaultError):
        return f"-FAULT {exc}\r\n".encode()
    if isinstance(exc, DriverError):
        return f"-DRIVER {exc}\r\n".encode()
    return RESPSerializer.error(str(exc))


# Lock Command Handlers (spec §6.5)

async def handle_lock(args: list[str], context: dict = None) -> bytes:
    """Handle LOCK instrument [instrument ...]. Atomic all-or-nothing."""
    if len(args) < 1:
        return RESPSerializer.error(
            "wrong number of arguments for 'ILOCK' command")

    session_id = (context or {}).get("session_id")
    if session_id is None:
        return RESPSerializer.error("no session context")

    registry = get_registry()

    # Pre-validate: all instruments must exist and be lockable
    instruments = []
    for name in args:
        try:
            inst = registry.get(name)
        except DriverError as e:
            return _state_error(e)
        # Check if lockable (IDLE or already owned by this session)
        if inst.state != InstrumentState.IDLE:
            if inst.lock_owner == session_id:
                instruments.append(inst)
                continue
            if inst.lock_owner is not None:
                return f"-LOCKED {name} owned by session {inst.lock_owner}\r\n".encode()
            return RESPSerializer.error(
                f"instrument '{name}' is not IDLE")
        instruments.append(inst)

    # All validated — acquire locks atomically
    for inst in instruments:
        if inst.lock_owner == session_id:
            continue  # already locked by this session
        inst.state = InstrumentState.LOCKED
        inst.lock_owner = session_id

    # Publish lock events
    for inst in instruments:
        await publish_lock_event("acquired", inst.name, session_id)

    return RESPSerializer.simple_string("OK")


async def handle_unlock(args: list[str], context: dict = None) -> bytes:
    """Handle UNLOCK instrument [instrument ...] and UNLOCK ALL."""
    session_id = (context or {}).get("session_id")
    if session_id is None:
        return RESPSerializer.error("no session context")

    registry = get_registry()

    # UNLOCK ALL
    if args and args[0].upper() == "ALL":
        for name in registry.list_instruments():
            inst = registry.get(name)
            if inst.lock_owner == session_id:
                try:
                    registry.unlock(name, session_id)
                except Exception:
                    pass
        return RESPSerializer.simple_string("OK")

    # UNLOCK instrument [instrument ...]
    if len(args) < 1:
        return RESPSerializer.error(
            "wrong number of arguments for 'IUNLOCK' command")

    try:
        for name in args:
            registry.unlock(name, session_id)
        return RESPSerializer.simple_string("OK")
    except IdleError as e:
        return _state_error(e)
    except LockedError as e:
        return _state_error(e)
    except DriverError as e:
        return _state_error(e)


async def handle_locks(args: list[str], context: dict = None) -> bytes:
    """Handle LOCKS — list all current locks."""
    registry = get_registry()
    result = []
    for name in registry.list_instruments():
        inst = registry.get(name)
        if inst.lock_owner is not None:
            result.append(f"{name}:session:{inst.lock_owner}")
    return RESPSerializer.array(result)


# Resource Command Handlers (spec §6.4)

async def handle_read(args: list[str], context: dict = None) -> bytes:
    """Handle READ instrument:resource."""
    if len(args) < 1:
        return RESPSerializer.error(
            "wrong number of arguments for 'IREAD' command")

    session_id = (context or {}).get("session_id")
    if session_id is None:
        return RESPSerializer.error("no session context")

    try:
        inst_name, resource = _parse_resource_address(args[0])
    except ValueError as e:
        return RESPSerializer.error(str(e))

    registry = get_registry()
    try:
        inst = registry.get(inst_name)
        if inst.lock_owner != session_id:
            if inst.lock_owner is None:
                raise IdleError(f"{inst_name} not locked")
            raise LockedError(f"{inst_name} owned by session {inst.lock_owner}")
        result = await registry.read(inst_name, resource)
        return RESPSerializer.bulk_string(result)
    except (IdleError, NotInitError, LockedError, FaultError, DriverError) as e:
        return _state_error(e)


async def handle_write(args: list[str], context: dict = None) -> bytes:
    """Handle WRITE instrument:resource value."""
    if len(args) < 2:
        return RESPSerializer.error(
            "wrong number of arguments for 'IWRITE' command")

    session_id = (context or {}).get("session_id")
    if session_id is None:
        return RESPSerializer.error("no session context")

    try:
        inst_name, resource = _parse_resource_address(args[0])
    except ValueError as e:
        return RESPSerializer.error(str(e))

    registry = get_registry()
    try:
        inst = registry.get(inst_name)
        if inst.lock_owner != session_id:
            if inst.lock_owner is None:
                raise IdleError(f"{inst_name} not locked")
            raise LockedError(f"{inst_name} owned by session {inst.lock_owner}")
        await registry.write(inst_name, resource, args[1])
        return RESPSerializer.simple_string("OK")
    except (IdleError, NotInitError, LockedError, FaultError, DriverError) as e:
        return _state_error(e)


async def handle_raw(args: list[str], context: dict = None) -> bytes:
    """Handle RAW instrument command_string."""
    if len(args) < 2:
        return RESPSerializer.error(
            "wrong number of arguments for 'IRAW' command")

    session_id = (context or {}).get("session_id")
    if session_id is None:
        return RESPSerializer.error("no session context")

    inst_name = args[0]
    command_str = " ".join(args[1:])

    registry = get_registry()
    try:
        inst = registry.get(inst_name)
        if inst.lock_owner != session_id:
            if inst.lock_owner is None:
                raise IdleError(f"{inst_name} not locked")
            raise LockedError(f"{inst_name} owned by session {inst.lock_owner}")
        result = await registry.passthrough(inst_name, command_str)
        return RESPSerializer.bulk_string(result)
    except (IdleError, NotInitError, LockedError, FaultError, DriverError) as e:
        return _state_error(e)


async def handle_readmulti(args: list[str], context: dict = None) -> bytes:
    """Handle IMREAD instrument:resource [instrument:resource ...]."""
    if len(args) < 1:
        return RESPSerializer.error(
            "wrong number of arguments for 'IMREAD' command")

    session_id = (context or {}).get("session_id")
    if session_id is None:
        return RESPSerializer.error("no session context")

    registry = get_registry()
    results = []

    for addr in args:
        try:
            inst_name, resource = _parse_resource_address(addr)
        except ValueError as e:
            return RESPSerializer.error(str(e))

        try:
            inst = registry.get(inst_name)
            if inst.lock_owner != session_id:
                if inst.lock_owner is None:
                    raise IdleError(f"{inst_name} not locked")
                raise LockedError(f"{inst_name} owned by session {inst.lock_owner}")
            result = await registry.read(inst_name, resource)
            results.append(result)
        except (IdleError, NotInitError, LockedError, FaultError, DriverError) as e:
            return _state_error(e)

    return RESPSerializer.array(results)


async def handle_save(args: list[str], context: dict = None) -> bytes:
    """Handle SAVE instrument target file_path.

    Generic file saving: driver retrieves data from the instrument
    and writes it to the local filesystem.

    Examples:
        ISAVE scope SCREEN /data/screenshot.png
        ISAVE sa    TRACE1 /data/trace.csv
        ISAVE meas  DATA   /data/results.csv
    """
    if len(args) < 3:
        return RESPSerializer.error(
            "wrong number of arguments for 'ISAVE' command")

    session_id = (context or {}).get("session_id")
    if session_id is None:
        return RESPSerializer.error("no session context")

    inst_name = args[0]
    target = args[1]
    file_path = args[2]

    registry = get_registry()
    try:
        inst = registry.get(inst_name)
        if inst.lock_owner != session_id:
            if inst.lock_owner is None:
                raise IdleError(f"{inst_name} not locked")
            raise LockedError(f"{inst_name} owned by session {inst.lock_owner}")
        result = await registry.save(inst_name, target, file_path)
        return RESPSerializer.bulk_string(result)
    except (IdleError, NotInitError, LockedError, FaultError, DriverError) as e:
        return _state_error(e)


async def handle_load(args: list[str], context: dict = None) -> bytes:
    """Handle LOAD instrument target file_path.

    Generic file loading: driver interprets the file and loads data
    into the instrument. What 'load' means is driver-specific.

    Examples:
        ILOAD awg CH1:MyPulse /data/pulse.csv
        ILOAD sa  corrections /data/corr.csv
    """
    if len(args) < 3:
        return RESPSerializer.error(
            "wrong number of arguments for 'ILOAD' command")

    session_id = (context or {}).get("session_id")
    if session_id is None:
        return RESPSerializer.error("no session context")

    inst_name = args[0]
    target = args[1]
    file_path = args[2]

    registry = get_registry()
    try:
        inst = registry.get(inst_name)
        if inst.lock_owner != session_id:
            if inst.lock_owner is None:
                raise IdleError(f"{inst_name} not locked")
            raise LockedError(f"{inst_name} owned by session {inst.lock_owner}")
        result = await registry.load(inst_name, target, file_path)
        return RESPSerializer.bulk_string(result)
    except (IdleError, NotInitError, LockedError, FaultError, DriverError) as e:
        return _state_error(e)


# Server Introspection Command Handlers (spec §6.1)

async def handle_info(args: list[str], context: dict = None) -> bytes:
    """Handle INFO [section] — server introspection."""
    from .server import get_server

    section = args[0].lower() if args else None
    valid_sections = ("server", "clients", "store", "instruments", "health")

    if section and section not in valid_sections:
        return RESPSerializer.error(
            f"invalid INFO section '{section}', "
            f"valid: {', '.join(valid_sections)}")

    lines = []
    server = get_server()
    store = get_store()
    registry = get_registry()

    if section is None or section == "server":
        lines.append("# Server")
        lines.append(f"version:{VERSION}")
        if server:
            uptime = time.time() - server.start_time
            lines.append(f"uptime_seconds:{uptime:.0f}")
            lines.append(f"host:{server.host}")
            lines.append(f"port:{server.port}")
            lines.append(f"max_clients:{server.max_clients}")
            lines.append(f"connected_clients:{len(server.client_handlers)}")
            lines.append(f"driver_timeout:{registry._driver_timeout}")
        else:
            lines.append("uptime_seconds:0")
            lines.append("connected_clients:0")

    if section is None or section == "clients":
        if lines:
            lines.append("")
        lines.append("# Clients")
        if server:
            lines.append(f"connected_clients:{len(server.client_handlers)}")
            lines.append(f"total_connections:{server.total_connections}")
            lines.append(f"rejected_connections:{server.rejected_connections}")
            lines.append(f"total_commands_processed:{server.total_commands}")
        else:
            lines.append("connected_clients:0")
            lines.append("total_commands_processed:0")

    if section is None or section == "store":
        if lines:
            lines.append("")
        lines.append("# Store")
        lines.append(f"keys:{store.dbsize()}")
        reserved = sum(
            1 for k in store._data if k.startswith(
                ('_sys:', '_drv:', '_inst:', '_sess:', '_lock:', '_watch:')))
        lines.append(f"reserved_keys:{reserved}")
        if server:
            lines.append(f"monitor_clients:{len(server.monitors)}")

    if section is None or section == "instruments":
        if lines:
            lines.append("")
        lines.append("# Instruments")
        instruments = registry.list_instruments()
        lines.append(f"instrument_count:{len(instruments)}")
        locked = ready = fault = unresponsive = total_calls = total_errors = 0
        for name in instruments:
            inst = registry.get(name)
            if inst.state == InstrumentState.LOCKED:
                locked += 1
            elif inst.state == InstrumentState.READY:
                ready += 1
            elif inst.state == InstrumentState.FAULT:
                fault += 1
            elif inst.state == InstrumentState.UNRESPONSIVE:
                unresponsive += 1
            total_calls += inst.total_calls
            total_errors += inst.total_errors
        lines.append(f"locked_count:{locked}")
        lines.append(f"ready_count:{ready}")
        lines.append(f"fault_count:{fault}")
        lines.append(f"unresponsive_count:{unresponsive}")
        lines.append(f"total_driver_calls:{total_calls}")
        lines.append(f"total_driver_errors:{total_errors}")

    if section is None or section == "health":
        if lines:
            lines.append("")
        lines.append("# Health")
        # Overall health status
        instruments = registry.list_instruments()
        fault_names = []
        for name in instruments:
            inst = registry.get(name)
            if inst.state in (
                    InstrumentState.FAULT, InstrumentState.UNRESPONSIVE):
                fault_names.append(f"{name}:{inst.state.value}")
        if server:
            uptime = time.time() - server.start_time
            lines.append(f"uptime_seconds:{uptime:.0f}")
        lines.append(f"status:{'DEGRADED' if fault_names else 'OK'}")
        lines.append(f"faulted_instruments:{len(fault_names)}")
        if fault_names:
            lines.append(f"faulted_list:{','.join(fault_names)}")

    return RESPSerializer.bulk_string("\r\n".join(lines))


async def handle_time(args: list[str], context: dict = None) -> bytes:
    """Handle TIME — return server time as [unix_seconds, microseconds]."""
    now = time.time()
    seconds = int(now)
    microseconds = int((now - seconds) * 1_000_000)
    return RESPSerializer.array([str(seconds), str(microseconds)])


async def handle_clientid(args: list[str], context: dict = None) -> bytes:
    """Handle CLIENT ID — return current session ID."""
    session_id = (context or {}).get("session_id")
    if session_id is None:
        return RESPSerializer.error("no session context")
    return RESPSerializer.integer(session_id)


async def handle_clientlist(args: list[str], context: dict = None) -> bytes:
    """Handle CLIENT LIST — list all connected clients."""
    from .server import get_server

    server = get_server()
    if not server:
        return RESPSerializer.bulk_string("")

    now = time.time()
    lines = []
    for cid, handler in sorted(server.client_handlers.items()):
        age = int(now - handler.connect_time)
        name = handler.name or ""
        lines.append(
            f"id={cid} addr={handler.address} name={name} "
            f"age={age} cmd={handler.cmd_count}")

    return RESPSerializer.bulk_string("\n".join(lines))


async def handle_clientname(args: list[str], context: dict = None) -> bytes:
    """Handle CLIENT NAME [name] — get or set client name."""
    handler = (context or {}).get("client_handler")
    if handler is None:
        return RESPSerializer.error("no session context")

    if not args:
        # Get name
        if handler.name is None:
            return RESPSerializer.null()
        return RESPSerializer.bulk_string(handler.name)

    # Set name
    handler.name = args[0]
    return RESPSerializer.simple_string("OK")


async def handle_monitor(args: list[str], context: dict = None) -> bytes:
    """Handle MONITOR — enter monitor mode, streams all commands in real-time.

    Sets _pending_monitor flag; actual registration happens in
    _process_message AFTER the +OK response is sent to the client,
    avoiding a race where monitor broadcasts arrive before +OK.
    """
    client_handler = (context or {}).get("client_handler")
    if client_handler is None:
        return RESPSerializer.error("no session context")

    if getattr(client_handler, "subscribing", False):
        return RESPSerializer.error(
            "cannot enter MONITOR while in subscriber mode")

    from .server import get_server
    server = get_server()
    if server is None:
        return RESPSerializer.error("server not available")

    # Deferred: actual registration in _process_message after response is sent
    client_handler._pending_monitor = True
    return RESPSerializer.simple_string("OK")


async def handle_subscribe(args: list[str], context: dict = None) -> bytes:
    """Handle SUBSCRIBE channel [channel ...] — enter subscriber mode.

    Response per channel (Redis format):
        *3\r\n$9\r\nsubscribe\r\n$N\r\nchannel\r\n:count\r\n
    """
    client_handler = (context or {}).get("client_handler")
    if client_handler is None:
        return RESPSerializer.error("no session context")

    if getattr(client_handler, "monitoring", False):
        return RESPSerializer.error(
            "cannot SUBSCRIBE while in monitor mode")

    if len(args) < 1:
        return RESPSerializer.error(
            "wrong number of arguments for 'SUBSCRIBE' command")

    bus = get_event_bus()

    # Build combined response for all channels
    response = b""
    for channel in args:
        if not is_valid_channel(channel):
            return RESPSerializer.error(
                f"invalid channel '{channel}', "
                f"valid: {', '.join(sorted(VALID_CHANNELS))} "
                f"(__event:kv supports glob filter, e.g. __event:kv:alert:*)")
        bus.subscribe(client_handler, channel)
        # Redis SUBSCRIBE response: ["subscribe", channel, sub_count]
        total = len(bus.subscriber_channels(client_handler))
        response += RESPSerializer.array(["subscribe", channel, total])

    # Enter subscriber mode
    client_handler.subscribing = True
    return response


async def handle_unsubscribe(args: list[str], context: dict = None) -> bytes:
    """Handle UNSUBSCRIBE [channel ...] — unsubscribe from channels.

    Without arguments, unsubscribes from all channels.
    If no subscriptions remain, exits subscriber mode.
    """
    client_handler = (context or {}).get("client_handler")
    if client_handler is None:
        return RESPSerializer.error("no session context")

    bus = get_event_bus()

    response = b""

    if not args:
        # Unsubscribe from all
        channels = bus.subscriber_channels(client_handler)
        if not channels:
            # Already unsubscribed from everything
            response = RESPSerializer.array(["unsubscribe", None, 0])
        else:
            for channel in channels:
                bus.unsubscribe(client_handler, channel)
                remaining = len(bus.subscriber_channels(client_handler))
                response += RESPSerializer.array(
                    ["unsubscribe", channel, remaining])
    else:
        for channel in args:
            bus.unsubscribe(client_handler, channel)
            remaining = len(bus.subscriber_channels(client_handler))
            response += RESPSerializer.array(
                ["unsubscribe", channel, remaining])

    # Exit subscriber mode if no subscriptions remain
    if not bus.subscriber_channels(client_handler):
        client_handler.subscribing = False

    return response


# Alias System (spec §6.6)

# In-memory alias store: name → (type, target)
# type is "SUB" (resource address) or "RAW" (instrument::scpi_command)
_aliases: dict[str, tuple[str, str]] = {}


async def handle_alias_set(args: list[str], context: dict = None) -> bytes:
    """Handle ALIAS.SET name type target.

    ALIAS.SET rf_power SUB pm1:POWER
    ALIAS.SET sa_pk2pk RAW sa::CALC:MARK1:Y?
    """
    if len(args) < 3:
        return RESPSerializer.error(
            "wrong number of arguments for 'ALIAS.SET' command")

    name = args[0]
    alias_type = args[1].upper()
    target = args[2]

    if alias_type not in ("SUB", "RAW"):
        return RESPSerializer.error(
            f"invalid alias type '{alias_type}', must be SUB or RAW")

    # Validate target format
    if alias_type == "SUB":
        if ":" not in target:
            return RESPSerializer.error(
                "SUB alias target must be instrument:resource")
    elif alias_type == "RAW":
        if "::" not in target:
            return RESPSerializer.error(
                "RAW alias target must be instrument::scpi_command")

    _aliases[name] = (alias_type, target)
    return RESPSerializer.simple_string("OK")


async def handle_alias_get(args: list[str], context: dict = None) -> bytes:
    """Handle ALIAS.GET name — returns [type, target]."""
    if len(args) < 1:
        return RESPSerializer.error(
            "wrong number of arguments for 'ALIAS.GET' command")

    name = args[0]
    alias = _aliases.get(name)
    if alias is None:
        return f"-NOALIAS alias '{name}' does not exist\r\n".encode()

    return RESPSerializer.array([alias[0], alias[1]])


async def handle_alias_del(args: list[str], context: dict = None) -> bytes:
    """Handle ALIAS.DEL name — removes an alias."""
    if len(args) < 1:
        return RESPSerializer.error(
            "wrong number of arguments for 'ALIAS.DEL' command")

    name = args[0]
    if name not in _aliases:
        return f"-NOALIAS alias '{name}' does not exist\r\n".encode()

    del _aliases[name]
    return RESPSerializer.simple_string("OK")


async def handle_alias_list(args: list[str], context: dict = None) -> bytes:
    """Handle ALIAS.LIST — returns all alias names."""
    return RESPSerializer.array(sorted(_aliases.keys()))


async def handle_aread(args: list[str], context: dict = None) -> bytes:
    """Handle AREAD alias_name — read through alias.

    SUB alias → resolves to IREAD instrument:resource
    RAW alias → resolves to IRAW instrument scpi_command
    Requires lock ownership and READY state.
    """
    if len(args) < 1:
        return RESPSerializer.error(
            "wrong number of arguments for 'AREAD' command")

    session_id = (context or {}).get("session_id")
    if session_id is None:
        return RESPSerializer.error("no session context")

    alias_name = args[0]
    alias = _aliases.get(alias_name)
    if alias is None:
        return f"-NOALIAS alias '{alias_name}' does not exist\r\n".encode()

    alias_type, target = alias
    registry = get_registry()

    if alias_type == "SUB":
        # target is "instrument:resource"
        try:
            inst_name, resource = _parse_resource_address(target)
        except ValueError as e:
            return RESPSerializer.error(str(e))

        try:
            inst = registry.get(inst_name)
            if inst.lock_owner != session_id:
                if inst.lock_owner is None:
                    raise IdleError(f"{inst_name} not locked")
                raise LockedError(
                    f"{inst_name} owned by session {inst.lock_owner}")
            result = await registry.read(inst_name, resource)
            return RESPSerializer.bulk_string(result)
        except (IdleError, NotInitError, LockedError, FaultError,
                DriverError) as e:
            return _state_error(e)

    else:  # RAW
        # target is "instrument::scpi_command"
        parts = target.split("::", 1)
        inst_name = parts[0]
        scpi_cmd = parts[1] if len(parts) > 1 else ""

        try:
            inst = registry.get(inst_name)
            if inst.lock_owner != session_id:
                if inst.lock_owner is None:
                    raise IdleError(f"{inst_name} not locked")
                raise LockedError(
                    f"{inst_name} owned by session {inst.lock_owner}")
            result = await registry.passthrough(inst_name, scpi_cmd)
            return RESPSerializer.bulk_string(result)
        except (IdleError, NotInitError, LockedError, FaultError,
                DriverError) as e:
            return _state_error(e)


async def handle_awrite(args: list[str], context: dict = None) -> bytes:
    """Handle AWRITE alias_name value — write through SUB alias.

    Only SUB aliases support AWRITE. RAW aliases are read-only.
    Requires lock ownership and READY state.
    """
    if len(args) < 2:
        return RESPSerializer.error(
            "wrong number of arguments for 'AWRITE' command")

    session_id = (context or {}).get("session_id")
    if session_id is None:
        return RESPSerializer.error("no session context")

    alias_name = args[0]
    value = args[1]

    alias = _aliases.get(alias_name)
    if alias is None:
        return f"-NOALIAS alias '{alias_name}' does not exist\r\n".encode()

    alias_type, target = alias

    if alias_type != "SUB":
        return RESPSerializer.error(
            "AWRITE only supported for SUB aliases, not RAW")

    try:
        inst_name, resource = _parse_resource_address(target)
    except ValueError as e:
        return RESPSerializer.error(str(e))

    registry = get_registry()
    try:
        inst = registry.get(inst_name)
        if inst.lock_owner != session_id:
            if inst.lock_owner is None:
                raise IdleError(f"{inst_name} not locked")
            raise LockedError(
                f"{inst_name} owned by session {inst.lock_owner}")
        await registry.write(inst_name, resource, value)
        return RESPSerializer.simple_string("OK")
    except (IdleError, NotInitError, LockedError, FaultError,
            DriverError) as e:
        return _state_error(e)


# -- JOURNAL command (spec §6.1) --

async def handle_journal(args: list[str], context: dict = None) -> bytes:
    """Handle JOURNAL [count | +offset [count] | ALL | CLEAR] — tail-style.

    JOURNAL           → last 10 entries
    JOURNAL 50        → last 50 entries
    JOURNAL +1        → from first entry to end (0-based: +1 = offset 0)
    JOURNAL +20       → from 20th entry to end
    JOURNAL +1 10     → first 10 entries
    JOURNAL ALL       → all entries
    JOURNAL CLEAR     → clear journal, return count cleared
    """
    journal = get_journal()

    if not args:
        entries = journal.tail(10)
    elif args[0].upper() == "ALL":
        entries = journal.all()
    elif args[0].upper() == "CLEAR":
        count = journal.clear()
        return RESPSerializer.integer(count)
    elif args[0].startswith("+"):
        # tail -n +N style: from position N (1-based, like tail)
        try:
            offset = int(args[0][1:]) - 1  # convert 1-based to 0-based
            if offset < 0:
                offset = 0
        except ValueError:
            return RESPSerializer.error("invalid offset")
        count = None
        if len(args) > 1:
            try:
                count = int(args[1])
                if count < 0:
                    return RESPSerializer.error("count must be positive")
            except ValueError:
                return RESPSerializer.error("invalid count")
        entries = journal.head(offset, count)
    else:
        try:
            count = int(args[0])
            if count < 0:
                return RESPSerializer.error("count must be positive")
        except ValueError:
            return RESPSerializer.error(
                "usage: JOURNAL [count | +offset [count] | ALL | CLEAR]")
        entries = journal.tail(count)

    # Return as array of formatted strings
    return RESPSerializer.array([e.to_str() for e in entries])


# -- DUMP command (spec §6.1) --

async def handle_dump(args: list[str], context: dict = None) -> bytes:
    """Handle DUMP — JSON snapshot of server state.

    Returns JSON with sections: kv, instruments, locks, sessions.
    Not available in monitor or subscriber mode.
    """
    client_handler = (context or {}).get("client_handler")

    # Block monitor/subscriber
    if client_handler is not None:
        if getattr(client_handler, "monitoring", False):
            return RESPSerializer.error(
                "cannot DUMP while in monitor mode")
        if getattr(client_handler, "subscribing", False):
            return RESPSerializer.error(
                "cannot DUMP while in subscriber mode")

    from .server import get_server

    store = get_store()
    registry = get_registry()
    server = get_server()

    # KV store (client keys only)
    kv = {}
    for k, v in store._data.items():
        if not store._is_reserved(k):
            kv[k] = v

    # Instruments
    instruments = {}
    for name in registry.list_instruments():
        inst = registry.get(name)
        instruments[name] = {
            "state": inst.state.value,
            "driver": type(inst.driver).__name__ if inst.driver else None,
            "lock_owner": inst.lock_owner,
            "total_calls": inst.total_calls,
            "total_errors": inst.total_errors,
        }

    # Locks
    locks = {}
    for name in registry.list_instruments():
        inst = registry.get(name)
        if inst.lock_owner is not None:
            locks[name] = inst.lock_owner

    # Sessions
    sessions = []
    if server:
        for ch in server.client_handlers.values():
            sessions.append({
                "id": ch.client_id,
                "name": getattr(ch, "name", None) or "",
                "address": getattr(ch, "address", ""),
                "cmd_count": getattr(ch, "cmd_count", 0),
            })

    dump = {
        "version": VERSION,
        "timestamp": time.time(),
        "kv": kv,
        "instruments": instruments,
        "locks": locks,
        "sessions": sessions,
    }

    return RESPSerializer.bulk_string(json.dumps(dump, indent=2))


# -- KGETALL command --

async def handle_getall(args: list[str], context: dict = None) -> bytes:
    """Handle KGETALL [prefix] — get all key-value pairs.

    KGETALL            → all client keys (excludes reserved)
    KGETALL alert:     → keys starting with 'alert:'

    Returns flat array [key1, val1, key2, val2, ...] like Redis HGETALL.
    """
    store = get_store()
    prefix = args[0] if args else None

    result = []
    for k, v in store._data.items():
        if store._is_reserved(k):
            continue
        if prefix and not k.startswith(prefix):
            continue
        result.append(k)
        result.append(v)

    return RESPSerializer.array(result)


# Global dispatcher instance
dispatcher = CommandDispatcher()

# Register server commands (no prefix — subcommands for CLIENT/COMMAND/DRIVER)
dispatcher.register("PING", handle_ping)
dispatcher.register("COMMAND LIST", handle_command)
dispatcher.register("INFO", handle_info)
dispatcher.register("TIME", handle_time)
dispatcher.register("CLIENT ID", handle_clientid)
dispatcher.register("CLIENT LIST", handle_clientlist)
dispatcher.register("CLIENT NAME", handle_clientname)
dispatcher.register("MONITOR", handle_monitor)
dispatcher.register("SUBSCRIBE", handle_subscribe)
dispatcher.register("UNSUBSCRIBE", handle_unsubscribe)
dispatcher.register("DUMP", handle_dump)
dispatcher.register("JOURNAL", handle_journal)

# Register KV commands (spec §6.2) — K prefix
dispatcher.register("KSET", handle_set)
dispatcher.register("KGET", handle_get)
dispatcher.register("KMGET", handle_mget)
dispatcher.register("KMSET", handle_mset)
dispatcher.register("KDEL", handle_del)
dispatcher.register("KEXISTS", handle_exists)
dispatcher.register("KKEYS", handle_keys)
dispatcher.register("KDBSIZE", handle_dbsize)
dispatcher.register("KFLUSH", handle_flushdb)
dispatcher.register("KGETALL", handle_getall)

# Register instrument commands (spec §6.3) — I prefix
dispatcher.register("IADD", handle_instrument_add)
dispatcher.register("IREMOVE", handle_instrument_remove)
dispatcher.register("IINIT", handle_instrument_init)
dispatcher.register("IINFO", handle_instrument_info)
dispatcher.register("ILIST", handle_instrument_list)
dispatcher.register("IRESOURCES", handle_instrument_resources)
dispatcher.register("IRESET", handle_instrument_reset)
dispatcher.register("IALIGN", handle_align)
dispatcher.register("DRIVER LIST", handle_driver_list)

# Register lock commands (spec §6.5) — I prefix
dispatcher.register("ILOCK", handle_lock)
dispatcher.register("IUNLOCK", handle_unlock)
dispatcher.register("ILOCKS", handle_locks)

# Register resource commands (spec §6.4) — I prefix
dispatcher.register("IREAD", handle_read)
dispatcher.register("IWRITE", handle_write)
dispatcher.register("IRAW", handle_raw)
dispatcher.register("IMREAD", handle_readmulti)
dispatcher.register("ILOAD", handle_load)
dispatcher.register("ISAVE", handle_save)

# Register alias commands (spec §6.6) — ALIAS subcommand + A prefix
dispatcher.register("ALIAS SET", handle_alias_set)
dispatcher.register("ALIAS GET", handle_alias_get)
dispatcher.register("ALIAS DEL", handle_alias_del)
dispatcher.register("ALIAS LIST", handle_alias_list)
dispatcher.register("AREAD", handle_aread)
dispatcher.register("AWRITE", handle_awrite)
