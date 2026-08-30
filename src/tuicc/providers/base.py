"""
The contract every WM provider must fulfil.

Core code and modules only ever talk to this interface
 — never to any WM-specific tool directly.

 """

from abc import ABC, abstractmethod

from tuicc.model import WMState

class Provider(ABC):
    @abstractmethod
    def get_state(self) -> WMState:
        """Return the current window-manager state as a WMState."""
        raise NotImplementedError

    @abstractmethod
    def focus_region(self, region_id: str) -> None:
        """Switch the WM's focus to the given region (e.g. workspace)."""
        raise NotImplementedError

    @abstractmethod
    def focus_window(self, window_id: str) -> None:
        """Switch the WM's focus to the given window."""
        raise NotImplementedError

    @abstractmethod
    def move_window_to_region(self, window_id: str, region_id: str) -> None:
        """Move the given window to the given region, without changing
        which region is currently visible.
        """
        raise NotImplementedError

    @abstractmethod
    def close_window(self, window_id: str) -> None:
        """Close the given window. Required, not optional like
        mark_self/resolve_pid/set_floating_geometry below — every WM
        worth supporting can close a window; there's no meaningful
        degraded case to fall back to the way there is for marks or
        floating-window geometry.
        """
        raise NotImplementedError

    def mark_self(self, app_id: str | None = None) -> None:
        """Mark tuicc's own window (called once at startup) so get_state()
        excludes it from the Windows it reports. app_id, if given, marks
        by WM criteria instead of "whatever's focused at call time" — see
        CLAUDE/NOTES/known-limitations.md#mark-self-focus-race for the
        race that fallback has, and design-decisions.md
        #optional-provider-methods for why this defaults to a no-op.
        """
        pass

    def cleanup_stale_self_marks(self) -> None:
        """Strips a _tuicc_self_<pid> mark sitting on the WRONG window —
        the concrete, checkable damage mark_self()'s known focus-race
        fallback can leave behind (see that method's own docstring): an
        earlier tuicc launch raced against whatever was focused at that
        moment and marked an unrelated window instead of its own,
        leaving that window silently excluded from get_state() forever
        after (found live, confirmed: a stray mark from an earlier test
        launch hid a real, unrelated app from tuicc's own sidebar/
        preview with no error anywhere). Called once at startup, BEFORE
        mark_self() marks the current instance's own window, so a
        leftover bad mark from a previous run never gets the chance to
        keep hiding an unrelated window this session. Optional, default
        no-op: sway/i3 both implement it via the same mark mechanism
        mark_self()/dismiss_self() already use.
        """
        pass

    def dismiss_self(self) -> None:
        """Hide tuicc's own window without ending the process — see
        CLAUDE/NOTES/design-decisions.md#dismiss-vs-quit. Optional,
        default no-op: sway/i3 implement it via the same mark
        mark_self() applies, immune to that method's fallback race.
        """
        pass

    def focus_self(self, fullscreen: bool = False, force_relayout: bool = False) -> None:
        """Reclaim keyboard focus for tuicc's own window — called by
        pending_moves.process() right after moving a spawned/restored
        window into its target region. Severe if unimplemented: see
        CLAUDE/NOTES/wm-quirks.md#focus-on-map-stealing. fullscreen/
        force_relayout re-assert fullscreen state and force a layout
        pass respectively — see #fullscreen-drop-on-map and
        #fullscreen-suppresses-layout for why. Optional, default no-op.
        """
        pass

    def no_focus_next_window(self, pid: int) -> None:
        """Ask the WM not to auto-focus the next window from pid, called
        right after spawn_detached() returns. A root-cause complement to
        focus_self(), not a replacement — see CLAUDE/NOTES/wm-quirks.md
        #no-focus-pid-criteria for why pid (not class/app_id), and
        CLAUDE/NOTES/known-limitations.md#fork-exec-pid-mismatch for
        when it silently no-ops. Optional, default no-op.
        """
        pass

    def wm_config(self):
        """Best-effort WmConfigInfo (wm_config_parser.py) parsed from the
        WM's own config text — see that module's docstring for the full
        reasoning (GitHub issue #9) and its real, documented limits.
        Called once at startup (app_setup.py), not per-frame — this
        doesn't change except on a WM config reload, unlike get_state();
        see RenderContext.wm_config's own docstring. Optional, default
        None: a WM whose IPC protocol has no config-introspection
        message at all just gets no extra defaults — modules already
        union this against whatever regions genuinely exist, so None
        degrades to exactly today's behavior, not a crash.
        """
        return None

    def resolve_pid(self, window_id: str) -> int | None:
        """Best-effort process id for the window's owning process (e.g.
        to relaunch it later — see session.py). Not part of the
        per-frame get_state() path. Optional, default no-op returning
        None: only needed for a WM whose get_state() doesn't already put
        pid on Window (sway does; i3.py resolves via X11 _NET_WM_PID).
        """
        return None

    def set_floating_geometry(self, window_id: str, region_id: str, rect: tuple[float, float, float, float]) -> None:
        """Move window_id into floating mode and position/resize it to
        rect (normalized 0..1, relative to region_id's own dimensions —
        looked up fresh, not from when the session was saved). Used by
        session restore. Optional, default no-op: sway/i3 both
        implement it; a WM with no floating concept just leaves the
        restored window at the WM's own default placement.
        """
        pass

    def copy_to_clipboard(self, text: str) -> bool:
        """Copy text to the system clipboard — sysmon.py's diagnostics
        row uses this so a real journal/kernel error dump can be
        pasted elsewhere (a search engine, a bug report, a support
        forum) instead of only ever being readable inside tuicc's own
        narrow preview box. Genuinely tied to the DISPLAY SERVER
        (Wayland vs X11), not the WM itself, but that maps 1:1 onto
        this codebase's own sway/i3 provider split (sway = Wayland,
        i3 = X11), so it lives here rather than as a separate
        registry the way audio/connectivity's real multi-backend
        cases need. Optional, default no-op returning False: sway/i3
        both implement it via an external CLI tool (wl-copy / xclip)
        that might not be installed — same missing-binary-is-not-an-
        error tolerance as brightness.py/cava's own external tools.
        Returns whether it actually worked, so a caller can choose to
        surface a failure rather than silently claim success.
        """
        return False
