"""IPv4 address lookup for a network interface — kernel-level, DHCP-
client-assigned data that neither wifi backend actually owns. iwd's
own D-Bus tree has no IP/DHCP interface at all (confirmed live,
2026-08-15, via a full GetManagedObjects walk); IP assignment happens
entirely outside it, done by whatever DHCP client the system runs
(systemd-networkd, dhcpcd, or NetworkManager's own internal client).
So this is one small, backend-agnostic function both connectivity/
iwd.py and connectivity/networkmanager.py call, keyed off the
interface name each backend already knows (e.g. "wlan0") — same
"reuse the one already-correct tool" precedent brightness.py/sensors.py
already set for brightnessctl/lm-sensors, `ip` (iproute2) here.
"""

import subprocess


def get_ipv4_address(iface: str) -> str | None:
    """None on any failure — missing/down interface, no `ip` binary,
    or genuinely no IPv4 assigned yet (still DHCP-negotiating). Same
    degrade-honestly tolerance as every other optional-tool wrapper in
    this codebase; this is real "unknown", not worth distinguishing
    further from the caller's point of view.
    """
    try:
        proc = subprocess.run(
            ["ip", "-4", "-brief", "addr", "show", iface],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return _parse_ip_brief_output(proc.stdout)


def _parse_ip_brief_output(output: str) -> str | None:
    """Pure parsing half, testable without a real interface. `ip -4
    -brief addr show <iface>` prints one line:
    "<iface>  <STATE>  <addr>/<prefix>  [<addr2>/<prefix2> ...]" —
    takes the first address, strips the /<prefix> CIDR suffix. Empty
    (no third column at all) means the interface exists but has no
    IPv4 yet — a real, expected case, not a parse error.
    """
    line = output.strip()
    if not line:
        return None
    parts = line.split()
    if len(parts) < 3:
        return None
    return parts[2].split("/")[0]
