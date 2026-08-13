"""Built-in handlers for target_kinds not owned by any specific module.

region/window aren't module-specific — any module reporting a region
or window item expects the same underlying action (focus_region /
focus_window), so these live here rather than in a module file.
Module-specific target_kinds (e.g. wifi/bluetooth toggles) self-register
from their own module instead — see connectivity.py, quick_actions.py.

Handler signature: (ctx, item, cfg) -> (should_dismiss, pending). ctx is
an ActionContext bundling the WM provider and the status worker.
should_dismiss=True means tuicc calls Provider.dismiss_self() after this
runs (hide, don't quit — see CLAUDE/NOTES/design-decisions.md
#dismiss-vs-quit). pending, if not None, becomes the new pending_confirm
value, deferred until the y/n answer comes in.

pending_confirm deliberately stays a plain ad hoc dict, not a dataclass:
four different producer call sites (sessions.py, power_menu.py,
quick_actions.py) build it with different key subsets.
"""

import os
import shlex
import subprocess
from dataclasses import dataclass, field

# Env vars that identify *this* WM session specifically — always taken
# from the current os.environ, never from a saved/captured env dict,
# even when that dict happens to set them. A session saved today and
# loaded after a relogin/reboot would otherwise carry yesterday's
# WAYLAND_DISPLAY/SWAYSOCK/etc. straight into the respawned process,
# pointing at a socket that no longer exists — a launch failure with a
# totally different, more confusing cause than the one the captured
# env is there to fix. See spawn_detached()'s env parameter.
_ALWAYS_LIVE_ENV_KEYS = frozenset({
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "I3SOCK",
    "SWAYSOCK",
    "XAUTHORITY",
})


@dataclass
class ActionContext:
    provider: object
    # The shared status_worker.StatusWorker instance — see
    # RenderContext.status's own docstring for why it's named `status`,
    # not `connectivity`, despite wifi/bluetooth handlers being its
    # first consumers here.
    status: object
    # Session entries (from session.py's load_session) waiting to be
    # spawned — a handler appends to this, main.py's loop drains it
    # over time (staggered, not all at once — see main.py's restore
    # processing). Shared mutable resource a handler can act on,
    # same shape as status above.
    restore_queue: list = field(default_factory=list)
    # A handler sets this to a region id to ask main.py to move
    # selection to that region's sidebar item right after this action
    # resolves — sessions.py's "load" branch sets it to wherever tuicc's
    # own window currently lives (ctx.provider.get_state().focused_region_id
    # — already tuicc's own region whenever tuicc has WM focus, see
    # CLAUDE.md), so confirming a session load returns you to the
    # sidebar instead of leaving the cursor sitting in the Sessions
    # module. None (the default) means no request; main.py clears it
    # back to None once consumed — a single-use signal, same "read once,
    # reset" idiom as main.py's own expect_focus_reclaim.
    reselect_region_id: str | None = None
    # A handler sets this to an exact NavItem id to ask main.py to move
    # selection there directly, no lookup needed — see
    # CLAUDE/NOTES/design-decisions.md#reselect-item-id-vs-region-search
    # for when this is used instead of reselect_region_id above. None
    # (the default) means no request; main.py clears it back to None
    # once consumed, same single-use idiom as reselect_region_id.
    reselect_item_id: str | None = None


