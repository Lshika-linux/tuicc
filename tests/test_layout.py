"""Tests for layout.py's boxes_to_toml_data() — the exact inverse of
config.py's build_layout_from_preset() parsing loop, used by resize
mode to serialize a Layout back into a preset TOML file.
"""

from tuicc.layout import ModuleBox, boxes_to_toml_data


def test_boxes_to_toml_data_shape():
    boxes = [
        ModuleBox(name="sidebar", x=0.0, y=0.0, w=0.26, h=0.6),
        ModuleBox(name="preview", x=0.26, y=0.09, w=0.74, h=0.91),
    ]

    data = boxes_to_toml_data(boxes)

    assert data == {"box": [
        {"name": "sidebar", "x": 0.0, "y": 0.0, "w": 0.26, "h": 0.6},
        {"name": "preview", "x": 0.26, "y": 0.09, "w": 0.74, "h": 0.91},
    ]}


def test_round_trips_through_build_layout_from_preset_parsing(tmp_path, monkeypatch):
    import tomli_w
    import tuicc.config as config_module
    from tuicc.config import build_layout_from_preset

    boxes = [
        ModuleBox(name="sidebar", x=0.0, y=0.0, w=0.26, h=0.6),
        ModuleBox(name="power_menu", x=0.0, y=0.9, w=0.26, h=0.1),
    ]
    data = boxes_to_toml_data(boxes)

    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    with open(user_dir / "1.toml", "wb") as f:
        tomli_w.dump(data, f)

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", user_dir)

    loaded = build_layout_from_preset(1)

    assert [b.name for b in loaded.boxes] == ["sidebar", "power_menu"]
    by_name = {b.name: b for b in loaded.boxes}
    assert by_name["sidebar"].w == 0.26
    assert by_name["power_menu"].y == 0.9


# ---------- fh (fixed rows) ----------

def test_boxes_to_toml_data_writes_fh_not_h_when_a_box_uses_fh():
    boxes = [ModuleBox(name="control", x=0.0, y=0.5, w=0.2, h=None, fh=10)]

    data = boxes_to_toml_data(boxes)

    assert data == {"box": [{"name": "control", "x": 0.0, "y": 0.5, "w": 0.2, "fh": 10}]}


def test_fh_round_trips_through_build_layout_from_preset_parsing(tmp_path, monkeypatch):
    import tomli_w
    import tuicc.config as config_module
    from tuicc.config import build_layout_from_preset

    boxes = [
        ModuleBox(name="sidebar", x=0.0, y=0.0, w=0.26, h=0.6),
        ModuleBox(name="control", x=0.0, y=0.6, w=0.26, h=None, fh=10),
    ]
    data = boxes_to_toml_data(boxes)

    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    with open(user_dir / "1.toml", "wb") as f:
        tomli_w.dump(data, f)

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", user_dir)

    loaded = build_layout_from_preset(1)

    by_name = {b.name: b for b in loaded.boxes}
    assert by_name["sidebar"].h == 0.6
    assert by_name["sidebar"].fh is None
    assert by_name["control"].fh == 10
    assert by_name["control"].h is None


# ---------- fw (fixed columns) ----------

def test_boxes_to_toml_data_writes_fw_not_w_when_a_box_uses_fw():
    boxes = [ModuleBox(name="bars", x=0.9, y=0.1, w=None, fw=8, h=0.7)]

    data = boxes_to_toml_data(boxes)

    assert data == {"box": [{"name": "bars", "x": 0.9, "y": 0.1, "fw": 8, "h": 0.7}]}


def test_fw_round_trips_through_build_layout_from_preset_parsing(tmp_path, monkeypatch):
    import tomli_w
    import tuicc.config as config_module
    from tuicc.config import build_layout_from_preset

    boxes = [
        ModuleBox(name="preview", x=0.0, y=0.0, w=0.9, h=0.9),
        ModuleBox(name="bars", x=0.9, y=0.0, w=None, fw=8, h=0.9),
    ]
    data = boxes_to_toml_data(boxes)

    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    with open(user_dir / "1.toml", "wb") as f:
        tomli_w.dump(data, f)

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", user_dir)

    loaded = build_layout_from_preset(1)

    by_name = {b.name: b for b in loaded.boxes}
    assert by_name["preview"].w == 0.9
    assert by_name["preview"].fw is None
    assert by_name["bars"].fw == 8
    assert by_name["bars"].w is None


def test_a_box_can_mix_fw_width_with_fh_height():
    boxes = [ModuleBox(name="bars", x=0.9, y=0.1, w=None, fw=8, h=None, fh=20)]

    data = boxes_to_toml_data(boxes)

    assert data == {"box": [{"name": "bars", "x": 0.9, "y": 0.1, "fw": 8, "fh": 20}]}


# ---------- fh_auto ----------

def test_boxes_to_toml_data_writes_fh_auto_when_true():
    boxes = [ModuleBox(name="control", x=0.0, y=0.5, w=0.2, h=None, fh=8, fh_auto=True)]

    data = boxes_to_toml_data(boxes)

    assert data == {"box": [{"name": "control", "x": 0.0, "y": 0.5, "w": 0.2, "fh": 8, "fh_auto": True}]}


def test_boxes_to_toml_data_omits_fh_auto_when_false():
    # A plain fh box's entry stays exactly as clean as before fh_auto
    # existed — no "fh_auto = false" clutter on every box.
    boxes = [ModuleBox(name="control", x=0.0, y=0.5, w=0.2, h=None, fh=8, fh_auto=False)]

    data = boxes_to_toml_data(boxes)

    assert data == {"box": [{"name": "control", "x": 0.0, "y": 0.5, "w": 0.2, "fh": 8}]}


def test_fh_auto_round_trips_through_build_layout_from_preset_parsing(tmp_path, monkeypatch):
    import tomli_w
    import tuicc.config as config_module
    from tuicc.config import build_layout_from_preset

    boxes = [ModuleBox(name="control", x=0.0, y=0.5, w=0.2, h=None, fh=8, fh_auto=True)]
    data = boxes_to_toml_data(boxes)

    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    with open(user_dir / "1.toml", "wb") as f:
        tomli_w.dump(data, f)

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", user_dir)

    loaded = build_layout_from_preset(1)

    assert loaded.boxes[0].fh_auto is True
    assert loaded.boxes[0].fh == 8
