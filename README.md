# TestCore

**Middleware for test bench orchestration.**

TestCore is a command server that sits between your test automation and your lab instruments. It provides a shared state machine, exclusive resource locking, and a real-time event system — all accessible through a text protocol over TCP from any language.

It is not a SCPI gateway. TestCore does not know or care what protocol your instruments speak. Drivers translate between TestCore's uniform resource model and whatever the hardware expects: SCPI, proprietary serial, REST, binary protocols. What TestCore manages is the layer above: who owns what, what state each instrument is in, and what happens when something goes wrong.

```
 Client A (Python)          Client B (Dashboard)       Client C (CLI debug)
     │                           │                          │
     │  IWRITE awg CH1:FREQ 1e9  │  SUBSCRIBE __event:kv    │  JOURNAL 20
     │                           │                          │
     ▼                           ▼                          ▼
  ┌─────────────────────────────────────────────────────────────┐
  │                      TestCore Server                        │
  │                                                             │
  │  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐  │
  │  │ Locking  │  │ KV Store │  │  Events  │  │  Journal   │  │
  │  └─────────┘  └──────────┘  └──────────┘  └────────────┘  │
  │                                                             │
  │  ┌─────────────────────────────────────────────────────┐   │
  │  │              Instrument Registry                     │   │
  │  │   awg: READY (owner: A)                             │   │
  │  │   psu: IDLE                                         │   │
  │  │   dmm: LOCKED (owner: A)                            │   │
  │  └─────────────────────────────────────────────────────┘   │
  └──────┬──────────────┬──────────────┬───────────────────────┘
         │              │              │
    ┌────┴────┐    ┌────┴────┐    ┌────┴────┐
    │ Driver  │    │ Driver  │    │ Driver  │
    │ (SCPI)  │    │ (Serial)│    │ (REST)  │
    └────┬────┘    └────┬────┘    └────┬────┘
         │              │              │
    ┌────┴────┐    ┌────┴────┐    ┌────┴────┐
    │   AWG   │    │   PSU   │    │   DMM   │
    └─────────┘    └─────────┘    └─────────┘
```

---

## Philosophy

Five rules. No exceptions.

1. **Minimal and predictable.** Every command does one thing. The server is a thin, fast router between TCP clients and hardware drivers. It does not interpret measurement results, does not implement test logic, and does not know what an oscilloscope is.

2. **Text protocol, human-debuggable.** RESP2 over TCP. Any engineer can connect via telnet, type `PING`, get `+PONG`. No SDK required. Works out of the box.

3. **In-memory, volatile by design.** All state lives in RAM. Persistence is the client's responsibility. `DUMP` exports a JSON snapshot. This is a live test tool, not a database.

4. **Single-threaded command dispatch.** Connections are concurrent (asyncio), but command execution is serial from a single dispatch loop. No race conditions by design. Driver calls are offloaded to a thread executor with a watchdog timeout.

5. **Safety as a first-class primitive.** `IUNLOCK` and client disconnect trigger `safe_state()` on hardware — outputs off, reset to defaults. The server ensures instruments are never left in an undefined state, even after a client crash.

---

## Why

### The problem

Test bench automation without a coordination layer means every script manages its own connections, its own state tracking, and its own error handling. Four instruments means four open sessions, four sets of timeout logic, and zero protection if two scripts try to drive the same hardware. When a script crashes mid-test, outputs stay on and the bench is in an unknown state.

Vendor frameworks solve some of this, but lock you into one ecosystem with proprietary licenses. Building your own coordination layer is months of work before you write your first test.

### What TestCore is

TestCore is infrastructure. It handles the problems that are the same on every bench, so your test code only deals with the problems that are unique to your test.

- **Structured hardware access.** Every instrument has a lifecycle (`IDLE → LOCKED → READY`), a resource list, and an owner. No implicit state — you always know what is connected, who controls it, and whether it has been initialized.

- **Exclusive locking with safety guarantees.** `ILOCK awg psu` acquires both atomically. Only the lock holder can write. On unlock or disconnect, `safe_state()` is called — outputs go off, instruments reset. The bench is always safe, even after a crash.

