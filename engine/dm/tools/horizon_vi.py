"""nn::vi (display, layers, the native window) and nv:: (the GPU driver glue) on the Linux host (NX2Linux S7bis): the Python side of engine/dm/gfx/hle_vi.c and hle_nv.c. The host has no vi service:
the game gets static objects that live in a page of guest memory, and the native window it hands to NVN's window builder."""
import struct

from horizon_overrides import hle, noop

RES_OK = 0
OBJECTS = 0x8_0000_0000   # one page: the display, the layer and the native window
DISPLAY, LAYER, NATIVE = OBJECTS, OBJECTS + 0x100, OBJECTS + 0x200


def objects(hz):
    """Map the page of static objects once"""
    if not getattr(hz, '_vi_ready', False):
        hz.aot.try_map(OBJECTS, 0x1000)
        hz.wr32(DISPLAY, 0x50534944); hz.wr32(LAYER, 0x5259414c); hz.wr32(NATIVE, 0x4e49574e); hz.wr64(NATIVE + 8, LAYER)
        hz._vi_ready = True
        hz.layer_size = None
        hz.layer_crop = None


noop('_ZN2nn2vi10InitializeEv', 'display service init; the host has no vi service')
noop('_ZN2nn2vi8FinalizeEv', 'display service shutdown; the host has no vi service')
noop('_ZN2nn2vi12DestroyLayerEPNS0_5LayerE', 'destroy layer; host static object')
noop('_ZN2nn2vi12CloseDisplayEPNS0_7DisplayE', 'close display; host static object')


@hle('_ZN2nn2vi18OpenDefaultDisplayEPPNS0_7DisplayE', 'open default display; the host returns a static object')
def open_display(hz, r): objects(hz); hz.wr64(r[0], DISPLAY); r[0] = RES_OK


@hle('_ZN2nn2vi11CreateLayerEPPNS0_5LayerEPNS0_7DisplayE', 'create layer; the host returns a static object')
def create_layer(hz, r): objects(hz); hz.wr64(r[0], LAYER); r[0] = RES_OK


@hle('_ZN2nn2vi11CreateLayerEPPNS0_5LayerEPNS0_7DisplayEii', 'create layer of a given size; the host returns a static object')
def create_layer_wh(hz, r):
    objects(hz); w, h = r[2] & 0xFFFFFFFF, r[3] & 0xFFFFFFFF
    if 0 < w < 0x10000 and 0 < h < 0x10000: hz.layer_size = (w, h)
    hz.note(f'vi::CreateLayer {w}x{h}'); hz.wr64(r[0], LAYER); r[0] = RES_OK


@hle('_ZN2nn2vi15GetNativeWindowEPPvPNS0_5LayerE', 'native window handle, passed to the NVN window builder; the host returns a static object')
def native_window(hz, r): objects(hz); hz.wr64(r[0], NATIVE); r[0] = RES_OK


@hle('_ZN2nn2vi12SetLayerCropEPNS0_5LayerEiiii', 'layer crop; the host records it and the present crops by it')
def set_crop(hz, r):
    objects(hz); x, y, w, h = (struct.unpack('<i', struct.pack('<I', v & 0xFFFFFFFF))[0] for v in r[1:5])
    hz.layer_crop = None if min(x, y) < 0 or min(w, h) <= 0 else (x, y, w, h); r[0] = RES_OK


@hle('_ZN2nn2vi19SetLayerScalingModeEPNS0_5LayerENS0_11ScalingModeE', 'layer scaling mode; the host present does its own scaling')
def set_scaling(hz, r): r[0] = RES_OK


@hle('_ZN2nn2vi20GetLatestFrameNumberEPmPKNS0_5LayerE', "the layer's latest frame number: the frames presented so far")
def frame_number(hz, r): hz.wr64(r[0], getattr(hz, 'frames_presented', 0)); r[0] = RES_OK


@hle('_ZN2nn2vi16ListFrameHistoryEPNS0_9FrameInfoEiPKNS0_5LayerE', "the layer's frame history: none kept, 0 entries")
def frame_history(hz, r): r[0] = 0


@hle('_ZN2nn2vi32GetDefaultDisplayPanelIdentifierEPNS0_15PanelIdentifierE', 'built-in panel identity; the host has no panel: all zero')
def panel_id(hz, r): hz.wr(r[0], bytes(0x18)); r[0] = RES_OK


@hle('_ZN2nn2vi12ListDisplaysEPNS0_11DisplayInfoEi', 'list displays; the host has one, "Default"')
def list_displays(hz, r):
    if (r[1] & 0xFFFFFFFF) <= 0 or not r[0]: r[0] = 0; return
    hz.wr(r[0], b'Default'.ljust(0x40, b'\0') + b'\1'.ljust(8, b'\0') + struct.pack('<QQQ', 1, 1920, 1080)); r[0] = 1


# ---- nv:: : the driver glue beneath NVN; NVN is replaced wholesale, so these only remember what the game installs ----
@hle('_ZN2nv20SetGraphicsAllocatorEPFPvmmS0_EPFvS0_S0_EPFS0_S0_mS0_ES0_', 'NVN is replaced wholesale; the allocator is recorded')
def nv_set_alloc(hz, r): hz.nv_alloc = tuple(r[:4])


@hle('_ZN2nv28SetGraphicsDevtoolsAllocatorEPFPvmmS0_EPFvS0_S0_EPFS0_S0_mS0_ES0_', 'NVN is replaced wholesale; the devtools allocator is recorded')
def nv_set_dev_alloc(hz, r): hz.nv_dev_alloc = tuple(r[:4])


@hle('_ZN2nv18InitializeGraphicsEPvm', 'NVN is replaced wholesale; the graphics pool is recorded for nv::GetGraphicsMemoryInfo')
def nv_init(hz, r): hz.nv_pool = (r[0], r[1]); hz.note(f'nv::InitializeGraphics: pool {r[1] / 1048576:.2f} MB, recorded')


@hle('_ZN2nv16FinalizeGraphicsEv', 'NVN is replaced wholesale; nothing to finalize')
def nv_fini(hz, r): hz.nv_pool = None


@hle('_ZN2nv21GetGraphicsMemoryInfoEPNS_10MemoryInfoE', 'pool figures from nv::InitializeGraphics, no driver statistics')
def nv_meminfo(hz, r):
    size = hz.nv_pool[1] if getattr(hz, 'nv_pool', None) else 0
    hz.wr(r[0], struct.pack('<QQQQ', size, 0, size, 0))   # [0] total, [1] used (0), [2] free: 0x20 bytes, the size SDK 10 and older have (SDK 11 has 0x40; writing more overruns an old caller)


@hle('_ZN2nv22DumpGraphicsMemoryInfoEv', 'the dump goes to the host log')
def nv_dump(hz, r): hz.note('nv::DumpGraphicsMemoryInfo')
