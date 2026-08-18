"""End-to-end smoke test: does the packaged default config actually run
without crashing, straight off a fresh clone — no manual edits, no
live WM, no curses?

Exists because nothing else in this suite would have caught the real
bug that prompted it: modules/rwb.py's nav_items() called
ctx.status.get("weather") unconditionally, but "weather" is the one
domain app_setup.py registers CONDITIONALLY (only when [weather] is
actually configured — the packaged default ships it commented out).
Every component involved had its own passing tests (status_worker.py's
own get()/get_error() tests, this module's, connectivity's...) — none
of them exercised the actual INTEGRATION: a real Config built from the
real packaged files, fed into a real collect_nav_items() call. That
seam is exactly what crashed, and exactly what component-level tests,
by construction, can't see.

Deliberately does NOT touch draw()/curses — this project's own
established convention leaves draw() untested (needs a real
curses.initscr() session; see theme_setup.py's/app_setup.py's own
"not unit-tested" docstrings for the standing reasoning) and this test
doesn't need to break that to catch the bug that actually happened:
the crash was in nav_items()/collect_nav_items(), never draw() itself.

_FreshInstallStatus below simulates ctx.status as a genuine fresh
install's first frame actually looks: every domain REGISTERED but not
yet polled (get()/get_error() return None, the real "registered,
nothing back yet" state — not the empty-list "genuinely nothing
there" case), EXCEPT "weather", which raises KeyError exactly like the
real StatusWorker does for a domain nothing ever registered. Not a
real StatusWorker/CombinedStatus with a hand-copied domain list — that
would need updating every time app_setup.py adds a new always-on
domain, and silently under-test if someone forgot. This fake needs no
such list: "everything exists except weather" doesn't require knowing
what "everything" actually is.
"""

import shutil

from tuicc import config as config_module
from tuicc.layout_engine import compute_boxes
from tuicc.model import WMState
from tuicc.context import RenderContext
from tuicc.render import collect_nav_items, PREVIEW_RENDERERS


class _AllDomainsExceptWeather:
    """domain_names()'s own return value only ever gets used via `in` —
    see rwb.py's own "weather" not in ctx.status.domain_names() check —
    so a real set is never needed, just correct membership."""
    def __contains__(self, name):
        return name != "weather"


class _FreshInstallStatus:
    """The full CombinedStatus public interface (see that class's own
    docstring for the real shape) — modules call more than just get()/
    get_error() (is_pending() for in-flight actions, get_action_error()
    for a failed one, has_pending() for the global blink), and any of
    them raising AttributeError here would be just as much a false
    "this doesn't crash" pass as leaving them out entirely.
    """
    def domain_names(self):
        return _AllDomainsExceptWeather()

    def get(self, name):
        if name == "weather":
            raise KeyError(name)  # matches StatusWorker's own real behavior for a name nothing ever registered
        return None  # registered, not polled yet — the real state on a fresh first frame

    def get_error(self, name):
        if name == "weather":
            raise KeyError(name)
        return None

    def get_action_error(self, name):
        if name == "weather":
            raise KeyError(name)
        return None

    def get_action_error_for(self, name, key):
        if name == "weather":
            raise KeyError(name)
        return None

    def is_pending(self, name, key):
        if name == "weather":
            raise KeyError(name)
        return False

    def has_pending(self):
        return False


def _load_packaged_default_config(tmp_path, monkeypatch):
    """A real Config, built from the ACTUAL shipped defaults/config.toml
    and packaged preset — not a hand-written test fixture that could
    silently drift from what's really shipped. Only USER_CONFIG_PATH/
    USER_PRESETS_DIR are redirected into tmp_path (so this never reads
    or writes ~/.config/tuicc); PACKAGED_PRESETS_DIR/DEFAULT_CONFIG_PATH
    stay pointed at the real package files, same as a genuine fresh
    install would use them.
    """
    user_config = tmp_path / "config.toml"
    shutil.copy(config_module.DEFAULT_CONFIG_PATH, user_config)
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", user_config)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", tmp_path / "presets")
    return config_module.load_config()


def _collect(cfg, term_width, term_height):
    boxes = compute_boxes(cfg.layout, term_width=term_width, term_height=term_height)
    ctx = RenderContext(
        state=WMState(),
        selected_id=None,
        focus_id=None,
        theme=cfg.theme,
        config=cfg,
        status=_FreshInstallStatus(),
        preview_renderers=PREVIEW_RENDERERS,
    )
    return collect_nav_items(cfg.layout, boxes, ctx)


def test_fresh_install_nav_items_collection_does_not_crash(tmp_path, monkeypatch):
    cfg = _load_packaged_default_config(tmp_path, monkeypatch)

    # The assertion IS that this doesn't raise — collect_nav_items()
    # ran through every module in the packaged default layout
    # (sidebar, launcher, connectivity, control, media, sysmon, rwb,
    # bars, sessions, power_menu...) against a fresh-install-shaped
    # status object.
    items = _collect(cfg, term_width=200, term_height=55)
    assert isinstance(items, list)


def test_fresh_install_nav_items_collection_does_not_crash_at_a_small_terminal_size(tmp_path, monkeypatch):
    # Same scenario, a genuinely small grid — some modules' own
    # windowing/scroll logic only kicks in below a size threshold, a
    # different code path than the large-grid case above.
    cfg = _load_packaged_default_config(tmp_path, monkeypatch)

    items = _collect(cfg, term_width=80, term_height=24)
    assert isinstance(items, list)
