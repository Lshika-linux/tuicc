"""Tests for winrestore.py — pure logic (capture_window/capture_region/
capture_session, resolve_launch_argv), the sequential region-by-region
restore driver (TiledRestoreState/advance_tiled_restore), plus a real
round-trip for save_session/load_session (plain temp-file I/O, same
category as test_presets.py's real file tests). No /proc access
anywhere in this module (see its own top docstring for why —
app_id/region/floating/rect + an optional tiled tree is all it
captures now, not a raw cmdline/environ snapshot).
"""

from tuicc.model import Window, Region, WMState
from tuicc.winrestore import (
    capture_window,
    capture_region,
    capture_session,
    save_session,
    load_session,
    resolve_launch_argv,
    TiledRestoreState,
    advance_tiled_restore,
    TreeBuildState,
    start_tree_build,
    advance_tree_build,
    SCRATCH_WORKSPACE_BASE,
)


def _window(id, app_id, floating=False, rect=(0, 0, 1, 1)):
    return Window(id=id, app_id=app_id, title="", focused=False, rect=rect, floating=floating)


class _FakeTreeProvider:
    """Records calls, returns canned answers — no real IPC.

    tiled_trees: region_id -> tree (capture side, get_tiled_tree()).
    layout_results: (window_id, layout) -> wrapper con id, or None for a
    simulated set_container_layout() failure — defaults to
    f"wrapper_of_{window_id}" for any pair not listed, so most tests
    don't need to spell out an explicit success value.
    """
    def __init__(self, tiled_trees=None, layout_results=None):
        self.tiled_trees = tiled_trees or {}
        self.layout_results = layout_results or {}
        self.move_calls = []
        self.layout_calls = []

    def get_tiled_tree(self, region_id):
        return self.tiled_trees.get(region_id)

    def move_window_to_region(self, window_id, region_id):
        self.move_calls.append((window_id, region_id))

    def set_container_layout(self, window_id, layout):
        self.layout_calls.append((window_id, layout))
        key = (window_id, layout)
        if key in self.layout_results:
            return self.layout_results[key]
        return f"wrapper_of_{window_id}"


# ---------- capture_window ----------

def test_capture_window_tiled_shape():
    entry = capture_window(_window("1", "kitty"))

    assert entry == {"app_id": "kitty"}


def test_capture_window_floating_includes_geometry():
    window = _window("1", "kitty", floating=True, rect=(0.35, 0.15, 0.3, 0.4))

    entry = capture_window(window)

    assert entry == {"app_id": "kitty", "x": 0.35, "y": 0.15, "w": 0.3, "h": 0.4}


def test_capture_window_tiled_omits_geometry():
    entry = capture_window(_window("1", "kitty", floating=False))

    assert "x" not in entry
    assert "y" not in entry


# ---------- capture_region ----------

def test_capture_region_no_provider_falls_back_to_tiled_flat():
    region = Region(id="3", name="3", windows=[_window("a", "kitty")])

    entry = capture_region(region)

    assert entry == {"target_region": "3", "tiled_flat": [{"app_id": "kitty"}]}


def test_capture_region_uses_provider_tree_when_available():
    region = Region(id="3", name="3", windows=[_window("a", "kitty")])
    tree = {"type": "window", "app_id": "kitty"}
    provider = _FakeTreeProvider(tiled_trees={"3": tree})

    entry = capture_region(region, provider)

    assert entry == {"target_region": "3", "tiled": tree}
    assert "tiled_flat" not in entry


def test_capture_region_falls_back_when_provider_tree_is_none():
    region = Region(id="3", name="3", windows=[_window("a", "kitty")])
    provider = _FakeTreeProvider()  # no tree for "3"

    entry = capture_region(region, provider)

    assert entry == {"target_region": "3", "tiled_flat": [{"app_id": "kitty"}]}


def test_capture_region_floating_windows_always_flat():
    region = Region(id="3", name="3", windows=[
        _window("a", "kitty", floating=True, rect=(0.1, 0.1, 0.2, 0.2)),
    ])
    tree = {"type": "window", "app_id": "firefox"}  # irrelevant, no tiled windows here
    provider = _FakeTreeProvider(tiled_trees={"3": tree})

    entry = capture_region(region, provider)

    assert "tiled" not in entry  # get_tiled_tree() not even consulted — no tiled windows
    assert entry["floating"] == [{"app_id": "kitty", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}]


