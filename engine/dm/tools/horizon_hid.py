"""nn::hid on the Linux host (NX2Linux S7bis): the Python side of the input half of engine/dm/host/hle_hid.c. The host has no hid service. For now one connected controller (player 1, FullKey when docked,
Handheld otherwise) at rest and no touch, mouse or keyboard; horizon_input.py will feed buttons from the SDL window. The structs follow the public Horizon ABI: NpadState = {s64 sampling number;
u64 buttons; s32 lx, ly, rx, ry; u32 attributes; u32 pad} (0x28 bytes); TouchScreenState<N> = {s64 sampling number; s32 count; s32 reserved; TouchState[N]}."""
import struct

from horizon_overrides import hle, noop
from horizon_oe import opmode

STYLE_FULLKEY, STYLE_HANDHELD = 1, 2
NPAD_NO1, NPAD_HANDHELD = 0, 0x20
ATTR_CONNECTED = 1
NPAD = struct.Struct('<qQiiiiII')


class HidState:
    def __init__(self): self.sampling = 0; self.buttons = 0; self.sticks = (0, 0, 0, 0)


def state(hz):
    if not hasattr(hz, 'hid'): hz.hid = HidState()
    return hz.hid


def style_of(npad_id): return (STYLE_FULLKEY if opmode() == 1 else STYLE_HANDHELD) if npad_id in (NPAD_NO1, NPAD_HANDHELD) else 0


def fill(hz, addr, npad_id):
    s = state(hz); s.sampling += 1
    lx, ly, rx, ry = s.sticks
    hz.wr(addr, NPAD.pack(s.sampling, s.buttons, lx, ly, rx, ry, ATTR_CONNECTED if style_of(npad_id) else 0, 0))


noop('_ZN2nn3hid14InitializeNpadEv', 'connect to the hid service; the host takes input from its own state')
noop('_ZN2nn3hid22SetSupportedNpadIdTypeEPKjm', 'supported controller ids; the host has one player')
noop('_ZN2nn3hid24SetSupportedNpadStyleSetENS_4util10BitFlagSetILi32ENS0_12NpadStyleTagEEE', 'supported controller styles; the host reports what it has')


@hle('_ZN2nn3hid15GetNpadStyleSetERKj', 'controller style query; follows the operation mode')
def get_style(hz, r): r[0] = style_of(hz.rd32(r[0]))


@hle('_ZN2nn3hid24GetSupportedNpadStyleSetEv', 'supported controller styles: full key and handheld')
def get_supported(hz, r): r[0] = STYLE_FULLKEY | STYLE_HANDHELD


def _states(style):
    def f(hz, r):   # (NpadState *out, int count, const uint32_t *id) -> samples written
        n, npad = r[1] & 0xFFFFFFFF, hz.rd32(r[2])
        if n <= 0 or n > 64 or style_of(npad) != style: r[0] = 0; return
        for i in range(n): fill(hz, r[0] + 0x28 * i, npad)
        r[0] = n
    return f


def _state(style):
    def f(hz, r):   # (NpadState *out, const uint32_t *id)
        npad = hz.rd32(r[1])
        if style_of(npad) == style: fill(hz, r[0], npad)
        else: hz.wr(r[0], bytes(0x28))
    return f


for _t, _style in (('16NpadFullKeyState', STYLE_FULLKEY), ('17NpadHandheldState', STYLE_HANDHELD)):
    hle(f'_ZN2nn3hid13GetNpadStatesEPNS0_{_t}EiRKj', 'controller states')(_states(_style))
    hle(f'_ZN2nn3hid12GetNpadStateEPNS0_{_t}ERKj', 'controller current state')(_state(_style))
for _t in ('16NpadJoyDualState', '16NpadJoyLeftState', '17NpadJoyRightState'):
    hle(f'_ZN2nn3hid13GetNpadStatesEPNS0_{_t}EiRKj', 'Joy-Con states: not connected on the host')(_states(-1))
    hle(f'_ZN2nn3hid12GetNpadStateEPNS0_{_t}ERKj', 'Joy-Con current state: not connected on the host')(_state(-1))


# ---- touch screen: nothing touches it yet (header only: a sampling number and a count of 0) ----
def _touch_header(hz, addr):
    s = state(hz); s.sampling += 1
    hz.wr(addr, struct.pack('<qii', s.sampling, 0, 0))


noop('_ZN2nn3hid21InitializeTouchScreenEv', 'connect to the hid touch screen')
noop('_ZN2nn3hid21InitializeTouchScreenEii', 'connect to the hid touch screen (SDK 18 two-argument version)')
for _n in range(1, 17):
    hle(f'_ZN2nn3hid19GetTouchScreenStateILm{_n}EEEvPNS0_16TouchScreenStateIXT_EEE', 'touch screen current state: no touches')(lambda hz, r: _touch_header(hz, r[0]))
    hle(f'_ZN2nn3hid20GetTouchScreenStatesILm{_n}EEEiPNS0_16TouchScreenStateIXT_EEEi', 'touch screen states: one empty entry')(
        lambda hz, r: (_touch_header(hz, r[0]) if (r[1] & 0xFFFFFFFF) > 0 else None, r.__setitem__(0, 1 if (r[1] & 0xFFFFFFFF) > 0 else 0)))


# ---- mouse, keyboard, gestures: not provided ----
noop('_ZN2nn3hid15InitializeMouseEv', 'mouse; not provided by the host')
noop('_ZN2nn3hid18InitializeKeyboardEv', 'keyboard; not provided by the host')
noop('_ZN2nn3hid17InitializeGestureEv', 'gestures; none recognized')
hle('_ZN2nn3hid14GetMouseStatesEPNS0_10MouseStateEi', 'mouse states: 0 entries')(lambda hz, r: r.__setitem__(0, 0))
hle('_ZN2nn3hid13GetMouseStateEPNS0_10MouseStateE', 'mouse state: not connected (0x28 bytes zeroed)')(lambda hz, r: hz.wr(r[0], bytes(0x28)))
hle('_ZN2nn3hid16GetKeyboardStateEPNS0_13KeyboardStateE', 'keyboard state: not connected (0x30 bytes zeroed)')(lambda hz, r: hz.wr(r[0], bytes(0x30)))
hle('_ZN2nn3hid17GetKeyboardStatesEPNS0_13KeyboardStateEi', 'keyboard state history: not connected')(lambda hz, r: r.__setitem__(0, 0))
hle('_ZN2nn3hid16GetGestureStatesEPNS0_12GestureStateEi', 'gesture states: none')(lambda hz, r: r.__setitem__(0, 0))
