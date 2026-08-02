"""Tests for keybinds.py — pure key-name -> curses code resolution."""

import curses

import pytest

from tuicc.keybinds import resolve_key, key_label


def test_special_key_left():
    assert resolve_key("Left") == curses.KEY_LEFT


def test_special_key_enter():
    assert resolve_key("Enter") == 10


def test_shift_tab_uses_btab():
    assert resolve_key("Shift+Tab") == curses.KEY_BTAB


def test_delete_uses_key_dc():
    assert resolve_key("Delete") == curses.KEY_DC


def test_f_key_uses_key_f0_offset():
    assert resolve_key("F2") == curses.KEY_F0 + 2


def test_f_keys_are_distinct():
    assert resolve_key("F1") != resolve_key("F2") != resolve_key("F3")


def test_single_character_uses_ord():
    assert resolve_key("h") == ord("h")


def test_single_character_uppercase_is_distinct_from_lowercase():
    assert resolve_key("H") != resolve_key("h")


def test_ctrl_l_is_ascii_control_code_12():
    assert resolve_key("Ctrl+L") == 12


def test_ctrl_a_is_1():
    assert resolve_key("Ctrl+A") == 1


def test_ctrl_z_is_26():
    assert resolve_key("Ctrl+Z") == 26


def test_ctrl_lowercase_letter_same_as_uppercase():
    # Ctrl doesn't have a separate lowercase code — Ctrl+l and Ctrl+L
    # are the same physical keypress and must resolve the same.
    assert resolve_key("Ctrl+l") == resolve_key("Ctrl+L")


def test_ctrl_with_digit_raises():
    with pytest.raises(ValueError):
        resolve_key("Ctrl+1")


def test_ctrl_with_multiple_letters_raises():
    with pytest.raises(ValueError):
        resolve_key("Ctrl+LL")


def test_unknown_name_raises():
    with pytest.raises(ValueError):
        resolve_key("NotAKey")


def test_empty_string_raises():
    with pytest.raises(ValueError):
        resolve_key("")


# ---------- key_label ----------

def test_key_label_special_key():
    assert key_label(curses.KEY_LEFT) == "Left"


def test_key_label_lowercase_character_is_uppercased():
    assert key_label(ord("y")) == "Y"


def test_key_label_already_uppercase_character():
    assert key_label(ord("Y")) == "Y"


def test_key_label_round_trips_with_resolve_key():
    assert key_label(resolve_key("n")) == "N"


def test_key_label_unrecoverable_code_falls_back_to_question_mark():
    # Ctrl+A resolves to code 1 — there's no way back to "Ctrl+A" from
    # just the integer, so this documents the honest fallback.
    assert key_label(resolve_key("Ctrl+A")) == "?"