- **Shared state for coordination.** The KV store is a blackboard visible to all clients. A test script writes `KSET meas:freq:900 "-42.3"`, a dashboard reads it in real time via events, a post-processing script collects everything with `KGETALL meas:`. No file polling, no shared directories, no message queues.

- **Real-time events.** `SUBSCRIBE __event:kv:meas:*` pushes every matching KSET to the subscriber instantly. Instrument state changes, lock transitions, client connects — all observable without polling.

- **Protocol-agnostic drivers.** A driver is a Python class with `read()`, `write()`, and `passthrough()`. It can talk SCPI over VISA, binary commands over serial, HTTP to a REST API, or anything else. TestCore doesn't care — it routes commands by resource name.

- **Language-agnostic clients.** Any language with a TCP socket works. Python, C#, LabVIEW, MATLAB, or a bash script with `nc`. The protocol is text-based (RESP2) and human-debuggable from a terminal.

### Concrete examples

**Production test station** — A C# test executive locks five instruments, runs a calibration sequence, and stores 200 measurements per unit in the KV store. A Python script subscribes to `__event:kv` and streams results to a database in real time. An engineer connects with the CLI to inspect instrument state without interrupting the test. Three clients, one bench, no conflicts.

**Automated characterization** — A Python script sweeps 100 frequency points. At each point it configures the signal generator, reads four power sensors and two oscilloscopes with a single `IMREAD` call, and batch-stores all results with `KMSET`. The entire sweep runs through one TCP connection using pipeline batching — three round-trips instead of six hundred.

**Overnight reliability test** — The script runs 1000 power cycles on a DUT. At cycle 437 the script crashes. TestCore detects the disconnect, releases all locks, and calls `safe_state()` — PSU outputs go off, signal generator resets. When you arrive in the morning the bench is safe, and `JOURNAL ALL` shows exactly where it stopped.

**New instrument, zero code** — You just received a multimeter with no TestCore driver. `IADD dmm generic_scpi TCPIP::192.168.1.50::5025` registers it with the passthrough-only SCPI driver. `IRAW dmm "MEAS:VOLT:DC?"` reads a voltage immediately. Write a proper driver with mapped resources later, when you need structured access.

**Multi-team bench sharing** — The hardware team uses aliases (`ALIAS SET dut_voltage SUB psu:CH1:VOLTAGE`) to abstract the physical wiring. The software team writes tests using alias names. When the bench is rewired, only the alias definitions change — no test code modified.

---

## Quick Start

```bash
pip install -e .
python -m testcore
```

The server listens on `127.0.0.1:6399` by default.

```bash
# Server options
python -m testcore --port 6400                # custom port
python -m testcore --bind 0.0.0.0             # listen on all interfaces
python -m testcore --driver-timeout 10        # 10s watchdog for driver calls
python -m testcore --max-clients 32           # limit connections
python -m testcore --journal-size 5000        # journal ring buffer size
python -m testcore --loglevel debug           # verbose logging
```

### Interactive session

```
testcore#1 > PING
PONG

testcore#1 > IADD awg dryrun
OK

testcore#1 > ILOCK awg
OK

testcore#1 > IINIT awg
OK

testcore#1 > IRESOURCES awg
1) "CH1"
2) "CH2"
3) "FREQ"
4) "VOUT"

testcore#1 > IWRITE awg CH1 3.3
OK

testcore#1 > IREAD awg CH1
"3.3"

testcore#1 > KSET meas:900:power "-12.3"
OK

testcore#1 > KGET meas:900:power
"-12.3"
```

### Python client library

