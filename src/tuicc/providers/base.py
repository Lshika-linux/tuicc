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
        """
        We need to hide tuicc itself from the preview and sidebar..

        Mark tuicc's own window (called once at startup) so get_state()
        can exclude it from the Windows it reports downstream — tuicc
        never lists itself in its own preview/sidebar.

        app_id, if given (from `[wm] self_app_id` in config.toml — the
        documented scratchpad launch uses `kitty --app-id tuicc_scratch`,
        see README's "Summoning tuicc" section), lets an implementation
        mark by WM CRITERIA (match a known, stable window property)
        instead of assuming tuicc's own window is whichever one is
        currently focused at call time — removing the race that
        assumption has when several instances launch back-to-back.
        None (the default, and the only thing a caller with no known
        app_id ever passes) falls back to that original focus-based
        assumption.

        NOT abstract: not every WM has an equivalent concept to sway/i3's
        marks. The default here is a no-op — a provider for a WM without
        one simply won't be able to filter tuicc's own window out, which
        is a known degraded case (tuicc may show up as a window in its
        own preview), not a crash or a required feature.

        Who needs to implement this: sway and i3 both do, via native WM
        marks. Implement it for a new provider if its WM has any
        equivalent "tag a window, filter by tag later" concept; if not,
        leave the no-op default and accept the degraded case above.
        """
        pass

    def dismiss_self(self) -> None:
        """
        Hide tuicc's own window without ending the process — the
        "dismiss" half of tuicc's persistent-process lifecycle model
        (see CLAUDE.md's lifecycle paragraph, and VISION.md section 2).
        main.py calls this instead of exiting wherever a handler used
        to mean "quit" — the process keeps running afterward, warm,
        ready for the next summon. The only real way to end tuicc is
        Ctrl+C.

        NOT abstract, default no-op: a provider for a WM with no hide/
        scratchpad-equivalent concept just can't dismiss this way — the
        window stays on screen, a known degraded case, not a crash,
        same optionality as mark_self()/resolve_pid()/
        set_floating_geometry() above.

        Who needs to implement this: sway and i3 both do, by targeting
        the same mark mark_self() already applies to this instance's
        own window — matching by that mark (not "whatever's focused
        right now") is what makes this immune to the same multi-instance
        timing race mark_self()'s focus-based fallback has.
        """
        pass

    def focus_self(self) -> None:
        """
        Reclaim keyboard focus for tuicc's own window — called by
        main.py's pending_moves loop right after it moves a freshly
        spawned (launcher) or restored (sessions) window into its
        target region.

        NOT implementing this for a WM whose default behavior steals
        focus on window-map makes tuicc's launcher (and session
        restore) NEAR UNUSABLE there: tuicc is a floating window that
        stays visually on top of whatever workspace it's summoned
        into, but most WMs focus a newly-mapped window regardless of
        stacking order — so the moment you spawn something from the
        launcher, your next keystrokes silently go to the new window
        sitting hidden underneath tuicc, even though tuicc still LOOKS
        focused on screen. Without this call, that happens on literally
        the first spawn — the launcher's whole "spawn without losing
        your place" promise (see README) breaks immediately, it isn't
        an edge case.

        NOT abstract, default no-op: only meaningful for a WM that can
        both (a) focus a specific window on demand and (b) actually
        exhibits the focus-stealing behavior above in the first place
        — same optionality as mark_self()/dismiss_self() above, but
        unlike those, the degraded case here is severe enough to call
        out explicitly rather than being a minor cosmetic gap.

        Who needs to implement this: sway and i3 both do, by focusing
        the same mark mark_self() already applies to this instance's
        own window — same mark, same reasoning as dismiss_self() for
        why matching by mark (not by some other lookup) is safe here.
        """
        pass

    def resolve_pid(self, window_id: str) -> int | None:
        """
        Best-effort process id for the window's owning process, for
        code that needs to relaunch it later (reading /proc/<pid>/cmdline
        to capture the exact command a window was started with — see
        session.py) — NOT part of the per-frame get_state() path, only
        called on demand for a handful of windows at a time, since it's
        allowed to be more expensive than get_state() needs to be.

        NOT abstract, default no-op returning None. Who needs to
        implement this: only a provider whose WM doesn't already put
        pid on Window via get_state(). sway does (its IPC tree exposes
        pid directly, see model.py's Window.pid docstring) — don't
        override this for sway-like providers, it's redundant. i3
        does not, so i3.py overrides this to resolve the window's X11
        id (leaf.window, not leaf.id — see i3's IPC docs) to a pid via
        the standard _NET_WM_PID EWMH property. A future provider for
        a WM with neither get_state()-level pid nor an X11 fallback
        available should just leave this as the no-op default — code
        calling it already treats None as "couldn't find out", not an
        error.
        """
        return None

    def set_floating_geometry(self, window_id: str, region_id: str, rect: tuple[float, float, float, float]) -> None:
        """
        Move window_id into floating mode and position/resize it to
        rect (normalized 0..1, same space as Window.rect — relative to
        region_id's own dimensions) — used by session restore to put a
        saved floating window back at its relative position/size, not
        just on the right workspace.

        Deliberately NOT given region_id's absolute pixel dimensions —
        those don't exist anywhere outside a provider's own raw WM
        tree (get_state() never exposes them, on purpose, so the rest
        of tuicc stays resolution-agnostic — see model.py's Region).
        An implementation looks region_id's CURRENT absolute rect up
        itself and converts against that, not whatever it was when the
        session was saved — a different (or reconfigured) monitor
        still gets a sane result instead of an off-screen window.

        NOT abstract, default no-op. Who needs to implement this: sway
        and i3 both do (same floating enable / resize set / move
        position command syntax). A provider for a WM with no
        floating-window concept, or no equivalent commands, should
        leave the no-op default — the restored window then just lands
        wherever the WM naturally places a new window, a known
        degraded case, not a crash.
        """
        pass