def spawn_detached(cmd, shell_true=False, log_path=None, env=None):
    """Run cmd as a detached background process that survives tuicc
    exiting right after this call.

    cmd is normally a string — shell_true=True runs it through the shell
    (needed for pipes/;/&&/$VARS); off by default, it's split into plain
    argv and run directly, no shell involved. cmd may also be a
    pre-split list (session.py's saved cmdline already is one) — passed
    straight through rather than re-joined and re-split, which could
    mangle an argument that legitimately contains a space. Shared by
    every spawn site in the codebase, so there's exactly one place that
    decides how a command becomes a process.

    Returns the spawned pid, for matching against Window.pid later (see
    pending_moves.py) — only exact for shell_true=False; with
    shell_true=True the pid belongs to the shell, not the GUI process it
    eventually execs.

    log_path=None (default) sends stdout/stderr to DEVNULL. When given,
    they're captured there instead — real diagnostic data for "this
    saved command silently didn't produce a window" instead of a crash
    looking identical to "never started at all." See
    CLAUDE/NOTES/known-limitations.md#restore-relaunch-crash for the
    concrete bug this exists for.

    env=None (default) inherits tuicc's own environment. When given
    (session.py's captured /proc/<pid>/environ snapshot), it's layered
    on top of the current environment with captured values winning,
    except for _ALWAYS_LIVE_ENV_KEYS — see
    CLAUDE/NOTES/design-decisions.md#spawn-detached-env-layering.
    """
    if shell_true:
        popen_cmd = cmd
    elif isinstance(cmd, str):
        popen_cmd = shlex.split(cmd)
    else:
        popen_cmd = cmd

    popen_env = None
    if env is not None:
        popen_env = {**os.environ, **env}
        for key in _ALWAYS_LIVE_ENV_KEYS:
            if key in os.environ:
                popen_env[key] = os.environ[key]
            else:
                popen_env.pop(key, None)

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stdout = stderr = open(log_path, "wb")
    else:
        stdout = stderr = subprocess.DEVNULL

    process = subprocess.Popen(
        popen_cmd, shell=shell_true,
        stdout=stdout,
        stderr=stderr,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env=popen_env,
    )
    if log_path is not None:
        stdout.close()
    return process.pid


def _handle_region(ctx, item, cfg):
    ctx.provider.focus_region(item.focus_target)
    return True, None


def _handle_window(ctx, item, cfg):
    ctx.provider.focus_window(item.focus_target)
    return True, None


BASE_HANDLERS = {
    "region": _handle_region,
    "window": _handle_window,
}


def dispatch_action(ctx, handlers, item, cfg):
    """Looks up handlers.get(item.target_kind) and runs it, returning
    (should_dismiss, pending) straight from the handler — same shape
    every handler already returns (see the module docstring) — or
    (False, None) if there's no handler registered for this
    target_kind at all. handlers is passed in explicitly (main.py
    passes render.ACTION_HANDLERS) rather than imported here, so this
    module stays as ignorant of render.py as it already is of any
    specific module.

    Both of this function's call sites in main.py are only ever
    reached with pending_confirm already None — the pending_confirm-
    is-not-None tier intercepts and continues first otherwise — so
    the caller can unconditionally assign pending_confirm from this
    function's return value, without an `if pending is not None:`
    guard, and get the exact same result.
    """
    handler = handlers.get(item.target_kind)
    if handler is None:
        return False, None
    return handler(ctx, item, cfg)


def handle_pending_confirm(ctx, pending, key, cfg):
    """Resolves a y/n confirm dialog. On confirm_yes (OR confirm — the
    same Enter used to confirm everything else, accepted here too since
    "yes" is itself a kind of confirm and pressing Enter reads as
    intuitive muscle memory once you're used to it elsewhere in tuicc;
    confirm_no has no such alternate, only its own bound key answers
    "no"): runs whichever action `pending` describes — branching on
    `"restore_entries" in pending`, not a discriminator field, matching
    how sessions.py/power_menu.py/quick_actions.py all build this dict
    — and returns (pending["dismiss_after_confirm"], None). On
    confirm_no: returns (False, None). Any other key leaves the dialog
    open, unchanged: (False, pending). Same (should_dismiss, pending)
    return order as dispatch_action/every handler, for consistency
    within this file.

    The caller still calls provider.dismiss_self() and does its own
    dismissed=True bookkeeping when should_dismiss comes back True —
    neither is reachable from the dict alone.
    """
    if key == cfg.keybinds["confirm_yes"] or key == cfg.keybinds["confirm"]:
        if "restore_entries" in pending:
            if "kill_regions" in pending:
                kill_regions = set(pending["kill_regions"])
                for region in ctx.provider.get_state().regions:
                    if region.id in kill_regions:
                        for window in region.windows:
                            ctx.provider.close_window(window.id)
            ctx.restore_queue.extend(pending["restore_entries"])
            ctx.reselect_region_id = ctx.provider.get_state().focused_region_id
        else:
            spawn_detached(pending["command"], pending["shell_true"])
        return pending["dismiss_after_confirm"], None
    if key == cfg.keybinds["confirm_no"]:
        return False, None
    return False, pending
