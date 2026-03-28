# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Integration tests — real TCP server, real RESP protocol, real client workflows.

These tests spin up a TestCoreServer on a random port and connect actual TCP
clients. Commands are sent as RESP arrays and inline text, responses are parsed
with the real RESPParser.  This exercises the full dispatch pipeline end-to-end.
"""

import asyncio
import pytest
from testcore.server import TestCoreServer
from testcore.protocol import RESPParser, RESPSerializer
from testcore.store import get_store
from testcore.instruments import get_registry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_state():
    """Reset store, registry, event bus before each test."""
    store = get_store()
    store._data.clear()
    store._owners.clear()
    store._meas.clear()
    registry = get_registry()
    for name in list(registry._instruments.keys()):
        try:
            registry.remove(name)
        except Exception:
            pass
    registry._instruments.clear()
    # Reset event bus
    import testcore.events as events_mod
    events_mod._event_bus = None


@pytest.fixture
async def server():
    """Start a real TestCoreServer on a random port."""
    srv = TestCoreServer(host="127.0.0.1", port=0)
    task = asyncio.create_task(srv.start())
    await asyncio.sleep(0.1)
    srv.test_port = srv.server.sockets[0].getsockname()[1]
    yield srv
    await srv.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


class Client:
    """Thin helper wrapping an asyncio TCP connection to the test server."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.parser = RESPParser()

    @classmethod
    async def connect(cls, port: int) -> "Client":
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        return cls(reader, writer)

    async def send(self, command: list[str]):
        """Send a command as a RESP array."""
        data = RESPSerializer.array(command)
        self.writer.write(data)
        await self.writer.drain()

    async def send_inline(self, line: str):
        """Send an inline command (plain text + \\r\\n)."""
        self.writer.write(f"{line}\r\n".encode())
        await self.writer.drain()

    async def read(self, timeout: float = 2.0):
        """Read and parse one RESP message."""
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError("No RESP message received in time")
            try:
                data = await asyncio.wait_for(
                    self.reader.read(4096), timeout=remaining
                )
            except asyncio.TimeoutError:
                raise TimeoutError("No RESP message received in time")
            if not data:
                raise ConnectionError("Connection closed")
            messages = self.parser.feed(data)
            if messages:
                return messages[0]

    async def read_raw(self, timeout: float = 2.0) -> bytes:
        """Read raw bytes from the socket."""
        try:
            return await asyncio.wait_for(
                self.reader.read(4096), timeout=timeout
            )
        except asyncio.TimeoutError:
            return b""

    async def close(self):
        self.writer.close()
        await self.writer.wait_closed()


# ---------------------------------------------------------------------------
# Basic command tests
# ---------------------------------------------------------------------------

class TestBasicCommands:
    """Verify core commands work end-to-end over TCP."""

    @pytest.mark.asyncio
    async def test_ping_pong(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["PING"])
            assert await c.read() == "PONG"
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_ping_with_message(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["PING", "hello"])
            assert await c.read() == "hello"
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_unknown_command(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["BOGUS"])
            resp = await c.read()
            assert "ERR" in resp
            assert "BOGUS" in resp
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_cmdlist_returns_array(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["COMMAND", "LIST"])
            resp = await c.read()
            assert isinstance(resp, list)
            assert "PING" in resp
            assert "KSET" in resp
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_time(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["TIME"])
            resp = await c.read()
            assert isinstance(resp, list)
            assert len(resp) == 2
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_clientid(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["CLIENT", "ID"])
            resp = await c.read()
            assert isinstance(resp, int)
            assert resp >= 1
        finally:
            await c.close()


# ---------------------------------------------------------------------------
# Inline command tests
# ---------------------------------------------------------------------------

