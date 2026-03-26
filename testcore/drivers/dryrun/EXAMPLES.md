# DryRun Driver — CLI Examples

Simulated instrument for testing. Default resources: `CH1`, `CH2`, `VOUT`, `FREQ`.

## Setup

```
IADD sim dryrun
ILOCK sim
IINIT sim
```

## Read / Write Resources

```
IREAD sim VOUT
"0.0"

IWRITE sim VOUT 3.3
OK

IREAD sim VOUT
"3.3"

IWRITE sim FREQ 1000
OK

IREAD sim FREQ
"1000"
```

## Discover Resources

```
IRESOURCES sim
1) "CH1"
2) "CH2"
3) "VOUT"
4) "FREQ"
```

## Read Multiple

```
IMREAD sim:VOUT sim:FREQ
1) "3.3"
2) "1000"
```

## Passthrough (IRAW)

```
IRAW sim *IDN?
"DRYRUN_ECHO: *IDN?"

IRAW sim MEAS:VOLT?
"DRYRUN_ECHO: MEAS:VOLT?"
```

## Instrument Info

```
IINFO sim
name:sim
driver:dryrun
state:READY
resources:4
lock_owner:1
...
```

## Load CSV Data

```
ILOAD sim CH1:TestWave /path/to/data.csv
"42 points loaded"
```

## Save Data (ISAVE)

```
ISAVE sim SCREEN /tmp/screenshot.png
"72 bytes saved"

ISAVE sim DATA /tmp/trace.csv
"4 rows saved"
```

Supported targets:
- `SCREEN` — simulated screenshot (PNG header)
- `DATA` — export all resource values as CSV

## Teardown

```
IUNLOCK sim
OK

IREMOVE sim
OK
```

## Full Session Example

```
testcore#1 > IADD sim dryrun
OK
testcore#1 > ILOCK sim
OK
testcore#1 > IINIT sim
OK
testcore#1 > IRESOURCES sim
1) "CH1"
2) "CH2"
3) "FREQ"
4) "VOUT"
testcore#1 > IWRITE sim VOUT 5.0
OK
testcore#1 > IREAD sim VOUT
"5.0"
testcore#1 > KSET meas:voltage 5.0
OK
testcore#1 > KGET meas:voltage
"5.0"
testcore#1 > IUNLOCK sim
OK
```
