"""LoopState — the explicit alternative to main()'s closure-capture +
nonlocal for its own loop-owned state, built incrementally across several
phases (CLAUDE/NOTES/design-decisions.md#loopstate-migration).

A field existing here always means its corresponding main.py locals and
closures have already migrated to it — never a placeholder for a later
phase. Fields recomputed fresh every frame and never read past the frame
that computed them (layout boxes, terminal size, WM state, the ordered
nav-item list) deliberately stay OUT — bundling them in would conflate
two different kinds of "shared state": genuine cross-frame session state
versus this-frame-only computed context. Same "plain fields, no methods"
style as resize_mode.ResizeState/launcher.LauncherState/
pending_moves.PendingMovesQueue — main.py still owns *when* to read or
write each field.
"""

from dataclasses import dataclass, field


@dataclass
class LoopState:
    # Which module owns raw keystrokes. "normal" (never popped, bottom
    # of stack) = nothing has stolen input, unbound printable keys
    # auto-claim for the launcher. resize's BROWSING level is the one
    # permanent exception — never joins this stack (see
    # CLAUDE/NOTES/design-decisions.md#mode-stack-phase-1 for why).
    mode_stack: list[str] = field(default_factory=lambda: ["normal"])

    # A generic transient toast — used by save/cycle-preset as much as
    # by resize, genuinely main-loop-level, not owned by either module.
    resize_message: str | None = None
    resize_message_until: float = 0.0
    # True when resize_message is a failure/error toast (a launcher/
    # restore spawn that exited nonzero, or timed out with no window
    # ever appearing — see pending_moves.PendingMovesResult) — drawn
    # with the "urgent" theme role instead of "accent". Naming mirrors
    # NavItem.preview_urgent's existing convention. Reset to False
    # whenever resize_message itself is cleared or reassigned.
    resize_message_urgent: bool = False

    # The compiled curses color-pair mapping currently in effect —
    # rebound in place after a live theme edit so rendering picks it up
    # without restart. No static default: real initial value comes from
    # app_setup.build_app(), computation-dependent.
    theme_pairs: dict = field(default_factory=dict)

    # A dict-shaped Y/N or typed-input prompt awaiting confirm_yes/
    # confirm_no/confirm — see actions.py's handle_pending_confirm().
    pending_confirm: dict | None = None
    # True from dismiss_self() until the next real keypress — see
    # CLAUDE/NOTES/known-limitations.md#dismissed-reset-timing.
    dismissed: bool = False
    # The region focused right before tuicc's own — for return_to_origin's
    # Escape. Tracks the value being *replaced* on each real focus
    # transition (see main.py's own comment at the transition-detector
    # site for the full reasoning).
    last_focused_region_id: str | None = None
    origin_region_id: str | None = None
    # True for one frame after pending_moves.process() calls
    # provider.focus_self() — a self-inflicted transition, not the user
    # going elsewhere; suppresses the selection reset that would
    # otherwise follow.
    expect_focus_reclaim: bool = False
    # window_id -> pid (i3 only — sway's Window.pid is already populated).
    resolved_pid_cache: dict = field(default_factory=dict)

    # Which nav item is highlighted, which region focus/launcher-spawn
    # targeting follows (independent of selected_id), and which module
    # owns Tab/Left-Right navigation. Always kept in sync with each
    # other via navigation.resolve_selection() — never assign one alone
    # outside a spot that's deliberately desyncing them (e.g. Left/Right
    # onto an empty module intentionally clears selected_id but not
    # active_module). No static default for active_module: real initial
    # value depends on cfg.layout.boxes, computation-dependent.
    active_module: str | None = None
    selected_id: str | None = None
    focus_id: str | None = None
