"""Tests for theme_presets.py — pure, disk-free: BUILTIN_THEME_PRESETS'
own shape, preset_cycle_list()'s ordering, and next_preset()'s "where
am I now" logic.
"""

from tuicc.theme import THEME_ROLES
from tuicc.theme_presets import BUILTIN_THEME_PRESETS, preset_cycle_list, next_preset


def test_every_builtin_preset_defines_every_role():
    for name, values in BUILTIN_THEME_PRESETS.items():
        assert set(values.keys()) == set(THEME_ROLES), name


def test_default_is_the_first_builtin():
    # F4 from a freshly-installed config should visibly move to the
    # SECOND entry, not silently stay put — "Default" being first
    # (not, say, alphabetically sorted) is load-bearing for that.
    assert next(iter(BUILTIN_THEME_PRESETS)) == "Default"


def test_preset_cycle_list_builtins_come_before_user_presets():
    cycle_list = preset_cycle_list({3: {"accent": "red"}, 1: {"accent": "blue"}})
    names = [name for name, _values in cycle_list]
    assert names[: len(BUILTIN_THEME_PRESETS)] == list(BUILTIN_THEME_PRESETS.keys())
    assert names[len(BUILTIN_THEME_PRESETS):] == ["Preset 1", "Preset 3"]  # ascending, not insertion order


def test_preset_cycle_list_with_no_user_presets_is_just_builtins():
    cycle_list = preset_cycle_list({})
    assert len(cycle_list) == len(BUILTIN_THEME_PRESETS)


def test_next_preset_from_default_is_the_second_builtin():
    cycle_list = preset_cycle_list({})
    name, _values = next_preset(BUILTIN_THEME_PRESETS["Default"], cycle_list)
    assert name == "Dracula"


def test_next_preset_wraps_from_the_last_entry_back_to_the_first():
    cycle_list = preset_cycle_list({})
    last_name, last_values = cycle_list[-1]
    name, _values = next_preset(last_values, cycle_list)
    assert name == "Default"


def test_next_preset_unrecognized_current_values_starts_from_the_first():
    # A hand-tweaked color (or a role predating this feature) doesn't
    # match any known preset — no ambiguous "closest" guess, just start
    # over from entry 0, same discipline as every other no-silent-
    # failure spot in this codebase.
    cycle_list = preset_cycle_list({})
    current = {role: "magenta" for role in THEME_ROLES}
    name, _values = next_preset(current, cycle_list)
    assert name == list(BUILTIN_THEME_PRESETS.keys())[0]


def test_next_preset_ignores_extra_keys_not_in_theme_roles():
    # current_values can carry stray keys get_raw_theme_values() might
    # return (e.g. a config.toml typo'd role) — comparison is scoped to
    # THEME_ROLES only, so an extra key must not block a real match.
    cycle_list = preset_cycle_list({})
    current = dict(BUILTIN_THEME_PRESETS["Default"])
    current["not_a_real_role"] = "whatever"
    name, _values = next_preset(current, cycle_list)
    assert name == "Dracula"


def test_next_preset_reaches_a_user_preset_after_cycling_past_every_builtin():
    user_presets = {1: {role: "green" for role in THEME_ROLES}}
    cycle_list = preset_cycle_list(user_presets)
    last_builtin_name, last_builtin_values = list(BUILTIN_THEME_PRESETS.items())[-1]
    name, values = next_preset(last_builtin_values, cycle_list)
    assert name == "Preset 1"
    assert values == user_presets[1]


def test_next_preset_empty_cycle_list_returns_none():
    assert next_preset({}, []) is None
