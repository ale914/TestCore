# TestCore

**A command server for test & measurement bench automation.**

TestCore sits between your test scripts and your lab instruments. It manages connections, enforces safe access, and lets multiple clients share a bench — all through a simple text protocol over TCP.

Any engineer can connect with a terminal, type `PING`, get `+PONG`. Any language with a TCP socket can automate a full test sequence. No vendor SDK, no proprietary framework.

```
 Test Script (Python/C#/LabVIEW/...)
        │  RESP2 over TCP
        ▼
   ┌──────────┐
   │ TestCore │  port 6399
   └────┬─────┘
        │
   ┌────┴───────────────────────────┐
   │  Driver Layer                  │
   ├──────┬──────┬──────┬───────────┤
   │ VSG  │  SA  │ PSU  │  Scope .. │
   └──────┴──────┴──────┴───────────┘
        │  VISA / TCP / USB
        ▼
   Physical Instruments
```

## Philosophy

Inspired by the simplicity of Redis — do one thing, do it well. TestCore adopts the same principles: text protocol, single-threaded dispatch, in-memory store. It uses RESP2 as its wire format because it works well: human-readable, well-documented, and already supported by client libraries in every language.

But TestCore is its own project, built for the specific needs of hardware test automation: instrument lifecycle management, resource locking, and autonomous safety guards.

## Why

- **Language-agnostic.** Any language with a TCP socket can control your instruments. No vendor SDK required.
- **Human-debuggable.** Connect with a terminal or telnet. Type commands, read responses. No binary blobs.
- **Multi-client.** A test script drives the instruments while a dashboard observes readings in real time — same server, no conflicts.
- **Safety built in.** Autonomous watch guards monitor instrument readings and execute emergency shutdown sequences independently of the test client.
- **Instrument-aware locking.** Write access requires explicit locks. Unlock triggers safe-state on hardware. No accidental cross-session contamination.
