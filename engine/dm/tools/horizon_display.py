"""The window a recompiled game draws into (NX2Linux S7bis): ctypes over linux/host's libnx2linux_display.so (SDL window + Vulkan swapchain, lavapipe or a real GPU). present() shows an RGBA8 frame.
The library is found through NX2LINUX_DISPLAY_LIB, else ~/.cache/nx2linux/build-host (where `cmake -B` of linux/host puts it)."""
import ctypes, os
from pathlib import Path

EVENTS = {1: 'quit', 2: 'resize', 3: 'key_down', 4: 'key_up', 5: 'pad_added', 6: 'pad_removed', 7: 'pad_down', 8: 'pad_up', 9: 'pad_axis'}


class DisplayError(Exception): pass


def library_path():
    p = os.environ.get('NX2LINUX_DISPLAY_LIB') or str(Path.home() / '.cache/nx2linux/build-host/libnx2linux_display.so')
    return p if os.path.exists(p) else None


class Display:
    def __init__(self, title='NX2Linux', width=1280, height=720, hidden=False, shader_dir=None):
        path = library_path()
        if path is None: raise DisplayError('libnx2linux_display.so not found: build linux/host (cmake -S linux/host -B ~/.cache/nx2linux/build-host && cmake --build ...) or set NX2LINUX_DISPLAY_LIB')
        L = self.lib = ctypes.CDLL(path)
        L.nx2_display_error.restype = ctypes.c_char_p
        L.nx2_display_gpu.restype = ctypes.c_char_p
        L.nx2_display_frames.restype = ctypes.c_uint64
        L.nx2_display_open.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_char_p]
        L.nx2_display_present.argtypes = [ctypes.c_char_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]
        if L.nx2_display_open(title.encode(), width, height, int(hidden), shader_dir.encode() if shader_dir else None):
            raise DisplayError(L.nx2_display_error().decode())
        self.open = True

    @property
    def gpu(self): return self.lib.nx2_display_gpu().decode()

    @property
    def frames(self): return self.lib.nx2_display_frames()

    def present(self, rgba, width, height, stride=0):
        """rgba: bytes of height rows of width RGBA8 pixels (stride bytes per row, 0: tightly packed)"""
        if len(rgba) < (stride or width * 4) * height: raise DisplayError(f'frame of {len(rgba)} bytes is too small for {width}x{height}')
        if self.lib.nx2_display_present(bytes(rgba), width, height, stride): raise DisplayError(self.lib.nx2_display_error().decode())

    def events(self):
        """The input events waiting: [(name, key, width, height)]"""
        out, ev = [], (ctypes.c_int32 * 4)()
        while self.lib.nx2_display_poll(ev): out.append((EVENTS.get(ev[0], str(ev[0])), ev[1], ev[2], ev[3]))
        return out

    def close(self):
        if self.open: self.lib.nx2_display_close(); self.open = False

    def __enter__(self): return self
    def __exit__(self, *a): self.close()
