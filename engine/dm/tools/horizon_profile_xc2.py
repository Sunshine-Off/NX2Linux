"""Per-title workarounds for Xenoblade Chronicles 2 (0100E95004038000), declared as such: each one replaces a function of the GAME (main), not of the SDK, and says what is missing in the host that makes it necessary.
They are applied only when NX2LINUX_PROFILE names the title (horizon_profile.apply). Remove an entry when the host grows the feature it stands for."""
from horizon_overrides import hle

TITLE = '0100e95004038000'
PROFILE = {}


def workaround(symbol, why):
    def deco(fn): PROFILE[symbol] = (fn, why); return fn
    return deco
