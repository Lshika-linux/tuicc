"""CPU/board temperature reading via `sensors -j` (lm-sensors) —
VISION.md's R6 system monitor's own CPU-temp stat, plus a
"hottest sensor overall" line that explains it (a bare "32.9°C" doesn't say WHICH sensor that even is —
CPU package? NVMe drive? WiFi chip? — describe_sensor() below exists
so the module can show "Hottest: NVMe (Composite) 32.9°C", not just the
number).

`sensors` itself is an OPTIONAL extra, same tolerance media/cava.py's
CavaReader already established for the cava binary: a fresh install
without lm-sensors configured (no `sensors-detect` ever run) is normal,
not a real failure — SensorsSource remembers a missing binary
permanently (same reasoning CavaReader's own `_binary_missing` flag
gives) so it stops re-attempting the subprocess spawn every poll
interval forever.

Live-verified against a real sandbox (T480, NixOS):
`sensors -j` can exit non-zero (stderr: "ERROR: Can't get value of
subfeature temp2_input: Can't read" — one dead sensor on this exact
machine) while STILL printing fully valid, usable JSON on stdout for
every chip that DID read successfully — so exit code is deliberately
NOT checked here, only whether stdout parses as JSON at all. A chip
input reading as a JSON string ("N/A") rather than a number (some
drivers report an unreadable subfeature this way, distinct from simply
omitting the key) is treated the same as "not a usable numeric
reading" by the isinstance(value, (int, float)) checks below.
"""

import json
import re
import subprocess

_TEMP_INPUT_RE = re.compile(r"^temp\d+_input$")

# (chip_name_prefix, feature_label_prefixes) — first match wins,
# ordered by how directly each represents "the CPU package as a
# whole" rather than one individual core or an ambient zone. Intel's
# coretemp driver exposes a "Package id N" feature distinct from each
# individual "Core N" (the package reading is the one that actually
# represents overall CPU thermal state, not any single core); AMD's
# k10temp exposes "Tctl"/"Tdie" instead (no per-core breakdown on most
# AMD platforms) — see each driver's own kernel documentation.
_CPU_PACKAGE_CANDIDATES = (
    ("coretemp", ("Package id",)),
    ("k10temp", ("Tctl", "Tdie")),
)

# Friendly display names for common sensors-exposed chip prefixes:
# "coretemp-isa-0000" / "nvme-pci-3d00" read as noise, "CPU" / "NVMe"
# don't. Deliberately small and not exhaustive — describe_sensor()
# falls back to the raw chip name (up to its first "-") for anything
# not listed here, still readable, just less polished.
_CHIP_FRIENDLY_NAMES = {
    "coretemp": "CPU",
    "k10temp": "CPU",
    "nvme": "NVMe",
    "acpitz": "ACPI",
    "iwlwifi": "WiFi",
    "thinkpad": "Chipset",
    "pch_skylake": "Chipset",
    "pch": "Chipset",
}


def parse_sensors_json(text: str) -> dict:
    """{} for blank/whitespace-only stdout (sensors ran but reported
    literally nothing, e.g. no chips detected at all) — a real
    json.JSONDecodeError on genuinely malformed non-empty output is
    deliberately NOT caught here, it propagates same as everywhere
    else's no-silent-failure discipline (see StatusWorker's own poll-
    exception-capture docstring): sensors producing garbage instead of
    JSON is a real anomaly worth surfacing, unlike a missing binary.
    """
    if not text.strip():
        return {}
    return json.loads(text)


def find_hottest_sensor(data: dict) -> tuple[float, str, str] | None:
    """(value_celsius, chip_name, feature_label) for the single highest
    real temperature reading across every chip sensors -j reported —
    restricted to keys matching temp\\d+_input specifically (fan RPM/
    voltage/current readings share the exact same JSON structure but
    use fan*_input/in*_input/curr*_input keys instead — this regex is
    what keeps a fan's "2698" RPM from ever being mistaken for a
    2698°C reading). None if no chip exposes any temp*_input reading
    at all.
    """
    best = None
    for chip_name, features in data.items():
        if not isinstance(features, dict):
            continue
        for feature_label, subfeatures in features.items():
            if not isinstance(subfeatures, dict):
                continue
            for key, value in subfeatures.items():
                if not _TEMP_INPUT_RE.match(key) or not isinstance(value, (int, float)):
                    continue
                if best is None or value > best[0]:
                    best = (float(value), chip_name, feature_label)
    return best