```python
from testcore_client import TestCore

tc = TestCore()
tc.ping()                                    # True
tc.kset("freq", "1000")                      # True
tc.kget("freq")                              # "1000"

# Instrument workflow
tc.iadd("awg", "drivers/agilent33500")
tc.ilock("awg")
tc.iinit("awg")
tc.iwrite("awg", "CH1:FREQ", "1000")
tc.iread("awg", "CH1:FREQ")                  # "1000.0"
tc.iunlock("awg")

# Pipeline (batch commands, single round-trip)
with tc.pipeline() as pipe:
    pipe.iwrite("awg", "CH1:FREQ", "900e6")
    pipe.iwrite("awg", "CH1:AMPL", "2.5")
    pipe.iwrite("awg", "CH1:OUTPUT", "ON")
    results = pipe.execute()

# Event subscription
tc.subscribe("__event:kv:meas:*")            # KV changes matching glob
tc.subscribe("__event:instrument")           # state transitions

# Server introspection
tc.dump()                                    # JSON snapshot of full state
tc.journal(20)                               # last 20 commands
tc.kgetall("meas:")                          # all KV pairs with prefix

tc.close()
```

---

## Architecture

### Component Map

| Component           | Module           | Responsibility                                                                 |
|---------------------|------------------|--------------------------------------------------------------------------------|
| Network Layer       | `server.py`      | Accept TCP connections, parse/serialize RESP2, feed commands to the event loop  |
| Protocol            | `protocol.py`    | RESP2 parser/serializer (all 5 types + inline commands)                         |
| Key-Value Store     | `store.py`       | In-memory dict for KSET/KGET/KDEL. Reserved key prefixes                       |
| Instrument Manager  | `instruments.py` | Instrument registry, state machine, route commands to driver instances          |
| Command Registry    | `commands.py`    | Dispatch table: command name → handler function. All built-in commands          |
| Event System        | `events.py`      | Server-side pub/sub for instrument, lock, KV, and session events               |
| Journal             | `journal.py`     | Command ring buffer for auditing and debugging                                 |

### Request Flow

```
Client → TCP Socket → RESP Parser (protocol.py)
  → Event Loop (server.py)
  → Dispatch Table Lookup (commands.py)
  → Handler Execution
    → [store.py | instruments.py | direct response]
  → RESP Serializer (protocol.py) → TCP Socket → Client
```

The event loop processes one command at a time. Driver calls are executed via `asyncio.to_thread()` with `asyncio.wait_for()` timeout. If the timeout expires, the instrument is marked `UNRESPONSIVE`.

### Concurrency Model

TestCore uses `asyncio`. The TCP server accepts connections concurrently. Each connection has its own read buffer and RESP parser. Parsed commands are dispatched sequentially from the event loop. This is the Redis model: I/O is multiplexed, execution is serial. No race conditions by design.

### Project Structure

```
testcore/
├── __init__.py          # Package exports, __version__ (single source of truth)
├── __main__.py          # Entry point (python -m testcore)
├── server.py            # AsyncIO TCP server, client handler
├── protocol.py          # RESP2 parser/serializer
├── store.py             # Key-value store with reserved prefixes
├── instruments.py       # Instrument registry, state machine
├── commands.py          # Command dispatch table, all 46 handlers
├── events.py            # EventBus pub/sub, event publishing helpers
├── journal.py           # Command journal ring buffer
├── base_driver.py       # BaseDriver ABC, ScpiDriver base, DriverError
└── drivers/
    ├── dryrun/          # Built-in dry-run simulator
    ├── agilent33500/    # Agilent 33500 (48 resources, ARB support)
    └── generic_scpi/    # Passthrough-only, any SCPI instrument

testcore_client/         # Python client library
├── __init__.py
└── client.py            # TestCore sync client (pipeline, events)

cli/                     # Windows C CLI client
├── testcore_cli.c       # CLI source (AGPL-3.0)
├── build.bat            # MinGW build script
└── linenoise/           # Line editing library (BSD-2-Clause)

tests/                   # 607 tests, 21 files, pytest
```

---

## RESP2 Protocol

TestCore implements RESP2 (Redis Serialization Protocol).

