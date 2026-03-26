# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""End-to-end tests for testcore_client library.

Spins up a real TestCoreServer and tests the Python client against it.
"""

import asyncio
import json
import threading
import time
import pytest

from testcore.server import TestCoreServer
from testcore.store import get_store
from testcore.instruments import get_registry
from testcore_client import (
    TestCore,
    CommandError,
    ReadOnlyError,
    IdleError,
    LockedError,
    NotInitError,
    FaultError,
    NoAliasError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_state():
    """Reset store, registry, event bus, aliases before each test."""
    store = get_store()
    store._data.clear()
    registry = get_registry()
    for name in list(registry._instruments.keys()):
        try:
            registry.remove(name)
        except Exception:
            pass
    registry._instruments.clear()
    import testcore.events as events_mod
    events_mod._event_bus = None
    from testcore.commands import _aliases
    _aliases.clear()


@pytest.fixture
def server_port():
    """Start a real TestCoreServer and return its port."""
    loop = asyncio.new_event_loop()
    srv = TestCoreServer(host="127.0.0.1", port=0)

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(srv.start())

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.3)

    # Get the port
    port = srv.server.sockets[0].getsockname()[1]
    yield port

    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=2)


@pytest.fixture
def tc(server_port):
    """Connected TestCore client."""
    client = TestCore(host="127.0.0.1", port=server_port, timeout=3.0)
    yield client
    client.close()


# ---------------------------------------------------------------------------
# Server commands
# ---------------------------------------------------------------------------

class TestServerCommands:

    def test_ping(self, tc):
        assert tc.ping() is True

    def test_ping_message(self, tc):
        assert tc.ping("hello") == "hello"

    def test_time(self, tc):
        result = tc.time()
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], int)
        assert isinstance(result[1], int)
        assert result[0] > 0

    def test_client_id(self, tc):
        cid = tc.client_id()
        assert isinstance(cid, int)
        assert cid > 0

    def test_client_name(self, tc):
        assert tc.client_name("test_script") is True

    def test_client_name_via_constructor(self, server_port):
        client = TestCore(host="127.0.0.1", port=server_port, name="my_test")
        cid = client.client_id()
        assert cid > 0
        client.close()

    def test_client_list(self, tc):
        tc.client_name("test_client")
        clients = tc.client_list()
        assert isinstance(clients, list)
        assert len(clients) >= 1
        assert any(c.get("name") == "test_client" for c in clients)

    def test_command_list(self, tc):
        cmds = tc.command_list()
        assert isinstance(cmds, list)
        assert "PING" in cmds
        assert "KSET" in cmds

    def test_command_list_pattern(self, tc):
        cmds = tc.command_list("K*")
        assert all(c.startswith("K") for c in cmds)

    def test_info(self, tc):
        info = tc.info()
        assert isinstance(info, str)
        assert "version" in info


# ---------------------------------------------------------------------------
# KV commands
# ---------------------------------------------------------------------------

class TestKVCommands:

    def test_set_get(self, tc):
        assert tc.kset("key1", "value1") is True
        assert tc.kget("key1") == "value1"

    def test_get_missing(self, tc):
        assert tc.kget("nonexistent") is None

    def test_set_numeric(self, tc):
        assert tc.kset("num", 42) is True
        assert tc.kget("num") == "42"

    def test_set_float(self, tc):
        assert tc.kset("flt", 3.14) is True
        assert tc.kget("flt") == "3.14"

    def test_set_nx(self, tc):
        tc.kset("nx_key", "first")
        assert tc.kset("nx_key", "second", nx=True) is False
        assert tc.kget("nx_key") == "first"

    def test_set_xx(self, tc):
        assert tc.kset("xx_key", "val", xx=True) is False
        tc.kset("xx_key", "val")
        assert tc.kset("xx_key", "new", xx=True) is True
        assert tc.kget("xx_key") == "new"

    def test_mget(self, tc):
        tc.kset("a", "1")
        tc.kset("b", "2")
        result = tc.kmget("a", "b", "c")
        assert result == ["1", "2", None]

    def test_mset_dict(self, tc):
        assert tc.kmset({"x": "1", "y": "2"}) is True
        assert tc.kget("x") == "1"
        assert tc.kget("y") == "2"

    def test_mset_kwargs(self, tc):
        assert tc.kmset(p="10", q="20") is True
        assert tc.kget("p") == "10"

    def test_del(self, tc):
        tc.kset("d1", "v")
        tc.kset("d2", "v")
        assert tc.kdel("d1", "d2", "d3") == 2

    def test_exists(self, tc):
        tc.kset("e1", "v")
        assert tc.kexists("e1", "e2") == 1

    def test_keys(self, tc):
        tc.kset("meas:a", "1")
        tc.kset("meas:b", "2")
        tc.kset("other", "3")
        keys = tc.kkeys("meas:*")
        assert sorted(keys) == ["meas:a", "meas:b"]

    def test_keys_all(self, tc):
        tc.kset("k1", "v")
        tc.kset("k2", "v")
        keys = tc.kkeys()
        assert "k1" in keys and "k2" in keys

    def test_dbsize(self, tc):
        tc.kset("s1", "v")
        tc.kset("s2", "v")
        assert tc.kdbsize() >= 2

    def test_flush(self, tc):
        tc.kset("f1", "v")
        assert tc.kflush() is True
        assert tc.kdbsize() == 0

    def test_readonly_error(self, tc):
        with pytest.raises(ReadOnlyError):
            tc.kset("_sys:protected", "val")


# ---------------------------------------------------------------------------
# Instrument commands
# ---------------------------------------------------------------------------

class TestInstrumentCommands:

    def test_add_list_remove(self, tc):
        assert tc.iadd("dev1", "dryrun") is True
        instruments = tc.ilist()
        assert "dev1" in instruments
        assert tc.iremove("dev1") is True
        assert "dev1" not in tc.ilist()

    def test_lock_unlock(self, tc):
        tc.iadd("inst1", "dryrun")
        assert tc.ilock("inst1") is True
        locks = tc.ilocked()
        assert "inst1" in locks
        assert tc.iunlock("inst1") is True

    def test_init(self, tc):
        tc.iadd("inst2", "dryrun")
        tc.ilock("inst2")
        assert tc.iinit("inst2") is True

    def test_read_write(self, tc):
        tc.iadd("gen", "dryrun")
        tc.ilock("gen")
        tc.iinit("gen")
        assert tc.iwrite("gen", "FREQ", "1e6") is True
        val = tc.iread("gen", "FREQ")
        assert val is not None

    def test_imread(self, tc):
        tc.iadd("m1", "dryrun")
        tc.ilock("m1")
        tc.iinit("m1")
        results = tc.imread("m1:FREQ", "m1:VOUT")
        assert isinstance(results, list)
        assert len(results) == 2

    def test_iraw(self, tc):
        tc.iadd("raw1", "dryrun")
        tc.ilock("raw1")
        tc.iinit("raw1")
        result = tc.iraw("raw1", "*IDN?")
        assert result is not None

    def test_isave_screen(self, tc, tmp_path):
        tc.iadd("sv1", "dryrun")
        tc.ilock("sv1")
        tc.iinit("sv1")
        out_file = str(tmp_path / "screen.png")
        result = tc.isave("sv1", "SCREEN", out_file)
        assert "bytes saved" in result
        # Verify file was actually created
        import os
        assert os.path.exists(out_file)
        assert os.path.getsize(out_file) > 0

    def test_isave_data(self, tc, tmp_path):
        tc.iadd("sv2", "dryrun")
        tc.ilock("sv2")
        tc.iinit("sv2")
        tc.iwrite("sv2", "FREQ", "1e6")
        out_file = str(tmp_path / "data.csv")
        result = tc.isave("sv2", "DATA", out_file)
        assert "rows saved" in result
        with open(out_file, "r") as f:
            content = f.read()
        assert "FREQ" in content

    def test_isave_unknown_target(self, tc, tmp_path):
        tc.iadd("sv3", "dryrun")
        tc.ilock("sv3")
        tc.iinit("sv3")
        from testcore_client import DriverError as ClientDriverError
        with pytest.raises(ClientDriverError):
            tc.isave("sv3", "UNKNOWN", str(tmp_path / "x.bin"))

    def test_iinfo(self, tc):
        tc.iadd("info1", "dryrun")
        info = tc.iinfo("info1")
        assert isinstance(info, str)
        assert "info1" in info

    def test_iresources(self, tc):
        tc.iadd("res1", "dryrun")
        resources = tc.iresources("res1")
        assert isinstance(resources, list)
        assert len(resources) > 0

    def test_idle_error(self, tc):
        tc.iadd("idle1", "dryrun")
        with pytest.raises(IdleError):
            tc.iread("idle1", "FREQ")

    def test_locked_error(self, server_port):
        tc1 = TestCore(host="127.0.0.1", port=server_port)
        tc2 = TestCore(host="127.0.0.1", port=server_port)
        tc1.iadd("shared", "dryrun")
        tc1.ilock("shared")
        with pytest.raises(LockedError):
            tc2.ilock("shared")
        tc1.close()
        tc2.close()

    def test_notinit_error(self, tc):
        tc.iadd("ni1", "dryrun")
        tc.ilock("ni1")
        with pytest.raises(NotInitError):
            tc.iread("ni1", "FREQ")

    def test_driver_list(self, tc):
        tc.iadd("drv1", "dryrun")
        drivers = tc.driver_list()
        assert isinstance(drivers, list)


# ---------------------------------------------------------------------------
# Alias commands
# ---------------------------------------------------------------------------

class TestAliasCommands:

    def test_alias_sub(self, tc):
        tc.iadd("vsg", "dryrun")
        tc.ilock("vsg")
        tc.iinit("vsg")
        assert tc.alias_set("freq", "SUB", "vsg:FREQ") is True
        assert tc.alias_get("freq") == ("SUB", "vsg:FREQ")
        val = tc.aread("freq")
        assert val is not None
        assert tc.awrite("freq", "1e9") is True

    def test_alias_list_del(self, tc):
        tc.alias_set("a1", "SUB", "x:Y")
        tc.alias_set("a2", "SUB", "x:Z")
        aliases = tc.alias_list()
        assert "a1" in aliases and "a2" in aliases
        assert tc.alias_del("a1") is True
        assert "a1" not in tc.alias_list()

    def test_noalias_error(self, tc):
        with pytest.raises(NoAliasError):
            tc.aread("nonexistent_alias")


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

class TestEvents:

    def test_listen_kv_event(self, server_port):
        """Subscriber receives KSET events via listen()."""
        tc_cmd = TestCore(host="127.0.0.1", port=server_port)
        tc_sub = TestCore(host="127.0.0.1", port=server_port, timeout=2.0)

        received = []

        def listener():
            for channel, payload in tc_sub.listen("__event:kv"):
                received.append((channel, payload))
                if len(received) >= 2:
                    break

        t = threading.Thread(target=listener, daemon=True)
        t.start()
        time.sleep(0.2)

        tc_cmd.kset("meas:power", "23.4")
        tc_cmd.kset("meas:freq", "900e6")

        t.join(timeout=3)

        assert len(received) == 2
        assert received[0][0] == "__event:kv"
        assert received[0][1]["key"] == "meas:power"
        assert received[0][1]["value"] == "23.4"
        assert received[1][1]["key"] == "meas:freq"

        tc_cmd.close()
        tc_sub.close()

    def test_subscribe_callback(self, server_port):
        """Subscriber receives events via callback."""
        tc_cmd = TestCore(host="127.0.0.1", port=server_port)
        tc_sub = TestCore(host="127.0.0.1", port=server_port, timeout=2.0)

        events = []

        def on_event(channel, payload):
            events.append(payload)
            if len(events) >= 1:
                tc_sub.close()  # Force exit from subscribe loop

        t = threading.Thread(
            target=tc_sub.subscribe,
            args=("__event:kv",),
            kwargs={"callback": on_event},
            daemon=True,
        )
        t.start()
        time.sleep(0.2)

        tc_cmd.kset("alert:stop", "emergency")
        t.join(timeout=3)

        assert len(events) >= 1
        assert events[0]["key"] == "alert:stop"

        tc_cmd.close()

    def test_listen_filtered(self, server_port):
        """Filtered subscription only receives matching keys."""
        tc_cmd = TestCore(host="127.0.0.1", port=server_port)
        tc_sub = TestCore(host="127.0.0.1", port=server_port, timeout=2.0)

        received = []

        def listener():
            for channel, payload in tc_sub.listen("__event:kv:alert:*"):
                received.append(payload)
                if len(received) >= 1:
                    break

        t = threading.Thread(target=listener, daemon=True)
        t.start()
        time.sleep(0.2)

        tc_cmd.kset("meas:power", "1")       # should NOT be received
        tc_cmd.kset("alert:fire", "stop")     # should be received

        t.join(timeout=3)

        assert len(received) == 1
        assert received[0]["key"] == "alert:fire"

        tc_cmd.close()
        tc_sub.close()


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

class TestConnection:

    def test_context_manager(self, server_port):
        with TestCore(host="127.0.0.1", port=server_port) as tc:
            assert tc.ping() is True

    def test_reconnect(self, server_port):
        tc = TestCore(host="127.0.0.1", port=server_port, name="recon")
        id1 = tc.client_id()
        tc.reconnect()
        id2 = tc.client_id()
        assert id2 > id1
        tc.close()

    def test_connection_refused(self):
        with pytest.raises(ConnectionError):
            TestCore(host="127.0.0.1", port=1)  # unlikely to be open


# ---------------------------------------------------------------------------
# Real-world workflow
# ---------------------------------------------------------------------------

class TestWorkflow:

    def test_typical_test_script(self, tc):
        """Simulate a simplified test script workflow."""
        # Setup (dryrun has CH1, CH2, VOUT, FREQ)
        tc.iadd("gen", "dryrun")
        tc.iadd("meas", "dryrun")

        # Lock and init
        tc.ilock("gen", "meas")
        tc.iinit("gen")
        tc.iinit("meas")

        # Configure
        tc.iwrite("gen", "FREQ", "900e6")
        tc.iwrite("gen", "VOUT", "1.5")

        # Read measurement
        reading = tc.iread("meas", "CH1")
        assert reading is not None

        # Store result
        tc.kset("meas:900MHz:ch1", reading)
        stored = tc.kget("meas:900MHz:ch1")
        assert stored == reading

        # Batch store
        tc.kmset({
            "meas:900MHz:freq": "900e6",
            "meas:900MHz:status": "pass",
        })

        # Verify
        results = tc.kmget("meas:900MHz:ch1", "meas:900MHz:freq",
                           "meas:900MHz:status")
        assert len(results) == 3
        assert results[2] == "pass"

        # Cleanup
        tc.iunlock("gen", "meas")

    def test_alias_workflow(self, tc):
        """Test with aliases."""
        tc.iadd("gen", "dryrun")
        tc.ilock("gen")
        tc.iinit("gen")

        # Setup aliases (dryrun has FREQ, VOUT)
        tc.alias_set("freq", "SUB", "gen:FREQ")
        tc.alias_set("vout", "SUB", "gen:VOUT")

        # Use aliases
        tc.awrite("freq", "900e6")
        tc.awrite("vout", "1.5")
        freq_val = tc.aread("freq")
        assert freq_val is not None

        tc.iunlock("gen")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class TestPipeline:

    def test_pipeline_kv_batch(self, tc):
        """Pipeline batches multiple KV operations."""
        with tc.pipeline() as pipe:
            pipe.kset("p1", "val1")
            pipe.kset("p2", "val2")
            pipe.kset("p3", "val3")
            pipe.kget("p1")
            pipe.kget("p2")
            pipe.kget("p3")
            results = pipe.execute()

        assert results[0] is True
        assert results[1] is True
        assert results[2] is True
        assert results[3] == "val1"
        assert results[4] == "val2"
        assert results[5] == "val3"

    def test_pipeline_empty(self, tc):
        """Empty pipeline returns empty list."""
        with tc.pipeline() as pipe:
            results = pipe.execute()
        assert results == []

    def test_pipeline_len(self, tc):
        """Pipeline tracks buffered command count."""
        pipe = tc.pipeline()
        assert len(pipe) == 0
        pipe.kset("a", "1")
        pipe.kget("a")
        assert len(pipe) == 2
        pipe.execute()
        assert len(pipe) == 0

    def test_pipeline_reset(self, tc):
        """Reset discards buffered commands."""
        pipe = tc.pipeline()
        pipe.kset("a", "1")
        pipe.kset("b", "2")
        pipe.reset()
        assert len(pipe) == 0
        results = pipe.execute()
        assert results == []

    def test_pipeline_chaining(self, tc):
        """Pipeline methods support chaining."""
        pipe = tc.pipeline()
        pipe.kset("c1", "1").kset("c2", "2").kget("c1")
        results = pipe.execute()
        assert results == [True, True, "1"]

    def test_pipeline_mixed_commands(self, tc):
        """Pipeline mixes different command types."""
        with tc.pipeline() as pipe:
            pipe.ping()
            pipe.kset("mix", "hello")
            pipe.kget("mix")
            pipe.kdbsize()
            results = pipe.execute()

        assert results[0] is True      # PING → True
        assert results[1] is True      # KSET → True
        assert results[2] == "hello"   # KGET
        assert results[3] >= 1         # KDBSIZE

    def test_pipeline_error_in_batch(self, tc):
        """Errors in pipeline appear as exceptions in results list."""
        with tc.pipeline() as pipe:
            pipe.kset("good", "val")
            pipe.kset("_sys:bad", "val")  # READONLY error
            pipe.kget("good")
            results = pipe.execute()

        assert results[0] is True
        assert isinstance(results[1], Exception)  # ReadOnlyError
        assert results[2] == "val"

    def test_pipeline_instrument_workflow(self, tc):
        """Pipeline for instrument setup."""
        tc.iadd("pgen", "dryrun")

        with tc.pipeline() as pipe:
            pipe.ilock("pgen")
            pipe.iinit("pgen")
            results = pipe.execute()

        assert results[0] is True
        assert results[1] is True

        # Read/write via pipeline
        with tc.pipeline() as pipe:
            pipe.iwrite("pgen", "FREQ", "1e6")
            pipe.iwrite("pgen", "VOUT", "2.0")
            pipe.iread("pgen", "FREQ")
            results = pipe.execute()

        assert results[0] is True
        assert results[1] is True
        assert results[2] is not None

        tc.iunlock("pgen")

    def test_pipeline_multiple_execute(self, tc):
        """Pipeline can be reused after execute."""
        pipe = tc.pipeline()
        pipe.kset("re1", "a")
        r1 = pipe.execute()
        assert r1 == [True]

        pipe.kset("re2", "b")
        pipe.kget("re1")
        r2 = pipe.execute()
        assert r2 == [True, "a"]
        pipe.reset()
