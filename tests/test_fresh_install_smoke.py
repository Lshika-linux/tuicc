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

Deliberately does NOT touch draw()/curses here — that's
test_draw_smoke.py's job now (a real stdscr turned out unnecessary,
see that file's own docstring for why "draw() needs curses" was never
actually true). This file stays scoped to nav_items()/collect_nav_
items(), the seam that actually crashed.
"""

from tuicc.layout_engine import compute_boxes
from tuicc.model import WMState
from tuicc.context import RenderContext
from tuicc.render import collect_nav_items, PREVIEW_RENDERERS

from _fresh_install_helpers import FreshInstallStatus, load_packaged_default_config


def _collect(cfg, term_width, term_height):
    boxes = compute_boxes(cfg.layout, term_width=term_width, term_height=term_height)
    ctx = RenderContext(
        state=WMState(),
        selected_id=None,
        focus_id=None,
        theme=cfg.theme,
        config=cfg,
        status=FreshInstallStatus(),
        preview_renderers=PREVIEW_RENDERERS,
    )
    return collect_nav_items(cfg.layout, boxes, ctx)


def test_fresh_install_nav_items_collection_does_not_crash(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)

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
    cfg = load_packaged_default_config(tmp_path, monkeypatch)

    items = _collect(cfg, term_width=80, term_height=24)
    assert isinstance(items, list)
