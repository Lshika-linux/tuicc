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

    # The compiled curses color-pair mapping currently in effect —
    # rebound in place after a live theme edit so rendering picks it up
    # without restart. No static default: real initial value comes from
    # app_setup.build_app(), computation-dependent.
    theme_pairs: dict = field(default_factory=dict)
