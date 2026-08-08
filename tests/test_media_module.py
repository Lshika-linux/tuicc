"""Tests for modules/media.py's pure logic (_player_label, marquee_text,
_build_rows, nav_items, handle_row) plus the expand/collapse state
machinery (is_expanded/collapse/_reconcile_expanded_state), the same
two-level model sessions.py established — see test_sessions_module.py's
own docstring for why module-level state is reset per-test by hand
rather than via an autouse fixture. draw() needs a real curses screen,
left untested here, same as every other module.
"""

from types import SimpleNamespace

import tuicc.modules.media as media_module
from tuicc.media.cava import ASCII_MAX_RANGE
from tuicc.media.model import Player
from tuicc.audio.model import AudioSink
from tuicc.modules.media import (
    _player_label, _body_label, _source_label, marquee_text, has_scrolling_content,
    _build_rows, nav_items, handle_row, is_expanded, collapse, _cava_row_level,
)


def _reset_module_state():
    media_module._expanded_bus_name = None


def _player(**overrides):
    defaults = dict(
        bus_name="org.mpris.MediaPlayer2.firefox", identity="Mozilla firefox",
        desktop_entry="firefox", playback_status="Playing", title="Some Title",
        artist="Some Artist", album="", url="",
        can_go_next=True, can_go_previous=True, can_pause=True, can_play=True,
    )
    defaults.update(overrides)
    return Player(**defaults)


# ---------- _player_label ----------

def test_player_label_includes_the_source_prefix():
    result = _player_label(_player(artist="Artist", title="Title", desktop_entry="firefox", url=""))
    assert result == "[firefox] Artist - Title"


def test_player_label_title_only_when_no_artist():
    result = _player_label(_player(artist="", title="Title", desktop_entry="firefox", url=""))
    assert result == "[firefox] Title"


def test_player_label_falls_back_to_identity_when_no_metadata_at_all():
    # Nothing worth attributing to a source when there's no title
    # either — showing the identity twice ("[Mozilla firefox] Mozilla
    # firefox") would just be noise.
    result = _player_label(_player(artist="", title="", identity="Mozilla firefox"))
    assert result == "Mozilla firefox"


def test_player_label_uses_the_url_domain_over_desktop_entry():
    result = _player_label(_player(
        artist="Artist", title="Title", desktop_entry="firefox",
        url="https://www.youtube.com/watch?v=abc",
    ))
    assert result == "[youtube.com] Artist - Title"


# ---------- _source_label ----------

def test_source_label_prefers_url_domain():
    player = _player(url="https://www.youtube.com/watch?v=abc", desktop_entry="firefox")
    assert _source_label(player) == "youtube.com"


def test_source_label_strips_www_prefix():
    player = _player(url="https://www.example.com/", desktop_entry="firefox")
    assert _source_label(player) == "example.com"


def test_source_label_falls_back_to_desktop_entry_when_no_url():
    player = _player(url="", desktop_entry="mpv")
    assert _source_label(player) == "mpv"


def test_source_label_falls_back_to_identity_when_neither_url_nor_desktop_entry():
    player = _player(url="", desktop_entry="", identity="Some Player")
    assert _source_label(player) == "Some Player"


def test_source_label_ignores_a_non_http_url():
    # A malformed/non-http url (or one with no real netloc) shouldn't
    # produce a nonsensical source — falls through to desktop_entry.
    player = _player(url="not a real url", desktop_entry="mpv")
    assert _source_label(player) == "mpv"


def test_source_label_prefers_identity_over_own_apps_own_domain():
    # Spotify tags tracks with an open.spotify.com URL — that's the
    # app's OWN web player, not "you're browsing this site" the way a
    # real browser tab's URL is. Found live: showed "[open.spotify.com]"
    # for the desktop app, which read as if Spotify were a browser tab.
    player = _player(
        url="https://open.spotify.com/track/abc", desktop_entry="spotify", identity="Spotify",
    )
    assert _source_label(player) == "Spotify"


# ---------- _body_label ----------
# draw() keeps the "[source]" prefix fixed/always-visible and only
# marquee-scrolls this part — found live, scrolling the whole combined
# string used to scroll the source tag out of view along with it.

