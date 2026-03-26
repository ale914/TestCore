# Agilent 33500 Driver — CLI Examples

Supports 33521A (1-ch) and 33522A (2-ch) waveform generators.

## Setup

IADD takes the instrument address as the third argument.
TestCore opens the connection (VISA, TCP, or serial) and passes it to the driver.

```
IADD awg agilent33500 TCPIP0::192.168.1.50::inst0::INSTR
ILOCK awg
IINIT awg
```

Supported address formats:
- VISA: `TCPIP0::192.168.1.50::inst0::INSTR`, `GPIB0::5::INSTR`, `USB0::...::INSTR`
- TCP socket: `192.168.1.50:5025`
- Serial: `COM3` (with optional transport overrides)

Serial example with overrides:
```
IADD awg agilent33500 COM3 baudrate=115200 timeout=10000
```

## Basic Waveform

```
IWRITE awg CH1:FUNC SIN
OK

IWRITE awg CH1:FREQ 10000
OK

IWRITE awg CH1:AMPL 2.5
OK

IWRITE awg CH1:OFFSET 0
OK

IWRITE awg CH1:OUTPUT ON
OK
```

## Read Back Settings

```
IREAD awg CH1:FUNC
"SIN"

IREAD awg CH1:FREQ
"10000.0"

IREAD awg CH1:AMPL
"2.5"

IREAD awg CH1:OUTPUT
"1"
```

## Discover Resources

```
IRESOURCES awg
 1) "CH1:AMPL"
 2) "CH1:ARB_ADVANCE"
 3) "CH1:ARB_FILTER"
 4) "CH1:ARB_FUNC"
 5) "CH1:ARB_PTPEAK"
 6) "CH1:ARB_SRATE"
 7) "CH1:AUTORANGE"
 8) "CH1:BURST"
 9) "CH1:BURST_CYCLES"
10) "CH1:BURST_GATE"
...
```

## Pulse Waveform

```
IWRITE awg CH1:FUNC PULS
OK

IWRITE awg CH1:FREQ 1000
OK

IWRITE awg CH1:PULSE_WIDTH 0.0001
OK

IWRITE awg CH1:LEAD_EDGE 0.000000010
OK

IWRITE awg CH1:TRAIL_EDGE 0.000000010
OK

IWRITE awg CH1:OUTPUT ON
OK
```

## Square Wave with Duty Cycle

```
IWRITE awg CH1:FUNC SQU
OK

IWRITE awg CH1:FREQ 5000
OK

IWRITE awg CH1:SQUARE_DUTY 25
OK

IWRITE awg CH1:OUTPUT ON
OK
```

## Sweep

```
IWRITE awg CH1:FUNC SIN
OK

IWRITE awg CH1:FREQ_START 100
OK

IWRITE awg CH1:FREQ_STOP 10000
OK

IWRITE awg CH1:SWEEP_TIME 5
OK

IWRITE awg CH1:SWEEP_SPACING LIN
OK

IWRITE awg CH1:SWEEP ON
OK

IWRITE awg CH1:OUTPUT ON
OK
```

## Burst Mode

```
IWRITE awg CH1:FUNC SIN
OK

IWRITE awg CH1:FREQ 1000
OK

IWRITE awg CH1:BURST_CYCLES 5
OK

IWRITE awg CH1:BURST_MODE TRIG
OK

IWRITE awg CH1:BURST ON
OK

IWRITE awg CH1:OUTPUT ON
OK
```

## Arbitrary Waveform

```
IWRITE awg CH1:ARB_SRATE 250000
OK

IWRITE awg CH1:ARB_FUNC MyPulse
OK

IWRITE awg CH1:FUNC ARB
OK

IWRITE awg CH1:OUTPUT ON
OK
```

## Load ARB from CSV

```
ILOAD awg CH1:MyPulse /data/pulse.csv
"1024 points loaded"
```

CSV file format (one float per line, range -1.0 to +1.0):
```
# pulse.csv
0.0
0.0
1.0
1.0
1.0
-1.0
-1.0
0.0
```

## SCPI Passthrough (IRAW)

For commands not mapped to resources:

```
IRAW awg *IDN?
"Agilent Technologies,33522A,MY12345678,2.05-1.19-2.00-52-00"

IRAW awg MEAS:FREQ?
"1000.02"

IRAW awg SYST:ERR?
"+0,No error"

IRAW awg DATA:VOL:CAT?
"MyPulse,Noise1"

IRAW awg DATA:VOL:CLE
OK
```

## Trigger

```
IWRITE awg CH1:TRIG_SOURCE EXT
OK

IWRITE awg CH1:TRIG_SLOPE POS
OK

IREAD awg CH1:TRIG_SOURCE
"EXT"
```

## Sync Output

```
IWRITE awg CH1:SYNC ON
OK

IWRITE awg CH1:SYNC_MODE NORM
OK

IWRITE awg CH1:SYNC_POLARITY NORM
OK
```

## Output Control

```
IWRITE awg CH1:LOAD 50
OK

IWRITE awg CH1:POLARITY NORM
OK

IREAD awg CH1:LOAD
"50"
```

## Dual Channel (33522A)

