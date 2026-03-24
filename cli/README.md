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

## Example Session

```
testcore-cli
Type HELP for commands, EXIT to quit

Connecting to 127.0.0.1:6399...
Connected!

127.0.0.1:6399> PING
PONG

127.0.0.1:6399> KSET temp "22.5"
OK

127.0.0.1:6399> KGET temp
"22.5"

127.0.0.1:6399> HELP

=== TestCore CLI ===

Local Commands:
  HELP      - Show this help message
  HISTORY   - Show command history
  CLEAR     - Clear the screen
  EXIT/QUIT - Exit the CLI

Server Commands:
  CLIENT ID
  CLIENT LIST
  COMMAND LIST
  IADD
  IREAD
  KGET
  KSET
  PING
  ...

127.0.0.1:6399> EXIT
Bye!
```

## Local Commands

| Command | Action |
|---------|--------|
| `HELP` | Query server for available commands |
| `HISTORY` | Show command history (ring buffer, 100 entries) |
| `CLEAR` | Clear the screen |
| `EXIT` / `QUIT` | Disconnect and exit |

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
src/cli/
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