def test_capture_region_omits_empty_keys():
    # A region with ONLY tiled windows saves no "floating" key at all,
    # and vice versa — never an empty list/None placeholder.
    region = Region(id="3", name="3", windows=[_window("a", "kitty")])

    entry = capture_region(region)

    assert "floating" not in entry


# ---------- capture_session ----------

def test_capture_session_covers_every_region():
    state = WMState(regions=[
        Region(id="1", name="1", windows=[_window("a", "kitty")]),
        Region(id="2", name="2", windows=[_window("b", "kitty")]),
    ])

    entries = capture_session(state)

    assert [e["target_region"] for e in entries] == ["1", "2"]


def test_capture_session_skips_empty_regions():
    state = WMState(regions=[
        Region(id="1", name="1", windows=[_window("a", "kitty")]),
        Region(id="2", name="2", windows=[]),
    ])

    entries = capture_session(state)

    assert [e["target_region"] for e in entries] == ["1"]


def test_capture_session_empty_state_returns_empty_list():
    assert capture_session(WMState(regions=[])) == []


def test_capture_session_threads_provider_through_to_each_region():
    tree = {"type": "window", "app_id": "kitty"}
    provider = _FakeTreeProvider(tiled_trees={"1": tree})
    state = WMState(regions=[Region(id="1", name="1", windows=[_window("a", "kitty")])])

    entries = capture_session(state, provider)

    assert entries == [{"target_region": "1", "tiled": tree}]


# ---------- save_session / load_session ----------

def test_save_then_load_round_trips_entries(tmp_path):
    entries = [
        {"target_region": "5", "tiled_flat": [{"app_id": "kitty"}]},
        {"target_region": "9", "floating": [{"app_id": "kitty", "x": 0.35, "y": 0.15, "w": 0.3, "h": 0.4}]},
    ]
    path = tmp_path / "test_winrestore.toml"

    save_session(entries, path)
    loaded = load_session(path)

    assert loaded == entries


def test_save_session_creates_parent_directory(tmp_path):
    path = tmp_path / "nested" / "winrestore.toml"

    save_session([], path)

    assert path.exists()


def test_load_session_missing_region_key_returns_empty_list(tmp_path):
    path = tmp_path / "empty.toml"
    path.write_text("")

    assert load_session(path) == []


# ---------- resolve_launch_argv ----------
# See CLAUDE/NOTES/design-decisions.md#winrestore-app-id-capture — the
# app_id-hint match rule here is exactly what resolve_restore_argv()
# (the old argv[0]-swap fallback this superseded) already verified live
# against Rafi's own real .desktop files.

def test_resolve_launch_argv_returns_split_exec_command():
    # desktop_apps entries are already %-token-stripped by the time
    # they reach here (launcher.scan_desktop_apps() does that cleanup)
    # — resolve_launch_argv() only ever splits on whitespace.
    desktop_apps = [("Spotify", "spotify", "spotify")]

    assert resolve_launch_argv("Spotify", desktop_apps) == ["spotify"]


def test_resolve_launch_argv_matches_app_id_case_insensitively():
    # A captured window.app_id and a .desktop's own StartupWMClass=
    # commonly differ only in case (confirmed live: "Spotify" vs.
    # "spotify", "Discord" vs. "discord").
    desktop_apps = [("Discord", "Discord", "discord")]

    assert resolve_launch_argv("discord", desktop_apps) == ["Discord"]


def test_resolve_launch_argv_splits_multi_word_exec():
    desktop_apps = [("Foo", "foo --flag bar", "foo")]

    assert resolve_launch_argv("foo", desktop_apps) == ["foo", "--flag", "bar"]


def test_resolve_launch_argv_none_when_no_desktop_entry_matches():
    assert resolve_launch_argv("kitty", []) is None


def test_resolve_launch_argv_none_when_matched_exec_is_empty():
    desktop_apps = [("Empty", "   ", "empty")]

    assert resolve_launch_argv("empty", desktop_apps) is None


