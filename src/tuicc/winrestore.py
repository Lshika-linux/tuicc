"""Window layout restore: capture which app sits in which region, tiled
or floating (+ relative geometry), so a later restore can relaunch the
same apps onto the same regions and refloat them at the same relative
spot — and, where the provider supports it (sway/i3, via
Provider.get_tiled_tree()/set_container_layout()), reconstruct the real
tiled/stacked/tabbed split tree instead of just flat per-window
placement.

    WMState -> capture_session() -> [{target_region, tiled?, tiled_flat?,
    floating?}] -> save_session() -> plain TOML file, user-editable
                                      like everything else in tuicc

Deliberately does NOT try to reproduce the exact running state (which
file/vault/tab was open) — an earlier version captured each window's
real /proc/<pid>/cmdline (+ environ) and replayed it verbatim on
restore, which turned out fragile in a way that kept resurfacing on
real machines: a nix-wrapped app's captured argv[0] is frequently the
raw binary a wrapper script already `exec`'d into by the time tuicc
could read it (skips whatever env setup the wrapper did — broke
Spotify's LD_LIBRARY_PATH live), and some apps (Electron in
particular, via a `process.title` reassignment that overwrites argv's
own NUL separators in place) hand back genuinely corrupted argv from
/proc/<pid>/cmdline (broke Discord/Obsidian live). Rafi's own call,
live: give up the "exactly what was open" promise — app_id + region +
floating/tiled + geometry ("the layout") is worth having reliably far
more than exact-state restore is worth having fragile. See
CLAUDE/NOTES/design-decisions.md#restore-argv0-resolution,
#winrestore-app-id-capture, and #append-layout-tiled-restore for the
full history.

resolve_launch_argv() relaunches through the exact same path
launcher.py's own spawns already use (a bare command name resolved by
subprocess.Popen against $PATH) rather than a second, bespoke spawn
mechanism — proven reliable already, and the only reason this module
needs launcher.py's desktop_apps list at all.

Matching a relaunched process back to the window it produces lives in
pending_moves.py, same as launcher spawns — see
queue_restore_entry()'s own docstring there for how a winrestore entry
deliberately skips the pid-identity tier and goes straight to
app_id-tier matching.

A restore batch runs strictly one region at a time — see
TiledRestoreState/advance_tiled_restore()'s own docstrings for why
(found live: with several regions' spawns all in flight together, an
app_id repeated across regions — the common case, not a corner case,
e.g. two terminals or two browser windows spread across different
workspaces — has no reliable way to tell which region a newly-mapped
window belongs to). A region with real tiled structure to rebuild
(TreeBuildState/advance_tree_build(), below) goes further still: it
places its own windows ONE LEAF AT A TIME, on a small reserved pool of
scratch workspaces, so a still-unbuilt subtree is never sitting next to
an unrelated one on the same workspace — see CLAUDE/NOTES/
design-decisions.md#append-layout-doesnt-exist-on-sway for the full
story of why (this replaced an earlier design built on sway/i3's
`append_layout` IPC command, which turned out to not exist on sway at
all — a permanent upstream decision, not a bug — after already being
built around and shipped once this session).

Floating geometry is saved normalized (0..1, same space as Window.rect)
rather than in absolute pixels — a saved layout stays meaningful if
restored on a different-resolution screen later, matching the reason
tuicc normalizes rect everywhere else (see providers/base.py).
"""

import shlex
import tomllib
import tomli_w
from dataclasses import dataclass, field
from pathlib import Path

from tuicc.model import WMState, Region, Window
from tuicc.tiled_tree import flatten_leaves
from tuicc.wm_config_parser import resolve_workspace_target

WINRESTORE_DIR = Path.home() / ".config" / "tuicc" / "winrestore"


def capture_window(window: Window) -> dict:
    """One window's saved-layout leaf record — app_id, plus relative
    rect when floating. No target_region/floating fields here: the
    parent region entry (capture_region()) already carries
    target_region, and which of its own tiled_flat/floating lists this
    entry sits in already says whether it's floating.
    """
    entry = {"app_id": window.app_id}
    if window.floating:
        x, y, w, h = window.rect
        entry["x"] = x
        entry["y"] = y
        entry["w"] = w
        entry["h"] = h
    return entry


