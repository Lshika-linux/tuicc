"""Tests for [wm] workspace_mode/workspace_names — the "manual" escape
hatch alongside GitHub issue #9's own "autodetect" fix (see
wm_config_parser.py/sidebar.py's slot_ids()): for whatever autodetect
genuinely can't see (a dynamically exec-generated binding, no static
declaration anywhere at all), an explicit, user-declared list.

Same "real Config, built from the actual shipped defaults/config.toml,
only USER_CONFIG_PATH/USER_PRESETS_DIR redirected" pattern
test_fresh_install_smoke.py's own _load_packaged_default_config()
already established — a hand-written minimal fixture could silently
drift from what's really shipped.
"""

import shutil

from tuicc import config as config_module


def _load_config_with_wm_overrides(tmp_path, monkeypatch, replacement_lines):
    """The packaged default config, with its live `workspace_mode =
    "autodetect"` line replaced wholesale by replacement_lines — not
    spliced in alongside it, which would leave two `workspace_mode =`
    lines in the same [wm] table (a real TOML "cannot overwrite a
    value" error, live-caught writing this same test file).
    """
    text = config_module.DEFAULT_CONFIG_PATH.read_text()
    marker = 'workspace_mode = "autodetect"'
    assert marker in text  # fails loudly if the packaged default ever changes this line
    text = text.replace(marker, replacement_lines, 1)

    user_config = tmp_path / "config.toml"
    user_config.write_text(text)
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", user_config)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", tmp_path / "presets")
    return config_module.load_config()


def test_default_is_autodetect_with_no_manual_names(tmp_path, monkeypatch):
    # The packaged default's own live workspace_mode = "autodetect"
    # line, unchanged.
    cfg = _load_config_with_wm_overrides(tmp_path, monkeypatch, 'workspace_mode = "autodetect"')
    assert cfg.workspace_mode == "autodetect"
    assert cfg.workspace_names is None


def test_missing_workspace_mode_key_also_defaults_to_autodetect(tmp_path, monkeypatch):
    # .get()-with-default, same treatment self_app_id/fullscreen_only
    # get — an existing config.toml that predates this key entirely
    # must not crash on load.
    cfg = _load_config_with_wm_overrides(tmp_path, monkeypatch, "")
    assert cfg.workspace_mode == "autodetect"
    assert cfg.workspace_names is None


def test_manual_mode_with_names_loads_fine(tmp_path, monkeypatch):
    cfg = _load_config_with_wm_overrides(
        tmp_path, monkeypatch,
        'workspace_mode = "manual"\nworkspace_names = ["1", "2", "chat"]\n',
    )
    assert cfg.workspace_mode == "manual"
    assert cfg.workspace_names == ["1", "2", "chat"]


def test_manual_mode_without_names_raises(tmp_path, monkeypatch):
    import pytest
    with pytest.raises(ValueError, match="workspace_names"):
        _load_config_with_wm_overrides(tmp_path, monkeypatch, 'workspace_mode = "manual"\n')


def test_manual_mode_with_empty_names_list_raises(tmp_path, monkeypatch):
    import pytest
    with pytest.raises(ValueError, match="workspace_names"):
        _load_config_with_wm_overrides(
            tmp_path, monkeypatch,
            'workspace_mode = "manual"\nworkspace_names = []\n',
        )


def test_invalid_workspace_mode_raises(tmp_path, monkeypatch):
    import pytest
    with pytest.raises(ValueError, match="workspace_mode"):
        _load_config_with_wm_overrides(tmp_path, monkeypatch, 'workspace_mode = "bogus"\n')
