"""Tests for brightness.py's parse_get_output — the pure logic half of
get_percent(). Fixture is real `brightnessctl -m` output captured live
on this machine.
"""

from tuicc.brightness import parse_get_output


BRIGHTNESSCTL_M_OUTPUT = "intel_backlight,backlight,1515,100%,1515\n"


def test_parses_the_percent_field():
    assert parse_get_output(BRIGHTNESSCTL_M_OUTPUT) == 100


def test_parses_a_partial_percentage():
    assert parse_get_output("intel_backlight,backlight,758,50%,1515\n") == 50


def test_only_uses_the_first_line():
    # -l lists every device (leds included) — get_percent() only ever
    # calls plain -m (one line, the default device), but this guards
    # the parser itself against being handed more than expected.
    multi = "intel_backlight,backlight,1515,100%,1515\nplatform::mute,leds,1,100%,1\n"
    assert parse_get_output(multi) == 100


def test_empty_output_returns_none():
    assert parse_get_output("") is None


def test_whitespace_only_output_returns_none():
    assert parse_get_output("   \n") is None


def test_too_few_fields_returns_none():
    assert parse_get_output("intel_backlight,backlight,1515\n") is None


def test_non_percent_field_returns_none():
    assert parse_get_output("intel_backlight,backlight,1515,oops,1515\n") is None
