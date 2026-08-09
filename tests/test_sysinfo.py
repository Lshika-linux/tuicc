"""Tests for sysinfo.py, against fake /proc + /sys trees under
tmp_path — same base_path-parameter pattern battery.py/procmon.py's
own tests already established. get_disk_usage() is the one exception
(no /proc equivalent exists) — monkeypatched via shutil.disk_usage
directly instead.
"""

from types import SimpleNamespace

import pytest

from tuicc import sysinfo
from tuicc.sysinfo import (
    SysInfoSampler, get_disk_usage, get_load_average, get_ram_percent,
    parse_cpu_line, parse_meminfo, parse_vmstat, read_cpu_times,
    read_swap_pages, read_throttle_count,
)


# ---------- parse_cpu_line / read_cpu_times ----------

def test_parse_cpu_line_returns_total_and_idle():
    # user=100 nice=0 system=50 idle=800 iowait=20 irq=0 softirq=0 steal=0
    total, idle = parse_cpu_line("cpu  100 0 50 800 20 0 0 0")
    assert idle == 820  # idle + iowait
    assert total == 970


def test_parse_cpu_line_pads_missing_fields():
    total, idle = parse_cpu_line("cpu  100 0 50 800")
    assert idle == 800
    assert total == 950


def test_parse_cpu_line_wrong_prefix_returns_none():
    assert parse_cpu_line("cpu0 100 0 50 800 20 0 0 0") is None


def test_read_cpu_times_missing_file_returns_none(tmp_path):
    assert read_cpu_times(str(tmp_path)) is None


def test_read_cpu_times_reads_the_summary_line(tmp_path):
    (tmp_path / "stat").write_text("cpu  100 0 50 800 20 0 0 0\ncpu0 ...\n")
    assert read_cpu_times(str(tmp_path)) == (970, 820)


# ---------- parse_meminfo / get_ram_percent ----------

def test_parse_meminfo_strips_kb_suffix():
    mem = parse_meminfo("MemTotal:       16345678 kB\nMemAvailable:    8000000 kB\n")
    assert mem == {"MemTotal": 16345678, "MemAvailable": 8000000}