| Type          | Prefix | Example                        | Use                    |
|---------------|--------|--------------------------------|------------------------|
| Simple String | `+`    | `+OK\r\n`                     | Success responses      |
| Error         | `-`    | `-ERR unknown command\r\n`     | Error responses        |
| Integer       | `:`    | `:1000\r\n`                    | Counters, timestamps   |
| Bulk String   | `$`    | `$5\r\nhello\r\n`             | Binary-safe values     |
| Array         | `*`    | `*2\r\n$3\r\nfoo\r\n...`      | Multi-value responses  |
| Null          | `$`    | `$-1\r\n`                     | Key not found          |

Clients send commands as RESP arrays of bulk strings. Inline commands (plain text + `\r\n`) are also accepted for telnet compatibility.

---

## Key Concepts

| Concept | Description |
|---------|-------------|
| **Driver** | A Python module implementing `BaseDriver`. Stateless, reusable across multiple instruments of the same type. |
| **Instrument** | A physical device registered with a unique name. Has lifecycle state: `IDLE` → `LOCKED` → `READY`. |
| **Resource** | A capability exposed by a driver (e.g., `FREQ`, `POWER`, `VOLTAGE`). Addressed as `instrument:resource`. |

### Instrument Lifecycle

```
IADD ──→ IDLE ──→ ILOCK ──→ LOCKED ──→ IINIT ──→ READY
           ▲                    ▲                    │
           └── IUNLOCK ─────────┘       FAULT/UNRESPONSIVE
           (safe_state)                  └── IRESET ──→ LOCKED
```

| State          | Owner     | Meaning                              | IREAD / IWRITE / IRAW | IINIT / IALIGN | IRESET |
|----------------|-----------|--------------------------------------|------------------------|----------------|--------|
| `IDLE`         | none      | Connected, no owner                  | Blocked (`-IDLE`)      | `-NOLOCK`      | no     |
| `LOCKED`       | session X | Owned, not yet initialized           | Blocked (`-NOTINIT`)   | **OK**         | no     |
| `READY`        | session X | Owned, initialized and operational   | **OK** (owner only)    | **OK**         | no     |
| `UNRESPONSIVE` | session X | Driver call timed out                | Blocked (`-FAULT`)     | no             | **OK** |
| `FAULT`        | session X | Unexpected driver exception          | Blocked (`-FAULT`)     | no             | **OK** |

Key behaviors:
- All resource access requires lock. Only the lock holder can IREAD/IWRITE/IRAW.
- **IUNLOCK → IDLE**: Calls `safe_state()`, clears owner. Next client must ILOCK + IINIT.
- **IALIGN**: Accepts current instrument state without re-initialization. `LOCKED → READY`.
- **Disconnect cleanup**: `safe_state()` called on all owned instruments, state → IDLE.

### Key-Value Store

The store is a Python `dict`. Keys and values are strings. Identical to Redis string type semantics.

**Reserved key prefixes** (read-only, written by the server for introspection):

| Prefix   | Purpose                                                  |
|----------|----------------------------------------------------------|
| `_sys:`  | Server internal state (uptime, version, stats)           |
| `_drv:`  | Driver module metadata (path, class name, loaded status) |
| `_inst:` | Instrument metadata (state, driver, lock_owner, stats)   |
| `_sess:` | Session data (per-connection state, client name)         |
| `_lock:` | Resource lock state                                      |

Client keys must not start with `_`. KSET on a `_` key returns `-READONLY`. KGET on reserved keys is allowed and is the primary introspection mechanism.

### Event System

When server-side events occur, subscribed clients receive async RESP push messages. Clients subscribe via `SUBSCRIBE`. This is a minimal subset of Redis Pub/Sub — only server-generated events, no client-to-client messaging.

| Channel                | Fires when                                                    |
|------------------------|---------------------------------------------------------------|
| `__event:instrument`   | Instrument state changes (ADD, REMOVE, INIT, FAULT, etc.)    |
| `__event:lock`         | Lock acquired, released, or force-released                    |
| `__event:session`      | Client connects or disconnects                                |
| `__event:kv`           | KSET stores a value (key, value, session_id)                  |
| `__event:kv:<glob>`    | KSET with key matching glob pattern (filtered server-side)    |

