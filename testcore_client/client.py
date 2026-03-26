# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""TestCore client — main API class."""

from __future__ import annotations

import json
from typing import Any, Callable, Iterator

from .connection import Connection
from .exceptions import TestCoreError, raise_for_error


class TestCore:
    """Synchronous client for TestCore Server.

    Usage:
        tc = TestCore()
        tc.ping()
        tc.kset("meas:power", 23.4)
        val = tc.kget("meas:power")

    Or as context manager:
        with TestCore(name="my_test") as tc:
            tc.ilock("vsg")
            tc.iread("vsg", "FREQ")
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 6399,
                 timeout: float = 5.0, name: str | None = None):
        self._conn = Connection(host, port, timeout)
        self._conn.connect()
        self._name = name
        if name:
            self.client_name(name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        try:
            if self._conn.connected:
                self._conn.close()
        except Exception:
            pass

    def close(self):
        """Close the connection."""
        self._conn.close()

    def reconnect(self):
        """Close and re-open the connection."""
        self._conn.close()
        self._conn.connect()
        if self._name:
            self.client_name(self._name)

    def _cmd(self, *args: str) -> Any:
        """Send command and return parsed response."""
        return self._conn.send_command(*args)

    def pipeline(self) -> Pipeline:
        """Create a pipeline for batching commands.

        Usage:
            with tc.pipeline() as pipe:
                pipe.kset("a", "1")
                pipe.kset("b", "2")
                pipe.kget("a")
                results = pipe.execute()
            # results == [True, True, "1"]
        """
        return Pipeline(self._conn)

    # ------------------------------------------------------------------
    # Server commands (§6.1)
    # ------------------------------------------------------------------

    def ping(self, message: str | None = None):
        """PING [message]. Returns True or echo string."""
        if message is not None:
            return self._cmd("PING", message)
        r = self._cmd("PING")
        return True if r == "PONG" else r

    def time(self) -> tuple[int, int]:
        """TIME. Returns (unix_seconds, microseconds)."""
        r = self._cmd("TIME")
        return (int(r[0]), int(r[1]))

    def info(self, section: str | None = None) -> str:
        """INFO [section]. Returns info string."""
        if section:
            return self._cmd("INFO", section)
        return self._cmd("INFO")

    def client_id(self) -> int:
        """CLIENT ID. Returns session id."""
        return self._cmd("CLIENT", "ID")

    def client_list(self) -> list[dict]:
        """CLIENT LIST. Returns list of client info dicts."""
        raw = self._cmd("CLIENT", "LIST")
        clients = []
        for line in raw.strip().split("\n"):
            if not line.strip():
                continue
            entry = {}
            for pair in line.strip().split(" "):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    entry[k] = v
            clients.append(entry)
        return clients

    def client_name(self, name: str) -> bool:
        """CLIENT NAME <name>. Set client name. Returns True."""
        r = self._cmd("CLIENT", "NAME", name)
        return r == "OK"

    def command_list(self, pattern: str | None = None) -> list[str]:
        """COMMAND LIST [pattern]. Returns list of command names."""
        if pattern:
            return self._cmd("COMMAND", "LIST", pattern)
        return self._cmd("COMMAND", "LIST")

    # ------------------------------------------------------------------
    # Key-Value commands (§6.2)
    # ------------------------------------------------------------------

    def kset(self, key: str, value: str | int | float,
             nx: bool = False, xx: bool = False,
             ro: bool = False) -> bool:
        """KSET key value [NX|XX] [RO]. Returns True if set, False if condition failed.

        RO marks the key as read-only for other clients. Only the creating
        session can overwrite or delete it. Protection is released on disconnect.
        """
        args = ["KSET", key, str(value)]
        if nx:
            args.append("NX")
        elif xx:
            args.append("XX")
        if ro:
            args.append("RO")
        r = self._cmd(*args)
        return r == "OK"

    def kget(self, key: str) -> str | None:
        """KGET key. Returns value or None."""
        return self._cmd("KGET", key)

    def kmget(self, *keys: str) -> list[str | None]:
        """KMGET key [key ...]. Returns list of values."""
        return self._cmd("KMGET", *keys)

    def kmset(self, mapping: dict[str, str | int | float] | None = None,
              **kwargs) -> bool:
        """KMSET key val [key val ...]. Accepts dict or kwargs."""
        pairs = mapping or kwargs
        args = ["KMSET"]
        for k, v in pairs.items():
            args.extend([k, str(v)])
        r = self._cmd(*args)
        return r == "OK"

    def kdel(self, *keys: str) -> int:
        """KDEL key [key ...]. Returns count deleted."""
        return self._cmd("KDEL", *keys)

    def kexists(self, *keys: str) -> int:
        """KEXISTS key [key ...]. Returns count existing."""
        return self._cmd("KEXISTS", *keys)

    def kkeys(self, pattern: str = "*") -> list[str]:
        """KKEYS [pattern]. Returns matching keys."""
        r = self._cmd("KKEYS", pattern)
        if r is None:
            return []
        if isinstance(r, str):
            return [r]
        return r

    def kdbsize(self) -> int:
        """KDBSIZE. Returns number of keys."""
        return self._cmd("KDBSIZE")

    def kflush(self) -> bool:
        """KFLUSH. Delete all user keys. Returns True."""
        r = self._cmd("KFLUSH")
        return r == "OK"

    def kgetall(self, prefix: str | None = None) -> dict[str, str]:
        """KGETALL [prefix]. Returns dict of key-value pairs."""
        args = ["KGETALL"]
        if prefix:
            args.append(prefix)
        r = self._cmd(*args)
        if not r:
            return {}
        # Flat array [k1, v1, k2, v2, ...] → dict
        return dict(zip(r[::2], r[1::2]))

    # ------------------------------------------------------------------
    # Server introspection (§6.1)
    # ------------------------------------------------------------------

    def dump(self) -> dict:
        """DUMP. Returns parsed JSON dict of server state."""
        r = self._cmd("DUMP")
        return json.loads(r)

    def journal(self, *args: str) -> list[str]:
        """JOURNAL [count | +offset [count] | ALL | CLEAR].

        Returns list of formatted journal entries, or int for CLEAR.
        """
        r = self._cmd("JOURNAL", *args)
        if isinstance(r, int):
            return r
        if r is None:
            return []
        if isinstance(r, str):
            return [r]
        return r

    # ------------------------------------------------------------------
    # Instrument lifecycle (§6.3, §6.4)
    # ------------------------------------------------------------------

    def iadd(self, name: str, driver: str, address: str | None = None,
             **kwargs) -> bool:
        """IADD name driver [address] [key=value ...]. Returns True.

        Example:
            tc.iadd("awg", "agilent33500", "TCPIP0::192.168.1.50::inst0::INSTR")
            tc.iadd("pm", "keysight_u2000", "COM3", baudrate=115200, timeout=10000)
            tc.iadd("sim", "dryrun")
        """
        args = ["IADD", name, driver]
        if address:
            args.append(address)
        for k, v in kwargs.items():
            args.append(f"{k}={v}")
        r = self._cmd(*args)
        return r == "OK"

    def iremove(self, name: str) -> bool:
        """IREMOVE name. Returns True."""
        r = self._cmd("IREMOVE", name)
        return r == "OK"

    def ilist(self) -> list[str]:
        """ILIST. Returns list of instrument names."""
        r = self._cmd("ILIST")
        if r is None:
            return []
        if isinstance(r, str):
            return [r]
        return r

    def iinfo(self, name: str) -> str:
        """IINFO name. Returns instrument info string."""
        return self._cmd("IINFO", name)

    def iresources(self, name: str) -> list[str]:
        """IRESOURCES name. Returns list of resource names."""
        r = self._cmd("IRESOURCES", name)
        if r is None:
            return []
        if isinstance(r, str):
            return [r]
        return r

    def driver_list(self) -> list[str]:
        """DRIVER LIST. Returns list of loaded drivers."""
        r = self._cmd("DRIVER", "LIST")
        if r is None:
            return []
        if isinstance(r, str):
            return [r]
        return r

    # ------------------------------------------------------------------
    # Lock (§6.5)
    # ------------------------------------------------------------------

    def ilock(self, *instruments: str) -> bool:
        """ILOCK name [name ...]. Lock instruments. Returns True."""
        r = self._cmd("ILOCK", *instruments)
        return r == "OK"

    def iunlock(self, *instruments: str) -> bool:
        """IUNLOCK name [name ...]. Unlock instruments. Returns True."""
        r = self._cmd("IUNLOCK", *instruments)
        return r == "OK"

    def ilocked(self) -> dict[str, int]:
        """ILOCKED. Returns dict {instrument: session_id}."""
        r = self._cmd("ILOCKED")
        if not r:
            return {}
        # Response is array of "name:session:id" strings
        result = {}
        for entry in r:
            # Format: "instrument:session:session_id"
            parts = entry.rsplit(":", 1)
            if len(parts) == 2:
                name = parts[0].replace(":session", "")
                result[name] = int(parts[1])
        return result

    # ------------------------------------------------------------------
    # Init / Reset / Align (§6.4)
    # ------------------------------------------------------------------

    def iinit(self, name: str, config_path: str | None = None,
              selftest: bool = False) -> bool:
        """IINIT name [config_path] [TST]. Initialize instrument. Returns True."""
        args = ["IINIT", name]
        if config_path:
            args.append(config_path)
        if selftest:
            args.append("TST")
        r = self._cmd(*args)
        return r == "OK"

    def ireset(self, name: str) -> bool:
        """IRESET name. Reset instrument. Returns True."""
        r = self._cmd("IRESET", name)
        return r == "OK"

    def ialign(self, name: str) -> bool:
        """IALIGN name. Align/calibrate instrument. Returns True."""
        r = self._cmd("IALIGN", name)
        return r == "OK"

    # ------------------------------------------------------------------
    # Read / Write / Raw / Load (§6.5)
    # ------------------------------------------------------------------

    def iread(self, instrument: str, resource: str) -> str:
        """IREAD instrument resource. Returns value string.

        Example:
            tc.iread("awg", "CH1:FREQ")
        """
        return self._cmd("IREAD", instrument, resource)

    def imread(self, *resources: str) -> list[str | None]:
        """IMREAD instrument:resource [instrument:resource ...]. Returns list of values.

        Example:
            tc.imread("awg:CH1:FREQ", "psu:VOLT", "dmm:DC_VOLTAGE")
        """
        r = self._cmd("IMREAD", *resources)
        if isinstance(r, list):
            return r
        return [r]

    def iwrite(self, instrument: str, resource: str,
               value: str | int | float) -> bool:
        """IWRITE instrument resource value. Returns True.

        Example:
            tc.iwrite("awg", "CH1:FREQ", 1000000000)
            tc.iwrite("psu", "VOLT", 3.3)
        """
        r = self._cmd("IWRITE", instrument, resource, str(value))
        return r == "OK"

    def iraw(self, instrument: str, scpi_command: str) -> str | None:
        """IRAW instrument scpi_command. Returns response or None."""
        r = self._cmd("IRAW", instrument, scpi_command)
        if r == "OK":
            return None
        return r

    def iload(self, instrument: str, target: str, file_path: str) -> str:
        """ILOAD instrument target file_path. Load file into instrument.

        Returns status string (e.g. '1024 points loaded').
        """
        return self._cmd("ILOAD", instrument, target, file_path)

    def isave(self, instrument: str, target: str, file_path: str) -> str:
        """ISAVE instrument target file_path. Save data from instrument to file.

        Target is driver-specific: 'SCREEN', 'TRACE1', 'DATA', etc.
        Returns status string (e.g. '72 bytes saved').
        """
        return self._cmd("ISAVE", instrument, target, file_path)

    # ------------------------------------------------------------------
    # Alias (§6.6)
    # ------------------------------------------------------------------

    def alias_set(self, name: str, alias_type: str, target: str) -> bool:
        """ALIAS SET name type target. Returns True."""
        r = self._cmd("ALIAS", "SET", name, alias_type, target)
        return r == "OK"

    def alias_get(self, name: str) -> tuple[str, str]:
        """ALIAS GET name. Returns (type, target)."""
        r = self._cmd("ALIAS", "GET", name)
        return (r[0], r[1])

    def alias_del(self, name: str) -> bool:
        """ALIAS DEL name. Returns True."""
        r = self._cmd("ALIAS", "DEL", name)
        return r == "OK"

    def alias_list(self) -> list[str]:
        """ALIAS LIST. Returns list of alias names."""
        r = self._cmd("ALIAS", "LIST")
        if r is None:
            return []
        if isinstance(r, str):
            return [r]
        return r

    def aread(self, alias: str) -> str:
        """AREAD alias. Read through alias. Returns value."""
        return self._cmd("AREAD", alias)

    def awrite(self, alias: str, value: str | int | float) -> bool:
        """AWRITE alias value. Write through SUB alias. Returns True."""
        r = self._cmd("AWRITE", alias, str(value))
        return r == "OK"

    # ------------------------------------------------------------------
    # Events / Subscribe (§5.3)
    # ------------------------------------------------------------------

    def subscribe(self, *channels: str,
                  callback: Callable[[str, dict], None] | None = None):
        """Subscribe to event channels and call callback for each event.

        Blocks the current thread. Use in a separate thread for concurrent
        operation with command sending on another TestCore instance.

        The callback receives (channel: str, payload: dict).
        Close the connection or raise KeyboardInterrupt to exit.
        """
        if not channels:
            raise ValueError("At least one channel required")

        # Send SUBSCRIBE command
        self._conn._send_packed(
            self._conn._encode_command(("SUBSCRIBE", *channels))
        )

        # Read subscribe confirmations
        for _ in channels:
            self._conn._read_raw_response()

        # Read events until connection closed
        try:
            while True:
                msg = self._conn._read_raw_response()
                if isinstance(msg, list) and len(msg) >= 3 and msg[0] == "event":
                    channel = msg[1]
                    try:
                        payload = json.loads(msg[2])
                    except (json.JSONDecodeError, TypeError):
                        payload = {"raw": msg[2]}
                    if callback:
                        callback(channel, payload)
        except (ConnectionError, KeyboardInterrupt):
            pass

    def listen(self, *channels: str) -> Iterator[tuple[str, dict]]:
        """Subscribe to event channels and yield (channel, payload) tuples.

        Usage:
            for channel, payload in tc.listen("__event:kv"):
                print(channel, payload)
                if done:
                    break

        After break or exhaustion, sends UNSUBSCRIBE to exit subscriber mode.
        """
        if not channels:
            raise ValueError("At least one channel required")

        self._conn._send_packed(
            self._conn._encode_command(("SUBSCRIBE", *channels))
        )

        # Read subscribe confirmations
        for _ in channels:
            self._conn._read_raw_response()

        try:
            while True:
                msg = self._conn._read_raw_response()
                if isinstance(msg, list) and len(msg) >= 3 and msg[0] == "event":
                    channel = msg[1]
                    try:
                        payload = json.loads(msg[2])
                    except (json.JSONDecodeError, TypeError):
                        payload = {"raw": msg[2]}
                    yield (channel, payload)
        except (ConnectionError, KeyboardInterrupt):
            pass
        finally:
            # Try to unsubscribe to exit subscriber mode
            try:
                self._conn._send_packed(
                    self._conn._encode_command(("UNSUBSCRIBE",))
                )
                # Drain unsubscribe confirmations
                for _ in range(len(channels)):
                    try:
                        self._conn._read_raw_response()
                    except Exception:
                        break
            except Exception:
                pass


class Pipeline:
    """Batches multiple commands into a single TCP round-trip.

    Usage:
        with tc.pipeline() as pipe:
            pipe.kset("a", "1")
            pipe.kset("b", "2")
            pipe.kget("a")
            results = pipe.execute()
        # results == [True, True, "1"]

    Commands are buffered locally and sent together on execute().
    Each method returns the Pipeline itself for optional chaining.

    Introspection/mode commands (DUMP, JOURNAL, MONITOR, SUBSCRIBE,
    UNSUBSCRIBE) are blocked — they don't belong in batch operations.
    """

    # Commands that cannot be used in a pipeline
    _BLOCKED: frozenset[str] = frozenset({
        "DUMP", "JOURNAL", "MONITOR", "SUBSCRIBE", "UNSUBSCRIBE",
    })

    def __init__(self, conn: Connection):
        self._conn = conn
        self._commands: list[tuple[str, ...]] = []
        self._parsers: list[Callable[[Any], Any]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def _queue(self, parser: Callable[[Any], Any], *args: str) -> Pipeline:
        cmd_name = args[0].upper() if args else ""
        if cmd_name in self._BLOCKED:
            raise TestCoreError(
                f"command '{cmd_name}' cannot be used in a pipeline")
        self._commands.append(args)
        self._parsers.append(parser)
        return self

    def execute(self) -> list[Any]:
        """Send all buffered commands and return list of results.

        Error responses from the server are raised as exceptions only if
        the result is accessed. In the returned list, errors appear as
        exception instances.
        """
        if not self._commands:
            return []
        raw = self._conn.send_pipeline(self._commands)
        results = []
        for raw_val, parser in zip(raw, self._parsers):
            if isinstance(raw_val, Exception):
                results.append(raw_val)
            else:
                try:
                    results.append(parser(raw_val))
                except Exception as e:
                    results.append(e)
        self._commands.clear()
        self._parsers.clear()
        return results

    def reset(self):
        """Discard all buffered commands."""
        self._commands.clear()
        self._parsers.clear()

    def __len__(self) -> int:
        return len(self._commands)

    # -- Parsers (static, reusable) ---

    @staticmethod
    def _parse_ok(r: Any) -> bool:
        return r == "OK"

    @staticmethod
    def _parse_identity(r: Any) -> Any:
        return r

    @staticmethod
    def _parse_list(r: Any) -> list:
        if r is None:
            return []
        if isinstance(r, str):
            return [r]
        return r

    # -- Server commands ---

    def ping(self, message: str | None = None) -> Pipeline:
        if message is not None:
            return self._queue(self._parse_identity, "PING", message)
        return self._queue(lambda r: True if r == "PONG" else r, "PING")

    # -- KV commands ---

    def kset(self, key: str, value: str | int | float,
             nx: bool = False, xx: bool = False,
             ro: bool = False) -> Pipeline:
        args = ["KSET", key, str(value)]
        if nx:
            args.append("NX")
        elif xx:
            args.append("XX")
        if ro:
            args.append("RO")
        return self._queue(self._parse_ok, *args)

    def kget(self, key: str) -> Pipeline:
        return self._queue(self._parse_identity, "KGET", key)

    def kmget(self, *keys: str) -> Pipeline:
        return self._queue(self._parse_identity, "KMGET", *keys)

    def kmset(self, mapping: dict[str, str | int | float] | None = None,
              **kwargs) -> Pipeline:
        pairs = mapping or kwargs
        args = ["KMSET"]
        for k, v in pairs.items():
            args.extend([k, str(v)])
        return self._queue(self._parse_ok, *args)

    def kdel(self, *keys: str) -> Pipeline:
        return self._queue(self._parse_identity, "KDEL", *keys)

    def kexists(self, *keys: str) -> Pipeline:
        return self._queue(self._parse_identity, "KEXISTS", *keys)

    def kkeys(self, pattern: str = "*") -> Pipeline:
        return self._queue(self._parse_list, "KKEYS", pattern)

    def kdbsize(self) -> Pipeline:
        return self._queue(self._parse_identity, "KDBSIZE")

    def kflush(self) -> Pipeline:
        return self._queue(self._parse_ok, "KFLUSH")

    def kgetall(self, prefix: str | None = None) -> Pipeline:
        args = ["KGETALL"]
        if prefix:
            args.append(prefix)
        def parse(r):
            if not r:
                return {}
            return dict(zip(r[::2], r[1::2]))
        return self._queue(parse, *args)

    # -- Instrument commands ---

    def iadd(self, name: str, driver: str, address: str | None = None,
             **kwargs) -> Pipeline:
        args = ["IADD", name, driver]
        if address:
            args.append(address)
        for k, v in kwargs.items():
            args.append(f"{k}={v}")
        return self._queue(self._parse_ok, *args)

    def iremove(self, name: str) -> Pipeline:
        return self._queue(self._parse_ok, "IREMOVE", name)

    def ilist(self) -> Pipeline:
        return self._queue(self._parse_list, "ILIST")

    def ilock(self, *instruments: str) -> Pipeline:
        return self._queue(self._parse_ok, "ILOCK", *instruments)

    def iunlock(self, *instruments: str) -> Pipeline:
        return self._queue(self._parse_ok, "IUNLOCK", *instruments)

    def iinit(self, name: str, config_path: str | None = None,
              selftest: bool = False) -> Pipeline:
        args = ["IINIT", name]
        if config_path:
            args.append(config_path)
        if selftest:
            args.append("TST")
        return self._queue(self._parse_ok, *args)

    def iread(self, instrument: str, resource: str) -> Pipeline:
        return self._queue(self._parse_identity, "IREAD", instrument, resource)

    def imread(self, *resources: str) -> Pipeline:
        def parse(r):
            return r if isinstance(r, list) else [r]
        return self._queue(parse, "IMREAD", *resources)

    def iwrite(self, instrument: str, resource: str,
               value: str | int | float) -> Pipeline:
        return self._queue(self._parse_ok, "IWRITE", instrument, resource, str(value))

    def iraw(self, instrument: str, scpi_command: str) -> Pipeline:
        return self._queue(
            lambda r: None if r == "OK" else r, "IRAW", instrument, scpi_command
        )

    def iload(self, instrument: str, target: str, file_path: str) -> Pipeline:
        return self._queue(self._parse_identity, "ILOAD", instrument, target, file_path)

    def isave(self, instrument: str, target: str, file_path: str) -> Pipeline:
        return self._queue(self._parse_identity, "ISAVE", instrument, target, file_path)

    # -- Alias commands ---

    def alias_set(self, name: str, alias_type: str, target: str) -> Pipeline:
        return self._queue(self._parse_ok, "ALIAS", "SET", name, alias_type, target)

    def aread(self, alias: str) -> Pipeline:
        return self._queue(self._parse_identity, "AREAD", alias)

    def awrite(self, alias: str, value: str | int | float) -> Pipeline:
        return self._queue(self._parse_ok, "AWRITE", alias, str(value))