class TestInlineCommands:
    """Verify the server accepts inline (plain text) commands."""

    @pytest.mark.asyncio
    async def test_inline_ping(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send_inline("PING")
            assert await c.read() == "PONG"
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_inline_kset_kget(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send_inline('KSET mykey myvalue')
            assert await c.read() == "OK"

            await c.send_inline("KGET mykey")
            assert await c.read() == "myvalue"
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_inline_quoted_value(self, server):
        """KSET key "hello world" should preserve the space inside quotes."""
        c = await Client.connect(server.test_port)
        try:
            await c.send_inline('KSET greeting "hello world"')
            assert await c.read() == "OK"

            await c.send_inline("KGET greeting")
            assert await c.read() == "hello world"
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_inline_case_insensitive(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send_inline("ping")
            assert await c.read() == "PONG"
        finally:
            await c.close()


# ---------------------------------------------------------------------------
# KV store workflow
# ---------------------------------------------------------------------------

class TestKVWorkflow:
    """End-to-end key-value operations."""

    @pytest.mark.asyncio
    async def test_set_get_del(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["KSET", "temp", "22.5"])
            assert await c.read() == "OK"

            await c.send(["KGET", "temp"])
            assert await c.read() == "22.5"

            await c.send(["KDEL", "temp"])
            assert await c.read() == 1  # integer: count of deleted keys

            await c.send(["KGET", "temp"])
            resp = await c.read()
            assert resp is None  # nil
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_kset_overwrite(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["KSET", "x", "1"])
            assert await c.read() == "OK"

            await c.send(["KSET", "x", "2"])
            assert await c.read() == "OK"

            await c.send(["KGET", "x"])
            assert await c.read() == "2"
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_kkeys_pattern(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["KSET", "sensor:temp", "22"])
            await c.read()
            await c.send(["KSET", "sensor:hum", "45"])
            await c.read()
            await c.send(["KSET", "config:rate", "100"])
            await c.read()

            await c.send(["KKEYS", "sensor:*"])
            resp = await c.read()
            assert isinstance(resp, list)
            assert "sensor:temp" in resp
            assert "sensor:hum" in resp
            assert "config:rate" not in resp
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_multi_client_kv_isolation(self, server):
        """Client A writes, client B reads — shared store."""
        a = await Client.connect(server.test_port)
        b = await Client.connect(server.test_port)
        try:
            await a.send(["KSET", "shared", "from_a"])
            assert await a.read() == "OK"

            await b.send(["KGET", "shared"])
            assert await b.read() == "from_a"
        finally:
            await a.close()
            await b.close()

    @pytest.mark.asyncio
    async def test_kmget(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["KSET", "a", "1"])
            await c.read()
            await c.send(["KSET", "b", "2"])
            await c.read()

            await c.send(["KMGET", "a", "b", "missing"])
            resp = await c.read()
            assert isinstance(resp, list)
            assert resp[0] == "1"
            assert resp[1] == "2"
            assert resp[2] is None
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_kmset(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["KMSET", "x", "10", "y", "20"])
            assert await c.read() == "OK"

            await c.send(["KGET", "x"])
            assert await c.read() == "10"
            await c.send(["KGET", "y"])
            assert await c.read() == "20"
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_kexists(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["KSET", "a", "1"])
            await c.read()

            await c.send(["KEXISTS", "a", "missing"])
            resp = await c.read()
            assert resp == 1  # only "a" exists
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_kdbsize(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["KDBSIZE"])
            assert await c.read() == 0

            await c.send(["KSET", "k1", "v1"])
            await c.read()
            await c.send(["KSET", "k2", "v2"])
            await c.read()

            await c.send(["KDBSIZE"])
            assert await c.read() == 2
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_kflush(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["KSET", "a", "1"])
            await c.read()
            await c.send(["KSET", "b", "2"])
            await c.read()

            await c.send(["KFLUSH"])
            assert await c.read() == "OK"

            await c.send(["KDBSIZE"])
            assert await c.read() == 0
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_kget_nonexistent(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["KGET", "no_such_key"])
            assert await c.read() is None
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_kdel_nonexistent(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["KDEL", "no_such_key"])
            assert await c.read() == 0
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_kdel_multiple(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["KSET", "a", "1"])
            await c.read()
            await c.send(["KSET", "b", "2"])
            await c.read()

            await c.send(["KDEL", "a", "b", "missing"])
            assert await c.read() == 2  # only a and b existed
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_kkeys_all(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["KSET", "x", "1"])
            await c.read()
            await c.send(["KSET", "y", "2"])
            await c.read()

            await c.send(["KKEYS", "*"])
            resp = await c.read()
            assert isinstance(resp, list)
            assert set(resp) == {"x", "y"}
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_kmset_odd_args_errors(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["KMSET", "a", "1", "b"])
            resp = await c.read()
            assert "ERR" in resp
        finally:
            await c.close()


# ---------------------------------------------------------------------------
# Instrument lifecycle
# ---------------------------------------------------------------------------

class TestInstrumentLifecycle:
    """End-to-end instrument add / lock / init / read / unlock flow."""

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, server):
        c = await Client.connect(server.test_port)
        try:
            # Add instrument
            await c.send(["IADD", "vsg", "dryrun"])
            resp = await c.read()
            assert resp == "OK"

            # List instruments
            await c.send(["ILIST"])
            resp = await c.read()
            assert isinstance(resp, list)
            assert "vsg" in resp

            # Lock
            await c.send(["ILOCK", "vsg"])
            assert await c.read() == "OK"

            # Init
            await c.send(["IINIT", "vsg"])
            assert await c.read() == "OK"

            # Read resource
            await c.send(["IREAD", "vsg:VOUT"])
            resp = await c.read()
            assert resp is not None

            # Unlock (triggers safe_state)
            await c.send(["IUNLOCK", "vsg"])
            assert await c.read() == "OK"

            # Remove
            await c.send(["IREMOVE", "vsg"])
            assert await c.read() == "OK"
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_lock_prevents_other_client(self, server):
        a = await Client.connect(server.test_port)
        b = await Client.connect(server.test_port)
        try:
            await a.send(["IADD", "scope", "dryrun"])
            assert await a.read() == "OK"

            # A locks
            await a.send(["ILOCK", "scope"])
            assert await a.read() == "OK"

            # B tries to lock — should fail
            await b.send(["ILOCK", "scope"])
            resp = await b.read()
            assert "ERR" in resp or "LOCKED" in resp

            # A unlocks
            await a.send(["IUNLOCK", "scope"])
            assert await a.read() == "OK"

            # Now B can lock
            await b.send(["ILOCK", "scope"])
            assert await b.read() == "OK"

            await b.send(["IUNLOCK", "scope"])
            await b.read()
        finally:
            await a.close()
            await b.close()

    @pytest.mark.asyncio
    async def test_disconnect_releases_lock(self, server):
        """When a client disconnects, its locks are auto-released."""
        a = await Client.connect(server.test_port)
        b = await Client.connect(server.test_port)
        try:
            await a.send(["IADD", "dmm", "dryrun"])
            assert await a.read() == "OK"

            await a.send(["ILOCK", "dmm"])
            assert await a.read() == "OK"

            # A disconnects abruptly
            await a.close()
            await asyncio.sleep(0.2)  # give server time to clean up

            # B should now be able to lock
            await b.send(["ILOCK", "dmm"])
            assert await b.read() == "OK"

            await b.send(["IUNLOCK", "dmm"])
            await b.read()
        finally:
            try:
                await b.close()
            except Exception:
                pass

    @pytest.mark.asyncio
    async def test_iinfo(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["IADD", "vsg", "dryrun"])
            await c.read()

            await c.send(["IINFO", "vsg"])
            resp = await c.read()
            assert isinstance(resp, str)
            assert "name:vsg" in resp
            assert "state:" in resp
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_iresources(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["IADD", "vsg", "dryrun"])
            await c.read()

            await c.send(["IRESOURCES", "vsg"])
            resp = await c.read()
            assert isinstance(resp, list)
            assert len(resp) > 0  # dryrun has simulated resources
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_idrivers(self, server):
        """IDRIVERS lists loaded drivers."""
        c = await Client.connect(server.test_port)
        try:
            # After IADD, the driver should appear in loaded list
            await c.send(["IADD", "vsg", "dryrun"])
            await c.read()

            await c.send(["DRIVER", "LIST"])
            resp = await c.read()
            assert isinstance(resp, list)
            assert "dryrun" in resp
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_ilocks_empty_then_populated(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["ILOCKED"])
            resp = await c.read()
            assert isinstance(resp, list)
            assert len(resp) == 0

            await c.send(["IADD", "scope", "dryrun"])
            await c.read()
            await c.send(["ILOCK", "scope"])
            await c.read()

            await c.send(["ILOCKED"])
            resp = await c.read()
            assert isinstance(resp, list)
            assert len(resp) == 1
            assert "scope" in resp[0]

            await c.send(["IUNLOCK", "scope"])
            await c.read()
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_iwrite_and_iread(self, server):
        """Write a resource value then read it back."""
        c = await Client.connect(server.test_port)
        try:
            await c.send(["IADD", "vsg", "dryrun"])
            await c.read()
            await c.send(["ILOCK", "vsg"])
            await c.read()
            await c.send(["IINIT", "vsg"])
            await c.read()

            # DryRun resources: CH1, CH2, VOUT, FREQ
            await c.send(["IWRITE", "vsg:VOUT", "3.3"])
            assert await c.read() == "OK"

            await c.send(["IREAD", "vsg:VOUT"])
            assert await c.read() == "3.3"

            await c.send(["IUNLOCK", "vsg"])
            await c.read()
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_iraw_passthrough(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["IADD", "vsg", "dryrun"])
            await c.read()
            await c.send(["ILOCK", "vsg"])
            await c.read()
            await c.send(["IINIT", "vsg"])
            await c.read()

            # DryRun's passthrough: queries return "SIMULATED: <cmd>"
            await c.send(["IRAW", "vsg", "*IDN?"])
            resp = await c.read()
            assert resp is not None

            await c.send(["IUNLOCK", "vsg"])
            await c.read()
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_ireadmulti(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["IADD", "vsg", "dryrun"])
            await c.read()
            await c.send(["ILOCK", "vsg"])
            await c.read()
            await c.send(["IINIT", "vsg"])
            await c.read()

            await c.send(["IMREAD", "vsg:VOUT", "vsg:FREQ"])
            resp = await c.read()
            assert isinstance(resp, list)
            assert len(resp) == 2

            await c.send(["IUNLOCK", "vsg"])
            await c.read()
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_iread_without_lock_errors(self, server):
        """Reading without holding the lock should fail."""
        c = await Client.connect(server.test_port)
        try:
            await c.send(["IADD", "vsg", "dryrun"])
            await c.read()

            await c.send(["IREAD", "vsg:VOUT"])
            resp = await c.read()
            assert "ERR" in resp or "IDLE" in resp
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_iwrite_without_lock_errors(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["IADD", "vsg", "dryrun"])
            await c.read()

            await c.send(["IWRITE", "vsg:VOUT", "1.0"])
            resp = await c.read()
            assert "ERR" in resp or "IDLE" in resp
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_iread_other_client_locked_errors(self, server):
        """Client B cannot read a resource locked by client A."""
        a = await Client.connect(server.test_port)
        b = await Client.connect(server.test_port)
        try:
            await a.send(["IADD", "vsg", "dryrun"])
            await a.read()
            await a.send(["ILOCK", "vsg"])
            await a.read()
            await a.send(["IINIT", "vsg"])
            await a.read()

            await b.send(["IREAD", "vsg:VOUT"])
            resp = await b.read()
            assert "ERR" in resp or "LOCKED" in resp

            await a.send(["IUNLOCK", "vsg"])
            await a.read()
        finally:
            await a.close()
            await b.close()

    @pytest.mark.asyncio
    async def test_iadd_duplicate_errors(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["IADD", "vsg", "dryrun"])
            assert await c.read() == "OK"

            await c.send(["IADD", "vsg", "dryrun"])
            resp = await c.read()
            assert "ERR" in resp
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_iremove_nonexistent_errors(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["IREMOVE", "ghost"])
            resp = await c.read()
            assert "ERR" in resp
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_ireset_on_idle_errors(self, server):
        """IRESET only works on FAULT/UNRESPONSIVE state, not IDLE."""
        c = await Client.connect(server.test_port)
        try:
            await c.send(["IADD", "vsg", "dryrun"])
            await c.read()

            # IRESET on IDLE instrument — should error
            await c.send(["IRESET", "vsg"])
            resp = await c.read()
            assert "ERR" in resp or "FAULT" in resp
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_iinit_without_lock_errors(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["IADD", "vsg", "dryrun"])
            await c.read()

            await c.send(["IINIT", "vsg"])
            resp = await c.read()
            assert "ERR" in resp or "IDLE" in resp
        finally:
            await c.close()


# ---------------------------------------------------------------------------
# MONITOR end-to-end
# ---------------------------------------------------------------------------

class TestMonitorIntegration:
    """MONITOR with real TCP: one client monitors, another sends commands."""

    @pytest.mark.asyncio
    async def test_monitor_receives_commands(self, server):
        monitor = await Client.connect(server.test_port)
        sender = await Client.connect(server.test_port)
        try:
            # Enter monitor mode
            await monitor.send(["MONITOR"])
            resp = await monitor.read()
            assert resp == "OK"

            # Sender executes commands
            await sender.send(["PING"])
            assert await sender.read() == "PONG"

            # Monitor should receive the PING broadcast
            raw = await monitor.read_raw(timeout=2.0)
            text = raw.decode("utf-8", errors="replace")
            assert '"PING"' in text
        finally:
            await monitor.close()
            await sender.close()

    @pytest.mark.asyncio
    async def test_monitor_receives_kv_commands(self, server):
        monitor = await Client.connect(server.test_port)
        sender = await Client.connect(server.test_port)
        try:
            await monitor.send(["MONITOR"])
            assert await monitor.read() == "OK"

            await sender.send(["KSET", "x", "42"])
            assert await sender.read() == "OK"

            raw = await monitor.read_raw(timeout=2.0)
            text = raw.decode("utf-8", errors="replace")
            assert '"KSET"' in text
            assert '"x"' in text
            assert '"42"' in text
        finally:
            await monitor.close()
            await sender.close()

    @pytest.mark.asyncio
    async def test_monitor_does_not_echo_monitor_cmd(self, server):
        """MONITOR command itself should not be broadcast (Redis behavior)."""
        mon1 = await Client.connect(server.test_port)
        mon2 = await Client.connect(server.test_port)
        sender = await Client.connect(server.test_port)
        try:
            # First monitor subscribes
            await mon1.send(["MONITOR"])
            assert await mon1.read() == "OK"

            # Second client subscribes — mon1 should NOT see "MONITOR"
            await mon2.send(["MONITOR"])
            assert await mon2.read() == "OK"

            # Send a real command to generate a broadcast
            await sender.send(["PING"])
            assert await sender.read() == "PONG"

            # Both monitors see PING
            raw1 = await mon1.read_raw(timeout=2.0)
            raw2 = await mon2.read_raw(timeout=2.0)

            # mon1 might have received both the (skipped) MONITOR broadcast
            # and the PING — verify PING is there and MONITOR is not
            text1 = raw1.decode("utf-8", errors="replace")
            assert '"PING"' in text1
            assert '"MONITOR"' not in text1

            text2 = raw2.decode("utf-8", errors="replace")
            assert '"PING"' in text2
        finally:
            await mon1.close()
            await mon2.close()
            await sender.close()

    @pytest.mark.asyncio
    async def test_monitor_multiple_commands(self, server):
        """Monitor sees a sequence of commands from another client."""
        monitor = await Client.connect(server.test_port)
        sender = await Client.connect(server.test_port)
        try:
            await monitor.send(["MONITOR"])
            assert await monitor.read() == "OK"

            # Send several commands
            await sender.send(["KSET", "a", "1"])
            await sender.read()
            await sender.send(["KSET", "b", "2"])
            await sender.read()
            await sender.send(["KGET", "a"])
            await sender.read()

            # Collect all monitor output
            collected = b""
            for _ in range(10):
                chunk = await monitor.read_raw(timeout=0.5)
                if not chunk:
                    break
                collected += chunk

            text = collected.decode("utf-8", errors="replace")
            assert '"KSET"' in text
            assert '"KGET"' in text
        finally:
            await monitor.close()
            await sender.close()

    @pytest.mark.asyncio
    async def test_monitor_disconnect_cleanup(self, server):
        """After monitor disconnects, server does not crash on next broadcast."""
        monitor = await Client.connect(server.test_port)
        sender = await Client.connect(server.test_port)
        try:
            await monitor.send(["MONITOR"])
            assert await monitor.read() == "OK"

            # Monitor disconnects
            await monitor.close()
            await asyncio.sleep(0.2)

            # Sender continues — server should not crash
            await sender.send(["PING"])
            assert await sender.read() == "PONG"

            await sender.send(["KSET", "alive", "yes"])
            assert await sender.read() == "OK"
        finally:
            await sender.close()


# ---------------------------------------------------------------------------
# Multi-client session isolation
# ---------------------------------------------------------------------------

class TestClientIsolation:
    """Verify session IDs and client names are per-connection."""

    @pytest.mark.asyncio
    async def test_different_client_ids(self, server):
        a = await Client.connect(server.test_port)
        b = await Client.connect(server.test_port)
        try:
            await a.send(["CLIENT", "ID"])
            id_a = await a.read()
            await b.send(["CLIENT", "ID"])
            id_b = await b.read()

            assert isinstance(id_a, int)
            assert isinstance(id_b, int)
            assert id_a != id_b
        finally:
            await a.close()
            await b.close()

    @pytest.mark.asyncio
    async def test_clientname_per_session(self, server):
        a = await Client.connect(server.test_port)
        b = await Client.connect(server.test_port)
        try:
            # CLIENTNAME <name> to set, CLIENTNAME (no args) to get
            await a.send(["CLIENT", "NAME", "alice"])
            assert await a.read() == "OK"

            await b.send(["CLIENT", "NAME", "bob"])
            assert await b.read() == "OK"

            await a.send(["CLIENT", "NAME"])
            assert await a.read() == "alice"

            await b.send(["CLIENT", "NAME"])
            assert await b.read() == "bob"
        finally:
            await a.close()
            await b.close()

    @pytest.mark.asyncio
    async def test_clientlist(self, server):
        a = await Client.connect(server.test_port)
        b = await Client.connect(server.test_port)
        try:
            await a.send(["CLIENT", "NAME", "test-a"])
            await a.read()
            await b.send(["CLIENT", "NAME", "test-b"])
            await b.read()

            await a.send(["CLIENT", "LIST"])
            resp = await a.read()
            # CLIENTLIST returns a bulk string with one line per client
            assert isinstance(resp, str)
            assert "test-a" in resp
            assert "test-b" in resp
        finally:
            await a.close()
            await b.close()


# ---------------------------------------------------------------------------
# INFO command
# ---------------------------------------------------------------------------

class TestInfoIntegration:
    """INFO returns real server stats."""

    @pytest.mark.asyncio
    async def test_info_has_sections(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["INFO"])
            resp = await c.read()
            assert isinstance(resp, str)
            assert "version:" in resp
            assert "connected_clients:" in resp
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_info_keys_section(self, server):
        c = await Client.connect(server.test_port)
        try:
            await c.send(["KSET", "a", "1"])
            await c.read()

            await c.send(["INFO"])
            resp = await c.read()
            assert "keys:" in resp
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_info_client_count_increases(self, server):
        a = await Client.connect(server.test_port)
        try:
            await a.send(["INFO"])
            info1 = await a.read()

            b = await Client.connect(server.test_port)
            await asyncio.sleep(0.1)
            try:
                await a.send(["INFO"])
                info2 = await a.read()

                # Parse connected_clients from both
                def get_clients(info: str) -> int:
                    for line in info.split("\n"):
                        if line.startswith("connected_clients:"):
                            return int(line.split(":")[1])
                    return -1

                c1 = get_clients(info1)
                c2 = get_clients(info2)
                assert c2 > c1
            finally:
                await b.close()
        finally:
            await a.close()


# ---------------------------------------------------------------------------
# Protocol edge cases
# ---------------------------------------------------------------------------

class TestProtocolEdgeCases:
    """Verify the server handles protocol edge cases gracefully."""

    @pytest.mark.asyncio
    async def test_empty_command(self, server):
        """Sending an empty line should not crash the server."""
        c = await Client.connect(server.test_port)
        try:
            await c.send_inline("")
            # Empty line is skipped; send a real command to verify connection is alive
            await c.send(["PING"])
            assert await c.read() == "PONG"
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_rapid_commands(self, server):
        """Send many commands in quick succession."""
        c = await Client.connect(server.test_port)
        try:
            for i in range(50):
                await c.send(["KSET", f"rapid:{i}", str(i)])
                assert await c.read() == "OK"

            await c.send(["KDBSIZE"])
            assert await c.read() == 50
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_missing_args_errors(self, server):
        """Commands with missing arguments return errors, not crashes."""
        c = await Client.connect(server.test_port)
        try:
            # KSET needs key + value
            await c.send(["KSET"])
            resp = await c.read()
            assert "ERR" in resp

            # KGET needs key
            await c.send(["KGET"])
            resp = await c.read()
            assert "ERR" in resp

            # IADD needs name + driver
            await c.send(["IADD"])
            resp = await c.read()
            assert "ERR" in resp

            # Connection still alive
            await c.send(["PING"])
            assert await c.read() == "PONG"
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_inline_multiple_spaces(self, server):
        """Extra whitespace between inline args is ignored."""
        c = await Client.connect(server.test_port)
        try:
            await c.send_inline("KSET   spacey    value")
            assert await c.read() == "OK"

            await c.send(["KGET", "spacey"])
            assert await c.read() == "value"
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_resp_and_inline_interleaved(self, server):
        """Mix RESP array and inline commands on the same connection."""
        c = await Client.connect(server.test_port)
        try:
            await c.send(["KSET", "k1", "resp"])
            assert await c.read() == "OK"

            await c.send_inline("KSET k2 inline")
            assert await c.read() == "OK"

            await c.send(["KGET", "k1"])
            assert await c.read() == "resp"

            await c.send_inline("KGET k2")
            assert await c.read() == "inline"
        finally:
            await c.close()


# ---------------------------------------------------------------------------
# Multi-client real-world workflow
# ---------------------------------------------------------------------------

class TestRealWorldWorkflow:
    """Simulate realistic multi-client test scenarios."""

    @pytest.mark.asyncio
    async def test_operator_and_observer(self, server):
        """Operator locks instrument and writes measurements to KV.
        Observer reads measurements from KV without needing a lock."""
        operator = await Client.connect(server.test_port)
        observer = await Client.connect(server.test_port)
        try:
            await operator.send(["CLIENT", "NAME", "operator"])
            await operator.read()
            await observer.send(["CLIENT", "NAME", "observer"])
            await observer.read()

            # Operator sets up instrument
            await operator.send(["IADD", "dmm", "dryrun"])
            await operator.read()
            await operator.send(["ILOCK", "dmm"])
            await operator.read()
            await operator.send(["IINIT", "dmm"])
            await operator.read()

            # Operator reads instrument and publishes to KV
            await operator.send(["IREAD", "dmm:VOUT"])
            voltage = await operator.read()

            await operator.send(["KSET", "meas:voltage", voltage])
            await operator.read()

            # Observer reads from KV (no lock needed)
            await observer.send(["KGET", "meas:voltage"])
            assert await observer.read() == voltage

            # Observer cannot read instrument directly
            await observer.send(["IREAD", "dmm:VOUT"])
            resp = await observer.read()
            assert "ERR" in resp or "LOCKED" in resp

            await operator.send(["IUNLOCK", "dmm"])
            await operator.read()
        finally:
            await operator.close()
            await observer.close()

    @pytest.mark.asyncio
    async def test_lock_handoff(self, server):
        """Client A configures, unlocks. Client B locks, measures, unlocks."""
        a = await Client.connect(server.test_port)
        b = await Client.connect(server.test_port)
        try:
            await a.send(["IADD", "gen", "dryrun"])
            await a.read()

            # A configures
            await a.send(["ILOCK", "gen"])
            await a.read()
            await a.send(["IINIT", "gen"])
            await a.read()
            await a.send(["IWRITE", "gen:VOUT", "5.0"])
            await a.read()
            await a.send(["IUNLOCK", "gen"])
            await a.read()

            # B measures
            await b.send(["ILOCK", "gen"])
            await b.read()
            await b.send(["IINIT", "gen"])
            await b.read()
            await b.send(["IREAD", "gen:VOUT"])
            resp = await b.read()
            assert resp is not None

            await b.send(["IUNLOCK", "gen"])
            await b.read()
        finally:
            await a.close()
            await b.close()

    @pytest.mark.asyncio
    async def test_multiple_instruments(self, server):
        """Add and operate on multiple instruments simultaneously."""
        c = await Client.connect(server.test_port)
        try:
            await c.send(["IADD", "gen", "dryrun"])
            await c.read()
            await c.send(["IADD", "scope", "dryrun"])
            await c.read()

            await c.send(["ILIST"])
            resp = await c.read()
            assert "gen" in resp
            assert "scope" in resp

            # Lock and init both
            for name in ["gen", "scope"]:
                await c.send(["ILOCK", name])
                await c.read()
                await c.send(["IINIT", name])
                await c.read()

            # Read from both
            await c.send(["IREAD", "gen:VOUT"])
            v1 = await c.read()
            await c.send(["IREAD", "scope:VOUT"])
            v2 = await c.read()
            assert v1 is not None
            assert v2 is not None

            # Unlock both
            for name in ["gen", "scope"]:
                await c.send(["IUNLOCK", name])
                await c.read()
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_monitor_sees_full_workflow_with_events(self, server):
        """MONITOR and event subscriber both see the workflow."""
        monitor = await Client.connect(server.test_port)
        worker = await Client.connect(server.test_port)
        try:
            await monitor.send(["MONITOR"])
            assert await monitor.read() == "OK"

            await worker.send(["IADD", "vsg", "dryrun"])
            await worker.read()

            raw = await monitor.read_raw(timeout=2.0)
            text = raw.decode("utf-8", errors="replace")
            assert '"IADD"' in text
        finally:
            await monitor.close()
            await worker.close()

    @pytest.mark.asyncio
    async def test_monitor_sees_full_workflow(self, server):
        """Monitor client observes an entire instrument workflow."""
        monitor = await Client.connect(server.test_port)
        worker = await Client.connect(server.test_port)
        try:
            await monitor.send(["MONITOR"])
            assert await monitor.read() == "OK"

            # Worker does a full workflow
            await worker.send(["IADD", "vsg", "dryrun"])
            await worker.read()
            await worker.send(["ILOCK", "vsg"])
            await worker.read()
            await worker.send(["IINIT", "vsg"])
            await worker.read()
            await worker.send(["IREAD", "vsg:VOUT"])
            await worker.read()
            await worker.send(["KSET", "result", "pass"])
            await worker.read()
            await worker.send(["IUNLOCK", "vsg"])
            await worker.read()

            # Collect monitor output
            collected = b""
            for _ in range(15):
                chunk = await monitor.read_raw(timeout=0.5)
                if not chunk:
                    break
                collected += chunk

            text = collected.decode("utf-8", errors="replace")
            # Monitor should have seen all commands
            assert '"IADD"' in text
            assert '"ILOCK"' in text
            assert '"IINIT"' in text
            assert '"IREAD"' in text
            assert '"KSET"' in text
            assert '"IUNLOCK"' in text
        finally:
            await monitor.close()
            await worker.close()


# ---------------------------------------------------------------------------
# Event system end-to-end
# ---------------------------------------------------------------------------

class TestEventIntegration:
    """SUBSCRIBE/event system with real TCP connections."""

    @pytest.mark.asyncio
    async def test_subscribe_kv_receives_kset(self, server):
        """Client 1 subscribes to __event:kv, client 2 does KSET,
        client 1 receives the event."""
        subscriber = await Client.connect(server.test_port)
        sender = await Client.connect(server.test_port)
        try:
            # Subscribe to kv events
            await subscriber.send(["SUBSCRIBE", "kv"])
            resp = await subscriber.read()
            assert isinstance(resp, list)
            assert resp[0] == "subscribe"
            assert resp[1] == "kv"

            # Sender does KSET
            await sender.send(["KSET", "meas:power", "23.4"])
            assert await sender.read() == "OK"

            # Subscriber should receive the event
            raw = await subscriber.read_raw(timeout=2.0)
            assert len(raw) > 0, "subscriber received no data after KSET"
            text = raw.decode("utf-8", errors="replace")
            assert "event" in text
            assert "kv" in text
            assert "meas:power" in text
            assert "23.4" in text
        finally:
            await subscriber.close()
            await sender.close()

    @pytest.mark.asyncio
    async def test_subscribe_instrument_receives_iadd(self, server):
        """Subscriber receives instrument ADD event."""
        subscriber = await Client.connect(server.test_port)
        sender = await Client.connect(server.test_port)
        try:
            await subscriber.send(["SUBSCRIBE", "instrument"])
            resp = await subscriber.read()
            assert resp[0] == "subscribe"

            await sender.send(["IADD", "dmm", "dryrun"])
            assert await sender.read() == "OK"

            raw = await subscriber.read_raw(timeout=2.0)
            assert len(raw) > 0, "subscriber received no data after IADD"
            text = raw.decode("utf-8", errors="replace")
            assert "instrument" in text
            assert "ADD" in text
            assert "dmm" in text
        finally:
            await subscriber.close()
            await sender.close()

    @pytest.mark.asyncio
    async def test_subscribe_lock_events(self, server):
        """Subscriber receives lock acquired event."""
        subscriber = await Client.connect(server.test_port)
        operator = await Client.connect(server.test_port)
        try:
            await subscriber.send(["SUBSCRIBE", "lock"])
            resp = await subscriber.read()
            assert resp[0] == "subscribe"

            await operator.send(["IADD", "gen", "dryrun"])
            assert await operator.read() == "OK"

            await operator.send(["ILOCK", "gen"])
            assert await operator.read() == "OK"

            raw = await subscriber.read_raw(timeout=2.0)
            assert len(raw) > 0, "subscriber received no data after ILOCK"
            text = raw.decode("utf-8", errors="replace")
            assert "lock" in text
            assert "acquired" in text
            assert "gen" in text

            await operator.send(["IUNLOCK", "gen"])
            await operator.read()
        finally:
            await subscriber.close()
            await operator.close()

    @pytest.mark.asyncio
    async def test_subscribe_session_disconnect_event(self, server):
        """Subscriber receives session disconnect event."""
        subscriber = await Client.connect(server.test_port)
        other = await Client.connect(server.test_port)
        try:
            await subscriber.send(["SUBSCRIBE", "session"])
            resp = await subscriber.read()
            assert resp[0] == "subscribe"

            # other client's connect event may have arrived
            # drain it
            try:
                await asyncio.wait_for(subscriber.reader.read(4096), timeout=0.3)
            except asyncio.TimeoutError:
                pass

            # other disconnects
            await other.close()
            await asyncio.sleep(0.3)

            raw = await subscriber.read_raw(timeout=2.0)
            assert len(raw) > 0, "subscriber received no data after disconnect"
            text = raw.decode("utf-8", errors="replace")
            assert "session" in text
            assert "disconnect" in text
        finally:
            await subscriber.close()

    @pytest.mark.asyncio
    async def test_subscriber_mode_blocks_commands(self, server):
        """Client in subscriber mode cannot issue normal commands."""
        c = await Client.connect(server.test_port)
        try:
            await c.send(["SUBSCRIBE", "kv"])
            await c.read()

            await c.send(["KSET", "foo", "bar"])
            resp = await c.read()
            assert isinstance(resp, str)
            assert "subscriber mode" in resp

            # PING still works
            await c.send(["PING"])
            assert await c.read() == "PONG"

            # UNSUBSCRIBE exits subscriber mode
            await c.send(["UNSUBSCRIBE"])
            await c.read()

            # Now KSET works
            await c.send(["KSET", "foo", "bar"])
            assert await c.read() == "OK"
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_multiple_kset_events(self, server):
        """Subscriber receives multiple KSET events in sequence."""
        subscriber = await Client.connect(server.test_port)
        sender = await Client.connect(server.test_port)
        try:
            await subscriber.send(["SUBSCRIBE", "kv"])
            await subscriber.read()

            # Send 3 KSETs
            for i in range(3):
                await sender.send(["KSET", f"key{i}", f"val{i}"])
                assert await sender.read() == "OK"

            # Collect all events
            collected = b""
            for _ in range(10):
                chunk = await subscriber.read_raw(timeout=0.5)
                if not chunk:
                    break
                collected += chunk

            text = collected.decode("utf-8", errors="replace")
            assert "key0" in text
            assert "key1" in text
            assert "key2" in text
        finally:
            await subscriber.close()
            await sender.close()

    @pytest.mark.asyncio
    async def test_kv_filter_only_matching(self, server):
        """Subscriber with __event:kv:alert:* only gets matching KSET."""
        subscriber = await Client.connect(server.test_port)
        sender = await Client.connect(server.test_port)
        try:
            await subscriber.send(["SUBSCRIBE", "kv:alert:*"])
            resp = await subscriber.read()
            assert resp[0] == "subscribe"
            assert resp[1] == "kv:alert:*"

            # Non-matching key — subscriber should NOT receive
            await sender.send(["KSET", "meas:power", "23.4"])
            assert await sender.read() == "OK"

            raw = await subscriber.read_raw(timeout=0.5)
            assert raw == b"", "subscriber received data for non-matching key"

            # Matching key — subscriber SHOULD receive
            await sender.send(["KSET", "alert:emergency", "stop all"])
            assert await sender.read() == "OK"

            raw = await subscriber.read_raw(timeout=2.0)
            assert len(raw) > 0, "subscriber received no data for matching key"
            text = raw.decode("utf-8", errors="replace")
            assert "alert:emergency" in text
            assert "stop all" in text
        finally:
            await subscriber.close()
            await sender.close()

    @pytest.mark.asyncio
    async def test_kv_filter_two_subscribers_different_patterns(self, server):
        """Two subscribers with different kv filters see different keys."""
        sub_alert = await Client.connect(server.test_port)
        sub_meas = await Client.connect(server.test_port)
        sender = await Client.connect(server.test_port)
        try:
            await sub_alert.send(["SUBSCRIBE", "kv:alert:*"])
            resp = await sub_alert.read()
            assert resp[0] == "subscribe"

            await sub_meas.send(["SUBSCRIBE", "kv:meas:*"])
            resp = await sub_meas.read()
            assert resp[0] == "subscribe"

            # Send alert — only sub_alert gets it
            await sender.send(["KSET", "alert:fire", "yes"])
            assert await sender.read() == "OK"

            raw_alert = await sub_alert.read_raw(timeout=2.0)
            assert b"alert:fire" in raw_alert

            raw_meas = await sub_meas.read_raw(timeout=0.5)
            assert raw_meas == b""

            # Send measurement — only sub_meas gets it
            await sender.send(["KSET", "meas:temp", "42"])
            assert await sender.read() == "OK"

            raw_meas = await sub_meas.read_raw(timeout=2.0)
            assert b"meas:temp" in raw_meas

            raw_alert = await sub_alert.read_raw(timeout=0.5)
            assert raw_alert == b""
        finally:
            await sub_alert.close()
            await sub_meas.close()
            await sender.close()


class TestKVReadOnly:
    """End-to-end tests for KSET RO with multiple clients."""

    @pytest.mark.asyncio
    async def test_ro_blocks_other_client(self, server):
        """Client A sets RO key, client B cannot overwrite it."""
        a = await Client.connect(server.test_port)
        b = await Client.connect(server.test_port)
        try:
            # Client A sets key with RO
            await a.send(["KSET", "meas:freq", "1000", "RO"])
            resp = await a.read()
            assert resp == "OK"

            # Client B tries to overwrite → error
            await b.send(["KSET", "meas:freq", "2000"])
            resp = await b.read()
            assert "READONLY" in resp

            # Client B can still read
            await b.send(["KGET", "meas:freq"])
            resp = await b.read()
            assert resp == "1000"
        finally:
            await a.close()
            await b.close()

    @pytest.mark.asyncio
    async def test_ro_owner_can_overwrite(self, server):
        """Owner can overwrite own RO key."""
        a = await Client.connect(server.test_port)
        try:
            await a.send(["KSET", "k1", "v1", "RO"])
            resp = await a.read()
            assert resp == "OK"

            await a.send(["KSET", "k1", "v2", "RO"])
            resp = await a.read()
            assert resp == "OK"

            await a.send(["KGET", "k1"])
            resp = await a.read()
            assert resp == "v2"
        finally:
            await a.close()

    @pytest.mark.asyncio
    async def test_ro_delete_blocked(self, server):
        """Client B cannot KDEL a key owned RO by client A."""
        a = await Client.connect(server.test_port)
        b = await Client.connect(server.test_port)
        try:
            await a.send(["KSET", "k1", "v1", "RO"])
            await a.read()

            await b.send(["KDEL", "k1"])
            resp = await b.read()
            assert "READONLY" in resp
        finally:
            await a.close()
            await b.close()

    @pytest.mark.asyncio
    async def test_ro_released_on_disconnect(self, server):
        """After client A disconnects, RO key becomes writable."""
        a = await Client.connect(server.test_port)
        b = await Client.connect(server.test_port)
        try:
            await a.send(["KSET", "k1", "v1", "RO"])
            await a.read()

            # Close A → RO released
            await a.close()
            await asyncio.sleep(0.1)

            # Now B can overwrite
            await b.send(["KSET", "k1", "v2"])
            resp = await b.read()
            assert resp == "OK"

            await b.send(["KGET", "k1"])
            resp = await b.read()
            assert resp == "v2"
        finally:
            await b.close()

    @pytest.mark.asyncio
    async def test_ro_kflush_skips_other_ro(self, server):
        """KFLUSH from client B does not remove client A's RO keys."""
        a = await Client.connect(server.test_port)
        b = await Client.connect(server.test_port)
        try:
            await a.send(["KSET", "protected", "yes", "RO"])
            await a.read()

            await b.send(["KSET", "normal", "val"])
            await b.read()

            # B flushes — should skip A's RO key
            await b.send(["KFLUSH"])
            await b.read()

            await a.send(["KGET", "protected"])
            resp = await a.read()
            assert resp == "yes"

            await a.send(["KGET", "normal"])
            resp = await a.read()
            assert resp is None
        finally:
            await a.close()
            await b.close()

    @pytest.mark.asyncio
    async def test_ro_kmset_blocked(self, server):
        """KMSET cannot overwrite RO key from different client."""
        a = await Client.connect(server.test_port)
        b = await Client.connect(server.test_port)
        try:
            await a.send(["KSET", "k1", "v1", "RO"])
            await a.read()

            await b.send(["KMSET", "k1", "new", "k2", "val"])
            resp = await b.read()
            assert "READONLY" in resp
        finally:
            await a.close()
            await b.close()


# ---------------------------------------------------------------------------
# MEAS tests
# ---------------------------------------------------------------------------

class TestMEAS:
    """End-to-end tests for the MEAS type."""

    async def _setup_instrument(self, client, name="sim"):
        """Add, lock, and init a dryrun instrument."""
        await client.send(["IADD", name, "dryrun"])
        assert await client.read() == "OK"
        await client.send(["ILOCK", name])
        assert await client.read() == "OK"
        await client.send(["IINIT", name])
        assert await client.read() == "OK"

    @pytest.mark.asyncio
    async def test_iread_meas_writes_entry(self, server):
        """IREAD with MEAS flag writes a MEAS entry readable via MGET."""
        c = await Client.connect(server.test_port)
        try:
            await self._setup_instrument(c)

            # IREAD with MEAS
            await c.send(["IREAD", "sim", "VOUT", "MEAS"])
            value = await c.read()
            assert isinstance(value, str)  # dryrun returns a value

            # MGET retrieves the MEAS
            await c.send(["MGET", "sim", "VOUT"])
            resp = await c.read()
            import json
            meas = json.loads(resp)
            assert meas["status"] == "OK"
            assert meas["value"] == value
            assert "ts" in meas
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_iread_without_meas_no_entry(self, server):
        """IREAD without MEAS flag does not write a MEAS entry."""
        c = await Client.connect(server.test_port)
        try:
            await self._setup_instrument(c)

            await c.send(["IREAD", "sim", "VOUT"])
            await c.read()

            await c.send(["MGET", "sim", "VOUT"])
            resp = await c.read()
            assert resp is None  # nil
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_mget_nonexistent(self, server):
        """MGET for a non-existent MEAS returns nil."""
        c = await Client.connect(server.test_port)
        try:
            await c.send(["MGET", "nosuch", "RES"])
            resp = await c.read()
            assert resp is None
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_mgetall_and_mkeys(self, server):
        """MGETALL and MKEYS return all MEAS entries."""
        c = await Client.connect(server.test_port)
        try:
            await self._setup_instrument(c)

            # Write two MEAS
            await c.send(["IREAD", "sim", "VOUT", "MEAS"])
            await c.read()
            await c.send(["IREAD", "sim", "FREQ", "MEAS"])
            await c.read()

            # MKEYS
            await c.send(["MKEYS"])
            keys = await c.read()
            assert isinstance(keys, list)
            assert len(keys) == 2
            assert "sim:VOUT" in keys
            assert "sim:FREQ" in keys

            # MGETALL
            await c.send(["MGETALL"])
            resp = await c.read()
            assert isinstance(resp, list)
            assert len(resp) == 4  # key, json, key, json

            # MKEYS filtered by instrument
            await c.send(["MKEYS", "sim"])
            keys = await c.read()
            assert len(keys) == 2

            await c.send(["MKEYS", "nosuch"])
            keys = await c.read()
            assert keys == [] or keys is None
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_mgetall_filter_by_instrument(self, server):
        """MGETALL with instrument filter returns only that instrument's MEAS."""
        c = await Client.connect(server.test_port)
        try:
            # Two instruments
            await self._setup_instrument(c, "sim1")
            await self._setup_instrument(c, "sim2")

            await c.send(["IREAD", "sim1", "VOUT", "MEAS"])
            await c.read()
            await c.send(["IREAD", "sim2", "VOUT", "MEAS"])
            await c.read()

            # Filter by sim1
            await c.send(["MGETALL", "sim1"])
            resp = await c.read()
            assert len(resp) == 2  # one key-value pair
            assert resp[0] == "sim1:VOUT"
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_meas_non_owner_can_read(self, server):
        """A non-owner client can read MEAS via MGET."""
        a = await Client.connect(server.test_port)
        b = await Client.connect(server.test_port)
        try:
            await self._setup_instrument(a)

            # Owner writes MEAS
            await a.send(["IREAD", "sim", "VOUT", "MEAS"])
            value = await a.read()

            # Non-owner reads MEAS
            await b.send(["MGET", "sim", "VOUT"])
            resp = await b.read()
            import json
            meas = json.loads(resp)
            assert meas["status"] == "OK"
            assert meas["value"] == value
        finally:
            await a.close()
            await b.close()

    @pytest.mark.asyncio
    async def test_meas_kset_blocked(self, server):
        """Clients cannot KSET keys with _meas: prefix."""
        c = await Client.connect(server.test_port)
        try:
            await c.send(["KSET", "_meas:fake:RES", "garbage"])
            resp = await c.read()
            assert "READONLY" in resp
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_meas_stale_on_unlock(self, server):
        """IUNLOCK marks MEAS as STALE, preserving last value."""
        c = await Client.connect(server.test_port)
        try:
            await self._setup_instrument(c)

            # Write MEAS
            await c.send(["IREAD", "sim", "VOUT", "MEAS"])
            value = await c.read()

            # Unlock → MEAS becomes STALE
            await c.send(["IUNLOCK", "sim"])
            assert await c.read() == "OK"

            # MGET returns STALE with last value
            await c.send(["MGET", "sim", "VOUT"])
            resp = await c.read()
            import json
            meas = json.loads(resp)
            assert meas["status"] == "STALE"
            assert meas["value"] == value  # value preserved
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_meas_stale_on_unlock_all(self, server):
        """IUNLOCK ALL marks all MEAS as STALE."""
        c = await Client.connect(server.test_port)
        try:
            await self._setup_instrument(c, "sim1")
            await self._setup_instrument(c, "sim2")

            await c.send(["IREAD", "sim1", "VOUT", "MEAS"])
            await c.read()
            await c.send(["IREAD", "sim2", "VOUT", "MEAS"])
            await c.read()

            await c.send(["IUNLOCK", "ALL"])
            assert await c.read() == "OK"

            import json
            await c.send(["MGET", "sim1", "VOUT"])
            m1 = json.loads(await c.read())
            assert m1["status"] == "STALE"

            await c.send(["MGET", "sim2", "VOUT"])
            m2 = json.loads(await c.read())
            assert m2["status"] == "STALE"
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_meas_stale_on_disconnect(self, server):
        """Client disconnect marks MEAS as STALE."""
        a = await Client.connect(server.test_port)
        b = await Client.connect(server.test_port)
        try:
            await self._setup_instrument(a)

            await a.send(["IREAD", "sim", "VOUT", "MEAS"])
            value = await a.read()

            # Owner disconnects
            await a.close()
            await asyncio.sleep(0.15)

            # MEAS should be STALE
            await b.send(["MGET", "sim", "VOUT"])
            resp = await b.read()
            import json
            meas = json.loads(resp)
            assert meas["status"] == "STALE"
            assert meas["value"] == value
        finally:
            try:
                await a.close()
            except Exception:
                pass
            await b.close()

    @pytest.mark.asyncio
    async def test_meas_overwrite_after_relock(self, server):
        """New owner can overwrite STALE MEAS with fresh reads."""
        a = await Client.connect(server.test_port)
        b = await Client.connect(server.test_port)
        try:
            await self._setup_instrument(a)

            await a.send(["IREAD", "sim", "VOUT", "MEAS"])
            await a.read()

            # Unlock
            await a.send(["IUNLOCK", "sim"])
            await a.read()

            # B locks and reads with MEAS
            await b.send(["ILOCK", "sim"])
            assert await b.read() == "OK"
            await b.send(["IINIT", "sim"])
            assert await b.read() == "OK"
            await b.send(["IREAD", "sim", "VOUT", "MEAS"])
            new_value = await b.read()

            # MEAS should be OK with new value
            await b.send(["MGET", "sim", "VOUT"])
            resp = await b.read()
            import json
            meas = json.loads(resp)
            assert meas["status"] == "OK"
            assert meas["value"] == new_value
        finally:
            await a.close()
            await b.close()

    @pytest.mark.asyncio
    async def test_meas_joined_syntax(self, server):
        """IREAD with joined syntax (sim:VOUT) and MEAS flag works."""
        c = await Client.connect(server.test_port)
        try:
            await self._setup_instrument(c)

            await c.send(["IREAD", "sim:VOUT", "MEAS"])
            value = await c.read()
            assert isinstance(value, str)

            await c.send(["MGET", "sim", "VOUT"])
            resp = await c.read()
            import json
            meas = json.loads(resp)
            assert meas["status"] == "OK"
            assert meas["value"] == value
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_meas_event_published(self, server):
        """IREAD MEAS publishes event on __event:meas channel."""
        owner = await Client.connect(server.test_port)
        monitor = await Client.connect(server.test_port)
        try:
            await self._setup_instrument(owner)

            # Subscribe to MEAS events
            await monitor.send(["SUBSCRIBE", "meas"])
            sub_resp = await monitor.read()
            assert isinstance(sub_resp, list)  # ['subscribe', channel, count]

            # Owner reads with MEAS
            await owner.send(["IREAD", "sim", "VOUT", "MEAS"])
            value = await owner.read()

            # Monitor should receive the event
            event = await monitor.read(timeout=2.0)
            assert isinstance(event, list)
            assert event[0] == "event"
            assert event[1] == "meas"
            import json
            payload = json.loads(event[2])
            assert payload["type"] == "meas"
            assert payload["instrument"] == "sim"
            assert payload["resource"] == "VOUT"
            assert payload["status"] == "OK"
            assert payload["value"] == value
        finally:
            await owner.close()
            await monitor.close()

    @pytest.mark.asyncio
    async def test_meas_stale_event_on_unlock(self, server):
        """IUNLOCK publishes STALE events on __event:meas."""
        owner = await Client.connect(server.test_port)
        monitor = await Client.connect(server.test_port)
        try:
            await self._setup_instrument(owner)

            await owner.send(["IREAD", "sim", "VOUT", "MEAS"])
            await owner.read()

            # Subscribe to MEAS events
            await monitor.send(["SUBSCRIBE", "meas"])
            await monitor.read()  # subscribe confirmation

            # Unlock → STALE event
            await owner.send(["IUNLOCK", "sim"])
            await owner.read()

            event = await monitor.read(timeout=2.0)
            assert isinstance(event, list)
            import json
            payload = json.loads(event[2])
            assert payload["status"] == "STALE"
            assert payload["instrument"] == "sim"
        finally:
            await owner.close()
            await monitor.close()

    @pytest.mark.asyncio
    async def test_meas_event_filtered(self, server):
        """Glob-filtered subscription on __event:meas works."""
        owner = await Client.connect(server.test_port)
        monitor = await Client.connect(server.test_port)
        try:
            await self._setup_instrument(owner, "sim1")
            await self._setup_instrument(owner, "sim2")

            # Subscribe only to sim1 events
            await monitor.send(["SUBSCRIBE", "meas:sim1:*"])
            await monitor.read()

            # Read from sim2 with MEAS (should NOT trigger event)
            await owner.send(["IREAD", "sim2", "VOUT", "MEAS"])
            await owner.read()

            # Read from sim1 with MEAS (should trigger event)
            await owner.send(["IREAD", "sim1", "VOUT", "MEAS"])
            await owner.read()

            # Monitor should receive only sim1 event
            event = await monitor.read(timeout=2.0)
            import json
            payload = json.loads(event[2])
            assert payload["instrument"] == "sim1"
        finally:
            await owner.close()
            await monitor.close()

    @pytest.mark.asyncio
    async def test_mget_wrong_args(self, server):
        """MGET with wrong number of args returns error."""
        c = await Client.connect(server.test_port)
        try:
            await c.send(["MGET", "onlyone"])
            resp = await c.read()
            assert "ERR" in resp
        finally:
            await c.close()

    @pytest.mark.asyncio
    async def test_lock_event_on_unlock(self, server):
        """IUNLOCK now publishes lock release event (gap fix)."""
        owner = await Client.connect(server.test_port)
        monitor = await Client.connect(server.test_port)
        try:
            await self._setup_instrument(owner)

            # Subscribe to lock events
            await monitor.send(["SUBSCRIBE", "lock"])
            await monitor.read()

            await owner.send(["IUNLOCK", "sim"])
            await owner.read()

            event = await monitor.read(timeout=2.0)
            assert isinstance(event, list)
            import json
            payload = json.loads(event[2])
            assert payload["type"] == "released"
            assert payload["instrument"] == "sim"
        finally:
            await owner.close()
            await monitor.close()
