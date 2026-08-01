"""Built-in handlers for target_kinds not owned by any specific module.

region/window aren't module-specific — any module reporting a region
or window item expects the same underlying action (focus_region /
focus_window), so these live here rather than in a module file.
Wifi/bluetooth toggle logic, by contrast, IS module-specific (only
connectivity.py knows what "toggle" means for that data), so those
handlers self-register from connectivity.py instead — same pattern
quick_actions.py uses for its own TARGET_KIND.

Handler signature: (ctx, item, cfg) -> (should_exit, pending). ctx is
an ActionContext bundling the WM provider and the connectivity
worker. should_exit=True means tuicc exits after this runs. pending,
if not None, becomes the new pending_confirm value.
"""

import shlex
import subprocess
from dataclasses import dataclass


@dataclass
class ActionContext:
    provider: object
    connectivity: object


def spawn_detached(cmd, shell_true=False):
    """Run cmd as a detached background process that survives tuicc
    exiting right after this call.

    shell_true=True runs cmd through the shell — needed for real shell
    syntax (pipes, ;, &&, $VARS). Off by default: split into plain
    argv and run directly, no shell involved, so no part of cmd is
    ever interpreted as shell syntax. Shared by main.py (launcher and
    confirm-dialog spawns) and quick_actions.py/power_menu.py's
    immediate (non-confirm) actions, so there's exactly one place that
    decides how a command string becomes a process.

    Returns the spawned process's pid — lets a caller match it against
    Window.pid later (see pending_moves.py), on providers that expose
    one. The pid is only ever exact for the shell_true=False path: with
    shell_true=True the pid belongs to the shell, not necessarily the
    GUI process it eventually execs.
    """
    popen_cmd = cmd if shell_true else shlex.split(cmd)
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
