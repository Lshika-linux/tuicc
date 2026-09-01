"""Tests for main.py's own module-level dispatch/handler functions —
the ~24 already-plain functions defined above def main(stdscr) (do_*/
handle_*/any_two_level_module_expanded/_apply_launcher_routing_
default), each taking explicit params, no closures. main.py itself
(building NORMAL_KEY_HANDLERS/HANDOFF_TARGETS/MODE_HANDLERS as
closures and the while True: dispatch loop, both inside def main())
stays untested here — a materially bigger, separate refactor to make
testable (same cost/complexity call VISION.md's own R2 section already
made deferring resize_mode's own dispatch extraction), not attempted
in this pass.

main.py is safely importable as a plain module: its only module-level
side effects are locale.setlocale(...) and sys.path.insert(...) (both
idempotent), and main(stdscr) itself never runs (guarded by
`if __name__ == "__main__":`).
"""

from types import SimpleNamespace

import main
from tuicc.loop_state import LoopState
from tuicc.model import WMState, Region
from tuicc.modules.launcher import LauncherState
from tuicc.resize_mode import ResizeState, SpawnPickerState
from tuicc.help_mode import HelpState
from tuicc.layout import ModuleBox
from tuicc.actions import ActionContext
from tuicc.connectivity.model import WifiNetwork, AdapterInfo, BluetoothAdapterInfo

from _fresh_install_helpers import load_packaged_default_config


class _FakeStatusWorker:
    def __init__(self, snapshots=None):
        self._snapshots = dict(snapshots or {})
        self.action_calls = []

    def set(self, name, value):
        self._snapshots[name] = value

    def get(self, name):
        return self._snapshots.get(name)

    def request_action(self, domain, action, value, pending_key=None):
        self.action_calls.append((domain, action, value, pending_key))


class _FakeMailbox:
    def __init__(self, pending=False, request=None):
        self._pending = pending
        self._request = request

    def has_pending(self):
        return self._pending


class _FakeAgent:
    def __init__(self, mailbox=None):
        self.mailbox = mailbox if mailbox is not None else _FakeMailbox()
        self.cancel_current_calls = 0
        self.reply_passphrase_calls = []
        self.reply_pairing_calls = []

    def cancel_current(self):
        self.cancel_current_calls += 1

    def reply_passphrase(self, text):
        self.reply_passphrase_calls.append(text)

    def reply_pairing(self, accept):
        self.reply_pairing_calls.append(accept)


class _FakeProvider:
    def __init__(self):
        self.no_focus_next_window_calls = []

    def no_focus_next_window(self, pid):
        self.no_focus_next_window_calls.append(pid)


# ==================== single-purpose do_*/handle_* ====================

def test_do_enter_resize_opens_a_browsing_session():
    resize = ResizeState()
    main.do_enter_resize(resize)
    assert resize.active is True
    assert resize.editing is False


def test_do_enter_help_opens_the_panel_and_claims_the_stack():
    loop_state = LoopState()
    help_state = HelpState()
    main.do_enter_help(loop_state, help_state)
    assert help_state.active is True
    assert loop_state.mode_stack[-1] == "help"