KV event filtering allows dashboard clients to react to specific changes without polling:

```
SUBSCRIBE __event:kv              -- all KSET events
SUBSCRIBE __event:kv:meas:*       -- only keys starting with "meas:"
SUBSCRIBE __event:kv:alert:*      -- only keys starting with "alert:"
```

### Session Management

Each TCP connection is a session with a sequential integer ID. On disconnect:

1. For each lock held: call `safe_state()` on the instrument, set state → `IDLE`.
2. Delete all session keys (`_sess:<id>:*`).
3. Remove from MONITOR and SUBSCRIBE lists.
4. Log disconnect to journal.
5. Publish event on `__event:session`.

Hardware is never left in an undefined state after a client crash.

---

## Command Reference (46 commands)

### Command Naming

Commands use **prefix-based naming** and **Redis-style subcommands**:

| Pattern | Category | Examples |
|---------|----------|---------|
| *(none)* | Server | `PING`, `INFO`, `MONITOR`, `SUBSCRIBE` |
| `CLIENT *` | Client subcommands | `CLIENT ID`, `CLIENT LIST`, `CLIENT NAME` |
| `COMMAND *` | Command subcommands | `COMMAND LIST` |
| `DRIVER *` | Driver subcommands | `DRIVER LIST` |
| `K*` | Key-Value store | `KSET`, `KGET`, `KDEL`, `KKEYS` |
| `I*` | Instruments | `IADD`, `IREAD`, `ILOCK`, `ISAVE` |
| `ALIAS *` | Alias subcommands | `ALIAS SET`, `ALIAS LIST` |
| `A*` | Alias access | `AREAD`, `AWRITE` |

### Server Commands

#### PING [message]
Returns `PONG` or echoes the message.
```
PING           →  +PONG
PING hello     →  $5\r\nhello
```

#### INFO [section]
Returns server information as bulk string. Sections: `server`, `clients`, `memory`, `instruments`, `stats`, `health`. Without argument, returns all.

#### TIME
Returns `[unix_seconds, microseconds]`. Identical to Redis TIME.

#### DUMP
Returns JSON snapshot of the entire server state: KV store (excluding reserved prefixes), instruments, locks, sessions, version, timestamp.
```
DUMP  →  $...\r\n{"version":"0.9.0","kv":{...},"instruments":{...},...}
```

#### JOURNAL [count | +offset [count] | ALL | CLEAR]
Returns entries from the command ring buffer. Tail-style syntax:
```
JOURNAL            →  last 100 entries (default)
JOURNAL 20         →  last 20 entries
JOURNAL +50        →  from entry 50 onward
JOURNAL +50 10     →  10 entries starting at offset 50
JOURNAL ALL        →  all entries in buffer
JOURNAL CLEAR      →  clear the ring buffer
```

#### CLIENT ID
Returns the numeric session ID of the current connection.

#### CLIENT LIST
Returns info about all connected clients as bulk string.

#### CLIENT NAME [name]
Without argument, returns current name. With argument, assigns a human-readable name.

#### COMMAND LIST [pattern]
Returns array of all registered command names. Glob-style pattern filtering.
```
COMMAND LIST           →  ["PING", "INFO", "KSET", ...]
COMMAND LIST I*        →  ["IADD", "IREAD", "IWRITE", ...]
```

#### MONITOR
Enters monitor mode: streams every command from every client in real time. Sending any command exits monitor mode.
```
MONITOR  →  +OK
         →  1706140800.123456 [#3] "KSET" "freq" "1000"
         →  1706140801.234567 [test-pc#3] "IREAD" "awg:CH1:FREQ"
```

#### SUBSCRIBE channel [channel ...]
Subscribes to event channels. Connection enters subscriber mode.
```
SUBSCRIBE __event:instrument __event:lock
SUBSCRIBE __event:kv:meas:*
```

#### UNSUBSCRIBE [channel ...]
Unsubscribes from channels. Without arguments, unsubscribes from all.

### Key-Value Commands