# ---------- advance_tiled_restore / TiledRestoreState ----------
# See CLAUDE/NOTES/design-decisions.md#append-layout-tiled-restore's
# "Phase 3 correction" — strictly sequential, one region at a time,
# found necessary live: append_layout's own swallow matching can't
# tell which of tuicc's own spawns a window belongs to, so two
# regions' placeholders open at once race for the same window
# whenever an app_id repeats across them.

class _FakeMoves:
    """Stands in for pending_moves.PendingMovesQueue — advance_tiled_restore()
    reads .entries (flat/"tiled" phases) and .resolved_tags (tree
    phase, via advance_tree_build())."""
    def __init__(self, entries=None, resolved_tags=None):
        self.entries = entries or []
        self.resolved_tags = resolved_tags if resolved_tags is not None else {}


def test_advance_tiled_restore_no_op_when_idle():
    state = TiledRestoreState()
    provider = _FakeTreeProvider()
    restore_queue = []

    advance_tiled_restore(state, _FakeMoves(), provider, restore_queue, now=0.0)

    assert restore_queue == []
    assert provider.move_calls == []
    assert provider.layout_calls == []


def test_advance_tiled_restore_resolves_target_region_against_workspace_names():
    # Found live: a numbered+named workspace ("2:II") that doesn't
    # live-exist yet at restore time (nothing on it, or a fresh
    # desktop) got created bare as literally "2" instead — sway/i3's
    # own `workspace number 2` has no way to know it should be "2:II"
    # unless told. Only a TreeBuildState's own FINAL move (the whole
    # tree, once folded down to one container) needs this resolution —
    # everything queued along the way targets a scratch workspace,
    # never a configured name.
    tree = {
        "type": "split", "layout": "tabbed",
        "children": [{"type": "window", "app_id": "firefox"}, {"type": "window", "app_id": "kitty"}],
    }
    state = TiledRestoreState(queued_regions=[{"target_region": "2", "tiled": tree}])

    advance_tiled_restore(state, _FakeMoves(), _FakeTreeProvider(), [], now=0.0, workspace_names=["1:I", "2:II"])

    assert state.active_phase == "tree"
    assert state.tree_build.target_region == "2:II"


def test_advance_tiled_restore_no_workspace_names_leaves_target_region_bare():
    tree = {
        "type": "split", "layout": "tabbed",
        "children": [{"type": "window", "app_id": "firefox"}, {"type": "window", "app_id": "kitty"}],
    }
    state = TiledRestoreState(queued_regions=[{"target_region": "2", "tiled": tree}])

    advance_tiled_restore(state, _FakeMoves(), _FakeTreeProvider(), [], now=0.0)

    assert state.tree_build.target_region == "2"


def test_advance_tiled_restore_trivial_single_window_tree_uses_flat_tiled_phase():
    # No real structure to build (capture_tiled_tree() already collapses
    # single-child splits down to this) — no TreeBuildState needed at
    # all, straight to the ordinary flat pipeline like tiled_flat always
    # was.
    tree = {"type": "window", "app_id": "firefox"}
    state = TiledRestoreState(queued_regions=[{"target_region": "1", "tiled": tree}])
    restore_queue = []

    advance_tiled_restore(state, _FakeMoves(), _FakeTreeProvider(), restore_queue, now=0.0)

    assert state.active_phase == "tiled"
    assert state.tree_build is None
    assert restore_queue == [{"app_id": "firefox", "target_region": "1", "floating": False}]


def test_advance_tiled_restore_tree_less_region_skips_straight_to_flat_phase():
    state = TiledRestoreState(queued_regions=[{"target_region": "1", "tiled_flat": [{"app_id": "kitty"}]}])
    restore_queue = []

    advance_tiled_restore(state, _FakeMoves(), _FakeTreeProvider(), restore_queue, now=0.0)

    assert state.active_phase == "flat"
    assert restore_queue == [{"app_id": "kitty", "target_region": "1", "floating": False, "placed_by_wm": False}]


