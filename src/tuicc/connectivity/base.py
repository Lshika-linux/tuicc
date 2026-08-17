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

from tuicc.connectivity.model import WifiNetwork, BluetoothDevice, AdapterInfo, ConnectionDiagnostics, BluetoothAdapterInfo


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

    @abstractmethod
    def forget(self, ssid: str) -> None:
        """Deletes a saved (known) network's stored credentials —
        connecting to it again afterward needs a fresh passphrase.
        Raises on failure, same StatusWorker action-error convention
        connect()/disconnect() already follow."""
        raise NotImplementedError

    @abstractmethod
    def connect_hidden(self, ssid: str) -> None:
        """Connects to a network that doesn't broadcast its SSID —
        can't be found in a normal scan, so the name has to come from
        the user typing it rather than picking a row. Same agent-
        passphrase round trip as connect() if it needs credentials."""
        raise NotImplementedError

    @abstractmethod
    def get_adapter_info(self) -> AdapterInfo | None:
        """Static-ish info about the wifi adapter/device itself (name,
        address, model, vendor, supported modes, current mode/power/
        state) — None only when no wifi adapter could be found at all
        (same "genuinely nothing there" case get_networks() already
        returns [] for, distinct from a real poll failure which raises
        instead, same as every other backend method here)."""
        raise NotImplementedError

    @abstractmethod
    def set_powered(self, powered: bool) -> None:
        """Turns the wifi radio on/off. Raises on failure, same
        convention as every other action method here."""
        raise NotImplementedError

    @abstractmethod
    def get_connection_diagnostics(self) -> ConnectionDiagnostics | None:
        """Live, negotiated details of whichever network is CURRENTLY
        connected — None when nothing is connected right now (a real
        "genuinely nothing there" case, not a failure). See
        ConnectionDiagnostics' own docstring for why this is genuinely
        different information from WifiNetwork.security, not a
        duplicate poll of the same thing."""
        raise NotImplementedError


class WifiAgent(ABC):
    """The contract a wifi backend's own D-Bus "Agent"/"secrets agent"
    must fulfil — one per WifiBackend, mutually exclusive by config
    (see registry.py's build_wifi_agent()). main.py's call sites read
    this contract generically (`wifi_agent`), never asking which
    concrete backend it is. Deliberately not extended to bluetooth:
    BluezAgent's reply_pairing(bool) is a fundamentally different shape
    than reply_passphrase(str). `mailbox` is a plain constructor-set
    attribute on every implementation, not an abstract property — see
    AgentMailbox's own module docstring for that convention.
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

    @abstractmethod
    def get_adapter_info(self) -> BluetoothAdapterInfo | None:
        """The controller/radio itself — mirrors WifiBackend's own
        get_adapter_info(). None only when no Bluetooth adapter could
        be found at all."""
        raise NotImplementedError

    @abstractmethod
    def set_powered(self, powered: bool) -> None:
        raise NotImplementedError

    @abstractmethod
    def set_pairable(self, pairable: bool) -> None:
        raise NotImplementedError
