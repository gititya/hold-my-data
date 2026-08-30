"""Network kill switch.

The PRD promises that no input, or derivative of input, ever reaches a network service.
Presidio does not make that promise for us -- spacy and transformers both fetch weights on
first use. So we enforce it at the socket layer instead of trusting a dependency tree.

Import this module before anything that might phone home. `cli` does so first.
"""

import os
import socket

ALLOW_ENV = "HOLDMYDATA_ALLOW_NETWORK"


class NetworkAccessBlocked(RuntimeError):
    """Raised when something tried to open a network connection during redaction."""


_real_socket = socket.socket
_installed = False


class _BlockedSocket(socket.socket):
    def connect(self, *args, **kwargs):
        raise NetworkAccessBlocked(
            "hold-my-data blocks all network access. Something in the dependency tree "
            f"tried to connect. To download a model deliberately, run once with {ALLOW_ENV}=1 "
            "-- never during redaction."
        )

    connect_ex = connect


def network_allowed() -> bool:
    return os.environ.get(ALLOW_ENV) == "1"


def install_guard() -> bool:
    """Block outbound connections. Returns True if the guard is active."""
    global _installed
    if network_allowed():
        return False
    if not _installed:
        socket.socket = _BlockedSocket
        _installed = True
    return True


def uninstall_guard() -> None:
    """Only for the deliberate model-download path and for tests."""
    global _installed
    socket.socket = _real_socket
    _installed = False


install_guard()
