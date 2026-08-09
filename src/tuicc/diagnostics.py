"""Failed-systemd-units + OOM + general dmesg/journalctl error scanning
— VISION.md's R6 system monitor's bottom "diagnostics" line. This is
the passive/read-only v1 scope agreed with the user: a summary
("All clear" / "N issues") plus a deduplicated breakdown modules/
sysmon.py shows in preview.py on hover — no "run this in a terminal"
action (explicitly deferred to backlog, post-v0.1.0).

Three independent sources, each tolerant of its own command being
unavailable (non-systemd init, or genuinely missing from PATH) without
that counting as a real error — same "degraded, not broken" stance the
rest of this codebase takes for optional system integrations:
- `systemctl --failed --output=json` — failed unit names.
- `journalctl -p err -o json --since <window>` — recent ERR-or-worse
  log lines, split into OOM-kill events and everything else
  (classify via is_oom_message), each deduplicated (dedupe_general_
  errors) since a single flapping driver can log the identical line
  many times in a row — live-verified on this session's own sandbox
  (a Bluetooth firmware retry loop logging the exact same line
  repeatedly within the same short window).

Bounded to a recent `since` window (default "-1h", journalctl's own
--since syntax) rather than scanning a potentially enormous full
journal on every poll — this diagnostics line only cares about RECENT
state, not full history.
"""

import json
import subprocess


def parse_failed_units_json(text: str) -> list[str]:
    """systemctl --failed --output=json's own array — a real failed
    unit's JSON object always includes a "unit" field (systemd's
    documented --output=json schema). Blank/empty-array output (the
    genuinely common "nothing is failed" case) returns [], not an
    error. Malformed non-empty output raises (json.JSONDecodeError),
    propagating same as everywhere else's no-silent-failure discipline
    — the caller is expected to have already handled "systemctl not
    found" (FileNotFoundError) separately, before this ever runs.
    """
    if not text.strip():
        return []
    data = json.loads(text)
    return [entry["unit"] for entry in data if "unit" in entry]


def parse_journal_lines(text: str) -> list[dict]:
    """One {"message", "identifier", "priority", "timestamp"} dict per
    journalctl -o json line (journalctl emits many more per-line
    fields; only what this module actually uses is kept). A single
    malformed line is skipped, not fatal — a truncated/interleaved
    line at the tail of a live, still-growing journal is a normal
    streaming race, unlike sensors.py's own single-JSON-document
    output, which has no such race and so DOES raise on malformed
    input. `identifier` falls back through SYSLOG_IDENTIFIER ->
    _COMM -> _SYSTEMD_UNIT -> "unknown", whichever the specific log
    source actually populated (not every subsystem sets all three,
    confirmed against this session's own live journal — bluetoothd's
    own lines set SYSLOG_IDENTIFIER, kernel lines don't).
    """
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = raw.get("MESSAGE", "")
        if not isinstance(message, str):
            message = ""  # binary/non-UTF8 MESSAGE payload — journalctl -o json quirk, treat as unreadable
        identifier = raw.get("SYSLOG_IDENTIFIER") or raw.get("_COMM") or raw.get("_SYSTEMD_UNIT") or "unknown"
        priority = int(raw["PRIORITY"]) if "PRIORITY" in raw else None
        timestamp = (int(raw["__REALTIME_TIMESTAMP"]) / 1_000_000) if "__REALTIME_TIMESTAMP" in raw else None
        entries.append({
            "message": message, "identifier": identifier,
            "priority": priority, "timestamp": timestamp,
        })
    return entries


def is_oom_message(message: str) -> bool:
    """A dedicated OOM category, not just lumped into "general errors"
    — the user specifically wants to know their machine started
    reaping processes for memory pressure, a categorically different
    (and more urgent) signal than an arbitrary logged error.
    """
    lowered = message.lower()
    return "out of memory" in lowered or "oom-killer" in lowered or "oom_kill" in lowered