def capture_region(region: Region, provider=None) -> dict:
    """One region's saved-layout entry:
    {"target_region": ..., "tiled": <tree>?, "tiled_flat": [...]?, "floating": [...]?}

    Tries provider.get_tiled_tree(region.id) first when a provider was
    given — real tree reconstruction (splith/splitv/stacked/tabbed) via
    TreeBuildState/advance_tree_build()/Provider.set_container_layout()
    at restore time. Falls back to a flat "tiled_flat" list (app_id-only, in whatever order
    region.windows reports them — no ordering promise, since a flat
    fallback has no tree structure to order against anyway) when
    provider is None, doesn't implement get_tiled_tree(), or this
    particular workspace's own tiled area came back empty (a workspace
    with only floating windows on it, or none at all) — same
    graceful, per-region degradation Provider.get_tiled_tree()'s own
    docstring describes. "tiled"/"tiled_flat"/"floating" are each
    omitted entirely (not an empty list/None) when there's nothing of
    that kind, so a region with only tiled windows saves no "floating"
    key at all, etc.
    """
    tiled_windows = [w for w in region.windows if not w.floating]
    floating_windows = [w for w in region.windows if w.floating]

    entry = {"target_region": region.id}
    # Never even asked when there's nothing tiled to capture in the
    # first place — an all-floating (or otherwise tiled-empty) region
    # has no reason to make a provider/IPC call at all.
    tree = provider.get_tiled_tree(region.id) if provider is not None and tiled_windows else None
    if tree is not None:
        entry["tiled"] = tree
    elif tiled_windows:
        entry["tiled_flat"] = [capture_window(w) for w in tiled_windows]
    if floating_windows:
        entry["floating"] = [capture_window(w) for w in floating_windows]
    return entry


def capture_session(state: WMState, provider=None) -> list[dict]:
    """Every region with at least one open window, as save-able
    entries — a region with nothing on it produces no entry at all
    (nothing to restore there). provider is optional (None keeps
    every region on the flat "tiled_flat" fallback, e.g. for a WM
    provider that doesn't implement get_tiled_tree() at all) — see
    capture_region()'s own docstring.
    """
    return [capture_region(region, provider) for region in state.regions if region.windows]


def save_session(entries: list[dict], path: Path) -> None:
    """Write entries as [[region]] blocks to path, atomically — a crash
    or Ctrl-C mid-write must never corrupt an existing save (same
    reasoning tileroot's dump -o uses for the same problem).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    with open(tmp_path, "wb") as f:
        tomli_w.dump({"region": entries}, f)
    tmp_path.replace(path)


def load_session(path: Path) -> list[dict]:
    """Read a previously saved layout back into the same shape
    capture_session() produces."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return data.get("region", [])


def resolve_launch_argv(app_id: str, desktop_apps) -> list[str] | None:
    """The command to relaunch app_id with — the matching .desktop
    entry's own Exec=, split into argv — or None if no desktop_apps
    entry matches (nothing to relaunch this app with at all). Matched
    by app_id_hint case-insensitively: a .desktop's StartupWMClass=/
    file-basename and a captured window.app_id commonly differ only in
    case (confirmed live against Rafi's own real .desktop files:
    "Spotify" vs. "spotify", "Discord" vs. "discord").

    pending_moves.py's promote_restore_queue() calls this right before
    spawn_detached() — winrestore.py itself never launches anything,
    same "capture/load only" boundary this module always had (see its
    own top docstring).
    """
    match = next(
        (exec_cmd for _name, exec_cmd, app_id_hint in desktop_apps if app_id_hint.lower() == app_id.lower()),
        None,
    )
    if not match:
        return None
    words = shlex.split(match)
    return words or None


