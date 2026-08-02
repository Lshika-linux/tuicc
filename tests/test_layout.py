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