def test_get_ram_percent_uses_mem_available(tmp_path):
    (tmp_path / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 250 kB\n")
    assert get_ram_percent(str(tmp_path)) == 75.0


def test_get_ram_percent_falls_back_without_mem_available(tmp_path):
    (tmp_path / "meminfo").write_text(
        "MemTotal: 1000 kB\nMemFree: 100 kB\nBuffers: 50 kB\nCached: 50 kB\n"
    )
    assert get_ram_percent(str(tmp_path)) == 80.0


def test_get_ram_percent_missing_file_returns_none(tmp_path):
    assert get_ram_percent(str(tmp_path)) is None


def test_get_ram_percent_missing_mem_total_returns_none(tmp_path):
    (tmp_path / "meminfo").write_text("MemAvailable: 250 kB\n")
    assert get_ram_percent(str(tmp_path)) is None


# ---------- get_load_average ----------

def test_get_load_average_parses_three_values(tmp_path):
    (tmp_path / "loadavg").write_text("0.52 0.58 0.59 2/1234 5678\n")
    assert get_load_average(str(tmp_path)) == (0.52, 0.58, 0.59)


def test_get_load_average_missing_file_returns_none(tmp_path):
    assert get_load_average(str(tmp_path)) is None


# ---------- get_disk_usage ----------

def test_get_disk_usage_computes_percent(monkeypatch):
    monkeypatch.setattr(
        sysinfo.shutil, "disk_usage",
        lambda path: SimpleNamespace(total=1000, used=250, free=750),
    )
    result = get_disk_usage("/")
    assert result == {"total": 1000, "used": 250, "free": 750, "percent": 25.0}


def test_get_disk_usage_missing_path_returns_none(monkeypatch):
    def raise_oserror(path):
        raise OSError("no such path")
    monkeypatch.setattr(sysinfo.shutil, "disk_usage", raise_oserror)
    assert get_disk_usage("/does/not/exist") is None


# ---------- parse_vmstat / read_swap_pages ----------

def test_parse_vmstat_reads_swap_fields():
    text = "pswpin 100\npswpout 200\nother_field 5\n"
    assert parse_vmstat(text) == {"pswpin": 100, "pswpout": 200, "other_field": 5}


def test_read_swap_pages_missing_fields_returns_none(tmp_path):
    (tmp_path / "vmstat").write_text("nr_free_pages 12345\n")
    assert read_swap_pages(str(tmp_path)) is None


def test_read_swap_pages_reads_both_counters(tmp_path):
    (tmp_path / "vmstat").write_text("pswpin 100\npswpout 200\n")
    assert read_swap_pages(str(tmp_path)) == (100, 200)


# ---------- read_throttle_count ----------

def test_read_throttle_count_missing_sys_path_returns_none(tmp_path):
    missing = tmp_path / "does_not_exist"
    assert read_throttle_count(str(missing)) is None


def test_read_throttle_count_sums_across_cores(tmp_path):
    for i, count in enumerate([3, 7]):
        core_dir = tmp_path / f"cpu{i}" / "thermal_throttle"
        core_dir.mkdir(parents=True)
        (core_dir / "core_throttle_count").write_text(f"{count}\n")
    assert read_throttle_count(str(tmp_path)) == 10


def test_read_throttle_count_no_core_exposes_it_returns_none(tmp_path):
    (tmp_path / "cpu0").mkdir()
    assert read_throttle_count(str(tmp_path)) is None


# ---------- SysInfoSampler ----------

def _write_stat(tmp_path, user=0, nice=0, system=0, idle=0, iowait=0):
    (tmp_path / "stat").write_text(f"cpu  {user} {nice} {system} {idle} {iowait} 0 0 0\n")


def test_first_poll_has_no_cpu_percent_yet(tmp_path):
    _write_stat(tmp_path, idle=1000)
    (tmp_path / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 500 kB\n")
    (tmp_path / "loadavg").write_text("0.1 0.2 0.3 1/1 1\n")
    (tmp_path / "vmstat").write_text("pswpin 0\npswpout 0\n")

    sampler = SysInfoSampler()
    result = sampler.poll(proc_path=str(tmp_path), sys_path=str(tmp_path / "nosys"),
                           disk_path=str(tmp_path))

    assert result["cpu_percent"] is None
    assert result["ram_percent"] == 50.0
    assert result["load_average"] == (0.1, 0.2, 0.3)
    assert result["swap_in_kb_s"] is None
    assert result["swap_out_kb_s"] is None
    assert result["throttled_recently"] is None


def test_second_poll_computes_cpu_percent_and_swap_rate(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(sysinfo.time, "monotonic", lambda: now[0])

    _write_stat(tmp_path, user=0, idle=1000)
    (tmp_path / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 500 kB\n")
    (tmp_path / "loadavg").write_text("0.1 0.2 0.3 1/1 1\n")
    (tmp_path / "vmstat").write_text("pswpin 0\npswpout 0\n")

    sampler = SysInfoSampler()
    sampler.poll(proc_path=str(tmp_path), sys_path=str(tmp_path / "nosys"), disk_path=str(tmp_path))

    now[0] = 1001.0
    # 100 more busy ticks (user), idle unchanged -> CPU was 100% busy
    # for that whole second (100 user ticks / 100 CLK_TCK == 1s of a
    # ~100Hz kernel's own reporting interval).
    import os as _os
    clk_tck = _os.sysconf("SC_CLK_TCK")
    _write_stat(tmp_path, user=int(clk_tck), idle=1000)
    (tmp_path / "vmstat").write_text("pswpin 10\npswpout 20\n")

    result = sampler.poll(proc_path=str(tmp_path), sys_path=str(tmp_path / "nosys"),
                           disk_path=str(tmp_path))

    assert result["cpu_percent"] == pytest.approx(100.0, abs=0.5)
    page_kb = _os.sysconf("SC_PAGE_SIZE") / 1024
    assert result["swap_in_kb_s"] == pytest.approx(10 * page_kb, rel=0.01)
    assert result["swap_out_kb_s"] == pytest.approx(20 * page_kb, rel=0.01)


def test_throttled_recently_true_when_count_increases(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(sysinfo.time, "monotonic", lambda: now[0])
    _write_stat(tmp_path, idle=1000)
    (tmp_path / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 500 kB\n")
    (tmp_path / "loadavg").write_text("0.1 0.2 0.3 1/1 1\n")
    (tmp_path / "vmstat").write_text("pswpin 0\npswpout 0\n")
    core_dir = tmp_path / "cpu0" / "thermal_throttle"
    core_dir.mkdir(parents=True)
    (core_dir / "core_throttle_count").write_text("0\n")

    sampler = SysInfoSampler()
    sampler.poll(proc_path=str(tmp_path), sys_path=str(tmp_path), disk_path=str(tmp_path))

    now[0] = 1001.0
    (core_dir / "core_throttle_count").write_text("3\n")
    result = sampler.poll(proc_path=str(tmp_path), sys_path=str(tmp_path), disk_path=str(tmp_path))

    assert result["throttled_recently"] is True


def test_throttled_recently_false_when_count_unchanged(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(sysinfo.time, "monotonic", lambda: now[0])
    _write_stat(tmp_path, idle=1000)
    (tmp_path / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 500 kB\n")
    (tmp_path / "loadavg").write_text("0.1 0.2 0.3 1/1 1\n")
    (tmp_path / "vmstat").write_text("pswpin 0\npswpout 0\n")
    core_dir = tmp_path / "cpu0" / "thermal_throttle"
    core_dir.mkdir(parents=True)
    (core_dir / "core_throttle_count").write_text("5\n")

    sampler = SysInfoSampler()
    sampler.poll(proc_path=str(tmp_path), sys_path=str(tmp_path), disk_path=str(tmp_path))

    now[0] = 1001.0
    result = sampler.poll(proc_path=str(tmp_path), sys_path=str(tmp_path), disk_path=str(tmp_path))

    assert result["throttled_recently"] is False
