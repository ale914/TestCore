# TestCore CLI Client

Interactive command-line client for TestCore server, written in C for Windows.

## Build

```bash
# Using build script
build.bat

# Manual
gcc -Wall -O2 testcore_cli.c linenoise/linenoise.c linenoise/stringbuf.c linenoise/utf8.c -o testcore_cli.exe -lws2_32 -s
```

Requires MinGW-w64 (`choco install mingw`).

## Usage

```bash
testcore_cli.exe                        # default: 127.0.0.1:6399
testcore_cli.exe -h 192.168.1.100       # custom host
testcore_cli.exe -p 6400                # custom port
testcore_cli.exe -h 10.0.0.5 -p 6399   # both
```

## Features

- **TAB completion** — fetches command list from server at startup
- **Inline hints** — shows argument syntax as you type
- **MONITOR mode** — press any key to stop, auto-exits server-side mode
- **SUBSCRIBE mode** — type UNSUBSCRIBE to exit
- **Auto-reconnect** — reconnects on connection loss, retries on startup
- **Prompt identity** — shows `testcore#N >` or `name#N >` after CLIENT NAME

## Example Session

```
connected 127.0.0.1:6399
testcore#1 > PING
PONG

testcore#1 > CLIENT NAME bench-a
OK

bench-a#1 > KSET temp "22.5"
OK

bench-a#1 > KGET temp
"22.5"

bench-a#1 > IADD sim dryrun
OK

bench-a#1 > IPING sim
"DryRun Simulator, Model 1000, SN 000000, v1.0"

bench-a#1 > JOURNAL 5 REL
+0.000000 [bench-a] KSET temp "22.5" -> ok
+0.012345 [bench-a] IADD sim dryrun -> ok
+0.003210 [bench-a] IPING sim -> ok

bench-a#1 > MONITOR
OK
monitor mode — press any key to stop
1711234567.123456 [bench-a#1] KSET temp 22.5
monitor stopped

bench-a#1 > EXIT
```

## Local Commands

| Command | Action |
|---------|--------|
| `CLEAR` | Clear the screen |
| `EXIT` / `QUIT` | Disconnect and exit |

All other input is sent to the server as-is (case preserved).

## RESP Display

| Type | Example | Display |
|------|---------|---------|
| Simple String | `+PONG` | `PONG` |
| Error | `-ERR msg` | `(error) ERR msg` (red) |
| Integer | `:42` | `(integer) 42` |
| Bulk String | `$5\r\nhello` | `"hello"` |
| Null | `$-1` | `(nil)` |
| Array | `*2\r\n...` | Numbered list |

## Files

```
cli/
├── testcore_cli.c         # CLI source (AGPL-3.0)
├── build.bat              # Build script
├── README.md
└── linenoise/             # Third-party line editing library (BSD-2-Clause)
    ├── LICENSE
    ├── linenoise.c
    ├── linenoise.h
    ├── linenoise-win32.c
    ├── stringbuf.c / .h
    └── utf8.c / .h
```
