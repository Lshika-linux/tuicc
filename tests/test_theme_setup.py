"""Tests for theme_setup.py's reassign_theme_pairs()/assign_control_toggle_pairs()
— specifically that every role's pair carries the SAME resolved
"background" color on its own background component, not -1.
curses.init_pair()/curses.start_color() need a real curses.initscr()
session to work at all (same reasoning app_setup.build_app() and this
whole file's own docstring already document as untestable-by-design) —
monkeypatching curses.init_pair/curses.color_pair with plain recorders
sidesteps that entirely, real regression coverage without a live screen.
"""

import tuicc.theme_setup as theme_setup_module
from tuicc.theme_setup import reassign_theme_pairs, assign_control_toggle_pairs


def _patch_curses(monkeypatch):
    calls = []

    def fake_init_pair(pair_number, fg, bg):
        calls.append((pair_number, fg, bg))

    def fake_color_pair(pair_number):
        return pair_number  # identity, just needs to be distinguishable

    monkeypatch.setattr(theme_setup_module.curses, "init_pair", fake_init_pair)
    monkeypatch.setattr(theme_setup_module.curses, "color_pair", fake_color_pair)
    return calls


def test_non_background_roles_default_to_bg_minus_one_when_background_is_inherit(monkeypatch):
    # No "background" key at all — same as background="inherit" resolving
    # to -1 — every role's own pair stays byte-for-byte what it always
    # was: color on fg, -1 on bg. Zero regression for Default.
    calls = _patch_curses(monkeypatch)

    reassign_theme_pairs({"text": 7, "accent": 6})

    assert (1, 7, -1) in calls
    assert (2, 6, -1) in calls


def test_background_role_puts_color_on_the_background_component(monkeypatch):
    calls = _patch_curses(monkeypatch)

    reassign_theme_pairs({"background": 17, "text": 7})

    assert (1, -1, 17) in calls  # background: fg=-1, bg=17


def test_every_other_role_also_gets_the_real_background_on_its_own_pair(monkeypatch):
    # The actual bug, found live via a raw pty capture: text drawn with
    # a pair whose OWN bg stayed -1 rendered as the TERMINAL's raw
    # native default (SGR 49) — a visibly mismatched dark box around
    # every string of text — not "inherit whatever bkgd() painted",
    # which -1 does NOT mean for an explicitly-assigned pair (only pair
    # 0/no-explicit-attribute does, see apply_background()'s own
    # docstring). Every role needs the SAME real background color baked
    # into its own pair for text to blend into the fill.
    calls = _patch_curses(monkeypatch)

    reassign_theme_pairs({"background": 17, "text": 7, "accent": 6})

    assert (2, 7, 17) in calls  # text: fg=7, bg=17 — matches the fill now
    assert (3, 6, 17) in calls  # accent: same


def test_pair_numbers_follow_dict_iteration_order(monkeypatch):
    calls = _patch_curses(monkeypatch)

    reassign_theme_pairs({"border": 4, "background": 17, "urgent": 1})

    numbers = [c[0] for c in calls]
    assert numbers == [1, 2, 3]


def test_returns_a_role_to_pair_mapping(monkeypatch):
    _patch_curses(monkeypatch)

    pairs = reassign_theme_pairs({"background": 17, "text": 7})

    assert pairs == {"background": 1, "text": 2}


def test_missing_background_role_defaults_bg_component_to_minus_one(monkeypatch):
    # No "background" key at all (a config predating the role) — every
    # other role is unaffected, nothing crashes.
    calls = _patch_curses(monkeypatch)

    reassign_theme_pairs({"text": 7})

    assert calls == [(1, 7, -1)]


def test_apply_background_uses_the_background_pair(monkeypatch):
    from tuicc.theme_setup import apply_background

    monkeypatch.setattr(theme_setup_module.curses, "color_pair", lambda n: n)
    calls = []

    class FakeStdscr:
        def bkgd(self, ch, attr):
            calls.append((ch, attr))

    apply_background(FakeStdscr(), {"background": 42, "text": 7})

    assert calls == [(" ", 42)]


def test_apply_background_falls_back_to_pair_zero_when_missing(monkeypatch):
    from tuicc.theme_setup import apply_background

    monkeypatch.setattr(theme_setup_module.curses, "color_pair", lambda n: n)
    calls = []

    class FakeStdscr:
        def bkgd(self, ch, attr):
            calls.append((ch, attr))

    apply_background(FakeStdscr(), {"text": 7})

    assert calls == [(" ", 0)]


# ---------- assign_control_toggle_pairs ----------

def test_control_toggle_pairs_default_bg_stays_minus_one(monkeypatch):
    calls = _patch_curses(monkeypatch)
    toggles = [{"states": [{"color": 4}, {"color": None}]}]

    assign_control_toggle_pairs(toggles, start_pair_number=10)

    assert calls == [(10, 4, -1)]  # the None-color state is skipped entirely


def test_control_toggle_pairs_use_the_given_background_color(monkeypatch):
    # Same fix as reassign_theme_pairs' own roles — a toggle state's
    # color is drawn as real text, needs the same real bg to blend in.
    calls = _patch_curses(monkeypatch)
    toggles = [{"states": [{"color": 4}]}]

    assign_control_toggle_pairs(toggles, start_pair_number=10, bg_color=17)

    assert calls == [(10, 4, 17)]


def test_control_toggle_pairs_number_multiple_states_sequentially(monkeypatch):
    calls = _patch_curses(monkeypatch)
    toggles = [
        {"states": [{"color": 1}, {"color": 2}]},
        {"states": [{"color": 3}]},
    ]

    pairs = assign_control_toggle_pairs(toggles, start_pair_number=5)

    numbers = [c[0] for c in calls]
    assert numbers == [5, 6, 7]
    assert pairs == {(0, 0): 5, (0, 1): 6, (1, 0): 7}
