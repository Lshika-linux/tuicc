"""Shared helpers for tests that need a real, fully-populated Config
built from the actual packaged defaults/config.toml — not a hand-
written test fixture that could silently drift from what's really
shipped. Originally lived only in test_fresh_install_smoke.py; pulled
out once test_draw_smoke.py needed the exact same thing, so the two
stay identical instead of drifting apart (see test_draw_smoke.py's own
docstring for why it needs this too).
"""

import shutil

from tuicc import config as config_module


class _AllDomainsExceptWeather:
    """domain_names()'s own return value only ever gets used via `in` —
    see rwb.py's own "weather" not in ctx.status.domain_names() check —
    so a real set is never needed, just correct membership."""
    def __contains__(self, name):
        return name != "weather"


class FreshInstallStatus:
    """The full CombinedStatus public interface (see that class's own
    docstring for the real shape) — modules call more than just get()/
    get_error() (is_pending() for in-flight actions, get_action_error()
    for a failed one, has_pending() for the global blink), and any of
    them raising AttributeError here would be just as much a false
    "this doesn't crash" pass as leaving them out entirely.

    Simulates ctx.status as a genuine fresh install's first frame
    actually looks: every domain REGISTERED but not yet polled (get()/
    get_error() return None, the real "registered, nothing back yet"
    state — not the empty-list "genuinely nothing there" case), EXCEPT
    "weather", which raises KeyError exactly like the real StatusWorker
    does for a domain nothing ever registered.
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


def load_packaged_default_config(tmp_path, monkeypatch):
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
