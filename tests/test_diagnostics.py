"""Tests for diagnostics.py. The journal-line fixtures below are REAL
`journalctl -p err -o json` lines captured live off a real
sandbox (T480, NixOS) — including the exact repeated-Bluetooth-line
case that motivated dedupe_general_errors() in the first place, not a
hand-built guess at the shape.
"""

import json

import pytest

from tuicc.diagnostics import (
    dedupe_general_errors, get_failed_units, get_journal_errors,
    is_oom_message, parse_failed_units_json, parse_journal_lines,
    poll_diagnostics,
)

# Real journalctl -o json lines (one per journal entry) — captured live.
REAL_JOURNAL_LINES = "\n".join([
    json.dumps({
        "MESSAGE": "Bluetooth: hci0: Reading supported features failed (-16)",
        "PRIORITY": "3", "SYSLOG_IDENTIFIER": "kernel",
        "__REALTIME_TIMESTAMP": "1786264823446731",
    }),
    json.dumps({
        "MESSAGE": "Failed to set default system config for hci0",
        "PRIORITY": "3", "SYSLOG_IDENTIFIER": "bluetoothd",
        "__REALTIME_TIMESTAMP": "1786264823501751",
    }),
    json.dumps({
        "MESSAGE": "Bluetooth: hci0: Reading supported features failed (-16)",
        "PRIORITY": "3", "SYSLOG_IDENTIFIER": "kernel",
        "__REALTIME_TIMESTAMP": "1786270691440593",
    }),
])


# ---------- parse_failed_units_json ----------

def test_parse_failed_units_json_empty_array_is_empty_list():
    assert parse_failed_units_json("[]") == []


def test_parse_failed_units_json_blank_stdout_is_empty_list():
    assert parse_failed_units_json("") == []


def test_parse_failed_units_json_extracts_unit_names():
    text = json.dumps([
        {"unit": "foo.service", "load": "loaded", "active": "failed", "sub": "failed"},
        {"unit": "bar.service", "load": "loaded", "active": "failed", "sub": "failed"},
    ])
    assert parse_failed_units_json(text) == ["foo.service", "bar.service"]


def test_parse_failed_units_json_malformed_raises():
    with pytest.raises(json.JSONDecodeError):
        parse_failed_units_json("not json {{{")


# ---------- parse_journal_lines ----------

def test_parse_journal_lines_reads_real_output():
    entries = parse_journal_lines(REAL_JOURNAL_LINES)
    assert len(entries) == 3
    assert entries[0]["message"] == "Bluetooth: hci0: Reading supported features failed (-16)"
    assert entries[0]["identifier"] == "kernel"
    assert entries[0]["priority"] == 3


def test_parse_journal_lines_skips_malformed_lines():
    text = "not json\n" + json.dumps({"MESSAGE": "ok", "PRIORITY": "3"})
    entries = parse_journal_lines(text)
    assert len(entries) == 1
    assert entries[0]["message"] == "ok"


def test_parse_journal_lines_blank_text_is_empty_list():
    assert parse_journal_lines("") == []
    assert parse_journal_lines("   \n  \n") == []


def test_parse_journal_lines_identifier_falls_back_through_fields():
    text = json.dumps({"MESSAGE": "x", "_COMM": "somecomm"})
    entries = parse_journal_lines(text)
    assert entries[0]["identifier"] == "somecomm"


def test_parse_journal_lines_missing_identifier_is_unknown():
    text = json.dumps({"MESSAGE": "x"})
    entries = parse_journal_lines(text)
    assert entries[0]["identifier"] == "unknown"


# ---------- is_oom_message ----------

def test_is_oom_message_true_for_real_oom_patterns():
    assert is_oom_message("Out of memory: Killed process 1234 (chrome)") is True
    assert is_oom_message("invoked oom-killer: gfp_mask=...") is True


def test_is_oom_message_false_for_unrelated_error():
    assert is_oom_message("Bluetooth: hci0: Reading supported features failed (-16)") is False


# ---------- dedupe_general_errors ----------

