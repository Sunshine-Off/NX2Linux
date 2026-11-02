"""Horizon on Linux (horizon_hle.py): the SVCs and the IPC framing a retail SDK uses, against a fake guest memory (no Remill, no game data)."""
import io, struct, sys, types, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import horizon_hle as hh


class FakeLib:
    def __init__(self, mem): self.mem = mem
    def aot_poke(self, a, data, n): self.mem[a:a + n] = data[:n]; return 0


class FakeAot:
    def __init__(self):
        self.mem, self.regions, self.mapped = bytearray(0x4000), [(0, 0x4000, b'')], []
        self.lib = FakeLib(self.mem)
    def peek(self, a, n): return bytes(self.mem[a:a + n])
    def try_map(self, base, size): self.mapped.append((base, size)); return True
    def set_svc_handler(self, fn): self.handler = fn


def make():
    aot = FakeAot()
    ms = types.SimpleNamespace(aot=aot, modules=[], TLS=0x1000, STOP=0xFFFF0000)
    ms.modules = []
    return hh.Horizon(ms, out=io.StringIO()), aot


def request(cmd, raw=b'', handles=()):
    """A libnx-style request: header, optional handle descriptor, SFCI + command + token + arguments, 16-byte aligned"""
    has_h = 1 if handles else 0
    out = struct.pack('<2I', 4, ((len(raw) + 16) // 4 + 4) | has_h << 31)
    if has_h: out += struct.pack('<I', len(handles) << 1) + b''.join(struct.pack('<I', h) for h in handles)
    out += b'\0' * (-len(out) % 16)
    return out + b'SFCI' + struct.pack('<III', 0, cmd, 0) + raw


class Svc(unittest.TestCase):
    def test_set_heap_size_maps_the_heap_and_returns_its_base(self):
        hz, aot = make()
        r = [0] * 31; r[1] = 0x400000
        hz.svc(0x01, r, 0)
        self.assertEqual((r[0], r[1]), (0, hz.HEAP_BASE)); self.assertIn((hz.HEAP_BASE, 0x400000), aot.mapped)

    def test_set_heap_size_wants_a_multiple_of_2_mib(self):
        hz, _ = make(); r = [0] * 31; r[1] = 0x1000
        hz.svc(0x01, r, 0); self.assertEqual(r[0], hh.R_INVALID_SIZE)

    def test_an_unimplemented_svc_is_an_error_and_is_counted(self):
        hz, _ = make(); r = [0] * 31
        hz.svc(0x3F, r, 0x1234); self.assertEqual(r[0], hh.R_NOT_IMPLEMENTED); self.assertEqual(hz.unhandled, {'svc0x3f': 1})

    def test_get_system_tick_moves(self):
        hz, _ = make(); a = [0] * 31; hz.svc(0x1e, a, 0)
        import time; time.sleep(0.01); b = [0] * 31; hz.svc(0x1e, b, 0)
        self.assertGreater(b[0], a[0])

    def test_wait_on_a_signaled_event_returns_its_index(self):
        hz, aot = make(); h = hz.new_handle(hh.Event(True))
        aot.mem[0x100:0x108] = struct.pack('<2I', 0xBAD, h)   # 0xBAD names nothing, so waiting on [h] alone is the case
        r = [0] * 31; r[1], r[2], r[3] = 0x104, 1, 0
        hz.svc(0x18, r, 0)
        self.assertEqual((r[0], r[1]), (0, 0))

    def test_a_wait_on_a_handle_that_names_nothing_is_invalid_handle(self):
        hz, aot = make(); aot.mem[0x100:0x104] = struct.pack('<I', 0xBAD)
        r = [0] * 31; r[1], r[2], r[3] = 0x100, 1, 0
        hz.svc(0x18, r, 0)
        self.assertEqual(r[0], hh.R_INVALID_HANDLE)

    def test_a_wait_that_times_out_says_so(self):
        hz, aot = make(); h = hz.new_handle(hh.Event(False))
        aot.mem[0x100:0x104] = struct.pack('<I', h)
        r = [0] * 31; r[1], r[2], r[3] = 0x100, 1, 2_000_000   # 2 ms
        hz.svc(0x18, r, 0)
        self.assertEqual(r[0], hh.R_TIMEOUT)

    def test_a_wait_is_woken_by_another_thread_signalling_the_event(self):
        import threading, time
        hz, aot = make(); e = hh.Event(False); h = hz.new_handle(e)
        aot.mem[0x100:0x104] = struct.pack('<I', h)
        def signal():
            time.sleep(0.05); r = [0] * 31; r[0] = h; hz.svc(0x11, r, 0)
        threading.Thread(target=signal).start()
        r = [0] * 31; r[1], r[2], r[3] = 0x100, 1, (1 << 64) - 1   # forever
        t0 = time.monotonic(); hz.svc(0x18, r, 0)
        self.assertEqual((r[0], r[1]), (0, 0)); self.assertLess(time.monotonic() - t0, 2)

    def test_create_event_gives_a_writable_and_a_readable_handle_of_one_event(self):
        hz, _ = make(); r = [0] * 31; hz.svc(0x45, r, 0)
        w, rd = r[1], r[2]
        self.assertIs(hz.handles[w], hz.handles[rd])


class Sync(unittest.TestCase):
    """Horizon's mutex word and condition variable (ArbitrateLock / ArbitrateUnlock, WaitProcessWideKeyAtomic / SignalProcessWideKey)"""
    MUTEX, KEY = 0x200, 0x300
    OWNER, WAITER = 0x11, 0x22

    def call(self, hz, num, *args):
        r = [0] * 31
        for i, a in enumerate(args): r[i] = a
        hz.svc(num, r, 0); return r

    def test_lock_returns_at_once_when_the_word_is_not_the_owner_with_listeners(self):
        hz, aot = make(); hz.wr32(self.MUTEX, 0)
        self.assertEqual(self.call(hz, 0x1a, self.OWNER, self.MUTEX, self.WAITER)[0], 0)

    def test_unlock_with_no_waiter_clears_the_word(self):
        hz, _ = make(); hz.wr32(self.MUTEX, self.OWNER | hz.LISTENERS)
        self.call(hz, 0x1b, self.MUTEX)
        self.assertEqual(hz.rd32(self.MUTEX), 0)

    def test_a_waiting_thread_gets_the_word_when_the_owner_unlocks(self):
        import threading, time
        hz, _ = make(); hz.wr32(self.MUTEX, self.OWNER | hz.LISTENERS)
        done = []
        def waiter():
            hz.local.thread = hh.KThread(hz, 0, 0, 0, 44, 0); self.call(hz, 0x1a, self.OWNER, self.MUTEX, self.WAITER); done.append(time.monotonic())
        t = threading.Thread(target=waiter); t.start(); time.sleep(0.05)
        self.assertEqual(done, [])   # still blocked
        self.call(hz, 0x1b, self.MUTEX); t.join(2)
        self.assertEqual(len(done), 1); self.assertEqual(hz.rd32(self.MUTEX), self.WAITER)

    def test_condvar_wait_times_out_and_takes_the_mutex_again(self):
        hz, _ = make(); hz.wr32(self.MUTEX, self.WAITER)   # held by the caller
        r = self.call(hz, 0x1c, self.MUTEX, self.KEY, self.WAITER, 3_000_000)
        self.assertEqual(r[0], hh.R_TIMEOUT); self.assertEqual(hz.rd32(self.MUTEX), self.WAITER)

    def test_signal_wakes_a_condvar_waiter(self):
        import threading, time
        hz, _ = make(); hz.wr32(self.MUTEX, self.WAITER)
        out = []
        def waiter():
            hz.local.thread = hh.KThread(hz, 0, 0, 0, 44, 0); out.append(self.call(hz, 0x1c, self.MUTEX, self.KEY, self.WAITER, (1 << 64) - 1)[0])
        t = threading.Thread(target=waiter); t.start(); time.sleep(0.05)
        self.call(hz, 0x1d, self.KEY, 1); t.join(2)
        self.assertEqual(out, [0])


class Ipc(unittest.TestCase):
    def connect(self, hz):
        r = [0] * 31; hz.aot.mem[0x200:0x204] = b'sm:\0'; r[1] = 0x200
        hz.svc(0x1f, r, 0); self.assertEqual(r[0], 0); return r[1]

    def send(self, hz, handle, msg):
        hz.aot.mem[0x1000:0x1000 + len(msg)] = msg
        r = [0] * 31; r[0] = handle; hz.svc(0x21, r, 0)
        return r[0], bytes(hz.aot.mem[0x1000:0x1100])

    def test_an_unknown_named_port_is_not_found(self):
        hz, aot = make(); aot.mem[0x200:0x206] = b'nope:\0'; r = [0] * 31; r[1] = 0x200
        hz.svc(0x1f, r, 0); self.assertEqual(r[0], hh.R_NOT_FOUND)

    def test_sm_initialize_replies_with_a_zero_result(self):
        hz, _ = make(); h = self.connect(hz)
        rc, out = self.send(hz, h, request(0))
        self.assertEqual(rc, 0)
        self.assertEqual(out[16:20], b'SFCO'); self.assertEqual(struct.unpack_from('<I', out, 24)[0], 0)

    def test_get_service_of_a_missing_service_names_it_and_fails(self):
        hz, _ = make(); h = self.connect(hz)
        rc, out = self.send(hz, h, request(1, b'fsp-srv\0'))
        self.assertEqual(rc, 0)
        self.assertNotEqual(struct.unpack_from('<I', out, 24)[0], 0)
        self.assertEqual(hz.missing_services, {'fsp-srv'})

    def test_an_unimplemented_command_is_an_error_reply(self):
        hz, _ = make(); h = self.connect(hz)
        _, out = self.send(hz, h, request(99))
        self.assertEqual(struct.unpack_from('<I', out, 24)[0], hh.R_NOT_IMPLEMENTED)


if __name__ == '__main__': unittest.main()
