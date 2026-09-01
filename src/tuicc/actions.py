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
    # A handler sets this to show a transient toast (main.py's shared
    # LoopState.resize_message mechanism, see loop_state.py's own
    # comment on why that field is generic despite its resize-mode
    # name) right after this dispatch resolves — e.g. sysmon.py's
    # copy-to-clipboard confirmation. Same single-use idiom as
    # reselect_region_id/reselect_item_id above: main.py drains it
    # (do_apply_toast()) immediately after the dispatch call that may
    # have set it, then resets both fields back to their defaults.
    # toast_urgent picks the "urgent" vs "accent" theme role, same
    # meaning as NavItem.preview_urgent/resize_message_urgent.
    toast_message: str | None = None
    toast_urgent: bool = False


def spawn_detached(cmd, shell_true=False, log_path=None, env=None):
    """Run cmd (shell string or pre-split argv list) as a detached
    background process that survives tuicc exiting. Shared by every
    spawn site. Returns the spawned pid (exact only for
    shell_true=False), or None if the process couldn't even be started
    at all (subprocess.Popen() itself raising OSError — a missing/
    unreadable executable, a permission error, or anything else in that
    family) rather than letting that exception propagate: found live,
    a session-restore entry whose saved cmdline pointed at a path that
    no longer resolved to anything crashed tuicc's entire main loop,
    not just that one spawn attempt — every caller of this function
    shared the same exposure. If log_path was given, the caught
    exception's own text is written into it, so the existing "see
    {log_path.name}" convention callers already use for a failure toast
    still has something real behind it. log_path captures stdout+stderr
    instead of DEVNULL — see CLAUDE/NOTES/known-limitations.md#restore-relaunch-crash.
    env layers over the current environment except _ALWAYS_LIVE_ENV_KEYS
    — see CLAUDE/NOTES/design-decisions.md#spawn-detached-env-layering.
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

    try:
        process = subprocess.Popen(
            popen_cmd, shell=shell_true,
            stdout=stdout,
            stderr=stderr,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=popen_env,
        )
    except OSError as e:
        if log_path is not None:
            stdout.write(str(e).encode())
            stdout.close()
        return None
    if log_path is not None:
        stdout.close()
    return process.pid


def _handle_region(ctx, item, cfg):
    # dismiss_self() BEFORE the switch, not after — see
    # CLAUDE/NOTES/wm-quirks.md#workspace-switch-fullscreen-invisible.
    # main.py's own dismiss_self() call (after should_dismiss=True comes
    # back) still runs too — a harmless no-op repeat, not skipped, so
    # its dismissed=True bookkeeping stays intact.
    ctx.provider.dismiss_self()
    ctx.provider.focus_region(item.focus_target)
    return True, None


def _handle_window(ctx, item, cfg):
    ctx.provider.dismiss_self()
    ctx.provider.focus_window(item.focus_target)
    return True, None


BASE_HANDLERS = {
    "region": _handle_region,
    "window": _handle_window,
}


def dispatch_action(ctx, handlers, item, cfg):
    """Looks up handlers.get(item.target_kind) and runs it, returning
    (should_dismiss, pending) straight from the handler, or (False,
    None) if no handler is registered. handlers is passed in explicitly
    (main.py passes render.ACTION_HANDLERS) so this module stays as
    ignorant of render.py as it is of any specific module.
    """
    handler = handlers.get(item.target_kind)
    if handler is None:
        return False, None
    return handler(ctx, item, cfg)


def handle_pending_confirm(ctx, pending, key, cfg):
    """Resolves a y/n confirm dialog. confirm_yes (or confirm — Enter
    doubles as "yes" here too, confirm_no has no such alternate) runs
    whichever action `pending` describes — branching on
    `"restore_entries" in pending`, not a discriminator field, matching
    how sessions.py/power_menu.py/quick_actions.py build this dict — and
    returns (pending["dismiss_after_confirm"], None). confirm_no
    returns (False, None). Any other key leaves the dialog open
    unchanged: (False, pending). The caller still calls
    provider.dismiss_self() itself when should_dismiss comes back True.
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
