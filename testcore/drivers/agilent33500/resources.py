# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""SCPI resource map for Agilent 33500 Series.

Flat resource names → (SCPI query, SCPI write template[, prefix]).
Default prefix is "SOUR" → SOURn:command.
Override with 3rd element: "OUTP" → OUTPn:command, "TRIG" → TRIGn:command.
"""

# NAME → (query command, write template[, prefix])
RESOURCES: dict[str, tuple[str, ...]] = {
    # -- Output (OUTPn: prefix, not SOURn:) --
    "FUNC":             ("FUNC?",                "FUNC {value}"),
    "OUTPUT":           ("STAT?",                "STAT {value}",        "OUTP"),
    "LOAD":             ("LOAD?",                "LOAD {value}",        "OUTP"),
    "POLARITY":         ("POL?",                 "POL {value}",         "OUTP"),

    # -- Amplitude --
    "AMPL":             ("VOLT?",                "VOLT {value}"),
    "OFFSET":           ("VOLT:OFFS?",           "VOLT:OFFS {value}"),
    "HIGH":             ("VOLT:HIGH?",           "VOLT:HIGH {value}"),
    "LOW":              ("VOLT:LOW?",            "VOLT:LOW {value}"),
    "UNIT":             ("VOLT:UNIT?",           "VOLT:UNIT {value}"),
    "AUTORANGE":        ("VOLT:RANG:AUTO?",      "VOLT:RANG:AUTO {value}"),

    # -- Frequency --
    "FREQ":             ("FREQ?",                "FREQ {value}"),
    "FREQ_MODE":        ("FREQ:MODE?",           "FREQ:MODE {value}"),
    "FREQ_START":       ("FREQ:STAR?",           "FREQ:STAR {value}"),
    "FREQ_STOP":        ("FREQ:STOP?",           "FREQ:STOP {value}"),
    "FREQ_CENTER":      ("FREQ:CENT?",           "FREQ:CENT {value}"),
    "FREQ_SPAN":        ("FREQ:SPAN?",           "FREQ:SPAN {value}"),

    # -- Pulse --
    "PULSE_WIDTH":      ("FUNC:PULS:WIDT?",      "FUNC:PULS:WIDT {value}"),
    "PULSE_DUTY":       ("FUNC:PULS:DCYC?",      "FUNC:PULS:DCYC {value}"),
    "PULSE_PERIOD":     ("FUNC:PULS:PER?",        "FUNC:PULS:PER {value}"),
    "LEAD_EDGE":        ("FUNC:PULS:TRAN:LEAD?",  "FUNC:PULS:TRAN:LEAD {value}"),
    "TRAIL_EDGE":       ("FUNC:PULS:TRAN:TRA?",   "FUNC:PULS:TRAN:TRA {value}"),
    "EDGE_TIME":        ("FUNC:PULS:TRAN?",       "FUNC:PULS:TRAN {value}"),

    # -- Waveform shape --
    "SQUARE_DUTY":      ("FUNC:SQU:DCYC?",      "FUNC:SQU:DCYC {value}"),
    "RAMP_SYMMETRY":    ("FUNC:RAMP:SYMM?",     "FUNC:RAMP:SYMM {value}"),

    # -- Arbitrary waveform --
    "ARB_FUNC":         ("FUNC:ARB?",            "FUNC:ARB {value}"),
    "ARB_SRATE":        ("FUNC:ARB:SRAT?",       "FUNC:ARB:SRAT {value}"),
    "ARB_FILTER":       ("FUNC:ARB:FILT?",       "FUNC:ARB:FILT {value}"),
    "ARB_PTPEAK":       ("FUNC:ARB:PTP?",        "FUNC:ARB:PTP {value}"),
    "ARB_ADVANCE":      ("FUNC:ARB:ADV?",        "FUNC:ARB:ADV {value}"),

    # -- Sweep --
    "SWEEP":            ("SWE:STAT?",            "SWE:STAT {value}"),
    "SWEEP_TIME":       ("SWE:TIME?",            "SWE:TIME {value}"),
    "SWEEP_SPACING":    ("SWE:SPAC?",            "SWE:SPAC {value}"),
    "SWEEP_HOLD":       ("SWE:HTIM?",            "SWE:HTIM {value}"),
    "SWEEP_RETURN":     ("SWE:RTIM?",            "SWE:RTIM {value}"),

    # -- Burst --
    "BURST":            ("BURS:STAT?",           "BURS:STAT {value}"),
    "BURST_MODE":       ("BURS:MODE?",           "BURS:MODE {value}"),
    "BURST_CYCLES":     ("BURS:NCYC?",          "BURS:NCYC {value}"),
    "BURST_PHASE":      ("BURS:PHAS?",          "BURS:PHAS {value}"),
    "BURST_PERIOD":     ("BURS:INT:PER?",       "BURS:INT:PER {value}"),
    "BURST_GATE":       ("BURS:GATE:POL?",      "BURS:GATE:POL {value}"),

    # -- Trigger (TRIGn: prefix) --
    "TRIG_SOURCE":      ("SOUR?",                "SOUR {value}",        "TRIG"),
    "TRIG_SLOPE":       ("SLOP?",                "SLOP {value}",        "TRIG"),
    "TRIG_OUT":         ("TRIG?",                "TRIG {value}",        "OUTP"),
    "TRIG_OUT_SLOPE":   ("TRIG:SLOP?",           "TRIG:SLOP {value}",  "OUTP"),

    # -- Sync output (OUTPn: prefix) --
    "SYNC":             ("SYNC?",                "SYNC {value}",        "OUTP"),
    "SYNC_MODE":        ("SYNC:MODE?",           "SYNC:MODE {value}",   "OUTP"),
    "SYNC_POLARITY":    ("SYNC:POL?",            "SYNC:POL {value}",    "OUTP"),
    "SYNC_SOURCE":      ("SYNC:SOUR?",           "SYNC:SOUR {value}",   "OUTP"),
}