def test_body_label_never_includes_the_source_prefix():
    player = _player(artist="Artist", title="Title", desktop_entry="firefox", url="https://youtube.com/x")
    assert _body_label(player) == "Artist - Title"


def test_body_label_falls_back_to_identity():
    player = _player(artist="", title="", identity="Mozilla firefox")
    assert _body_label(player) == "Mozilla firefox"


# ---------- has_scrolling_content ----------
# main.py uses this to decide the redraw cadence — found live, the
# marquee's own per-0.3s step math was correct but a 1s idle redraw
# cadence turned a smooth 1-character slide into a visible jump.

def test_has_scrolling_content_true_for_a_long_body():
    player = _player(artist="A Really Very Long Artist Name Indeed", title="An Equally Long Title")
    assert has_scrolling_content([player]) is True


def test_has_scrolling_content_false_for_a_short_body():
    player = _player(artist="A", title="B")
    assert has_scrolling_content([player]) is False


def test_has_scrolling_content_false_for_no_players():
    assert has_scrolling_content([]) is False
    assert has_scrolling_content(None) is False


# ---------- marquee_text ----------

def test_marquee_text_returns_as_is_when_it_fits():
    assert marquee_text("Short", width=20, now=0.0) == "Short"


def test_marquee_text_returns_exactly_width_chars_when_too_long():
    result = marquee_text("A very long title that overflows", width=10, now=0.0)
    assert len(result) == 10


def test_marquee_text_scrolls_over_time():
    text = "A very long title that overflows"
    first = marquee_text(text, width=10, now=0.0)
    later = marquee_text(text, width=10, now=10.0)  # many steps later
    assert first != later


def test_marquee_text_zero_width_returns_empty():
    assert marquee_text("anything", width=0, now=0.0) == ""


# ---------- is_expanded / collapse ----------

def test_is_expanded_reflects_expanded_bus_name():
    _reset_module_state()
    assert is_expanded() is False
    media_module._expanded_bus_name = "org.mpris.MediaPlayer2.firefox"
    assert is_expanded() is True


def test_collapse_clears_and_returns_the_bus_name():
    _reset_module_state()
    media_module._expanded_bus_name = "org.mpris.MediaPlayer2.firefox"
    result = collapse()
    assert result == "org.mpris.MediaPlayer2.firefox"
    assert is_expanded() is False


def test_collapse_when_nothing_expanded_returns_none():
    _reset_module_state()
    assert collapse() is None


# ---------- _build_rows / _reconcile_expanded_state ----------

class _FakeStatus:
    def __init__(self, snapshots=None, errors=None):
        self._snapshots = snapshots or {}
        self._errors = errors or {}

    def get(self, domain_name):
        return self._snapshots.get(domain_name)

    def get_error(self, domain_name):
        return self._errors.get(domain_name)

    def is_pending(self, domain_name, key):
        return False


def _ctx(**status_kwargs):
    return SimpleNamespace(status=_FakeStatus(**status_kwargs), theme={}, selected_id=None, cava=None)


def _kinds(rows):
    return [kind for kind, _payload in rows]


def test_no_players_and_no_error_renders_as_empty_not_error():
    _reset_module_state()
    rows = _build_rows(_ctx(snapshots={"media": None}), box_h=20)
    assert ("empty", "(nothing playing)") in rows
    assert "error" not in _kinds(rows)


def test_media_error_renders_as_error_row():
    _reset_module_state()
    rows = _build_rows(_ctx(snapshots={"media": None}, errors={"media": "session bus unreachable"}), box_h=20)
    assert ("error", "session bus unreachable") in rows


def test_one_row_per_player():
    _reset_module_state()
    players = [_player(bus_name="a"), _player(bus_name="b")]
    rows = _build_rows(_ctx(snapshots={"media": players}), box_h=20)
    player_rows = [payload for kind, payload in rows if kind == "player"]
    assert [p.bus_name for p in player_rows] == ["a", "b"]


def test_no_sinks_and_no_error_renders_as_empty_not_error():
    _reset_module_state()
    rows = _build_rows(_ctx(snapshots={"audio": None}), box_h=20)
    assert ("empty", "(no sinks found)") in rows


