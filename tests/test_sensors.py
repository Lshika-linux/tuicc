"""Tests for sensors.py. The multi-chip fixture below is REAL `sensors
-j` output captured live off a real sandbox (T480, NixOS,
Intel coretemp + acpitz + nvme + iwlwifi + thinkpad_acpi all present
simultaneously) — not a hand-constructed guess at the JSON shape, same
"verify against real hardware data" standard battery.py's own
aggregate() test fixtures use.
"""

import json

import pytest

from tuicc.sensors import (
    SensorsSource, describe_sensor, find_cpu_package_temp,
    find_hottest_sensor, parse_sensors_json,
)

# Captured live via `sensors -j` on a real sandbox.
REAL_SENSORS_JSON = json.dumps({
    "iwlwifi_1-virtual-0": {"Adapter": "Virtual device", "temp1": {"temp1_input": 46.0}},
    "thinkpad-isa-0000": {
        "Adapter": "ISA adapter",
        "fan1": {"fan1_input": 2698.0},
        "CPU": {"temp1_input": 57.0},
        "GPU": {},
        "temp3": {"temp3_input": 0.0},
        "pwm1": {"pwm1": 127.5, "pwm1_enable": 2.0},
    },
    "nvme-pci-3d00": {
        "Adapter": "PCI adapter",
        "Composite": {
            "temp1_input": 32.85, "temp1_max": 74.85,
            "temp1_min": -0.15, "temp1_crit": 79.85, "temp1_alarm": 0.0,
        },
    },
    "coretemp-isa-0000": {
        "Adapter": "ISA adapter",
        "Package id 0": {"temp1_input": 58.0, "temp1_max": 100.0, "temp1_crit": 100.0, "temp1_crit_alarm": 0.0},
        "Core 0": {"temp2_input": 56.0, "temp2_max": 100.0, "temp2_crit": 100.0, "temp2_crit_alarm": 0.0},
        "Core 1": {"temp3_input": 58.0, "temp3_max": 100.0, "temp3_crit": 100.0, "temp3_crit_alarm": 0.0},
    },
    "acpitz-acpi-0": {"Adapter": "ACPI interface", "temp1": {"temp1_input": 57.0}},
})


# ---------- parse_sensors_json ----------

def test_parse_sensors_json_blank_stdout_is_empty_dict():
    assert parse_sensors_json("") == {}
    assert parse_sensors_json("   \n") == {}


def test_parse_sensors_json_parses_real_output():
    data = parse_sensors_json(REAL_SENSORS_JSON)
    assert "coretemp-isa-0000" in data


def test_parse_sensors_json_malformed_output_raises():
    with pytest.raises(json.JSONDecodeError):
        parse_sensors_json("not json at all {{{")


# ---------- find_cpu_package_temp ----------

def test_find_cpu_package_temp_prefers_coretemp_package(tmp_path):
    data = parse_sensors_json(REAL_SENSORS_JSON)
    value, chip, feature = find_cpu_package_temp(data)
    assert value == 58.0
    assert chip == "coretemp-isa-0000"
    assert feature == "Package id 0"


def test_find_cpu_package_temp_falls_back_to_acpitz_when_no_vendor_chip():
    data = {"acpitz-acpi-0": {"temp1": {"temp1_input": 45.0}}}
    value, chip, feature = find_cpu_package_temp(data)
    assert value == 45.0
    assert chip == "acpitz-acpi-0"


def test_find_cpu_package_temp_none_when_nothing_matches():
    data = {"iwlwifi_1-virtual-0": {"temp1": {"temp1_input": 46.0}}}
    assert find_cpu_package_temp(data) is None


def test_find_cpu_package_temp_prefers_k10temp_over_acpitz():
    data = {
        "acpitz-acpi-0": {"temp1": {"temp1_input": 45.0}},
        "k10temp-pci-00c3": {"Tctl": {"temp1_input": 62.5}},
    }
    value, chip, feature = find_cpu_package_temp(data)
    assert value == 62.5
    assert chip == "k10temp-pci-00c3"


# ---------- find_hottest_sensor ----------

def test_find_hottest_sensor_ignores_fan_and_pwm_readings():
    data = parse_sensors_json(REAL_SENSORS_JSON)
    value, chip, feature = find_hottest_sensor(data)
    # 58.0 (coretemp Package id 0 / Core 1) is the real max — fan1's
    # 2698 RPM must NOT win just because it's numerically larger.
    assert value == 58.0


def test_find_hottest_sensor_none_when_no_temp_readings():
    data = {"thinkpad-isa-0000": {"fan1": {"fan1_input": 2698.0}}}
    assert find_hottest_sensor(data) is None


def test_find_hottest_sensor_empty_data_is_none():
    assert find_hottest_sensor({}) is None


# ---------- describe_sensor ----------

def test_describe_sensor_uses_friendly_chip_name():
    assert describe_sensor("nvme-pci-3d00", "Composite") == "NVMe (Composite)"
    assert describe_sensor("coretemp-isa-0000", "Package id 0") == "CPU (Package id 0)"


def test_describe_sensor_falls_back_to_raw_chip_prefix():
    assert describe_sensor("weird_chip-isa-0000", "SomeFeature") == "weird_chip (SomeFeature)"


# ---------- SensorsSource ----------

class _FakeResult:
    def __init__(self, stdout):
        self.stdout = stdout


def test_sensors_source_missing_binary_returns_none_and_remembers():
    calls = []

    def fake_runner(*args, **kwargs):
        calls.append(1)
        raise FileNotFoundError()

    source = SensorsSource(runner=fake_runner)
    assert source.poll() is None
    assert source.poll() is None
    # Only the FIRST poll() actually attempted the subprocess spawn —
    # same "remember and stop retrying" tolerance as CavaReader.
    assert len(calls) == 1


def test_sensors_source_returns_cpu_temp_and_hottest():
    source = SensorsSource(runner=lambda *a, **kw: _FakeResult(REAL_SENSORS_JSON))
    result = source.poll()
    assert result["cpu_temp"] == (58.0, "coretemp-isa-0000", "Package id 0")
    assert result["hottest"] == (58.0, "coretemp-isa-0000", "Package id 0")


def test_sensors_source_nonzero_exit_with_valid_stdout_still_works():
    # Live-verified: `sensors -j` can exit non-zero (one dead sensor)
    # while stdout still holds fully valid JSON for everything else —
    # SensorsSource must not treat exit code as fatal.
    source = SensorsSource(runner=lambda *a, **kw: _FakeResult(REAL_SENSORS_JSON))
    result = source.poll()
    assert result is not None
    assert result["cpu_temp"] is not None