def dedupe_general_errors(entries: list[dict]) -> list[dict]:
    """Collapses repeated (identifier, message) pairs into one entry
    with a "count" plus first/last-seen timestamps — found live, asked
    for: this exact session's own journal has the identical "Bluetooth:
    hci0: Reading supported features failed (-16)" kernel line appear
    multiple times within the same short window (a driver retry loop),
    and showing that as N separate diagnostic lines would bury
    genuinely distinct issues under repetition of one root cause.
    Order-preserving — first occurrence's position determines where
    the collapsed entry lands in the result, so the breakdown reads in
    a stable, chronological-ish order rather than being re-sorted.
    """
    seen_order = []
    grouped = {}
    for entry in entries:
        key = (entry["identifier"], entry["message"])
        if key not in grouped:
            grouped[key] = {
                "identifier": entry["identifier"], "message": entry["message"],
                "count": 0, "first_seen": entry["timestamp"], "last_seen": entry["timestamp"],
            }
            seen_order.append(key)
        group = grouped[key]
        group["count"] += 1
        ts = entry["timestamp"]
        if ts is not None:
            if group["first_seen"] is None or ts < group["first_seen"]:
                group["first_seen"] = ts
            if group["last_seen"] is None or ts > group["last_seen"]:
                group["last_seen"] = ts
    return [grouped[key] for key in seen_order]


def get_failed_units(runner=subprocess.run) -> list[str] | None:
    """None if systemctl itself isn't available (non-systemd init, or
    genuinely missing from PATH) — an environment limitation, not an
    error (same "degraded, not broken" tolerance the Provider
    contract's optional methods get). `systemctl --failed --output=
    json` exits 0 whether or not any unit is actually failed (confirmed
    live on this session's own machine) — a genuine failure (e.g. can't
    reach the system bus) surfaces via whatever exception subprocess/
    json parsing raises instead, propagating to StatusWorker's own
    poll-error capture same as everywhere else.
    """
    try:
        result = runner(
            ["systemctl", "--failed", "--output=json"],
            capture_output=True, text=True, timeout=5,
        )
    except FileNotFoundError:
        return None
    return parse_failed_units_json(result.stdout)


def get_journal_errors(runner=subprocess.run, since: str = "-1h",
                        priority: str = "err") -> list[dict] | None:
    """None if journalctl itself isn't available — same tolerance as
    get_failed_units. `since` bounds this to a recent window
    (journalctl's own --since syntax, e.g. "-1h") — see module
    docstring for why an unbounded full-journal scan every poll would
    be the wrong default.
    """
    try:
        result = runner(
            ["journalctl", "-p", priority, "-o", "json", "--no-pager", "--since", since],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        return None
    return parse_journal_lines(result.stdout)


def poll_diagnostics(unit_runner=subprocess.run, journal_runner=subprocess.run,
                      since: str = "-1h") -> dict:
    """This Domain's own `poll` callable — {"failed_units",
    "oom_events", "general_errors", "summary"}. The first three are
    None only when their OWN command genuinely couldn't run — otherwise
    a real (possibly empty) list, same None-vs-[] discipline as
    everywhere else in this codebase. `summary` is the bottom
    diagnostics line's own text:
    - "All clear" only when every category was BOTH checkable and
      empty.
    - "N issue(s)" when anything was found — N counts deduped GROUPS,
      not raw repeat occurrences (see dedupe_general_errors' own
      docstring for why the same flapping driver logging one message
      50 times shouldn't inflate this to 50).
    - "Diagnostics unavailable" only when nothing was found AND at
      least one category couldn't be checked at all — deliberately NOT
      "All clear" in that case, since "all clear" would be a claim
      this function can't actually back up.
    """
    failed_units = get_failed_units(unit_runner)
    all_entries = get_journal_errors(journal_runner, since=since)

    oom_events = None
    general_errors = None
    if all_entries is not None:
        oom_events = dedupe_general_errors([e for e in all_entries if is_oom_message(e["message"])])
        general_errors = dedupe_general_errors([e for e in all_entries if not is_oom_message(e["message"])])

    issue_count = 0
    any_unknown = False
    for category in (failed_units, oom_events, general_errors):
        if category is None:
            any_unknown = True
        else:
            issue_count += len(category)

    if issue_count > 0:
        summary = f"{issue_count} issue{'s' if issue_count != 1 else ''}"
    elif any_unknown:
        summary = "Diagnostics unavailable"
    else:
        summary = "All clear"

    return {
        "failed_units": failed_units,
        "oom_events": oom_events,
        "general_errors": general_errors,
        "summary": summary,
    }