#### KSET key value [NX|XX]
Sets a key. `NX`: only if not exists. `XX`: only if exists. Returns `OK` or nil.
```
KSET meas:freq 900e6           →  +OK
KSET meas:freq 900e6 NX        →  $-1  (already exists)
```

#### KGET key
Returns value or nil.

#### KMGET key [key ...]
Returns array of values for multiple keys. Nil for missing keys.
```
KMGET meas:power meas:freq meas:evm  →  ["-12.3", "900e6", "1.23"]
```

#### KMSET key value [key value ...]
Sets multiple keys atomically.
```
KMSET meas:power -12.3 meas:freq 900e6  →  +OK
```

#### KDEL key [key ...]
Removes one or more keys. Returns count of deleted keys.

#### KEXISTS key [key ...]
Returns count of specified keys that exist.

#### KKEYS pattern
Returns all keys matching glob pattern.

#### KDBSIZE
Returns number of client keys (excluding reserved prefixes).

#### KFLUSH
Removes all client keys. Reserved prefixes untouched.

#### KGETALL [prefix]
Returns all key-value pairs as a flat array `[k1, v1, k2, v2, ...]`. Excludes reserved prefixes. Optional prefix filters keys.
```
KGETALL               →  ["freq", "1000", "power", "-12.3", ...]
KGETALL meas:         →  ["meas:power", "-12.3", "meas:freq", "900e6"]
```

### Instrument Lifecycle Commands

#### IADD name driver_path [address] [key=value ...]
Creates an instrument instance. Opens transport based on address, passes it to the driver. State → `IDLE`. SCPI drivers require an address — only DryRun works without one.

Address routing (handled by TestCore transport layer):
- VISA strings (`*::INSTR`, `GPIB*`, `USB*`) → pyvisa
- Raw TCP (`host:port`) → socket
- Serial (`COM*`, `/dev/tty*`) → pyserial

```
IADD awg  agilent33500 TCPIP0::192.168.1.10::inst0::INSTR  →  +OK
IADD smu  keithley2400 COM3 baudrate=115200                  →  +OK
IADD sim  dryrun                                              →  +OK
```

#### IREMOVE name
Calls `safe_state()`, `disconnect()`. Releases any lock. Removes from registry.

#### IINIT name [config_file_path]
**Requires `LOCKED` or `READY`.** Full instrument initialization. Optional config file (driver-proprietary format). Calls `init()`, then `discover()` to populate resources. State → `READY`.
```
IINIT awg                          →  +OK
IINIT awg ./configs/awg_setup.cfg  →  +OK
```

#### IINFO name
Returns instrument metadata: name, driver, state, resource count, lock owner, call stats, and driver info (vendor, model, serial).

#### ILIST
Returns array of all instrument names.

#### IRESOURCES name
Returns resource list from `discover()`.

#### IRESET name
Resets an instrument in `UNRESPONSIVE`/`FAULT` state. Requires owner. Reconnects. State → `LOCKED`.

#### IALIGN instrument [instrument ...]
**Requires `LOCKED`.** Accepts current state without re-initialization. `LOCKED → READY`. Refreshes resource list.

#### DRIVER LIST
Returns array of registered driver module names.

### Resource Access Commands

Resource address format: `instrument:resource` (e.g., `awg:CH1:FREQ`, `psu:VOLTAGE`).

#### IREAD resource
Reads current value from hardware. **Requires lock + READY.**
```
IREAD awg:CH1:FREQ     →  "1000000.0"
IREAD psu:VOLTAGE       →  "3.300"
```

#### IWRITE resource value
Writes a value. **Requires lock + READY.**
```
IWRITE awg:CH1:FREQ 900e6    →  +OK
IWRITE psu:VOLTAGE 3.3       →  +OK
```

#### IRAW instrument command_string
Raw SCPI passthrough. **Requires lock + READY.**
```
IRAW awg "*IDN?"                    →  "Agilent,33522A,..."
IRAW dmm "MEAS:VOLT:DC?"           →  "1.23456"
```