def _flatten_region(entry: dict) -> tuple[list[dict], list[dict]]:
    """One loaded region entry -> (tiled_entries, flat_entries), each in
    pending_moves.queue_restore_entry()'s own expected shape ({"app_id",
    "target_region", "floating", + x/y/w/h when floating}).

    tiled_entries is populated ONLY for a region whose saved tree is a
    single bare window (a lone tiled window — capture_tiled_tree()
    already collapses single-child splits down to this, so there's
    real structure to lose) — nothing to group, so it goes straight to
    target_region via the ordinary flat pipeline, same as tiled_flat
    always has. A region whose tree has real split structure
    (tree["type"] == "split") is NOT flattened here at all — its
    windows are placed one at a time by TreeBuildState/
    advance_tree_build() below, which needs the original nested tree,
    not a flat leaf list, to know what to group with what.
    """
    target_region = entry["target_region"]
    tree = entry.get("tiled")
    tiled_entries = (
        [{"app_id": tree["app_id"], "target_region": target_region, "floating": False}]
        if tree is not None and tree["type"] == "window" else []
    )

    flat_entries = [
        {"app_id": w["app_id"], "target_region": target_region, "floating": False, "placed_by_wm": False}
        for w in entry.get("tiled_flat", [])
    ]
    flat_entries += [
        {
            "app_id": w["app_id"], "target_region": target_region,
            "floating": True, "placed_by_wm": False,
            "x": w["x"], "y": w["y"], "w": w["w"], "h": w["h"],
        }
        for w in entry.get("floating", [])
    ]
    return tiled_entries, flat_entries


def _batch_in_flight(target_region: str, restore_queue: list, moves) -> bool:
    """Whether any winrestore entry targeting target_region is still
    unresolved — either sitting unpopped in restore_queue, or present
    in moves.entries with no last_matched_at yet (pending_moves.process()
    sets that the instant a match resolves; its own existing
    MOVE_TIMEOUT_SECONDS give-up eventually drops an entry that never
    matches at all, so this converges on its own — no separate timeout
    needed here). "placed_by_wm" in entry is the marker: only
    pending_moves.queue_restore_entry() ever sets it (queue_launcher_spawn()
    — a plain typed launch — never does), so an unrelated launcher spawn
    that happens to target the same region mid-restore can't be
    mistaken for part of this batch.
    """
    if any(e["target_region"] == target_region for e in restore_queue):
        return True
    return any(
        e.get("target_region") == target_region and "placed_by_wm" in e and e.get("last_matched_at") is None
        for e in moves.entries
    )


SCRATCH_WORKSPACE_BASE = 9000
# How many nesting levels TreeBuildState reserves a scratch workspace
# for (9000..9007) — far deeper than any realistic real layout. A tree
# nested deeper than this degrades its remaining structure to a flat
# leaf list on whatever depth it hit the cap at (see _push_child()) —
# documented, not crashed; picking these numbers this high (rather than
# low ones like 1-8) is the whole reason collision with a real
# workspace is effectively impossible, mirroring MARK_PREFIX's own
# "reserved namespace, not configurable" precedent.
MAX_TREE_BUILD_DEPTH = 8


def _scratch_workspace(depth: int) -> str:
    return str(SCRATCH_WORKSPACE_BASE + depth)


@dataclass
class _BuildFrame:
    """One split node currently being folded — see TreeBuildState's own
    docstring for the algorithm. remaining: this split's own children
    not yet started. layout: this split's own layout
    ("splith"/"splitv"/"stacked"/"tabbed"). depth: which scratch
    workspace (_scratch_workspace(depth)) this frame folds its children
    on. accumulated_id: the running fold result's real con id — None
    until the first child lands, a bare leaf/subtree id once exactly
    one child has landed, the GROUP's own con id (from
    Provider.set_container_layout()) once a 2nd child has been folded
    in. has_wrapper: whether accumulated_id already refers to a real
    multi-member group — once true, a further child arriving on this
    frame's own scratch workspace auto-joins that group on its own
    (live-confirmed: a window moved onto a workspace that already holds
    a real grouped container joins it directly, adopting its layout —
    see CLAUDE/NOTES/design-decisions.md
    #append-layout-doesnt-exist-on-sway), so set_container_layout() is
    only ever called for the SECOND child, never the third and later.
    waiting_tag: set while a leaf child's own restore entry has been
    queued and not yet matched.
    """
    remaining: list
    layout: str
    depth: int
    accumulated_id: str | None = None
    has_wrapper: bool = False
    waiting_tag: str | None = None