```
IWRITE awg CH1:FUNC SIN
OK

IWRITE awg CH1:FREQ 1000
OK

IWRITE awg CH1:OUTPUT ON
OK

IWRITE awg CH2:FUNC SQU
OK

IWRITE awg CH2:FREQ 1000
OK

IWRITE awg CH2:OUTPUT ON
OK
```

## Read Multiple Resources

```
IMREAD awg:CH1:FREQ awg:CH1:AMPL awg:CH1:FUNC
1) "1000.0"
2) "2.5"
3) "SIN"
```

## Teardown

IUNLOCK triggers `safe_state()` which turns off all outputs automatically.

```
IUNLOCK awg
OK

IREMOVE awg
OK
```

## Full Session Example

```
testcore#1 > IADD awg agilent33500 TCPIP0::192.168.1.50::inst0::INSTR
OK
testcore#1 > ILOCK awg
OK
testcore#1 > IINIT awg
OK
testcore#1 > IWRITE awg CH1:FUNC SIN
OK
testcore#1 > IWRITE awg CH1:FREQ 10000
OK
testcore#1 > IWRITE awg CH1:AMPL 1.0
OK
testcore#1 > IWRITE awg CH1:OUTPUT ON
OK
testcore#1 > IREAD awg CH1:FREQ
"10000.0"
testcore#1 > KSET awg:ch1:freq 10000
OK
testcore#1 > IUNLOCK awg
OK
```

## Resource Reference

| Resource | Description | Example Values |
|----------|-------------|----------------|
| `FUNC` | Waveform function | SIN, SQU, RAMP, PULS, ARB, NOIS, DC |
| `FREQ` | Frequency (Hz) | 1000, 1e6 |
| `AMPL` | Amplitude (Vpp) | 0.01 to 10 |
| `OFFSET` | DC offset (V) | -5 to 5 |
| `OUTPUT` | Output on/off | ON, OFF, 1, 0 |
| `LOAD` | Output load (ohm) | 50, INF |
| `POLARITY` | Output polarity | NORM, INV |
| `HIGH` | High voltage level | 0 to 5 |
| `LOW` | Low voltage level | -5 to 0 |
| `UNIT` | Amplitude units | VPP, VRMS, DBM |
| `AUTORANGE` | Voltage autorange | ON, OFF |
| `FREQ_MODE` | Frequency mode | CW, SWE, LIST |
| `FREQ_START` | Sweep start freq | 100 |
| `FREQ_STOP` | Sweep stop freq | 1e6 |
| `FREQ_CENTER` | Sweep center freq | 500e3 |
| `FREQ_SPAN` | Sweep span | 999900 |
| `PULSE_WIDTH` | Pulse width (s) | 0.001, 100e-9 |
| `PULSE_DUTY` | Pulse duty cycle (%) | 10 to 90 |
| `LEAD_EDGE` | Pulse leading edge (s) | 8.4e-9 |
| `TRAIL_EDGE` | Pulse trailing edge (s) | 8.4e-9 |
| `EDGE_TIME` | Pulse edge time both (s) | 8.4e-9 |
| `PULSE_PERIOD` | Pulse period (s) | 0.001 |
| `SQUARE_DUTY` | Square duty cycle (%) | 1 to 99 |
| `RAMP_SYMMETRY` | Ramp symmetry (%) | 0 to 100 |
| `ARB_FUNC` | Active arb waveform | MyPulse |
| `ARB_SRATE` | Arb sample rate (Sa/s) | 250000, 250e6 |
| `ARB_FILTER` | Arb filter | NORM, STEP, OFF |
| `ARB_PTPEAK` | Arb peak-to-peak (V) | 0.01 to 10 |
| `ARB_ADVANCE` | Arb advance mode | TRIG, SRAT |
| `SWEEP` | Sweep on/off | ON, OFF |
| `SWEEP_TIME` | Sweep time (s) | 1, 500 |
| `SWEEP_SPACING` | Sweep spacing | LIN, LOG |
| `SWEEP_HOLD` | Sweep hold time (s) | 0, 5 |
| `SWEEP_RETURN` | Sweep return time (s) | 0 |
| `BURST` | Burst on/off | ON, OFF |
| `BURST_MODE` | Burst mode | TRIG, GAT |
| `BURST_CYCLES` | Burst cycle count | 1 to 1e6, INF |
| `BURST_PHASE` | Burst start phase (deg) | 0 to 360 |
| `BURST_PERIOD` | Burst period (s) | 0.001 |
| `BURST_GATE` | Burst gate polarity | NORM, INV |
| `TRIG_SOURCE` | Trigger source | IMM, EXT, BUS |
| `TRIG_SLOPE` | Trigger slope | POS, NEG |
| `TRIG_OUT` | Trigger output | ON, OFF |
| `TRIG_OUT_SLOPE` | Trigger output slope | POS, NEG |
| `SYNC` | Sync output | ON, OFF |
| `SYNC_MODE` | Sync mode | NORM, CARR, MARK |
| `SYNC_POLARITY` | Sync polarity | NORM, INV |
| `SYNC_SOURCE` | Sync source | CH1, CH2 |