def test_audio_error_renders_as_error_row():
    _reset_module_state()
    rows = _build_rows(_ctx(snapshots={"audio": None}, errors={"audio": "wpctl not found"}), box_h=20)
    assert ("error", "wpctl not found") in rows


def test_output_rows_include_every_sink():
    _reset_module_state()
    sinks = [AudioSink(id="1", name="Speakers", volume=50, muted=False, is_default=True),
             AudioSink(id="2", name="Headphones", volume=80, muted=False, is_default=False)]
    rows = _build_rows(_ctx(snapshots={"audio": sinks}), box_h=20)
    output_rows = [payload for kind, payload in rows if kind == "output_item"]
    assert [s.name for s in output_rows] == ["Speakers", "Headphones"]


# ---------- cava visualizer section ----------
# The visualizer itself draws INLINE within "output_item" rows (see
# draw()'s own output_item handling) — _build_rows() only ever adds a
# row for it in the one case there's nowhere inline to put a warning:
# a real cava error, with no output_item rows for it to attach to.

class _FakeCava:
    def __init__(self, error=None):
        self._error = error

    def get_error(self):
        return self._error


def test_no_extra_row_when_ctx_cava_is_none():
    _reset_module_state()
    rows = _build_rows(_ctx(snapshots={}), box_h=20)
    assert "cava_top" not in _kinds(rows)
    assert not any(kind == "error" and "cava" in str(payload).lower() for kind, payload in rows)


def test_no_extra_row_when_cava_present_and_no_error():
    _reset_module_state()
    ctx = _ctx(snapshots={})
    ctx.cava = _FakeCava(error=None)
    rows = _build_rows(ctx, box_h=20)
    # No dedicated visualizer rows at all — it draws inline within
    # whatever "output_item" rows the Output section already produced.
    assert _kinds(rows) == ["header", "empty", "spacer", "header", "empty"]


def test_cava_error_gets_its_own_row():
    _reset_module_state()
    ctx = _ctx(snapshots={})
    ctx.cava = _FakeCava(error="cava not found on PATH — install it to enable the visualizer")
    rows = _build_rows(ctx, box_h=20)
    assert ("error", "cava not found on PATH — install it to enable the visualizer") in rows


# ---------- _cava_row_level ----------

def test_cava_row_level_bottommost_row_gets_low_bits():
    # 2 rows, raw_height near the max — bottommost row (row_idx=1, the
    # LAST/lowest) should be fully lit (level 8) once the bar climbs
    # past its own 0..8 slice, same as the old cava_bottom's behavior.
    assert _cava_row_level(raw_height=ASCII_MAX_RANGE, row_idx=1, num_rows=2) == 8


def test_cava_row_level_topmost_row_only_lights_once_bar_is_tall_enough():
    # A short bar (well under half of ASCII_MAX_RANGE) shouldn't light
    # the TOP row of a 2-row visualizer at all yet.
    assert _cava_row_level(raw_height=1, row_idx=0, num_rows=2) == 0


def test_cava_row_level_zero_height_is_zero_everywhere():
    assert _cava_row_level(raw_height=0, row_idx=0, num_rows=3) == 0
    assert _cava_row_level(raw_height=0, row_idx=2, num_rows=3) == 0