@dataclass
class TreeBuildState:
    """Drives ONE region's own real (tree["type"] == "split") tiled tree
    reconstruction, one IPC step per call — see advance_tree_build()'s
    own docstring for the per-tick mechanics. stack is an explicit,
    manually-maintained call stack (one _BuildFrame per split node
    currently open) standing in for real recursion, which can't be used
    here: building has to pause mid-subtree, potentially for several
    frames, waiting on a real window to spawn and match.

    target_region: the REAL destination workspace (already resolved via
    resolve_workspace_target() by the caller — see
    advance_tiled_restore()'s own docstring for why that resolution
    can't happen in here), applied once, right at the very end, to
    whatever single container the whole tree folded down to.

    tag_counter: a private, ever-increasing counter for building unique
    per-leaf tags (see _next_tag()) — plain incrementing int, not a
    global counter, so two TreeBuildStates (sequential, never
    concurrent — only one region is ever active at a time, see
    TiledRestoreState) can't collide even if their tags briefly outlive
    the queue slot they were consumed from.

    failed: set once any Provider.set_container_layout() call fails
    mid-build (a real, but expected-to-be-rare failure — the mechanism
    itself was live-verified reliable; a failure here likely means the
    window in question vanished mid-restore). The one child that
    directly caused the failure is force-moved to target_region right
    where it's detected, by advance_tree_build() itself (it's already
    resolved, right there, when this happens). Once set, EVERY
    subsequent advance_tree_build() call aborts the rest of the build:
    every frame still on the stack that has an accumulated_id also gets
    force-moved to target_region (flat placement — better than leaving
    it stranded on a scratch workspace forever), and the state is
    marked done. A leaf whose spawn was still in flight (mid
    waiting_tag) at the exact moment a SIBLING's fold failed is the one
    acknowledged, documented gap in this recovery: it will still
    eventually spawn and match (nothing cancels the underlying
    restore_queue entry), land on its own scratch workspace, and then
    never be moved further — an accepted, rare cost, not silently
    different from the ordinary "known limitations get documented"
    convention used everywhere else in this codebase.
    """
    stack: list = field(default_factory=list)
    target_region: str | None = None
    tag_counter: int = 0
    failed: bool = False


def start_tree_build(tree: dict, target_region: str) -> TreeBuildState:
    """tree must be a real split node (tree["type"] == "split") — a bare
    single-window tree needs no grouping at all and goes through the
    existing flat tiled_entries path instead (see _flatten_region());
    advance_tiled_restore() below is the only caller and already makes
    this check before calling here. target_region is the ALREADY-
    RESOLVED real destination (see TreeBuildState's own docstring).
    """
    root = _BuildFrame(remaining=list(tree["children"]), layout=tree["layout"], depth=0)
    return TreeBuildState(stack=[root], target_region=target_region)


def _next_tag(state: TreeBuildState) -> str:
    state.tag_counter += 1
    return f"treebuild_{id(state)}_{state.tag_counter}"


def _push_child(state: TreeBuildState, frame: _BuildFrame, child: dict, restore_queue: list) -> None:
    """Starts building frame's next child — either queues one leaf spawn
    (setting frame.waiting_tag) or pushes a new frame for a nested
    split, one nesting level deeper. See MAX_TREE_BUILD_DEPTH's own
    comment for the depth-cap degrade: past it, a nested split's own
    structure is simply flattened into plain leaf children of the
    CURRENT frame instead of getting its own frame — a documented,
    exceedingly rare degrade, not a crash.
    """
    if child["type"] == "window":
        tag = _next_tag(state)
        restore_queue.append({
            "app_id": child["app_id"],
            "target_region": _scratch_workspace(frame.depth),
            "floating": False,
            "tag": tag,
        })
        frame.waiting_tag = tag
        return
    if frame.depth + 1 >= MAX_TREE_BUILD_DEPTH:
        frame.remaining = [{"type": "window", "app_id": a} for a in flatten_leaves(child)] + frame.remaining
        return
    state.stack.append(_BuildFrame(remaining=list(child["children"]), layout=child["layout"], depth=frame.depth + 1))


def _fold_child(provider, frame: _BuildFrame, child_id: str, arrived_at_depth: int) -> bool:
    """Folds one newly-available child (a leaf that just matched, or a
    nested frame's own finished single result) into frame. Returns
    False on a set_container_layout() failure (the caller sets
    state.failed), True otherwise.

    arrived_at_depth lets this tell a plain leaf (spawned directly onto
    _scratch_workspace(frame.depth) already — nothing to move) apart
    from a completed child frame's own result (still sitting on ITS
    OWN, one-deeper scratch workspace — needs moving up to
    frame.depth's own scratch workspace FIRST, before it can join
    anything there).
    """
    if arrived_at_depth != frame.depth:
        provider.move_window_to_region(child_id, _scratch_workspace(frame.depth))
    if frame.accumulated_id is None:
        frame.accumulated_id = child_id
        return True
    if frame.has_wrapper:
        # Auto-join already did the work — see _BuildFrame's own
        # docstring. frame.accumulated_id (the group's own con id)
        # stays valid and unchanged.
        return True
    wrapper_id = provider.set_container_layout(frame.accumulated_id, frame.layout)
    if wrapper_id is None:
        return False
    frame.accumulated_id = wrapper_id
    frame.has_wrapper = True
    return True


