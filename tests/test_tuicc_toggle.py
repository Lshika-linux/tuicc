"""Tests for contrib/{sway,i3}/tuicc_toggle.py's pure logic —
find_tuicc()'s tree-walk and _fullscreen_only()'s config read. The
rest of each script (main(), _run_for_both_criteria()) is real
subprocess/swaymsg/i3-msg calls, same "fake the I/O boundary, don't
reach past it" line every other subprocess-touching function in this
codebase draws — left untested here, same as spawn_detached()'s own
callers.

The two scripts are near-identical by design (i3's own docstring:
"Same idea as contrib/sway/tuicc_toggle.py") — shared behavior is
tested once via parametrize, not duplicated file-for-file. Importable
as contrib.sway.tuicc_toggle / contrib.i3.tuicc_toggle via Python's
implicit namespace packages, now that pytest.ini's pythonpath includes
"." (added for tests/test_main.py's `import main`).
"""

import pytest

import contrib.sway.tuicc_toggle as sway_toggle
import contrib.i3.tuicc_toggle as i3_toggle

TOGGLES = [sway_toggle, i3_toggle]
TOGGLE_IDS = ["sway", "i3"]


def _workspace(name, nodes=None, floating_nodes=None):
    return {"type": "workspace", "name": name, "nodes": nodes or [], "floating_nodes": floating_nodes or []}


def _window(app_id=None, window_class=None, nodes=None, floating_nodes=None):
    node = {"nodes": nodes or [], "floating_nodes": floating_nodes or []}
    if app_id is not None:
        node["app_id"] = app_id
    if window_class is not None:
        node["window_properties"] = {"class": window_class}
    return node


# ---------- find_tuicc(): shared behavior ----------

@pytest.mark.parametrize("toggle", TOGGLES, ids=TOGGLE_IDS)
def test_find_tuicc_returns_none_when_not_present(toggle):
    tree = _workspace("1", nodes=[_window(window_class="firefox")])
    assert toggle.find_tuicc(tree) is None


@pytest.mark.parametrize("toggle", TOGGLES, ids=TOGGLE_IDS)
def test_find_tuicc_matches_by_window_class(toggle):
    target = _window(window_class=toggle.APP_ID)
    tree = _workspace("1", nodes=[target])

    result = toggle.find_tuicc(tree)

    assert result == (target, "1")


@pytest.mark.parametrize("toggle", TOGGLES, ids=TOGGLE_IDS)
def test_find_tuicc_found_nested_under_floating_nodes(toggle):
    target = _window(window_class=toggle.APP_ID)
    tree = _workspace("2", floating_nodes=[target])

    result = toggle.find_tuicc(tree)

    assert result == (target, "2")


@pytest.mark.parametrize("toggle", TOGGLES, ids=TOGGLE_IDS)
def test_find_tuicc_found_several_levels_deep(toggle):
    target = _window(window_class=toggle.APP_ID)
    inner_split = {"nodes": [target], "floating_nodes": []}
    outer_split = {"nodes": [inner_split], "floating_nodes": []}
    tree = _workspace("3", nodes=[outer_split])

    result = toggle.find_tuicc(tree)

    assert result == (target, "3")


@pytest.mark.parametrize("toggle", TOGGLES, ids=TOGGLE_IDS)
def test_find_tuicc_reports_the_scratchpad_workspace_name(toggle):
    # __i3_scratch is what main()'s own "is it currently shown or just
    # parked in the scratchpad" branch checks workspace_name against —
    # this must come back exactly that string, not something else.
    target = _window(window_class=toggle.APP_ID)
    tree = _workspace("__i3_scratch", nodes=[target])

    result = toggle.find_tuicc(tree)

    assert result == (target, "__i3_scratch")


@pytest.mark.parametrize("toggle", TOGGLES, ids=TOGGLE_IDS)
def test_find_tuicc_root_has_no_workspace_field_yet(toggle):
    # A real tree's root node isn't itself a workspace — workspace_name
    # starts as whatever the caller passed (None, at the real call
    # site) and only updates once a "type": "workspace" node is walked.
    target = _window(window_class=toggle.APP_ID)
    root = {"nodes": [_workspace("4", nodes=[target])], "floating_nodes": []}

    result = toggle.find_tuicc(root)

    assert result == (target, "4")


# ---------- find_tuicc(): the one real sway/i3 divergence ----------

def test_sway_find_tuicc_also_matches_by_app_id():
    target = _window(app_id=sway_toggle.APP_ID)
    tree = _workspace("1", nodes=[target])

    assert sway_toggle.find_tuicc(tree) == (target, "1")


def test_i3_find_tuicc_does_not_match_by_app_id():
    # i3 is X11-only — window_properties.class is the only criteria
    # i3's own for_window rules can match on, so app_id is deliberately
    # never checked here (see the module's own docstring). A window
    # that only carries app_id (no window_properties.class) must be
    # invisible to i3's find_tuicc, unlike sway's.
    target = _window(app_id=i3_toggle.APP_ID)
    tree = _workspace("1", nodes=[target])

    assert i3_toggle.find_tuicc(tree) is None


# ---------- _fullscreen_only() ----------

@pytest.mark.parametrize("toggle", TOGGLES, ids=TOGGLE_IDS)
def test_fullscreen_only_false_when_no_config_file_exists(toggle, tmp_path, monkeypatch):
    monkeypatch.setattr(toggle, "CONFIG_PATH", tmp_path / "config.toml")
    assert toggle._fullscreen_only() is False


@pytest.mark.parametrize("toggle", TOGGLES, ids=TOGGLE_IDS)
def test_fullscreen_only_true_when_configured(toggle, tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text("[wm]\nfullscreen_only = true\n")
    monkeypatch.setattr(toggle, "CONFIG_PATH", config_path)

    assert toggle._fullscreen_only() is True


@pytest.mark.parametrize("toggle", TOGGLES, ids=TOGGLE_IDS)
def test_fullscreen_only_false_when_explicitly_configured_false(toggle, tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text("[wm]\nfullscreen_only = false\n")
    monkeypatch.setattr(toggle, "CONFIG_PATH", config_path)

    assert toggle._fullscreen_only() is False


@pytest.mark.parametrize("toggle", TOGGLES, ids=TOGGLE_IDS)
def test_fullscreen_only_false_when_wm_section_is_missing(toggle, tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text("[theme]\naccent = \"cyan\"\n")
    monkeypatch.setattr(toggle, "CONFIG_PATH", config_path)

    assert toggle._fullscreen_only() is False


@pytest.mark.parametrize("toggle", TOGGLES, ids=TOGGLE_IDS)
def test_fullscreen_only_false_on_malformed_toml(toggle, tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text("this is not [ valid toml")
    monkeypatch.setattr(toggle, "CONFIG_PATH", config_path)

    assert toggle._fullscreen_only() is False
