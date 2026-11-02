"""The SDK functions the host replaces (horizon_overrides.py and the modules that fill it), against a fake guest memory: nn::fs over a RomFS image, nn::account, nn::oe, nn::vi, and the NVN device
(texture layout, clears replayed at submit, presentation). No Remill, no game data."""
import io, os, struct, sys, tempfile, types, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
import horizon_hle as hh
import horizon_overrides as ho
import nvn_layout
import romfs_image
from test_horizon_hle import FakeAot


class Aot(FakeAot):
    def __init__(self):
        super().__init__()
        self.mem = bytearray(0x40000); self.lib.mem = self.mem; self.regions = [(0, 0x40000, b'')]
        self.imports = {}
    def register_import(self, addr, fn, name): self.imports[addr] = (name, fn); return 0


def make():
    aot = Aot()
    ms = types.SimpleNamespace(aot=aot, modules=[], TLS=0x1000, STOP=0xFFFF0000, _n=0)
    def import_address(name): ms._n += 1; return 0x70000000 + 16 * ms._n
    ms._import_address = import_address
    hz = hh.Horizon(ms, out=io.StringIO())
    ho.install(hz)   # fills the table (no module exports anything here, so nothing is selected)
    return hz, aot


def call(hz, symbol, *args):
    r = [0] * 31
    for i, a in enumerate(args): r[i] = a
    ho.TABLE[symbol][0](hz, r)
    return r


def put(aot, addr, data): aot.mem[addr:addr + len(data)] = data


class Fs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        root = Path(cls.dir.name, 'src'); (root / 'data/sub').mkdir(parents=True)
        (root / 'data/a.bin').write_bytes(b'0123456789abcdef'); (root / 'data/sub/b.txt').write_bytes(b'hello')
        cls.img = str(Path(cls.dir.name, 'RomFS.img')); romfs_image.pack(str(root), cls.img)

    @classmethod
    def tearDownClass(cls): cls.dir.cleanup()

    def setUp(self):
        self.hz, self.aot = make()
        self.hz.fs.romfs_img = self.img
        put(self.aot, 0x100, b'rom\0'); self.assertEqual(call(self.hz, '_ZN2nn2fs8MountRomEPKcPvm', 0x100, 0, 0)[0], 0)

    def open(self, path):
        put(self.aot, 0x200, path.encode() + b'\0')
        r = call(self.hz, '_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci', 0x300, 0x200, 1)
        return r[0], self.hz.rd64(0x300)

    def test_a_file_is_opened_sized_and_read(self):
        rc, h = self.open('rom:/data/a.bin'); self.assertEqual(rc, 0)
        self.assertEqual(h & 0xF, 0)   # the low bits are clear, as a heap pointer's are
        call(self.hz, '_ZN2nn2fs11GetFileSizeEPlNS0_10FileHandleE', 0x400, h); self.assertEqual(self.hz.rd64(0x400), 16)
        r = call(self.hz, '_ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvm', 0x408, h, 4, 0x500, 8)
        self.assertEqual((r[0], self.hz.rd64(0x408), bytes(self.aot.mem[0x500:0x508])), (0, 8, b'456789ab'))

    def test_a_read_past_the_end_is_short(self):
        _, h = self.open('rom:/data/a.bin')
        call(self.hz, '_ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvm', 0x408, h, 12, 0x500, 100)
        self.assertEqual(self.hz.rd64(0x408), 4)

    def test_a_missing_file_is_path_not_found(self):
        self.assertEqual(self.open('rom:/data/nope')[0], 0x202)

    def test_an_unmounted_name_is_not_found(self):
        self.assertEqual(self.open('save:/x')[0], 0x202)

    def test_entry_types(self):
        put(self.aot, 0x200, b'rom:/data\0')
        call(self.hz, '_ZN2nn2fs12GetEntryTypeEPNS0_18DirectoryEntryTypeEPKc', 0x300, 0x200); self.assertEqual(self.hz.rd32(0x300), 0)
        put(self.aot, 0x200, b'rom:/data/a.bin\0')
        call(self.hz, '_ZN2nn2fs12GetEntryTypeEPNS0_18DirectoryEntryTypeEPKc', 0x300, 0x200); self.assertEqual(self.hz.rd32(0x300), 1)

    def test_a_directory_lists_its_entries_in_order(self):
        put(self.aot, 0x200, b'rom:/data\0')
        self.assertEqual(call(self.hz, '_ZN2nn2fs13OpenDirectoryEPNS0_15DirectoryHandleEPKci', 0x300, 0x200, 3)[0], 0)
        h = self.hz.rd64(0x300)
        call(self.hz, '_ZN2nn2fs13ReadDirectoryEPlPNS0_14DirectoryEntryENS0_15DirectoryHandleEl', 0x400, 0x1000, h, 8)
        self.assertEqual(self.hz.rd64(0x400), 2)
        names = [bytes(self.aot.mem[0x1000 + i * 0x310:0x1000 + i * 0x310 + 8]).split(b'\0')[0] for i in range(2)]
        self.assertEqual(names, [b'a.bin', b'sub'])
        self.assertEqual(self.aot.mem[0x1000 + 0x304], 1); self.assertEqual(self.aot.mem[0x1000 + 0x310 + 0x304], 0)   # a file, then a directory
        self.assertEqual(struct.unpack_from('<q', self.aot.mem, 0x1000 + 0x308)[0], 16)


