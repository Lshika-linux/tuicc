"""Tests for modules/bars.py's pure logic — _readout/_vol_spec/_bri_spec/
_bat_spec/_collect_bars/_quantize_percent/_compact_fallback_line. draw()
itself needs a real curses screen and is left untested here, same split
every other module in this codebase uses (see test_control_module.py/
test_media_module.py's own docstrings).
"""

from types import SimpleNamespace

from tuicc.audio.model import AudioSink
from tuicc.modules.bars import (
    BarSpec,
    _bat_spec,
    _bri_spec,
    _collect_bars,
    _compact_fallback_line,
    _quantize_percent,
    _readout,
    _vol_spec,
)


# ---------- _readout ----------

def test_readout_plain_percent():
    assert _readout(42) == "42%"


def test_readout_max_at_100():
    assert _readout(100) == "MAX"


def test_readout_min_at_0():
    assert _readout(0) == "MIN"


def test_readout_none_is_dashes():
    assert _readout(None) == "---"


def test_readout_special_overrides_percent():
    assert _readout(50, special="MUT") == "MUT"


def test_readout_clamps_out_of_range_percent():
    assert _readout(150) == "MAX"
    assert _readout(-10) == "MIN"


# ---------- _vol_spec ----------

def _sink(volume, muted=False, is_default=True, id="s1"):
    return AudioSink(id=id, name="Speakers", volume=volume, muted=muted, is_default=is_default)


def test_vol_spec_poll_error_is_urgent():
    spec = _vol_spec(None, "wpctl not found")
    assert spec.percent is None
    assert spec.urgent is True
    assert spec.readout == "---"


def test_vol_spec_no_error_but_none_is_not_urgent():
    # e.g. right after startup, before the first poll has completed —
    # same "unknown, not a claim of failure" treatment as the rest of
    # this codebase
    spec = _vol_spec(None, None)
    assert spec.urgent is False


def test_vol_spec_empty_sink_list_is_not_urgent():
    spec = _vol_spec([], None)
    assert spec.percent is None
    assert spec.urgent is False
    assert spec.readout == "---"


def test_vol_spec_no_default_sink_is_not_urgent():
    sinks = [_sink(50, is_default=False)]
    spec = _vol_spec(sinks, None)
    assert spec.percent is None
    assert spec.urgent is False


def test_vol_spec_picks_the_default_sink():
    sinks = [_sink(30, is_default=False, id="a"), _sink(70, is_default=True, id="b")]
    spec = _vol_spec(sinks, None)
    assert spec.percent == 70
    assert spec.readout == "70%"


def test_vol_spec_muted_keeps_real_percent_but_shows_mut_and_dims():
    spec = _vol_spec([_sink(55, muted=True)], None)
    assert spec.percent == 55  # real level preserved — what it returns to on unmute
    assert spec.readout == "MUT"
    assert spec.dim is True
    assert spec.urgent is False


def test_vol_spec_label_is_vol():
    assert _vol_spec([], None).label == "VOL"


# ---------- _bri_spec ----------

def test_bri_spec_normal_value():
    spec = _bri_spec(80, None)
    assert spec.percent == 80
    assert spec.readout == "80%"
    assert spec.urgent is False


def test_bri_spec_poll_error_is_urgent():
    spec = _bri_spec(None, "brightnessctl not found")
    assert spec.urgent is True
    assert spec.percent is None


def test_bri_spec_none_without_error_is_not_urgent():
    assert _bri_spec(None, None).urgent is False


# ---------- _bat_spec ----------

def test_bat_spec_no_battery_hardware_returns_none():
    # data=None AND error=None -> genuinely no battery on this machine —
    # draw() must omit the bar entirely, not show a permanently-broken dash
    assert _bat_spec(None, None) is None


def test_bat_spec_poll_error_shows_urgent_bar():
    spec = _bat_spec(None, "permission denied")
    assert spec is not None
    assert spec.urgent is True
    assert spec.percent is None


def test_bat_spec_charging_shows_chg_readout():
    spec = _bat_spec({"percent": 42, "status": "Charging"}, None)
    assert spec.percent == 42  # real level preserved, same as VOL-muted
    assert spec.readout == "CHG"
    assert spec.urgent is False
    assert spec.charging is True  # drives the fill's own accent-color glow, see draw()


def test_bat_spec_discharging_shows_plain_percent():
    spec = _bat_spec({"percent": 88, "status": "Discharging"}, None)
    assert spec.readout == "88%"
    assert spec.charging is False
    assert spec.plugged is False


def test_bat_spec_plugged_but_not_charging_keeps_real_percent_and_sets_plugged():
    # A charger IS connected (ac_online=True) but this pack isn't
    # accepting charge right now — e.g. a ThinkPad charge_control
    # threshold pausing mid-range. Must NOT show a "FUL"-style label —
    # the pack isn't necessarily full, could be sitting at any percent.
    spec = _bat_spec({"percent": 45, "status": "Not charging", "ac_online": True}, None)
    assert spec.readout == "45%"  # real percent, not overridden
    assert spec.charging is False
    assert spec.plugged is True
    assert spec.label == "AC"  # swapped from "BAT" — the only visible cue, see BarSpec.plugged's own docstring