def test_advance_tiled_restore_waits_while_tiled_phase_still_in_restore_queue():
    tree = {"type": "window", "app_id": "kitty"}
    state = TiledRestoreState(queued_regions=[{"target_region": "1", "tiled": tree}])
    restore_queue = []
    advance_tiled_restore(state, _FakeMoves(), _FakeTreeProvider(), restore_queue, now=0.0)  # starts region 1

    # Entry still sitting unpopped in restore_queue — region 1 hasn't
    # even been spawned yet, must not advance or duplicate it.
    advance_tiled_restore(state, _FakeMoves(), _FakeTreeProvider(), restore_queue, now=1.0)

    assert restore_queue == [{"app_id": "kitty", "target_region": "1", "floating": False}]
    assert state.active_phase == "tiled"  # unchanged


def test_advance_tiled_restore_waits_while_tiled_phase_unmatched_in_moves():
    tree = {"type": "window", "app_id": "kitty"}
    state = TiledRestoreState(queued_regions=[{"target_region": "1", "tiled": tree}])
    restore_queue = []
    advance_tiled_restore(state, _FakeMoves(), _FakeTreeProvider(), restore_queue, now=0.0)
    restore_queue.pop(0)  # simulate promote_restore_queue() popping+spawning it
    moves = _FakeMoves(entries=[{"target_region": "1", "placed_by_wm": False}])  # no last_matched_at yet

    advance_tiled_restore(state, moves, _FakeTreeProvider(), restore_queue, now=1.0)

    assert state.active_phase == "tiled"  # still waiting, not matched yet


def test_advance_tiled_restore_releases_pending_flat_once_tiled_phase_clears():
    tree = {"type": "window", "app_id": "kitty"}
    state = TiledRestoreState(queued_regions=[{
        "target_region": "1", "tiled": tree, "tiled_flat": [{"app_id": "firefox"}],
    }])
    restore_queue = []
    advance_tiled_restore(state, _FakeMoves(), _FakeTreeProvider(), restore_queue, now=0.0)
    restore_queue.pop(0)
    # Matched — process() sets last_matched_at the instant it resolves.
    moves = _FakeMoves(entries=[{"target_region": "1", "placed_by_wm": False, "last_matched_at": 1.0}])

    advance_tiled_restore(state, moves, _FakeTreeProvider(), restore_queue, now=1.0)

    assert state.active_phase == "flat"
    assert state.pending_flat == []
    assert restore_queue == [{"app_id": "firefox", "target_region": "1", "floating": False, "placed_by_wm": False}]


def test_advance_tiled_restore_ignores_settle_seconds_linger():
    # A matched entry lingers in moves.entries for SETTLE_SECONDS
    # afterward (pending_moves.py's own fork/exec-descendant detection
    # tail) — structurally meaningless for a pid=None winrestore entry,
    # so advance_tiled_restore() must not wait it out (last_matched_at
    # being SET is what matters, not whether the entry is still present
    # at all).
    state = TiledRestoreState(active_region_id="1", active_phase="tiled")
    moves = _FakeMoves(entries=[{"target_region": "1", "placed_by_wm": False, "last_matched_at": 5.0}])

    advance_tiled_restore(state, moves, _FakeTreeProvider(), [], now=5.1)

    assert state.active_phase == "flat"  # advanced immediately, no SETTLE_SECONDS wait


def test_advance_tiled_restore_finishes_region():
    state = TiledRestoreState(active_region_id="1", active_phase="flat")

    advance_tiled_restore(state, _FakeMoves(), _FakeTreeProvider(), [], now=0.0)

    assert state.active_region_id is None
    assert state.active_phase is None


def test_advance_tiled_restore_starts_next_region_immediately_once_previous_clears():
    state = TiledRestoreState(
        queued_regions=[{"target_region": "2", "tiled": {"type": "window", "app_id": "code"}}],
        active_region_id="1", active_phase="flat",
    )
    restore_queue = []

    advance_tiled_restore(state, _FakeMoves(), _FakeTreeProvider(), restore_queue, now=0.0)

    assert state.active_region_id == "2"
    assert state.active_phase == "tiled"
    assert restore_queue == [{"app_id": "code", "target_region": "2", "floating": False}]


