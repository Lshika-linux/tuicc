"""The contracts wifi and bluetooth backends must fulfil.

Two separate ABCs in one file, not two separate files: they're
independent contracts (a wifi backend never needs to know a bluetooth
backend exists), but small enough that splitting them into their own
files/folders, i feel would be more ceremony than the code warrants. What
must stay separate is *implementations* (iwd.py never imports
bluez.py) — that's the boundary that actually matters, not file
count.
"""

from abc import ABC, abstractmethod

from tuicc.connectivity.model import WifiNetwork, BluetoothDevice


class WifiBackend(ABC):
    @abstractmethod
    def get_networks(self) -> list[WifiNetwork]:
        raise NotImplementedError

    @abstractmethod
    def connect(self, ssid: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def scan(self) -> None:
        """Fire-and-forget: ask the backend to (re)scan for networks.
        No return value — a scan's results show up via the next
        get_networks() call, once the backend's own scan completes
        (see IwdBackend.scan()'s docstring for why this doesn't wait).
        """
        raise NotImplementedError

    @abstractmethod
    def is_scanning(self) -> bool:
        """Real ground truth for whether a scan is currently in
        progress — see IwdBackend.is_scanning()'s own docstring for
        why this needs to be its own poll, separate from scan() and
        from get_networks()."""
        raise NotImplementedError


class WifiAgent(ABC):
    """The contract a wifi backend's own D-Bus "Agent"/"secrets agent"
    must fulfil — one per WifiBackend of the same protocol, mutually
    exclusive by config, exactly like WifiBackend/WIFI_BACKENDS itself
    (see registry.py's build_wifi_agent()). Generalized out of
    IwdAgent (the only implementation until NetworkManagerAgent) once
    a second one needed the exact same ROLE through a structurally
    similar but not identical D-Bus mechanism — main.py's own call
    sites read this contract generically (`wifi_agent`), never asking
    which concrete backend it is.

    Deliberately NOT extended to bluetooth: BluezAgent's
    reply_pairing(bool) is a fundamentally different shape than
    reply_passphrase(str) (yes/no vs typed text) — there is, and is
    expected to stay, exactly one bluetooth backend/agent pair, so
    there's nothing to generalize there.

    `mailbox` (an AgentMailbox instance) is a plain constructor-set
    attribute on every implementation, not forced through an abstract
    property here — same "expose the live object, not a snapshot"
    convention AgentMailbox's own module docstring already
    establishes; the ceremony of an abstract property buys nothing
    over just documenting the expectation.
    """
    @abstractmethod
    def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_error(self):
        raise NotImplementedError

    @abstractmethod
    def reply_passphrase(self, passphrase: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def cancel_current(self) -> None:
        raise NotImplementedError


class BluetoothBackend(ABC):
    @abstractmethod
    def get_devices(self) -> list[BluetoothDevice]:
        raise NotImplementedError

    @abstractmethod
    def connect(self, device_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self, device_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def start_discovery(self) -> None:
        """Fire-and-forget: ask the backend to start scanning for
        nearby devices. New devices show up via the next
        get_devices() call, once discovered."""
        raise NotImplementedError

    @abstractmethod
    def stop_discovery(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def is_discovering(self) -> bool:
        """Real ground truth for whether discovery is currently in
        progress — see BluezBackend.is_discovering()'s own docstring
        for why this needs to be its own poll, separate from
        start_discovery() and from get_devices()."""
        raise NotImplementedError
