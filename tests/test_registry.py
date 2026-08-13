"""Tests for connectivity/registry.py — no dedicated test file existed
for this before (each backend's own construction was only ever
exercised indirectly, through main.py or another test's setup). Real
enough behavior to verify directly now that a second wifi backend
exists: an unknown name must raise, not silently return something
wrong, and each registered name must actually resolve to the class it
claims to.
"""

import pytest

from tuicc.connectivity.registry import (
    WIFI_BACKENDS,
    WIFI_AGENTS,
    BLUETOOTH_BACKENDS,
    build_wifi_backend,
    build_wifi_agent,
    build_bluetooth_backend,
)
from tuicc.connectivity.iwd import IwdBackend
from tuicc.connectivity.iwd_agent import IwdAgent
from tuicc.connectivity.networkmanager import NetworkManagerBackend
from tuicc.connectivity.networkmanager_agent import NetworkManagerAgent
from tuicc.connectivity.bluez import BluezBackend


def test_build_wifi_backend_iwd():
    assert isinstance(build_wifi_backend("iwd"), IwdBackend)


def test_build_wifi_backend_networkmanager():
    assert isinstance(build_wifi_backend("networkmanager"), NetworkManagerBackend)


def test_build_wifi_backend_unknown_name_raises():
    with pytest.raises(ValueError):
        build_wifi_backend("something-made-up")


def test_build_wifi_agent_iwd():
    assert isinstance(build_wifi_agent("iwd"), IwdAgent)


def test_build_wifi_agent_networkmanager():
    assert isinstance(build_wifi_agent("networkmanager"), NetworkManagerAgent)


def test_build_wifi_agent_unknown_name_raises():
    with pytest.raises(ValueError):
        build_wifi_agent("something-made-up")


def test_build_bluetooth_backend_bluez():
    assert isinstance(build_bluetooth_backend("bluez"), BluezBackend)


def test_build_bluetooth_backend_unknown_name_raises():
    with pytest.raises(ValueError):
        build_bluetooth_backend("something-made-up")


def test_wifi_backends_and_agents_have_matching_keys():
    # A wifi agent is 1:1 with a wifi backend of the same protocol —
    # every registered backend name should have a matching agent name
    # (see base.py's WifiAgent docstring), so main.py's generic
    # wifi_agent = build_wifi_agent(cfg.wifi_backend_name) never hits
    # a backend that resolves but an agent that doesn't.
    assert set(WIFI_BACKENDS.keys()) == set(WIFI_AGENTS.keys())


def test_bluetooth_backends_has_exactly_bluez():
    assert set(BLUETOOTH_BACKENDS.keys()) == {"bluez"}
