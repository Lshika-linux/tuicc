"""Tests for procmon.py, against a fake /proc tree built under
tmp_path — same "base_path is a parameter, no monkeypatching os
internals" pattern battery.py's own tests already established (see
that file's own docstring). Real /proc/<pid>/stat's exact field
layout (comm parenthesized, ppid at field 4, utime/stime at 14/15) is
reproduced by hand in _write_proc below, not just guessed — verified
against proc(5).
"""

from tuicc import procmon
from tuicc.procmon import (
    CLK_TCK, PidFeed, ProcMonSampler, WindowInfo, WindowStat,
    scan_all_processes, subtree_pids,
)


def _write_proc(tmp_path, pid, ppid, utime=0, stime=0, comm="app", rss_kb=None):
    d = tmp_path / str(pid)
    d.mkdir(exist_ok=True)
    # Fields after the space-separated "pid (comm) " prefix — state is
    # field 3 ("S"), ppid field 4, then pgrp/session/tty_nr/tpgid/flags/
    # minflt/cminflt/majflt/cmajflt (fields 5-13, 9 padding values),
    # then utime (14) stime (15).
    fields = ["S", str(ppid)] + ["0"] * 9 + [str(utime), str(stime)]
    (d / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields) + "\n")
    if rss_kb is not None:
        (d / "status").write_text(f"Name:\t{comm}\nVmRSS:\t{rss_kb} kB\n")


# ---------- scan_all_processes / subtree_pids ----------

def test_scan_all_processes_reads_ppid_and_ticks(tmp_path):
    _write_proc(tmp_path, pid=100, ppid=1, utime=50, stime=10)
    samples = scan_all_processes(str(tmp_path))
    assert samples[100].ppid == 1
    assert samples[100].utime == 50
    assert samples[100].stime == 10


def test_scan_all_processes_ignores_non_pid_entries(tmp_path):
    (tmp_path / "self").mkdir()
    (tmp_path / "net").mkdir()
    _write_proc(tmp_path, pid=100, ppid=1)
    samples = scan_all_processes(str(tmp_path))
    assert list(samples.keys()) == [100]


def test_scan_all_processes_missing_proc_dir_returns_empty(tmp_path):
    missing = tmp_path / "does_not_exist"
    assert scan_all_processes(str(missing)) == {}


def test_scan_all_processes_handles_comm_with_spaces_and_parens(tmp_path):
    _write_proc(tmp_path, pid=100, ppid=1, comm="weird (name) here", utime=5, stime=1)
    samples = scan_all_processes(str(tmp_path))
    assert samples[100].utime == 5
    assert samples[100].stime == 1


def test_subtree_pids_includes_root_and_all_descendants():
    children_map = {1: [2, 3], 2: [4], 3: [], 4: []}
    assert set(subtree_pids(1, children_map)) == {1, 2, 3, 4}


def test_subtree_pids_leaf_process_is_just_itself():
    assert subtree_pids(5, children_map={}) == [5]


# ---------- ProcMonSampler ----------

def test_first_poll_has_no_cpu_percent_yet(tmp_path):
    _write_proc(tmp_path, pid=100, ppid=1, utime=100, stime=0, rss_kb=1000)
    sampler = ProcMonSampler()
    windows = [WindowInfo(window_id="w1", app_id="app", title="App", pid=100)]

    results = sampler.poll(windows, str(tmp_path))

    assert results == [WindowStat(
        window_id="w1", app_id="app", title="App", pid=100,
        cpu_percent=None, rss_kb=1000,
    )]


def test_pid_none_window_has_no_cpu_or_rss(tmp_path):
    sampler = ProcMonSampler()
    windows = [WindowInfo(window_id="w1", app_id="app", title="App", pid=None)]

    results = sampler.poll(windows, str(tmp_path))

    assert results == [WindowStat(
        window_id="w1", app_id="app", title="App", pid=None,
        cpu_percent=None, rss_kb=None,
    )]