class SaveData(unittest.TestCase):
    def test_save_data_is_a_writable_host_directory(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ['XDG_DATA_HOME'] = d
            try:
                hz, aot = make()
                put(aot, 0x100, b'save\0'); put(aot, 0x120, bytes(16))
                self.assertEqual(call(hz, '_ZN2nn2fs13MountSaveDataEPKcRKNS_7account3UidE', 0x100, 0x120)[0], 0)
                put(aot, 0x200, b'save:/slot1.dat\0')
                self.assertEqual(call(hz, '_ZN2nn2fs10CreateFileEPKcl', 0x200, 8)[0], 0)
                self.assertEqual(call(hz, '_ZN2nn2fs10CreateFileEPKcl', 0x200, 8)[0], 0x402)   # exists
                call(hz, '_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci', 0x300, 0x200, 3); h = hz.rd64(0x300)
                put(aot, 0x400, b'DATA1234')
                call(hz, '_ZN2nn2fs9WriteFileENS0_10FileHandleElPKvmRKNS0_11WriteOptionE', h, 0, 0x400, 8, 0x500)
                call(hz, '_ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvm', 0x408, h, 0, 0x600, 8)
                self.assertEqual(bytes(aot.mem[0x600:0x608]), b'DATA1234')
                self.assertTrue(any(Path(d).rglob('slot1.dat')))
            finally: os.environ.pop('XDG_DATA_HOME')


class Account(unittest.TestCase):
    def test_one_local_user_whose_handle_gives_back_its_uid(self):
        hz, aot = make()
        r = call(hz, '_ZN2nn7account22TryOpenPreselectedUserEPNS0_10UserHandleE', 0x100); self.assertEqual(r[0], 1)
        call(hz, '_ZN2nn7account9GetUserIdEPNS0_3UidERKNS0_10UserHandleE', 0x200, 0x100)
        self.assertEqual(bytes(aot.mem[0x200:0x210]), bytes(aot.mem[0x100:0x110]))
        call(hz, '_ZN2nn7account12ListAllUsersEPiPNS0_3UidEi', 0x300, 0x400, 4); self.assertEqual(hz.rd32(0x300), 1)


class Oe(unittest.TestCase):
    def test_docked_1080p_by_default_and_handheld_on_request(self):
        hz, aot = make()
        self.assertEqual(call(hz, '_ZN2nn2oe16GetOperationModeEv')[0], 1)
        call(hz, '_ZN2nn2oe27GetDefaultDisplayResolutionEPiS1_', 0x100, 0x104); self.assertEqual((hz.rd32(0x100), hz.rd32(0x104)), (1920, 1080))
        os.environ['NX2LINUX_OPMODE'] = '0'
        try:
            call(hz, '_ZN2nn2oe27GetDefaultDisplayResolutionEPiS1_', 0x100, 0x104); self.assertEqual((hz.rd32(0x100), hz.rd32(0x104)), (1280, 720))
        finally: os.environ.pop('NX2LINUX_OPMODE')

    def test_language_is_a_code_in_x0(self):
        hz, _ = make()
        self.assertEqual(struct.pack('<Q', call(hz, '_ZN2nn2oe18GetDesiredLanguageEv')[0])[:5], b'en-US')


class Hid(unittest.TestCase):
    def test_one_connected_controller_at_rest_and_no_touch(self):
        hz, aot = make()
        put(aot, 0x100, struct.pack('<I', 0))   # NpadId No1
        self.assertEqual(call(hz, '_ZN2nn3hid15GetNpadStyleSetERKj', 0x100)[0], 1)   # docked: FullKey
        r = call(hz, '_ZN2nn3hid13GetNpadStatesEPNS0_16NpadFullKeyStateEiRKj', 0x200, 2, 0x100); self.assertEqual(r[0], 2)
        s0 = struct.unpack_from('<qQiiiiII', aot.mem, 0x200); s1 = struct.unpack_from('<qQiiiiII', aot.mem, 0x228)
        self.assertEqual((s0[1], s0[6] & 1), (0, 1)); self.assertNotEqual(s0[0], s1[0])   # no buttons, connected, distinct sampling numbers
        self.assertEqual(call(hz, '_ZN2nn3hid13GetNpadStatesEPNS0_17NpadHandheldStateEiRKj', 0x300, 1, 0x100)[0], 0)   # the wrong style reports nothing
        call(hz, '_ZN2nn3hid20GetTouchScreenStatesILm16EEEiPNS0_16TouchScreenStateIXT_EEEi', 0x400, 1)
        self.assertEqual(struct.unpack_from('<i', aot.mem, 0x408)[0], 0)   # a touch count of 0


class Stubs(unittest.TestCase):
    def test_a_stubbed_override_answers_zero_and_is_counted(self):
        hz, _ = make()
        sym = next(s for s in ho.TABLE if 'not ported from' in ho.TABLE[s][1])
        r = [7] * 31; ho.TABLE[sym][0](hz, r)
        self.assertEqual((r[0], hz.stub_calls[sym]), (0, 1))


class Audio(unittest.TestCase):
    def test_a_started_voice_releases_its_wave_buffer_after_its_samples(self):
        import horizon_audio as au
        hz, aot = make(); a = au.state(hz); a.tick_samples, a.rate = 240, 48000
        put(aot, 0x100, struct.pack('<QQiiB', 0x5000, 960, 0, 480, 0))   # a WaveBuffer of 480 samples (two ticks of 240)
        call(hz, '_ZN2nn5audio16AcquireVoiceSlotEPNS0_19AudioRendererConfigEPNS0_9VoiceTypeEiiNS0_12SampleFormatEiPKvm', 0x10, 0x20, 48000, 1, 2, 0, 0)
        self.assertEqual(call(hz, '_ZN2nn5audio16AppendWaveBufferEPNS0_9VoiceTypeEPKNS0_10WaveBufferE', 0x20, 0x100)[0], 1)
        a.tick(); self.assertEqual(call(hz, '_ZN2nn5audio21GetReleasedWaveBufferEPNS0_9VoiceTypeE', 0x20)[0], 0)   # not playing: nothing is consumed
        call(hz, '_ZN2nn5audio17SetVoicePlayStateEPNS0_9VoiceTypeENS1_9PlayStateE', 0x20, 0)
        a.tick(); self.assertEqual(call(hz, '_ZN2nn5audio21GetReleasedWaveBufferEPNS0_9VoiceTypeE', 0x20)[0], 0)   # half played
        a.tick(); self.assertEqual(call(hz, '_ZN2nn5audio21GetReleasedWaveBufferEPNS0_9VoiceTypeE', 0x20)[0], 0x100)
        self.assertEqual(call(hz, '_ZN2nn5audio25GetVoicePlayedSampleCountEPKNS0_9VoiceTypeE', 0x20)[0], 480)

    def test_memory_pools_attach_at_once(self):
        hz, _ = make()
        call(hz, '_ZN2nn5audio17AcquireMemoryPoolEPNS0_19AudioRendererConfigEPNS0_14MemoryPoolTypeEPvm', 0, 0x30, 0, 0)
        self.assertEqual(call(hz, '_ZN2nn5audio18GetMemoryPoolStateEPKNS0_14MemoryPoolTypeE', 0x30)[0], 3)
        call(hz, '_ZN2nn5audio23RequestAttachMemoryPoolEPNS0_14MemoryPoolTypeE', 0x30)
        self.assertEqual(call(hz, '_ZN2nn5audio18GetMemoryPoolStateEPKNS0_14MemoryPoolTypeE', 0x30)[0], 5)

    def test_an_audio_out_buffer_is_released_after_the_time_its_samples_take(self):
        import time
        hz, aot = make()
        call(hz, '_ZN2nn5audio21SetAudioOutBufferInfoEPNS0_14AudioOutBufferEPvmm', 0x100, 0x5000, 4800, 4800)   # 1200 frames of PCM16 stereo = 25 ms
        call(hz, '_ZN2nn5audio20AppendAudioOutBufferEPNS0_8AudioOutEPNS0_14AudioOutBufferE', 0x40, 0x100)
        self.assertEqual(call(hz, '_ZN2nn5audio25GetReleasedAudioOutBufferEPNS0_8AudioOutE', 0x40)[0], 0)
        time.sleep(0.04)
        self.assertEqual(call(hz, '_ZN2nn5audio25GetReleasedAudioOutBufferEPNS0_8AudioOutE', 0x40)[0], 0x100)


class Time(unittest.TestCase):
    def test_the_user_clock_and_the_calendar_follow_the_host(self):
        import time
        hz, aot = make()
        call(hz, '_ZN2nn4time23StandardUserSystemClock14GetCurrentTimeEPNS0_9PosixTimeE', 0x100)
        self.assertLess(abs(hz.rd64(0x100) - time.time()), 2)
        put(aot, 0x200, struct.pack('<q', 86400 * 365))   # 1971-01-01 UTC, whatever the zone gives
        call(hz, '_ZN2nn4time14ToCalendarTimeEPNS0_12CalendarTimeEPNS0_22CalendarAdditionalInfoERKNS0_9PosixTimeE', 0x300, 0x320, 0x200)
        year, month = struct.unpack_from('<hb', aot.mem, 0x300); self.assertEqual((year, month), (1971, 1))


class Layout(unittest.TestCase):
    def test_a_linear_render_target_is_stride_times_height(self):
        d = nvn_layout.Desc(target=1, width=1280, height=720, depth=1, format=0x25, flags=0x40)   # RGBA8, LINEAR_RENDER_TARGET
        self.assertEqual(nvn_layout.layout(d).size, 1280 * 4 * 720)

    def test_a_block_linear_rgba8_is_a_whole_number_of_gobs(self):
        l = nvn_layout.layout(nvn_layout.Desc(target=1, width=256, height=256, depth=1, format=0x25))
        self.assertEqual(l.size % 512, 0); self.assertGreaterEqual(l.size, 256 * 256 * 4); self.assertEqual(l.align, 512)

    def test_bytes_per_block_of_common_formats(self):
        self.assertEqual([nvn_layout.bpb_of(f) for f in (0x25, 0x41, 0x45, 0x79)], [4, 8, 8, 16])   # RGBA8, BC1, BC4, ASTC 4x4

    def test_mip_levels_add_to_the_storage(self):
        one = nvn_layout.layout(nvn_layout.Desc(target=1, width=256, height=256, depth=1, format=0x25, levels=1)).size
        all_ = nvn_layout.layout(nvn_layout.Desc(target=1, width=256, height=256, depth=1, format=0x25, levels=9)).size
        self.assertGreater(all_, one)


class NvnDevice(unittest.TestCase):
    def setUp(self):
        self.hz, self.aot = make()
        self.shown = []
        class Display:
            def present(d, rgba, w, h): self.shown.append((rgba, w, h))
            def events(d): return []
        self.hz.display_factory = lambda: Display()
        self.hz.verbose_nvn = False
        put(self.aot, 0x100, b'nvnDeviceGetProcAddress\0')
        self.loader = call(self.hz, 'nvnBootstrapLoader', 0x100)[0]
        self.nvn = self.hz.nvn

    def fn(self, name, *args):
        put(self.aot, 0x2000, name.encode() + b'\0')
        addr = self.nvn.lookup(name)
        r = [0] * 31
        for i, a in enumerate(args): r[i] = a
        self.aot.imports[addr][1](r)
        return r[0]

    def test_the_loader_gives_import_addresses_by_name(self):
        self.assertNotEqual(self.loader, 0)
        self.assertEqual(self.nvn.lookup('nvnDeviceGetProcAddress'), self.loader)
        self.assertEqual(self.nvn.lookup('nvnNoSuchThing'), 0)

    def test_device_limits_are_the_tegra_x1_ones(self):
        self.fn('nvnDeviceInitialize', 0x4000, 0x4100)
        self.fn('nvnDeviceGetInteger', 0x4000, 0x4, 0x300); self.assertEqual(self.hz.rd32(0x300), 256)
        self.fn('nvnDeviceGetInteger', 0x4000, 0x0, 0x300); self.assertEqual(self.hz.rd32(0x300), 53)

    def test_a_texture_builder_reports_the_layout_size(self):
        self.fn('nvnTextureBuilderSetDefaults', 0x5000)
        self.fn('nvnTextureBuilderSetTarget', 0x5000, 1); self.fn('nvnTextureBuilderSetSize2D', 0x5000, 256, 256); self.fn('nvnTextureBuilderSetFormat', 0x5000, 0x25)
        want = nvn_layout.layout(nvn_layout.Desc(target=1, width=256, height=256, depth=0, format=0x25)).size
        self.assertEqual(self.fn('nvnTextureBuilderGetStorageSize', 0x5000), want)

    def window(self):
        for i, t in enumerate((0x6000, 0x6200)):
            self.fn('nvnTextureBuilderSetDefaults', 0x5000); self.fn('nvnTextureBuilderSetSize2D', 0x5000, 4, 2); self.fn('nvnTextureBuilderSetFormat', 0x5000, 0x25)
            self.assertEqual(self.fn('nvnTextureInitialize', t, 0x5000), 1)
        put(self.aot, 0x6400, struct.pack('<2Q', 0x6000, 0x6200))
        self.fn('nvnWindowBuilderSetDefaults', 0x7000); self.fn('nvnWindowBuilderSetTextures', 0x7000, 2, 0x6400)
        self.assertEqual(self.fn('nvnWindowInitialize', 0x7100, 0x7000), 1)

    def test_a_cleared_window_texture_is_what_is_presented(self):
        self.window()
        idx_addr, sync = 0x7200, 0x7300
        self.fn('nvnSyncInitialize', sync, 0x4000)
        self.assertEqual(self.fn('nvnWindowAcquireTexture', 0x7100, sync, idx_addr), 0); idx = self.hz.rd32(idx_addr); self.assertEqual(idx, 0)
        self.fn('nvnCommandBufferInitialize', 0x7400, 0x4000); self.fn('nvnCommandBufferBeginRecording', 0x7400)
        put(self.aot, 0x7500, struct.pack('<Q', 0x6000)); self.fn('nvnCommandBufferSetRenderTargets', 0x7400, 1, 0x7500, 0, 0, 0)
        put(self.aot, 0x7600, struct.pack('<4f', 1.0, 0.0, 0.5, 1.0)); self.fn('nvnCommandBufferClearColor', 0x7400, 0, 0x7600, 0xF)
        handle = self.fn('nvnCommandBufferEndRecording', 0x7400)
        self.fn('nvnQueueInitialize', 0x7700, 0x7800)
        put(self.aot, 0x7900, struct.pack('<Q', handle)); self.fn('nvnQueueSubmitCommands', 0x7700, 1, 0x7900)
        self.fn('nvnQueuePresentTexture', 0x7700, 0x7100, idx)
        rgba, w, h = self.shown[0]
        self.assertEqual((w, h, rgba[:4]), (4, 2, bytes([255, 0, 128, 255])))
        self.assertEqual(self.nvn.stats['clears'], 1); self.assertEqual(self.hz.frames_presented, 1)

    def test_the_channel_mask_keeps_the_other_channels(self):
        self.window()
        self.fn('nvnCommandBufferInitialize', 0x7400, 0x4000); self.fn('nvnCommandBufferBeginRecording', 0x7400)
        put(self.aot, 0x7500, struct.pack('<Q', 0x6000)); self.fn('nvnCommandBufferSetRenderTargets', 0x7400, 1, 0x7500, 0, 0, 0)
        put(self.aot, 0x7600, struct.pack('<4f', 1.0, 1.0, 1.0, 1.0)); self.fn('nvnCommandBufferClearColor', 0x7400, 0, 0x7600, 0x1)   # red only
        h = self.fn('nvnCommandBufferEndRecording', 0x7400)
        put(self.aot, 0x7900, struct.pack('<Q', h)); self.fn('nvnQueueSubmitCommands', 0x7700, 1, 0x7900)
        self.fn('nvnQueuePresentTexture', 0x7700, 0x7100, 0)
        self.assertEqual(self.shown[0][0][:4], bytes([255, 0, 0, 0]))

    def test_an_event_is_stored_in_the_pool(self):
        self.fn('nvnMemoryPoolBuilderSetDefaults', 0x8000); self.fn('nvnMemoryPoolBuilderSetStorage', 0x8000, 0x9000, 0x1000)
        self.fn('nvnMemoryPoolInitialize', 0x8100, 0x8000)
        self.fn('nvnEventBuilderSetDefaults', 0x8200); self.fn('nvnEventBuilderSetStorage', 0x8200, 0x8100, 0x10)
        self.fn('nvnEventInitialize', 0x8300, 0x8200)
        self.fn('nvnEventSignal', 0x8300, 0, 7); self.assertEqual(self.fn('nvnEventGetValue', 0x8300), 7)
        self.fn('nvnEventSignal', 0x8300, 1, 3); self.assertEqual(self.fn('nvnEventGetValue', 0x8300), 10)
        self.assertEqual(self.hz.rd32(0x9010), 10)   # the game's own memory holds it


if __name__ == '__main__': unittest.main()