def test_advance_tiled_restore_two_regions_never_both_active():
    # The core correctness invariant the sequential redesign exists for.
    state = TiledRestoreState(queued_regions=[
        {"target_region": "1", "tiled": {"type": "window", "app_id": "kitty"}},
        {"target_region": "2", "tiled": {"type": "window", "app_id": "kitty"}},
    ])
    restore_queue = []

    advance_tiled_restore(state, _FakeMoves(), _FakeTreeProvider(), restore_queue, now=0.0)

    assert restore_queue == [{"app_id": "kitty", "target_region": "1", "floating": False}]
    assert state.active_region_id == "1"
    assert len(state.queued_regions) == 1 and state.queued_regions[0]["target_region"] == "2"


def test_advance_tiled_restore_unrelated_launcher_entry_does_not_block_advancing():
    # A plain launcher-typed spawn (queue_launcher_spawn()'s own shape
    # — no "placed_by_wm" key at all) happening to target the same
    # region mid-restore must not be mistaken for part of this batch.
    state = TiledRestoreState(active_region_id="1", active_phase="flat")
    moves = _FakeMoves(entries=[{"target_region": "1", "app_id": "unrelated"}])  # no placed_by_wm key

    advance_tiled_restore(state, moves, _FakeTreeProvider(), [], now=0.0)

    assert state.active_region_id is None  # advanced anyway


def test_advance_tiled_restore_drives_tree_phase_end_to_end():
    # Full integration: a real split tree, driven purely through
    # advance_tiled_restore() (not advance_tree_build() directly),
    # exactly as frame_update.py calls it every frame.
    tree = {
        "type": "split", "layout": "tabbed",
        "children": [{"type": "window", "app_id": "a"}, {"type": "window", "app_id": "b"}],
    }
    state = TiledRestoreState(queued_regions=[{"target_region": "1", "tiled": tree}])
    provider = _FakeTreeProvider()
    restore_queue = []

    advance_tiled_restore(state, _FakeMoves(), provider, restore_queue, now=0.0)  # starts the region (tree phase)
    assert state.active_phase == "tree"
    advance_tiled_restore(state, _FakeMoves(), provider, restore_queue, now=0.05)  # queues leaf "a"
    assert restore_queue[0]["app_id"] == "a"
    tag_a = restore_queue[0]["tag"]
    restore_queue.pop(0)

    advance_tiled_restore(state, _FakeMoves(resolved_tags={tag_a: "con_a"}), provider, restore_queue, now=0.1)
    advance_tiled_restore(state, _FakeMoves(), provider, restore_queue, now=0.2)  # dispatches leaf "b"
    tag_b = restore_queue[0]["tag"]
    restore_queue.pop(0)

    advance_tiled_restore(state, _FakeMoves(resolved_tags={tag_b: "con_b"}), provider, restore_queue, now=0.3)
    # Tree fully folded on this call -> transitions to "flat" phase.
    advance_tiled_restore(state, _FakeMoves(), provider, restore_queue, now=0.4)

    assert provider.layout_calls == [("con_a", "tabbed")]
    assert provider.move_calls[-1] == ("wrapper_of_con_a", "1")
    assert state.active_phase == "flat"
    assert state.tree_build is None


# ---------- TreeBuildState / advance_tree_build ----------
# See CLAUDE/NOTES/design-decisions.md#append-layout-doesnt-exist-on-sway
# for the full story: append_layout (an earlier design built entirely
# around it) turned out to not exist on sway at all — this replaces it
# with plain move/layout IPC commands, addressed by real con id, driven
# one leaf at a time on a small reserved pool of scratch workspaces.

def test_start_tree_build_builds_root_frame():
    tree = {
        "type": "split", "layout": "tabbed",
        "children": [{"type": "window", "app_id": "kitty"}, {"type": "window", "app_id": "firefox"}],
    }

    state = start_tree_build(tree, "1")

    assert state.target_region == "1"
    assert len(state.stack) == 1
    assert state.stack[0].layout == "tabbed"
    assert state.stack[0].depth == 0
    assert state.stack[0].remaining == tree["children"]
    assert state.failed is False