def test_second_poll_computes_cpu_percent_from_tick_delta(tmp_path, monkeypatch):
    sampler = ProcMonSampler()
    windows = [WindowInfo(window_id="w1", app_id="app", title="App", pid=100)]

    now = [1000.0]
    monkeypatch.setattr(procmon.time, "monotonic", lambda: now[0])

    _write_proc(tmp_path, pid=100, ppid=1, utime=0, stime=0, rss_kb=500)
    sampler.poll(windows, str(tmp_path))

    # One second later, this process burned exactly CLK_TCK ticks total
    # (utime+stime) -> 100% of one core for that whole second.
    now[0] = 1001.0
    _write_proc(tmp_path, pid=100, ppid=1, utime=CLK_TCK, stime=0, rss_kb=500)
    results = sampler.poll(windows, str(tmp_path))

    assert results[0].cpu_percent == 100.0


def test_subtree_cpu_and_rss_are_summed_across_children(tmp_path, monkeypatch):
    sampler = ProcMonSampler()
    windows = [WindowInfo(window_id="w1", app_id="app", title="App", pid=100)]

    now = [2000.0]
    monkeypatch.setattr(procmon.time, "monotonic", lambda: now[0])

    _write_proc(tmp_path, pid=100, ppid=1, utime=0, stime=0, rss_kb=100)
    _write_proc(tmp_path, pid=101, ppid=100, utime=0, stime=0, rss_kb=200)
    sampler.poll(windows, str(tmp_path))

    now[0] = 2001.0
    _write_proc(tmp_path, pid=100, ppid=1, utime=CLK_TCK // 2, stime=0, rss_kb=100)
    _write_proc(tmp_path, pid=101, ppid=100, utime=CLK_TCK // 2, stime=0, rss_kb=200)
    results = sampler.poll(windows, str(tmp_path))

    assert results[0].rss_kb == 300  # 100 (parent) + 200 (child)
    assert results[0].cpu_percent == 100.0  # half a core each, summed to one full core


def test_window_pid_that_has_since_exited_is_just_zero_ticks(tmp_path):
    sampler = ProcMonSampler()
    windows = [WindowInfo(window_id="w1", app_id="gone", title="Gone", pid=999)]

    results = sampler.poll(windows, str(tmp_path))

    assert results[0].pid == 999
    assert results[0].cpu_percent is None  # no prior sample yet regardless
    assert results[0].rss_kb is None  # no matching /proc entry at all


# ---------- PidFeed ----------

def test_pid_feed_starts_empty():
    feed = PidFeed()
    assert feed.get() == []


def test_pid_feed_set_then_get_round_trips():
    feed = PidFeed()
    windows = [WindowInfo(window_id="w1", app_id="app", title="App", pid=100)]
    feed.set(windows)
    assert feed.get() == windows


def test_pid_feed_get_returns_a_copy_not_the_live_list():
    feed = PidFeed()
    windows = [WindowInfo(window_id="w1", app_id="app", title="App", pid=100)]
    feed.set(windows)
    got = feed.get()
    got.append(WindowInfo(window_id="w2", app_id="x", title="X", pid=None))
    assert len(feed.get()) == 1  # mutation of the returned list didn't leak back in


# ---------- flatten_windows ----------

def test_flatten_windows_collects_across_all_regions():
    from types import SimpleNamespace
    win1 = SimpleNamespace(id="c1", app_id="firefox", title="Firefox", pid=100)
    win2 = SimpleNamespace(id="c2", app_id="kitty", title="kitty", pid=200)
    state = SimpleNamespace(regions=[
        SimpleNamespace(windows=[win1]),
        SimpleNamespace(windows=[win2]),
    ])

    result = procmon.flatten_windows(state)

    assert result == [
        WindowInfo(window_id="c1", app_id="firefox", title="Firefox", pid=100),
        WindowInfo(window_id="c2", app_id="kitty", title="kitty", pid=200),
    ]


def test_flatten_windows_empty_state_is_empty_list():
    from types import SimpleNamespace
    state = SimpleNamespace(regions=[])
    assert procmon.flatten_windows(state) == []
