"""The no-network promise is the one claim that must be proven, not asserted.

PRD section 6: nothing reaches a network service. Presidio does not guarantee this for us,
so we enforce it and test the enforcement.
"""

import socket

import pytest

from holdmydata import offline


def test_guard_is_installed_by_default():
    assert offline.install_guard() is True


def test_connect_is_blocked():
    offline.install_guard()
    s = socket.socket()
    with pytest.raises(offline.NetworkAccessBlocked):
        s.connect(("1.1.1.1", 80))


def test_guard_can_be_lifted_only_by_env(monkeypatch):
    monkeypatch.setenv(offline.ALLOW_ENV, "1")
    assert offline.network_allowed() is True
    assert offline.install_guard() is False
    monkeypatch.delenv(offline.ALLOW_ENV)
    assert offline.network_allowed() is False