def test_advance_tree_build_queues_first_leaf_on_scratch_workspace():
    tree = {
        "type": "split", "layout": "tabbed",
        "children": [{"type": "window", "app_id": "kitty"}, {"type": "window", "app_id": "firefox"}],
    }
    state = start_tree_build(tree, "1")
    restore_queue = []

    done = advance_tree_build(state, _FakeMoves(), _FakeTreeProvider(), restore_queue)

    assert done is False
    assert len(restore_queue) == 1
    entry = restore_queue[0]
    assert entry["app_id"] == "kitty"
    assert entry["target_region"] == str(SCRATCH_WORKSPACE_BASE)
    assert entry["floating"] is False
    assert state.stack[0].waiting_tag == entry["tag"]


def test_advance_tree_build_waits_until_tag_resolves():
    tree = {"type": "split", "layout": "tabbed", "children": [
        {"type": "window", "app_id": "kitty"}, {"type": "window", "app_id": "firefox"},
    ]}
    state = start_tree_build(tree, "1")
    restore_queue = []
    advance_tree_build(state, _FakeMoves(), _FakeTreeProvider(), restore_queue)
    tag = state.stack[0].waiting_tag

    done = advance_tree_build(state, _FakeMoves(), _FakeTreeProvider(), restore_queue)

    assert done is False
    assert state.stack[0].waiting_tag == tag  # still waiting, unchanged
    assert len(restore_queue) == 1  # not re-queued


def test_advance_tree_build_folds_first_leaf_then_dispatches_second():
    tree = {"type": "split", "layout": "tabbed", "children": [
        {"type": "window", "app_id": "kitty"}, {"type": "window", "app_id": "firefox"},
    ]}
    state = start_tree_build(tree, "1")
    provider = _FakeTreeProvider()
    restore_queue = []
    advance_tree_build(state, _FakeMoves(), provider, restore_queue)
    tag = state.stack[0].waiting_tag

    advance_tree_build(state, _FakeMoves(resolved_tags={tag: "con_kitty"}), provider, restore_queue)

    assert state.stack[0].accumulated_id == "con_kitty"
    assert state.stack[0].has_wrapper is False
    assert provider.layout_calls == []  # only 1 child folded so far — nothing to group with yet

    advance_tree_build(state, _FakeMoves(), provider, restore_queue)  # dispatches "firefox"

    assert len(restore_queue) == 2
    assert restore_queue[1]["app_id"] == "firefox"
    assert restore_queue[1]["target_region"] == str(SCRATCH_WORKSPACE_BASE)  # same depth, same scratch ws


def test_advance_tree_build_groups_on_second_child():
    tree = {"type": "split", "layout": "tabbed", "children": [
        {"type": "window", "app_id": "kitty"}, {"type": "window", "app_id": "firefox"},
    ]}
    state = start_tree_build(tree, "1")
    provider = _FakeTreeProvider()
    restore_queue = []
    advance_tree_build(state, _FakeMoves(), provider, restore_queue)
    tag1 = state.stack[0].waiting_tag
    advance_tree_build(state, _FakeMoves(resolved_tags={tag1: "con_kitty"}), provider, restore_queue)
    advance_tree_build(state, _FakeMoves(), provider, restore_queue)  # dispatch 2nd
    tag2 = state.stack[0].waiting_tag

    advance_tree_build(state, _FakeMoves(resolved_tags={tag2: "con_firefox"}), provider, restore_queue)

    assert provider.layout_calls == [("con_kitty", "tabbed")]
    assert state.stack[0].accumulated_id == "wrapper_of_con_kitty"
    assert state.stack[0].has_wrapper is True


def test_advance_tree_build_stacked_layout_uses_stacking_set_keyword():
    # Translation itself is tiled_tree.set_container_layout()'s own job
    # (tested there) — this only confirms advance_tree_build() passes
    # the ORIGINAL "stacked" value through untranslated, not "stacking",
    # since the provider is the one that knows about the read/set
    # spelling asymmetry, not this module.
    tree = {"type": "split", "layout": "stacked", "children": [
        {"type": "window", "app_id": "a"}, {"type": "window", "app_id": "b"},
    ]}
    state = start_tree_build(tree, "1")
    provider = _FakeTreeProvider()
    restore_queue = []
    advance_tree_build(state, _FakeMoves(), provider, restore_queue)
    tag1 = state.stack[0].waiting_tag
    advance_tree_build(state, _FakeMoves(resolved_tags={tag1: "con_a"}), provider, restore_queue)
    advance_tree_build(state, _FakeMoves(), provider, restore_queue)
    tag2 = state.stack[0].waiting_tag

    advance_tree_build(state, _FakeMoves(resolved_tags={tag2: "con_b"}), provider, restore_queue)

    assert provider.layout_calls == [("con_a", "stacked")]