#### IMREAD resource [resource ...]
Multi-read in one command. Returns array. Each read is independent — failures don't block others.
```
IMREAD awg:CH1:FREQ awg:CH1:AMPL psu:VOLTAGE  →  ["1e9", "2.5", "3.3"]
```

#### ILOAD instrument target file_path
Loads data from file into instrument (e.g., CSV waveform into arb generator). Driver-specific.
```
ILOAD awg CH1:MyWave ./waveforms/pulse.csv  →  "1024 points loaded"
```

#### ISAVE instrument target file_path
Saves data from instrument to file. Driver-specific.

### Lock Commands

#### ILOCK instrument [instrument ...]
Acquires exclusive access. Atomic all-or-nothing when locking multiple instruments. `IDLE → LOCKED`.
```
ILOCK awg psu             →  +OK
ILOCK awg                 →  -LOCKED awg owned by session 3
```

#### IUNLOCK instrument [instrument ...]
Releases locks. Calls `safe_state()` on each. State → `IDLE`. Only the owner can unlock.

#### IUNLOCK ALL
Releases all locks held by the current session.

#### ILOCKS
Returns all currently held locks with instrument name and owning session.

### Alias Commands

Aliases map human-readable names to instrument resources or raw commands. Test scripts use aliases; hardware details stay in configuration.

#### ALIAS SET name type target
Type is `SUB` (resource) or `RAW` (raw command string).
```
ALIAS SET rf_power  SUB pm1:POWER
ALIAS SET sa_acpr   RAW sa::CALC:ACPR:RES?
```

#### ALIAS GET name
Returns `[type, target]`.

#### ALIAS DEL name
Removes an alias.

#### ALIAS LIST
Returns all alias names.

#### AREAD alias_name
Reads through an alias. Resolves to `read()` for SUB, `passthrough()` for RAW. **Requires lock + READY.**

#### AWRITE alias_name value
Writes through a SUB alias. **Requires lock + READY.** RAW aliases don't support AWRITE.

---

## Error Handling

All errors are RESP error strings with a class prefix:

| Prefix       | Meaning                                                            |
|--------------|--------------------------------------------------------------------|
| `ERR`        | Generic: unknown command, wrong argument count, syntax error       |
| `IDLE`       | Instrument has no owner, requires ILOCK                            |
| `LOCKED`     | Instrument locked by another session                               |
| `NOLOCK`     | Operation requires lock but instrument is IDLE                     |
| `NOTINIT`    | Instrument is LOCKED but not initialized, requires IINIT or IALIGN |
| `DRIVER`     | Error from driver (DriverError message follows)                    |
| `TIMEOUT`    | Driver call exceeded watchdog timeout                              |
| `FAULT`      | Instrument is UNRESPONSIVE/FAULT, requires IRESET                  |
| `READONLY`   | Attempt to KSET a reserved key prefix                              |
| `NORESOURCE` | Instrument or resource does not exist                              |
| `NOALIAS`    | Alias does not exist                                               |
| `WRONGTYPE`  | Operation against wrong value type (e.g., KINCR on non-numeric)    |

Examples:
```
-IDLE awg not locked
-LOCKED awg owned by session 3 (test-pc)
-NOTINIT awg requires IINIT or IALIGN
-DRIVER unknown resource: INVALID
```

---

## Configuration

Command-line arguments:

| Parameter        | Default     | CLI Flag           | Description                              |
|------------------|-------------|--------------------|-----------------------------------------|
| `bind`           | `127.0.0.1` | `--bind`          | Listen address                           |
| `port`           | `6399`      | `--port`          | Listen port                              |
| `driver_timeout` | `5.0`       | `--driver-timeout` | Watchdog timeout for driver calls (s)   |
| `journal_size`   | `1000`      | `--journal-size`  | Ring buffer entries                      |
| `max_clients`    | `64`        | `--max-clients`   | Maximum simultaneous connections         |
| `loglevel`       | `info`      | `--loglevel`      | Logging verbosity                        |

### Reliability