def test_do_enter_box_editing_claims_the_stack(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    loop_state = LoopState()
    resize = ResizeState()
    box = cfg.layout.boxes[0]

    main.do_enter_box_editing(loop_state, resize, box)

    assert resize.editing is True
    assert loop_state.mode_stack[-1] == "resize_editing"


def test_do_spawn_picker_opens_when_a_module_is_still_available(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    cfg.layout.boxes = [b for b in cfg.layout.boxes if b.name != "bars"]  # leave "bars" spawnable
    loop_state = LoopState()
    spawn_picker = SpawnPickerState()

    main.do_spawn_picker(loop_state, cfg, spawn_picker)

    assert spawn_picker.active is True
    assert loop_state.mode_stack[-1] == "spawn_picker"


def test_do_spawn_picker_does_not_claim_the_stack_when_nothing_is_spawnable(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    cfg.layout.boxes = [ModuleBox(name=n, x=0, y=0, w=0.1, h=0.1) for n in main.MODULES.keys()]
    loop_state = LoopState()
    spawn_picker = SpawnPickerState()

    main.do_spawn_picker(loop_state, cfg, spawn_picker)

    assert spawn_picker.active is False
    assert loop_state.mode_stack == ["normal"]


def test_do_dismiss_hides_and_resets(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)

    class _DismissProvider:
        def __init__(self):
            self.dismiss_calls = 0

        def dismiss_self(self):
            self.dismiss_calls += 1

    provider = _DismissProvider()
    loop_state = LoopState(mode_stack=["normal", "help"], selected_id="control:0")
    wifi_agent = _FakeAgent()

    main.do_dismiss(loop_state, ResizeState(), SpawnPickerState(), HelpState(), LauncherState(), provider, wifi_agent, None)

    assert loop_state.dismissed is True
    assert provider.dismiss_calls == 1
    assert loop_state.mode_stack == ["normal"]
    assert wifi_agent.cancel_current_calls == 1


def test_any_two_level_module_expanded_reports_sessions(tmp_path, monkeypatch):
    main.sessions_mode.start_naming(1, "x")  # not "expanded", just proving nothing here false-positives
    main.sessions_mode.handle_naming_key(27)
    assert main.any_two_level_module_expanded() is False


def test_do_save_layout_writes_and_shows_a_toast(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    loop_state = LoopState()
    resize = ResizeState()
    main.do_enter_resize(resize)

    main.do_save_layout(loop_state, cfg, resize)

    assert loop_state.resize_message == f"Saved preset {cfg.preset_number}"
    assert resize.active is False  # exit_edit_mode() ends the whole session


def test_do_new_preset_forks_a_new_preset_number(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    original_number = cfg.preset_number
    loop_state = LoopState()
    resize = ResizeState()

    main.do_new_preset(loop_state, cfg, resize)

    assert cfg.preset_number != original_number
    assert loop_state.resize_message == f"Saved as new preset {cfg.preset_number}"


def test_do_cycle_preset_switches_to_the_next_available_preset(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    loop_state = LoopState()
    resize = ResizeState()
    main.do_new_preset(loop_state, cfg, resize)  # now there are 2 presets to cycle between
    first_after_fork = cfg.preset_number

    main.do_cycle_preset(loop_state, cfg, resize)

    assert cfg.preset_number != first_after_fork
    assert loop_state.active_module == (cfg.layout.boxes[0].name if cfg.layout.boxes else None)


def test_do_apply_reselect_uses_reselect_item_id_when_set():
    loop_state = LoopState()
    action_ctx = ActionContext(provider=None, status=None, reselect_item_id="control:2")

    main.do_apply_reselect(loop_state, action_ctx, ordered=[])

    assert loop_state.selected_id == "control:2"
    assert loop_state.active_module == "control"
    assert action_ctx.reselect_item_id is None


def test_do_apply_toast_drains_the_toast_message():
    loop_state = LoopState()
    action_ctx = ActionContext(provider=None, status=None, toast_message="Copied", toast_urgent=True)

    main.do_apply_toast(loop_state, action_ctx)

    assert loop_state.resize_message == "Copied"
    assert loop_state.resize_message_urgent is True
    assert action_ctx.toast_message is None
    assert action_ctx.toast_urgent is False


def test_do_apply_toast_is_a_no_op_when_nothing_was_set():
    loop_state = LoopState(resize_message="unrelated")
    action_ctx = ActionContext(provider=None, status=None)

    main.do_apply_toast(loop_state, action_ctx)

    assert loop_state.resize_message == "unrelated"


# ==================== sessions/sysmon naming/nice-edit ====================

def test_handle_sessions_naming_confirm_applies_and_releases_the_claim(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "set_session_name", lambda slot, name: calls.append((slot, name)))
    cfg = SimpleNamespace(keybinds={"confirm": ord("\n")}, session_names={1: "Slot 1"})
    main.sessions_mode.start_naming(1, "")
    try:
        for ch in "New":
            main.handle_sessions_naming(ord(ch), cfg)
        still_claiming = main.handle_sessions_naming(cfg.keybinds["confirm"], cfg)

        assert still_claiming is False
        assert cfg.session_names[1] == "New"
        assert calls == [(1, "New")]
    finally:
        main.sessions_mode.handle_naming_key(27)


def test_handle_sessions_naming_escape_cancels_without_writing(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "set_session_name", lambda slot, name: calls.append((slot, name)))
    cfg = SimpleNamespace(keybinds={"confirm": ord("\n")}, session_names={1: "Slot 1"})
    main.sessions_mode.start_naming(1, "old")

    still_claiming = main.handle_sessions_naming(27, cfg)

    assert still_claiming is False
    assert calls == []
    assert cfg.session_names[1] == "Slot 1"


def test_handle_sysmon_nice_confirm_applies(monkeypatch):
    monkeypatch.setattr("os.setpriority", lambda *a: None)
    cfg = SimpleNamespace(keybinds={"confirm": ord("\n")})
    main.sysmon_mode.start_nice_edit("w1", pid=1234, current=0)
    try:
        main.handle_sysmon_nice(ord("5"), cfg)
        still_claiming = main.handle_sysmon_nice(cfg.keybinds["confirm"], cfg)
        assert still_claiming is False
        assert main.sysmon_mode.is_editing_nice() is False
    finally:
        main.sysmon_mode.handle_nice_key(27)


def test_handle_sysmon_nice_escape_cancels():
    cfg = SimpleNamespace(keybinds={"confirm": ord("\n")})
    main.sysmon_mode.start_nice_edit("w1", pid=1234, current=0)

    still_claiming = main.handle_sysmon_nice(27, cfg)

    assert still_claiming is False
    assert main.sysmon_mode.is_editing_nice() is False


# ==================== connectivity: browsing / hidden-ssid / passphrase / pairing ====================

def _browsing_cfg():
    return SimpleNamespace(keybinds={
        "confirm": ord("\n"), "scan": ord("s"), "wifi_forget": ord("f"),
        "wifi_connect_hidden": ord("h"), "wifi_power_toggle": ord("p"),
        "bt_power_toggle": ord("p"), "bt_pairable_toggle": ord("a"),
        "confirm_yes": ord("y"), "confirm_no": ord("n"),
        "wifi_passphrase_visibility_toggle": ord("v"),
    })


def test_handle_connectivity_browsing_scan_requests_a_wifi_scan():
    cfg = _browsing_cfg()
    status = _FakeStatusWorker()
    loop_state = LoopState()
    main.connectivity_mode.start_browsing("wifi")
    try:
        still_claiming = main.handle_connectivity_browsing(cfg.keybinds["scan"], loop_state, cfg, status, set(), set())
        assert still_claiming is True
        assert status.action_calls == [("wifi", "scan", None, None)]
    finally:
        main.connectivity_mode.stop_browsing()


def test_handle_connectivity_browsing_forget_request_then_confirm_yes():
    cfg = _browsing_cfg()
    network = WifiNetwork(ssid="MyWifi", connected=False)
    status = _FakeStatusWorker({"wifi": [network]})
    loop_state = LoopState(selected_id="connectivity:wifi:MyWifi")
    main.connectivity_mode.start_browsing("wifi")
    try:
        main.handle_connectivity_browsing(cfg.keybinds["wifi_forget"], loop_state, cfg, status, set(), set())
        assert main.connectivity_mode.is_confirming_forget() is True

        main.handle_connectivity_browsing(cfg.keybinds["confirm_yes"], loop_state, cfg, status, set(), set())

        assert main.connectivity_mode.is_confirming_forget() is False
        assert ("wifi", "forget", "MyWifi", None) in status.action_calls
    finally:
        main.connectivity_mode.stop_browsing()
        main.connectivity_mode.cancel_forget()


def test_handle_connectivity_browsing_hidden_ssid_key_claims_the_stack():
    cfg = _browsing_cfg()
    status = _FakeStatusWorker()
    loop_state = LoopState()
    main.connectivity_mode.start_browsing("wifi")
    try:
        still_claiming = main.handle_connectivity_browsing(cfg.keybinds["wifi_connect_hidden"], loop_state, cfg, status, set(), set())
        assert still_claiming is True
        assert loop_state.mode_stack[-1] == "connectivity_hidden_ssid"
        assert main.connectivity_mode.is_entering_hidden_ssid() is True
    finally:
        main.connectivity_mode.stop_browsing()
        main.connectivity_mode.cancel_hidden_ssid_entry()


def test_handle_connectivity_browsing_wifi_power_toggle_requests_the_opposite_state():
    cfg = _browsing_cfg()
    status = _FakeStatusWorker({"wifi_adapter": AdapterInfo(powered=True)})
    loop_state = LoopState()
    main.connectivity_mode.start_browsing("wifi")
    try:
        main.handle_connectivity_browsing(cfg.keybinds["wifi_power_toggle"], loop_state, cfg, status, set(), set())
        assert status.action_calls == [("wifi", "set_powered", False, "power")]
    finally:
        main.connectivity_mode.stop_browsing()


def test_handle_connectivity_browsing_bt_pairable_toggle():
    cfg = _browsing_cfg()
    status = _FakeStatusWorker({"bluetooth_adapter": BluetoothAdapterInfo(powered=True, pairable=False)})
    loop_state = LoopState()
    main.connectivity_mode.start_browsing("bluetooth")
    try:
        main.handle_connectivity_browsing(cfg.keybinds["bt_pairable_toggle"], loop_state, cfg, status, set(), set())
        assert status.action_calls == [("bluetooth", "set_pairable", True, "pairable")]
    finally:
        main.connectivity_mode.stop_browsing()


def test_handle_connectivity_browsing_confirm_on_a_valid_selection_toggles_it():
    cfg = _browsing_cfg()
    network = WifiNetwork(ssid="MyWifi", connected=False)
    status = _FakeStatusWorker({"wifi": [network]})
    loop_state = LoopState(selected_id="connectivity:wifi:MyWifi")
    main.connectivity_mode.start_browsing("wifi")
    try:
        main.handle_connectivity_browsing(cfg.keybinds["confirm"], loop_state, cfg, status, set(), set())
        assert ("wifi", "connect", "MyWifi", None) in status.action_calls
    finally:
        main.connectivity_mode.stop_browsing()


def test_handle_connectivity_browsing_escape_exits_to_the_header():
    cfg = _browsing_cfg()
    status = _FakeStatusWorker()
    loop_state = LoopState()
    main.connectivity_mode.start_browsing("wifi")
    try:
        still_claiming = main.handle_connectivity_browsing(27, loop_state, cfg, status, set(), set())
        assert still_claiming is False
        assert main.connectivity_mode.is_browsing() is False
        assert loop_state.selected_id == "connectivity:wifi:header"
    finally:
        main.connectivity_mode.stop_browsing()  # safety net if the assertion above ever regresses


def test_handle_connectivity_hidden_ssid_confirm_requests_a_connect():
    cfg = SimpleNamespace(keybinds={"confirm": ord("\n")})
    status = _FakeStatusWorker()
    loop_state = LoopState()
    main.connectivity_mode.start_hidden_ssid_entry()
    try:
        for ch in "MyHiddenNet":
            main.handle_connectivity_hidden_ssid(ord(ch), loop_state, cfg, status)
        still_claiming = main.handle_connectivity_hidden_ssid(cfg.keybinds["confirm"], loop_state, cfg, status)

        assert still_claiming is False
        assert status.action_calls == [("wifi", "connect_hidden", "MyHiddenNet", None)]
    finally:
        main.connectivity_mode.cancel_hidden_ssid_entry()


def test_handle_connectivity_passphrase_confirm_replies_and_marks_submitted():
    cfg = SimpleNamespace(keybinds={"confirm": ord("\n"), "wifi_passphrase_visibility_toggle": ord("v")})
    wifi_agent = _FakeAgent(mailbox=_FakeMailbox(pending=True))
    main.connectivity_mode.start_passphrase_entry("MyWifi")
    try:
        for ch in "secret123":
            main.handle_connectivity_passphrase(ord(ch), cfg, wifi_agent)
        still_claiming = main.handle_connectivity_passphrase(cfg.keybinds["confirm"], cfg, wifi_agent)

        assert still_claiming is True  # waits for the daemon's own answer, doesn't release the claim yet
        assert wifi_agent.reply_passphrase_calls == ["secret123"]
        assert main.connectivity_mode.is_passphrase_waiting() is True
    finally:
        main.connectivity_mode.cancel_passphrase_entry()


def test_handle_connectivity_passphrase_mailbox_gone_cancels():
    cfg = SimpleNamespace(keybinds={"confirm": ord("\n")})
    wifi_agent = _FakeAgent(mailbox=_FakeMailbox(pending=False))  # daemon cancelled on its own
    main.connectivity_mode.start_passphrase_entry("MyWifi")

    still_claiming = main.handle_connectivity_passphrase(ord("x"), cfg, wifi_agent)

    assert still_claiming is False
    assert main.connectivity_mode.is_entering_passphrase() is False


def test_handle_connectivity_pairing_confirm_yes_replies_true():
    cfg = SimpleNamespace(keybinds={"confirm": ord("\n"), "confirm_yes": ord("y"), "confirm_no": ord("n")})
    bluez_agent = _FakeAgent(mailbox=_FakeMailbox(pending=True))
    request = SimpleNamespace(kind="confirm", device_id="AA:BB", device_name="Headphones", passkey=123456)
    main.connectivity_mode.start_pairing_confirm(request)
    try:
        still_claiming = main.handle_connectivity_pairing(cfg.keybinds["confirm_yes"], cfg, bluez_agent)
        assert still_claiming is True
        assert bluez_agent.reply_pairing_calls == [True]
    finally:
        main.connectivity_mode.cancel_pairing_confirm()


def test_handle_connectivity_pairing_escape_rejects():
    cfg = SimpleNamespace(keybinds={"confirm": ord("\n"), "confirm_yes": ord("y"), "confirm_no": ord("n")})
    bluez_agent = _FakeAgent(mailbox=_FakeMailbox(pending=True))
    request = SimpleNamespace(kind="confirm", device_id="AA:BB", device_name="Headphones", passkey=123456)
    main.connectivity_mode.start_pairing_confirm(request)

    still_claiming = main.handle_connectivity_pairing(27, cfg, bluez_agent)

    assert still_claiming is False
    assert bluez_agent.cancel_current_calls == 1
    assert main.connectivity_mode.is_confirming_pairing() is False


# ==================== launcher ====================

def test_handle_launcher_confirm_spawns_and_queues_a_move(tmp_path, monkeypatch):
    monkeypatch.setattr(main.launcher_mode, "_apps_cache", [("kitty", "kitty", "kitty")])
    monkeypatch.setattr(main, "spawn_detached", lambda *a, **k: 4242)
    cfg = SimpleNamespace(keybinds={"confirm": ord("\n"), "up": curses_up(), "down": curses_down()})
    provider = _FakeProvider()
    moves = main.pending_moves.PendingMovesQueue()
    app = SimpleNamespace(wm_config=None)
    launcher = LauncherState(typing_mode=True, search_query="kitty", search_selected_index=0,
                              saved_selected_id="sidebar:1", saved_active_module="sidebar")
    loop_state = LoopState(focus_id="2")
    state = WMState(regions=[Region(id="2", name="2", windows=[])], focused_region_id="2")

    still_claiming = main.handle_launcher(cfg.keybinds["confirm"], loop_state, cfg, state, launcher, provider, moves, app)

    assert still_claiming is False
    assert len(moves.entries) == 1
    assert moves.entries[0]["target_region"] == "2"
    assert provider.no_focus_next_window_calls == [4242]
    assert loop_state.selected_id == "sidebar:1"


def test_handle_launcher_confirm_when_spawn_fails_shows_a_toast(tmp_path, monkeypatch):
    monkeypatch.setattr(main.launcher_mode, "_apps_cache", [("kitty", "kitty", "kitty")])
    monkeypatch.setattr(main, "spawn_detached", lambda *a, **k: None)
    cfg = SimpleNamespace(keybinds={"confirm": ord("\n"), "up": curses_up(), "down": curses_down()})
    provider = _FakeProvider()
    moves = main.pending_moves.PendingMovesQueue()
    app = SimpleNamespace(wm_config=None)
    launcher = LauncherState(typing_mode=True, search_query="kitty", search_selected_index=0)
    loop_state = LoopState()
    state = WMState()

    main.handle_launcher(cfg.keybinds["confirm"], loop_state, cfg, state, launcher, provider, moves, app)

    assert moves.entries == []
    assert loop_state.resize_message_urgent is True


def test_apply_launcher_routing_default_falls_back_to_pre_routing_focus_id(tmp_path, monkeypatch):
    monkeypatch.setattr(main.launcher_mode, "_apps_cache", [("kitty", "kitty", "kitty")])
    loop_state = LoopState(focus_id="stale")
    launcher = LauncherState(typing_mode=True, search_query="kitty", search_selected_index=0, pre_routing_focus_id="3")
    app = SimpleNamespace(wm_config=None)

    main._apply_launcher_routing_default(loop_state, launcher, app)

    assert loop_state.focus_id == "3"  # no routing rule configured (wm_config=None) — reverts to pre-routing target


def curses_up():
    import curses
    return curses.KEY_UP


def curses_down():
    import curses
    return curses.KEY_DOWN


# ==================== spawn picker / resize editing handoff ====================

def test_handle_spawn_picker_choosing_a_module_hands_off_to_box_editing(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    cfg.layout.boxes = [b for b in cfg.layout.boxes if b.name != "bars"]
    loop_state = LoopState()
    spawn_picker = SpawnPickerState()
    resize = ResizeState()
    main.do_spawn_picker(loop_state, cfg, spawn_picker)
    assert spawn_picker.choices[0] == "bars"  # the module this test removed above, offered back first

    # resize_mode.choose() picks by digit position ("1" == choices[0]),
    # not Enter — see its own docstring.
    still_claiming = main.handle_spawn_picker(ord("1"), loop_state, cfg, spawn_picker, resize)

    assert still_claiming is True
    assert loop_state.mode_stack[-1] == "resize_editing"
    assert any(b.name == "bars" for b in cfg.layout.boxes)


def test_handle_resize_editing_handoff_pops_and_calls_the_target(tmp_path, monkeypatch):
    import curses
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    loop_state = LoopState(active_module=cfg.layout.boxes[0].name)
    resize = ResizeState()
    box = cfg.layout.boxes[0]
    main.do_enter_box_editing(loop_state, resize, box)
    calls = []
    handoff_targets = {"save_layout": lambda: calls.append("save_layout")}

    # cfg.keybinds["save_layout"] (F3, packaged default) — a real
    # handoff, not just any unrecognized key — see resize_mode.py's own
    # _handoff() for what commits/triggers this.
    still_claiming = main.handle_resize_editing(
        cfg.keybinds["save_layout"], loop_state, resize, cfg, {}, {}, 200, 55, handoff_targets,
    )

    assert still_claiming is True  # "stack already correctly arranged, don't pop again"
    assert calls == ["save_layout"]
    assert loop_state.mode_stack == ["normal"]  # "resize_editing" popped before the handoff ran


def test_handle_resize_editing_ordinary_key_falls_through_to_the_editor(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    loop_state = LoopState(active_module=cfg.layout.boxes[0].name, mode_stack=["normal", "resize_editing"])
    resize = ResizeState()
    box = cfg.layout.boxes[0]
    main.do_enter_box_editing(loop_state, resize, box)
    handoff_targets = {}

    # Escape at the editing level returns to browsing (per resize_mode's
    # own two-level session model) — still_claiming False here, so
    # main()'s own dispatch pops "resize_editing" off mode_stack right
    # after this call (browsing itself is never a mode_stack entry —
    # see CLAUDE/GUIDE.md's own ResizeState section).
    still_claiming = main.handle_resize_editing(
        27, loop_state, resize, cfg, {}, {}, 200, 55, handoff_targets,
    )

    assert still_claiming is False
    assert resize.editing is False
    assert resize.active is True