def test_advance_tree_build_third_child_auto_joins_without_layout_call():
    # Live-confirmed: once a real group exists, a window landing on the
    # SAME scratch workspace joins it directly, adopting its layout —
    # set_container_layout() only needs calling once per NEW group.
    tree = {"type": "split", "layout": "tabbed", "children": [
        {"type": "window", "app_id": "a"}, {"type": "window", "app_id": "b"}, {"type": "window", "app_id": "c"},
    ]}
    state = start_tree_build(tree, "1")
    provider = _FakeTreeProvider()
    restore_queue = []
    for expected_app_id in ("a", "b", "c"):
        advance_tree_build(state, _FakeMoves(), provider, restore_queue)
        tag = state.stack[0].waiting_tag
        assert restore_queue[-1]["app_id"] == expected_app_id
        advance_tree_build(state, _FakeMoves(resolved_tags={tag: f"con_{expected_app_id}"}), provider, restore_queue)

    assert provider.layout_calls == [("con_a", "tabbed")]  # only once — "c" auto-joined
    assert state.stack[0].accumulated_id == "wrapper_of_con_a"  # unchanged since


def test_advance_tree_build_nested_split_uses_deeper_scratch_workspace():
    tree = {
        "type": "split", "layout": "tabbed",
        "children": [
            {"type": "window", "app_id": "kitty"},
            {"type": "split", "layout": "stacked", "children": [
                {"type": "window", "app_id": "firefox"}, {"type": "window", "app_id": "firefox"},
            ]},
        ],
    }
    state = start_tree_build(tree, "1")
    provider = _FakeTreeProvider()
    restore_queue = []
    advance_tree_build(state, _FakeMoves(), provider, restore_queue)  # queue kitty on scratch(0)
    tag = state.stack[0].waiting_tag
    advance_tree_build(state, _FakeMoves(resolved_tags={tag: "con_kitty"}), provider, restore_queue)  # fold

    advance_tree_build(state, _FakeMoves(), provider, restore_queue)  # dispatch nested split -> push frame

    assert len(state.stack) == 2
    assert state.stack[1].depth == 1

    advance_tree_build(state, _FakeMoves(), provider, restore_queue)  # queue 1st firefox leaf

    assert restore_queue[-1]["target_region"] == str(SCRATCH_WORKSPACE_BASE + 1)


def test_advance_tree_build_hands_finished_subtree_up_to_parent():
    tree = {
        "type": "split", "layout": "tabbed",
        "children": [
            {"type": "window", "app_id": "kitty"},
            {"type": "split", "layout": "stacked", "children": [
                {"type": "window", "app_id": "firefox"}, {"type": "window", "app_id": "code"},
            ]},
        ],
    }
    state = start_tree_build(tree, "1")
    provider = _FakeTreeProvider()
    restore_queue = []
    advance_tree_build(state, _FakeMoves(), provider, restore_queue)
    tag = state.stack[0].waiting_tag
    advance_tree_build(state, _FakeMoves(resolved_tags={tag: "con_kitty"}), provider, restore_queue)
    advance_tree_build(state, _FakeMoves(), provider, restore_queue)  # push inner frame
    advance_tree_build(state, _FakeMoves(), provider, restore_queue)  # queue firefox on scratch(1)
    tag = state.stack[1].waiting_tag
    advance_tree_build(state, _FakeMoves(resolved_tags={tag: "con_firefox"}), provider, restore_queue)
    advance_tree_build(state, _FakeMoves(), provider, restore_queue)  # queue code on scratch(1)
    tag = state.stack[1].waiting_tag
    advance_tree_build(state, _FakeMoves(resolved_tags={tag: "con_code"}), provider, restore_queue)
    assert provider.layout_calls == [("con_firefox", "stacked")]
    assert state.stack[1].accumulated_id == "wrapper_of_con_firefox"

    # Inner frame has no remaining children -> next tick pops it and
    # hands its single result up to the outer (root) frame.
    advance_tree_build(state, _FakeMoves(), provider, restore_queue)

    assert len(state.stack) == 1  # inner frame popped
    # Handing up moves the finished subtree from its own scratch(1) to
    # the outer frame's own scratch(0) BEFORE folding it in.
    assert ("wrapper_of_con_firefox", str(SCRATCH_WORKSPACE_BASE)) in provider.move_calls
    # Outer frame already had kitty (1 member, no wrapper yet) -> this
    # arrival triggers the outer group call.
    assert provider.layout_calls[-1] == ("con_kitty", "tabbed")
    assert state.stack[0].accumulated_id == "wrapper_of_con_kitty"