def advance_tree_build(state: TreeBuildState, moves, provider, restore_queue: list) -> bool:
    """One step of building ONE region's real tiled tree — call every
    frame from advance_tiled_restore() while state.active_phase ==
    "tree". Returns True once the WHOLE tree has been folded down to a
    single container and transplanted onto state.target_region (the
    region's tiled phase is then done, same as the old flat "tiled"
    phase clearing) — False otherwise, having done at most one of:
    aborted (state.failed, see TreeBuildState's own docstring), folded
    a just-resolved leaf/subtree into its frame, popped a fully-folded
    frame and handed its result up to its parent, or dispatched the
    next child of the frame on top of the stack. Never blocks. moves
    is the same PendingMovesQueue advance_tiled_restore() already
    threads everywhere else — only moves.resolved_tags is read here
    (and popped, once consumed — see that field's own docstring for
    why it has to persist across frames rather than being a one-shot
    return value).

    A _fold_child() failure right here — not just the state.failed
    branch below, which only sees whatever was ALREADY accumulated in
    earlier frames — also force-moves the ONE child that just failed to
    join anything (child_id/result_id, whichever this call just tried
    to fold) straight to target_region, since it's sitting right here
    already resolved and is otherwise the one thing state.failed's own
    stack-only sweep can never see (it was never recorded on any
    frame). The one true remaining gap (see TreeBuildState's own
    docstring) is a leaf whose spawn is STILL in flight (waiting_tag
    set, not yet resolved) at the exact moment a SIBLING's fold fails —
    accepted as rare and documented, not fixed with more machinery.
    """
    if state.failed:
        for frame in state.stack:
            if frame.accumulated_id is not None:
                provider.move_window_to_region(frame.accumulated_id, state.target_region)
        state.stack = []
        return True

    if not state.stack:
        return True

    frame = state.stack[-1]

    if frame.waiting_tag is not None:
        if frame.waiting_tag not in moves.resolved_tags:
            return False
        child_id = moves.resolved_tags.pop(frame.waiting_tag)
        frame.waiting_tag = None
        if not _fold_child(provider, frame, child_id, arrived_at_depth=frame.depth):
            state.failed = True
            provider.move_window_to_region(child_id, state.target_region)
        return False

    if not frame.remaining:
        result_id = frame.accumulated_id
        state.stack.pop()
        if not state.stack:
            if result_id is not None:
                provider.move_window_to_region(result_id, state.target_region)
            return True
        if result_id is not None and not _fold_child(provider, state.stack[-1], result_id, arrived_at_depth=frame.depth):
            state.failed = True
            provider.move_window_to_region(result_id, state.target_region)
        return False

    _push_child(state, frame, frame.remaining.pop(0), restore_queue)
    return False


@dataclass
class TiledRestoreState:
    """Drives one "load" batch's region entries strictly one region at a
    time — see advance_tiled_restore()'s own docstring for why this has
    to be sequential rather than the batch-everything-up-front design
    the feature first shipped with (CLAUDE/NOTES/design-decisions.md
    #append-layout-doesnt-exist-on-sway). Same "plain state a driver
    function mutates" shape ResizeState/LauncherState already use —
    main.py holds one instance for the process's whole lifetime,
    threaded through update_frame() same as pending_moves.
    PendingMovesQueue already is.

    queued_regions: raw region entries (capture_session()'s own shape)
    not started yet, in the order winrestore.py queued them.
    active_region_id/active_phase: which region (if any) is currently
    "in flight" — "tree" while a real TreeBuildState is folding a split
    tree (tree_build holds it), "tiled" while waiting on a trivial
    single-window tree's own flat entry (mechanically identical to
    "flat" below, kept as a separate name only because it's this
    region's FIRST sub-phase, still holding pending_flat back), "flat"
    while waiting on tiled_flat/floating. None/None when nothing is
    active. pending_flat: the active region's own tiled_flat/floating
    entries, held back until its tree/tiled phase clears (a same-region
    floating window sharing an app_id with that region's own
    still-open tiled placeholders has the identical cross-match risk a
    second region's placeholders would).
    """
    queued_regions: list = field(default_factory=list)
    active_region_id: str | None = None
    active_phase: str | None = None  # "tree" | "tiled" | "flat" | None
    pending_flat: list = field(default_factory=list)
    tree_build: TreeBuildState | None = None