def test_cava_row_level_scales_to_however_many_rows_are_available():
    # Same raw_height, more rows available -> proportionally lower
    # per-row level (the bar's total height in "levels" scales UP with
    # more rows, so any single row's own slice reads lower for the
    # same raw signal) — found live design intent: a visualizer next to
    # 4 sinks should look meaningfully taller/finer than next to 1.
    one_row = _cava_row_level(raw_height=ASCII_MAX_RANGE // 2, row_idx=0, num_rows=1)
    four_rows = _cava_row_level(raw_height=ASCII_MAX_RANGE // 2, row_idx=0, num_rows=4)
    assert one_row >= four_rows


def test_cava_row_level_num_rows_zero_is_zero_not_a_crash():
    assert _cava_row_level(raw_height=100, row_idx=0, num_rows=0) == 0


def test_reconcile_clears_expanded_state_when_player_vanishes():
    # Found live design concern: without this, is_expanded() would keep
    # reporting True forever for a player that quit while expanded.
    _reset_module_state()
    media_module._expanded_bus_name = "org.mpris.MediaPlayer2.gone"
    _build_rows(_ctx(snapshots={"media": [_player(bus_name="org.mpris.MediaPlayer2.other")]}), box_h=20)
    assert is_expanded() is False


def test_reconcile_leaves_expanded_state_when_player_still_present():
    _reset_module_state()
    media_module._expanded_bus_name = "org.mpris.MediaPlayer2.firefox"
    _build_rows(_ctx(snapshots={"media": [_player(bus_name="org.mpris.MediaPlayer2.firefox")]}), box_h=20)
    assert is_expanded() is True


# ---------- nav_items ----------

def _fake_ctx_for_nav(players=None, sinks=None):
    return SimpleNamespace(
        status=_FakeStatus(snapshots={"media": players, "audio": sinks}),
        theme={}, selected_id=None, cava=None,
    )


def test_nav_items_collapsed_player_is_a_single_row_item():
    _reset_module_state()
    ctx = _fake_ctx_for_nav(players=[_player()])
    box = (0, 0, 30, 10)

    items = nav_items(box, ctx, "media")

    assert [item.id for item in items] == ["media:org.mpris.MediaPlayer2.firefox:row"]
    assert items[0].target_kind == "media_row"
    assert items[0].focus_target == "org.mpris.MediaPlayer2.firefox"


def test_nav_items_expanded_player_shows_full_transport():
    _reset_module_state()
    media_module._expanded_bus_name = "org.mpris.MediaPlayer2.firefox"
    ctx = _fake_ctx_for_nav(players=[_player()])
    box = (0, 0, 30, 10)

    items = nav_items(box, ctx, "media")

    ids = [item.id for item in items]
    assert ids == [
        "media:org.mpris.MediaPlayer2.firefox:prev",
        "media:org.mpris.MediaPlayer2.firefox:playpause",
        "media:org.mpris.MediaPlayer2.firefox:next",
    ]
    assert all(item.target_kind == "media_transport" for item in items)
    assert all(item.focus_target == "org.mpris.MediaPlayer2.firefox" for item in items)


def test_nav_items_expanded_omits_prev_and_next_when_unsupported():
    _reset_module_state()
    media_module._expanded_bus_name = "org.mpris.MediaPlayer2.firefox"
    ctx = _fake_ctx_for_nav(players=[_player(can_go_previous=False, can_go_next=False)])
    box = (0, 0, 30, 10)

    items = nav_items(box, ctx, "media")

    assert [item.id for item in items] == ["media:org.mpris.MediaPlayer2.firefox:playpause"]


def test_nav_items_one_row_per_output_sink():
    _reset_module_state()
    sinks = [AudioSink(id="1", name="Speakers", volume=50, muted=False, is_default=True),
             AudioSink(id="2", name="Headphones", volume=80, muted=False, is_default=False)]
    ctx = _fake_ctx_for_nav(sinks=sinks)
    box = (0, 0, 30, 10)

    items = nav_items(box, ctx, "media")

    output_items = [item for item in items if item.target_kind == "media_output"]
    assert [item.focus_target for item in output_items] == ["1", "2"]


# ---------- handle_row ----------

def test_handle_row_expands_the_player():
    _reset_module_state()
    ctx = SimpleNamespace(reselect_item_id=None)
    item = SimpleNamespace(focus_target="org.mpris.MediaPlayer2.firefox")

    should_dismiss, pending = handle_row(ctx, item, cfg=None)

    assert media_module._expanded_bus_name == "org.mpris.MediaPlayer2.firefox"
    assert should_dismiss is False
    assert pending is None


def test_handle_row_sets_reselect_item_id_to_playpause():
    _reset_module_state()
    ctx = SimpleNamespace(reselect_item_id=None)
    item = SimpleNamespace(focus_target="org.mpris.MediaPlayer2.firefox")

    handle_row(ctx, item, cfg=None)

    assert ctx.reselect_item_id == "media:org.mpris.MediaPlayer2.firefox:playpause"