def test_advance_tree_build_root_finish_moves_result_to_real_target_region():
    tree = {"type": "split", "layout": "tabbed", "children": [
        {"type": "window", "app_id": "a"}, {"type": "window", "app_id": "b"},
    ]}
    state = start_tree_build(tree, "3:III")
    provider = _FakeTreeProvider()
    restore_queue = []
    advance_tree_build(state, _FakeMoves(), provider, restore_queue)
    tag = state.stack[0].waiting_tag
    advance_tree_build(state, _FakeMoves(resolved_tags={tag: "con_a"}), provider, restore_queue)
    advance_tree_build(state, _FakeMoves(), provider, restore_queue)
    tag = state.stack[0].waiting_tag
    advance_tree_build(state, _FakeMoves(resolved_tags={tag: "con_b"}), provider, restore_queue)

    done = advance_tree_build(state, _FakeMoves(), provider, restore_queue)

    assert done is True
    assert provider.move_calls[-1] == ("wrapper_of_con_a", "3:III")
    assert state.stack == []


def test_advance_tree_build_deep_nesting_degrades_to_flat_leaves():
    # A split nested past MAX_TREE_BUILD_DEPTH gets flattened into plain
    # leaf children of whatever frame hit the cap, instead of getting
    # its own (unavailable) scratch workspace — documented degrade, not
    # a crash.
    from tuicc.winrestore import MAX_TREE_BUILD_DEPTH, _BuildFrame

    deep_child = {"type": "split", "layout": "stacked", "children": [
        {"type": "window", "app_id": "x"}, {"type": "window", "app_id": "y"},
    ]}
    frame = _BuildFrame(remaining=[deep_child], layout="tabbed", depth=MAX_TREE_BUILD_DEPTH - 1)
    state = TreeBuildState(stack=[frame], target_region="1")

    advance_tree_build(state, _FakeMoves(), _FakeTreeProvider(), [])

    # The nested split was flattened straight into this frame's own
    # remaining list, as plain leaf children.
    assert frame.remaining == [{"type": "window", "app_id": "x"}, {"type": "window", "app_id": "y"}]


def test_advance_tree_build_aborts_and_recovers_on_layout_failure():
    tree = {"type": "split", "layout": "tabbed", "children": [
        {"type": "window", "app_id": "a"}, {"type": "window", "app_id": "b"},
    ]}
    state = start_tree_build(tree, "1")
    provider = _FakeTreeProvider(layout_results={("con_a", "tabbed"): None})  # simulated failure
    restore_queue = []
    advance_tree_build(state, _FakeMoves(), provider, restore_queue)
    tag = state.stack[0].waiting_tag
    advance_tree_build(state, _FakeMoves(resolved_tags={tag: "con_a"}), provider, restore_queue)
    advance_tree_build(state, _FakeMoves(), provider, restore_queue)
    tag = state.stack[0].waiting_tag

    # Folding "b" in triggers the (failing) group call.
    advance_tree_build(state, _FakeMoves(resolved_tags={tag: "con_b"}), provider, restore_queue)

    assert state.failed is True
    # The child that directly caused the failure is recovered immediately.
    assert ("con_b", "1") in provider.move_calls

    done = advance_tree_build(state, _FakeMoves(), provider, restore_queue)

    assert done is True
    assert state.stack == []
    # Whatever was already accumulated (con_a, never successfully
    # grouped) is also force-moved to the real target, not stranded.
    assert ("con_a", "1") in provider.move_calls