- **Graceful shutdown:** On SIGINT/SIGTERM, `safe_state()` + `disconnect()` on all instruments before exit.
- **Max clients:** Connections beyond limit are rejected with `-ERR max clients reached`.
- **RESP buffer limit:** 1 MB per client. Oversized messages close the connection.
- **Unlock safety:** If `safe_state()` fails during IUNLOCK, the instrument transitions to `FAULT`, not `IDLE`. Requires `IRESET`.

---

## Writing a Driver

Drivers implement the `BaseDriver` abstract class:

```python
from testcore import BaseDriver, DriverError

class MyInstrumentDriver(BaseDriver):
    def connect(self, config: dict) -> None:
        """Open VISA/TCP connection. Called on IADD.
        config contains a ready-to-use transport object."""

    def disconnect(self) -> None:
        """Close connection. Called on IREMOVE / shutdown. Must not raise."""

    def init(self) -> None:
        """Full instrument reset and self-test. Called on IINIT."""

    def configure(self, config_path: str) -> None:
        """Apply proprietary config file. Called on IINIT with path."""

    def discover(self) -> list[str]:
        """Return available resources. Called after init and on IRESOURCES."""
        return ["FREQ", "POWER", "MODULATION"]

    def read(self, resource: str) -> str:
        """Read a resource value."""

    def write(self, resource: str, value: str) -> None:
        """Write a resource value."""

    def passthrough(self, command: str) -> str:
        """Raw SCPI tunneling. Called on IRAW."""

    def load(self, target: str, file_path: str) -> str:
        """Load data from file into instrument. Called on ILOAD."""

    def save(self, target: str, file_path: str) -> str:
        """Save data from instrument to file. Called on ISAVE."""

    def safe_state(self) -> None:
        """Put instrument in safe/idle state. Called on IUNLOCK, disconnect,
        and before IREMOVE. Must not raise."""

    def info(self) -> dict:
        """Return metadata: vendor, model, serial, version."""
```

### DriverError

Drivers signal errors via `raise DriverError(message)`. The server returns `-DRIVER <message>`. Any other exception type marks the instrument as `FAULT`.

### ScpiDriver Base

For SCPI instruments, `ScpiDriver` provides a base class with built-in `*RST`, `*CLS`, `*TST?`, `*IDN?` handling, system error checking, and passthrough. Subclass it and define a `_RESOURCES` dict mapping resource names to SCPI query/write strings.

### Bundled Drivers

- **DryRun** (`drivers/dryrun/`) — Simulator for testing without hardware. Stores values in memory, echoes passthrough commands. No address required.
- **Agilent 33500** (`drivers/agilent33500/`) — Waveform generator. 48 mapped resources per channel (frequency, amplitude, burst, sweep, modulation, ARB). CSV waveform loading via ILOAD. Auto-detects 1/2 channel models.
- **Generic SCPI** (`drivers/generic_scpi/`) — Passthrough-only driver. Connect any SCPI instrument without writing a custom driver. Use `IRAW` for all communication. Supports line-by-line SCPI config files.

### Driver Lifecycle Sequence

1. **IADD** (thin init): Import module → instantiate → open transport → `connect(config)` → state `IDLE`
2. **IINIT** (full init): `configure(path)` if provided → `init()` → `discover()` → state `READY`
3. **IALIGN** (accept state): `discover()` only → state `READY` (no reset/re-init)
4. **IUNLOCK** / disconnect: `safe_state()` → state `IDLE`
5. **IREMOVE**: `safe_state()` → `disconnect()` → removed

### Timeout and Watchdog

Every driver method call is wrapped in `asyncio.to_thread()` + `asyncio.wait_for()` with configurable timeout (default: 5s). If timeout fires: instrument is marked `UNRESPONSIVE`, all subsequent commands return `-FAULT` until `IRESET`.

---

## Running Tests

```bash
python -m pytest tests/ -v          # 607 tests
python -m pytest tests/ --tb=short  # compact output
python -m pytest tests/ -k "monitor"  # filter by name
```

---


## License

AGPL-3.0