def test_dedupe_general_errors_collapses_repeated_identical_lines():
    entries = parse_journal_lines(REAL_JOURNAL_LINES)
    deduped = dedupe_general_errors(entries)
    # 3 raw entries, but 2 of them are the identical kernel Bluetooth
    # line -> 2 groups, the kernel one with count=2.
    assert len(deduped) == 2
    kernel_group = next(g for g in deduped if g["identifier"] == "kernel")
    assert kernel_group["count"] == 2
    assert kernel_group["first_seen"] < kernel_group["last_seen"]


def test_dedupe_general_errors_empty_list_is_empty():
    assert dedupe_general_errors([]) == []


def test_dedupe_general_errors_preserves_first_occurrence_order():
    entries = [
        {"identifier": "a", "message": "first", "timestamp": 1.0},
        {"identifier": "b", "message": "second", "timestamp": 2.0},
        {"identifier": "a", "message": "first", "timestamp": 3.0},
    ]
    deduped = dedupe_general_errors(entries)
    assert [g["identifier"] for g in deduped] == ["a", "b"]


# ---------- get_failed_units / get_journal_errors (runner injection) ----------

class _FakeResult:
    def __init__(self, stdout):
        self.stdout = stdout


def test_get_failed_units_missing_binary_returns_none():
    def raise_missing(*a, **kw):
        raise FileNotFoundError()
    assert get_failed_units(runner=raise_missing) is None


def test_get_failed_units_parses_runner_output():
    runner = lambda *a, **kw: _FakeResult(json.dumps([{"unit": "x.service"}]))
    assert get_failed_units(runner=runner) == ["x.service"]


def test_get_journal_errors_missing_binary_returns_none():
    def raise_missing(*a, **kw):
        raise FileNotFoundError()
    assert get_journal_errors(runner=raise_missing) is None


def test_get_journal_errors_parses_runner_output():
    runner = lambda *a, **kw: _FakeResult(REAL_JOURNAL_LINES)
    entries = get_journal_errors(runner=runner)
    assert len(entries) == 3


# ---------- poll_diagnostics ----------

def test_poll_diagnostics_all_clear_when_everything_empty():
    unit_runner = lambda *a, **kw: _FakeResult("[]")
    journal_runner = lambda *a, **kw: _FakeResult("")
    result = poll_diagnostics(unit_runner=unit_runner, journal_runner=journal_runner)
    assert result["summary"] == "All clear"
    assert result["failed_units"] == []
    assert result["oom_events"] == []
    assert result["general_errors"] == []


def test_poll_diagnostics_counts_deduped_groups_not_raw_occurrences():
    unit_runner = lambda *a, **kw: _FakeResult("[]")
    journal_runner = lambda *a, **kw: _FakeResult(REAL_JOURNAL_LINES)
    result = poll_diagnostics(unit_runner=unit_runner, journal_runner=journal_runner)
    # 3 raw journal lines dedupe to 2 groups -> "2 issues", not "3 issues".
    assert result["summary"] == "2 issues"
    assert len(result["general_errors"]) == 2


def test_poll_diagnostics_separates_oom_from_general():
    oom_line = json.dumps({
        "MESSAGE": "Out of memory: Killed process 999 (chrome)",
        "PRIORITY": "2", "SYSLOG_IDENTIFIER": "kernel",
        "__REALTIME_TIMESTAMP": "1786264823000000",
    })
    unit_runner = lambda *a, **kw: _FakeResult("[]")
    journal_runner = lambda *a, **kw: _FakeResult(REAL_JOURNAL_LINES + "\n" + oom_line)
    result = poll_diagnostics(unit_runner=unit_runner, journal_runner=journal_runner)
    assert len(result["oom_events"]) == 1
    assert len(result["general_errors"]) == 2


def test_poll_diagnostics_singular_issue_wording():
    unit_runner = lambda *a, **kw: _FakeResult(json.dumps([{"unit": "x.service"}]))
    journal_runner = lambda *a, **kw: _FakeResult("")
    result = poll_diagnostics(unit_runner=unit_runner, journal_runner=journal_runner)
    assert result["summary"] == "1 issue"


def test_poll_diagnostics_unavailable_when_nothing_found_but_uncheckable():
    def raise_missing(*a, **kw):
        raise FileNotFoundError()
    unit_runner = lambda *a, **kw: _FakeResult("[]")
    result = poll_diagnostics(unit_runner=unit_runner, journal_runner=raise_missing)
    assert result["summary"] == "Diagnostics unavailable"
    assert result["general_errors"] is None
