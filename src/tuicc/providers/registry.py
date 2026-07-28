"""Registry mapping provider names (from config) to Provider classes.

Kept separate from base.py to avoid a circular import: base.py defines
the Provider contract, sway.py (and future i3.py, scroll.py....) import
base.py to implement it — this file imports all of them, so it must
not be imported by any of them.
"""

from tuicc.providers.base import Provider
from tuicc.providers.sway import SwayProvider
from tuicc.providers.i3 import I3Provider

PROVIDERS = {
    "sway": SwayProvider,
    "i3": I3Provider,
}


def build_provider(name: str) -> Provider:
    if name not in PROVIDERS:
        raise ValueError(f"Unknown provider: {name!r}. Available: {list(PROVIDERS.keys())}")
    return PROVIDERS[name]()