def find_cpu_package_temp(data: dict) -> tuple[float, str, str] | None:
    """(value_celsius, chip_name, feature_label) for the CPU package's
    OWN temperature specifically — not just "whatever's hottest overall"
    (see find_hottest_sensor for that), vendor-aware since coretemp/
    k10temp expose this under different driver-specific feature labels
    (see _CPU_PACKAGE_CANDIDATES). Falls back to "acpitz"'s generic
    ACPI thermal zone (its own temp1) when neither vendor driver is
    present — a real, if less precise, CPU-adjacent reading rather than
    nothing at all. None if this machine's sensors output has neither.
    """
    for chip_prefix, label_prefixes in _CPU_PACKAGE_CANDIDATES:
        for chip_name, features in data.items():
            if not chip_name.startswith(chip_prefix) or not isinstance(features, dict):
                continue
            for feature_label, subfeatures in features.items():
                if not any(feature_label.startswith(p) for p in label_prefixes):
                    continue
                if not isinstance(subfeatures, dict):
                    continue
                for key, value in subfeatures.items():
                    if _TEMP_INPUT_RE.match(key) and isinstance(value, (int, float)):
                        return float(value), chip_name, feature_label

    for chip_name, features in data.items():
        if not chip_name.startswith("acpitz") or not isinstance(features, dict):
            continue
        for feature_label, subfeatures in features.items():
            if not isinstance(subfeatures, dict):
                continue
            for key, value in subfeatures.items():
                if _TEMP_INPUT_RE.match(key) and isinstance(value, (int, float)):
                    return float(value), chip_name, feature_label
    return None


def describe_sensor(chip_name: str, feature_label: str) -> str:
    """A short, friendly label for a (chip_name, feature_label) pair —
    "NVMe (Composite)" not "nvme-pci-3d00 / Composite" — this is the
    actual answer to "why am I looking at this specific number" (see
    this module's own docstring). Falls back to the raw chip name (up
    to its first "-", sensors' own bus-address suffix) when no
    friendly mapping exists in _CHIP_FRIENDLY_NAMES.
    """
    prefix = chip_name.split("-")[0].lower()
    chip_part = chip_name.split("-")[0]
    for key, friendly in _CHIP_FRIENDLY_NAMES.items():
        if prefix.startswith(key):
            chip_part = friendly
            break
    return f"{chip_part} ({feature_label})"


class SensorsSource:
    """Stateful wrapper around the `sensors -j` subprocess — the
    module-level functions above are all pure/parsing-only, this class
    is the one piece that actually shells out, and the one that
    remembers a missing binary (same reasoning/pattern as media/
    cava.py's CavaReader._binary_missing — see this module's own
    docstring). `runner` defaults to subprocess.run but is a parameter
    so tests can inject a fake instead of actually shelling out or
    monkeypatching subprocess globally, same "inject the dependency"
    idea battery.py's base_path parameter uses for filesystem access.
    """

    def __init__(self, runner=subprocess.run):
        self._runner = runner
        self._binary_missing = False

    def poll(self) -> dict | None:
        """None once `sensors` is confirmed missing — an optional
        feature not showing up, not an error (same tolerance as
        CavaReader's missing-binary case). Else a dict
        {"cpu_temp": (value, chip, feature) | None, "hottest": (...) |
        None} once sensors genuinely ran — "checked, found nothing"
        (both fields None) is a different state from "couldn't check
        at all" (this method returning None).
        """
        if self._binary_missing:
            return None
        try:
            result = self._runner(
                ["sensors", "-j"], capture_output=True, text=True, timeout=5,
            )
        except FileNotFoundError:
            self._binary_missing = True
            return None
        data = parse_sensors_json(result.stdout)
        return {
            "cpu_temp": find_cpu_package_temp(data),
            "hottest": find_hottest_sensor(data),
        }
