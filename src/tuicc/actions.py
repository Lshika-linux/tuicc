"""Built-in handlers for target_kinds not owned by any specific module.

region/window aren't module-specific — any module reporting a region
or window item expects the same underlying action (focus_region /
focus_window), so these live here rather than in a module file.
Wifi/bluetooth toggle logic, by contrast, IS module-specific (only
connectivity.py knows what "toggle" means for that data), so those
handlers self-register from connectivity.py instead — same pattern
quick_actions.py uses for its own TARGET_KIND.

Handler signature: (ctx, item, cfg) -> (should_dismiss, pending). ctx
is an ActionContext bundling the WM provider and the connectivity
worker. should_dismiss=True means tuicc calls Provider.dismiss_self()
after this runs — hides tuicc, the process keeps running. This never
terminates tuicc; the only way to actually quit is Ctrl+C (main.py's
try/finally handles cleanup for that path). pending, if not None,
becomes the new pending_confirm value — see its "dismiss_after_confirm"
key for the same meaning, deferred until the y/n answer comes in.

dispatch_action/handle_pending_confirm below are pure, value-returning
functions, not ActionContext mutators — the same carve-out
resize_mode.choose()/help_mode.apply_color_edit() get from their
sibling modules' usual "state-first, mutate in place" convention.
pending_confirm deliberately stays a plain ad hoc dict here rather than
a dataclass: four different producer call sites (sessions.py,
power_menu.py, quick_actions.py) build it with different key subsets,
and none of them should have to agree on a shared schema just because
this file needs to read a few of those keys back out.
"""

import shlex
import subprocess
from dataclasses import dataclass, field


@dataclass
class ActionContext:
    provider: object
    connectivity: object
    # Session entries (from session.py's load_session) waiting to be
    # spawned — a handler appends to this, main.py's loop drains it
    # over time (staggered, not all at once — see main.py's restore
    # processing). Shared mutable resource a handler can act on,
    # same shape as connectivity above.
    restore_queue: list = field(default_factory=list)


def spawn_detached(cmd, shell_true=False):
    """Run cmd as a detached background process that survives tuicc
    exiting right after this call.

    cmd is normally a string — shell_true=True runs it through the
    shell (needed for real shell syntax: pipes, ;, &&, $VARS); off by
    default, it's split into plain argv and run directly, no shell
    involved, so no part of cmd is ever interpreted as shell syntax.
    cmd may also be a pre-split list (session.py's saved cmdline
    already is one, from /proc/<pid>/cmdline) — passed straight
    through rather than re-joined and re-split, which could mangle an
    argument that legitimately contains a space. Shared by main.py
    (launcher, confirm-dialog, and session-restore spawns) and
    quick_actions.py/power_menu.py's immediate (non-confirm) actions,
    so there's exactly one place that decides how a command becomes a
    process.

    Returns the spawned process's pid — lets a caller match it against
    Window.pid later (see pending_moves.py), on providers that expose
    one. The pid is only ever exact for the shell_true=False path: with
    shell_true=True the pid belongs to the shell, not necessarily the
    GUI process it eventually execs.
    """
    if shell_true:
        popen_cmd = cmd
    elif isinstance(cmd, str):
        popen_cmd = shlex.split(cmd)
    else:
        popen_cmd = cmd
    process = subprocess.Popen(
        popen_cmd, shell=shell_true,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
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
    """Resolves a y/n confirm dialog. On confirm_yes: runs whichever
    action `pending` describes — branching on `"restore_entries" in
    pending`, not a discriminator field, matching how sessions.py/
    power_menu.py/quick_actions.py all build this dict — and returns
    (pending["dismiss_after_confirm"], None). On confirm_no: returns
    (False, None). Any other key leaves the dialog open, unchanged:
    (False, pending). Same (should_dismiss, pending) return order as
    dispatch_action/every handler, for consistency within this file.

    The caller still calls provider.dismiss_self() and does its own
    dismissed=True bookkeeping when should_dismiss comes back True —
    neither is reachable from the dict alone.
    """
    if key == cfg.keybinds["confirm_yes"]:
        if "restore_entries" in pending:
            if "kill_regions" in pending:
                kill_regions = set(pending["kill_regions"])
                for region in ctx.provider.get_state().regions:
                    if region.id in kill_regions:
                        for window in region.windows:
                            ctx.provider.close_window(window.id)
            ctx.restore_queue.extend(pending["restore_entries"])
        else:
            spawn_detached(pending["command"], pending["shell_true"])
        return pending["dismiss_after_confirm"], None
    if key == cfg.keybinds["confirm_no"]:
        return False, None
    return False, pending