def advance_tiled_restore(
    state: TiledRestoreState, moves, provider, restore_queue: list, now: float, workspace_names=None,
) -> None:
    """One step of the sequential region-by-region restore — call every
    frame (frame_update.py), unconditionally; a cheap no-op whenever
    nothing is queued or active. See CLAUDE/NOTES/design-decisions.md
    #append-layout-doesnt-exist-on-sway for the full story of why this
    has to be sequential (found live, building the now-retired
    append_layout mechanism this replaced): with several regions'
    worth of spawns all in flight together, an app_id repeated across
    regions has no way to tell which region a newly-mapped window
    belongs to (a REAL, common case live-confirmed on Rafi's own
    machine — kitty appeared 3 times, firefox twice, in one ordinary
    saved layout — not a rare edge case). Keeping only ONE region "in
    flight" at a time removes that ambiguity entirely.

    Each call does AT MOST one of: tick the active region's own
    TreeBuildState (phase "tree"); notice the active region/phase has
    otherwise cleared and either release its held-back pending_flat
    ("tiled" phase done) or finish the region outright ("flat" phase
    done); or, if nothing is active, pop the next queued region and
    start it (a real split tree gets a fresh TreeBuildState, a trivial
    single-window tree gets its one flat entry, a tree-less region
    goes straight to its tiled_flat/floating batch). Never blocks —
    "waiting" just means returning without having changed anything,
    tried again next frame.

    workspace_names (wm_config.workspace_names, default None) resolves
    a region's own bare target_region ("2") against the WM's configured
    full name ("2:II") — same resolve_workspace_target() call
    pending_moves.process() already makes before EVERY
    move_window_to_region(), and for the exact same reason: a
    numbered+named workspace that doesn't live-exist yet (nothing on
    it, or a totally fresh desktop) would otherwise get CREATED bare
    under just "2" by `workspace number 2` — sway/i3 have no way to
    know it should be "2:II" unless told. A TreeBuildState resolves
    this ONCE, at start_tree_build() time, into its own target_region —
    every entry it queues along the way stays bare (targeting a scratch
    workspace, which is never a configured name anyway) until that
    final move. None (the default) leaves target_region unchanged, same
    as resolve_workspace_target() itself degrades with no candidates.
    """
    if state.active_region_id is not None:
        if state.active_phase == "tree":
            if not advance_tree_build(state.tree_build, moves, provider, restore_queue):
                return
            state.tree_build = None
            restore_queue.extend(state.pending_flat)
            state.pending_flat = []
            state.active_phase = "flat"
            return
        if _batch_in_flight(state.active_region_id, restore_queue, moves):
            return
        if state.active_phase == "tiled":
            restore_queue.extend(state.pending_flat)
            state.pending_flat = []
            state.active_phase = "flat"
            return
        state.active_region_id = None
        state.active_phase = None

    if not state.queued_regions:
        return

    entry = state.queued_regions.pop(0)
    target_region = entry["target_region"]
    tree = entry.get("tiled")
    tiled_entries, flat_entries = _flatten_region(entry)

    if tree is not None and tree["type"] == "split":
        resolved_region = resolve_workspace_target(target_region, workspace_names)
        state.tree_build = start_tree_build(tree, resolved_region)
        state.active_region_id = target_region
        state.active_phase = "tree"
        state.pending_flat = flat_entries
    elif tiled_entries:
        restore_queue.extend(tiled_entries)
        state.active_region_id = target_region
        state.active_phase = "tiled"
        state.pending_flat = flat_entries
    else:
        restore_queue.extend(flat_entries)
        state.active_region_id = target_region
        state.active_phase = "flat"
        state.pending_flat = []