def test_bat_spec_no_charger_and_not_charging_is_plain():
    spec = _bat_spec({"percent": 45, "status": "Not charging", "ac_online": False}, None)
    assert spec.plugged is False
    assert spec.charging is False
    assert spec.label == "BAT"


def test_bat_spec_missing_ac_online_key_defaults_to_not_plugged():
    # Backward-compatible with a snapshot that predates ac_online (or a
    # machine where get_ac_online() itself returned None) — .get()
    # falls back to None/falsy, not a KeyError.
    spec = _bat_spec({"percent": 45, "status": "Not charging"}, None)
    assert spec.plugged is False


def test_bat_spec_charging_wins_over_plugged():
    # Charging already implies AC is connected — even if ac_online were
    # somehow also True in the data, Charging status takes priority and
    # plugged must stay False (mutually exclusive, see _bat_spec's own
    # docstring).
    spec = _bat_spec({"percent": 60, "status": "Charging", "ac_online": True}, None)
    assert spec.charging is True
    assert spec.plugged is False


def test_vol_and_bri_specs_never_set_charging():
    # charging is a BAT-only concept — VOL/BRI specs must default to
    # False, not leave it unset/ambiguous.
    assert _vol_spec([_sink(50)], None).charging is False
    assert _bri_spec(80, None).charging is False


def test_bat_spec_label_is_bat():
    assert _bat_spec({"percent": 50, "status": "Full"}, None).label == "BAT"


# ---------- _collect_bars ----------

class _FakeStatus:
    def __init__(self, snapshots=None, errors=None):
        self._snapshots = snapshots or {}
        self._errors = errors or {}

    def get(self, domain_name):
        return self._snapshots.get(domain_name)

    def get_error(self, domain_name):
        return self._errors.get(domain_name)


def _ctx(**status_kwargs):
    return SimpleNamespace(status=_FakeStatus(**status_kwargs))


def test_collect_bars_no_status_worker_returns_empty():
    assert _collect_bars(SimpleNamespace(status=None)) == []


def test_collect_bars_always_includes_vol_and_bri_when_no_battery():
    ctx = _ctx(snapshots={
        "audio": [_sink(60)],
        "brightness": 90,
        "battery": None,
    })
    labels = [s.label for s in _collect_bars(ctx)]
    assert labels == ["VOL", "BRI"]  # BAT omitted — no battery hardware


def test_collect_bars_includes_battery_when_present():
    ctx = _ctx(snapshots={
        "audio": [_sink(60)],
        "brightness": 90,
        "battery": {"percent": 14, "status": "Charging"},
    })
    labels = [s.label for s in _collect_bars(ctx)]
    assert labels == ["VOL", "BRI", "BAT"]


def test_collect_bars_includes_battery_bar_on_poll_error_even_without_data():
    ctx = _ctx(
        snapshots={"audio": [], "brightness": 50, "battery": None},
        errors={"battery": "permission denied"},
    )
    labels = [s.label for s in _collect_bars(ctx)]
    assert labels == ["VOL", "BRI", "BAT"]


# ---------- _quantize_percent ----------

def test_quantize_percent_rounds_to_nearest_step():
    assert _quantize_percent(61, step=5) == 60
    assert _quantize_percent(63, step=5) == 65


def test_quantize_percent_exact_multiple_is_unchanged():
    assert _quantize_percent(60, step=5) == 60


def test_quantize_percent_default_step_is_five():
    assert _quantize_percent(61) == 60


def test_quantize_percent_clamps_to_0_100():
    assert _quantize_percent(150, step=5) == 100
    assert _quantize_percent(-10, step=5) == 0


def test_quantize_percent_ten_percent_step():
    assert _quantize_percent(61, step=10) == 60
    assert _quantize_percent(66, step=10) == 70


def test_quantize_percent_takes_no_box_height_parameter_at_all():
    # the whole point: quantization is computed BEFORE any row/height math
    # (draw() calls this once per bar, then separately renders the result
    # across however many rows the box has) — its signature has no
    # num_rows/bar_rows parameter, so the same real percent always
    # quantizes identically no matter how tall the bar is drawn.
    import inspect
    assert "step" in inspect.signature(_quantize_percent).parameters
    assert "num_rows" not in inspect.signature(_quantize_percent).parameters
    assert "bar_rows" not in inspect.signature(_quantize_percent).parameters


# ---------- _compact_fallback_line ----------

def test_compact_fallback_line_joins_label_and_readout():
    specs = [
        BarSpec("VOL", 50, "50%", dim=False, urgent=False),
        BarSpec("BAT", 14, "CHG", dim=False, urgent=False),
    ]
    assert _compact_fallback_line(specs) == "VOL 50%   BAT CHG"
