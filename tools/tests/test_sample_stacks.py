"""tools/sample_stacks.py (NX2Linux issue 19): parsing what gdb prints, deciding what each thread is doing, the report, the choice of the process, and the refusal of gdb. The text below is gdb's own format (a real capture of a small
test program, then threads written in the same shape for the host: Python, ctypes, the recompiled code, the GIL). The last class runs the real gdb on a small C program when gdb, gcc and the right to attach are there."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import sample_stacks as ss

REAL = """\
Thread 4 (Thread 0x7fe51cb796c0 (LWP 296694) "spinner"):
#0  0x00000000004004e1 in spin_here () at /tmp/x/t.c:8
#1  0x0000000000400511 in spinner (a=0x0) at /tmp/x/t.c:9
#2  0x00007fe51cbefc42 in start_thread () from /lib64/libc.so.6
#3  0x00007fe51cc735ac in __clone3 () from /lib64/libc.so.6

Thread 3 (Thread 0x7fe51c3786c0 (LWP 296695) "waiter"):
#0  0x00007fe51cbf8352 in __syscall_cancel_arch () from /lib64/libc.so.6
#1  0x00007fe51cbec54c in __internal_syscall_cancel () from /lib64/libc.so.6
#2  0x00007fe51cbec897 in __futex_abstimed_wait_cancelable64 () from /lib64/libc.so.6
#3  0x00007fe51cbef0ec in pthread_cond_wait@@GLIBC_2.3.2 () from /lib64/libc.so.6
#4  0x000000000040054f in waiter (a=0x0) at /tmp/x/t.c:10
#5  0x00007fe51cbefc42 in start_thread () from /lib64/libc.so.6
"""

HOST = """\
Thread 6 (Thread 0x7f01 (LWP 101) "guest-12"):
#0  0x00007f0200001000 in sub_8061a40 () from /home/u/.cache/nx2linux/aot-cache/lib/abc.so
#1  0x00007f0200002000 in sub_8061b00 () from /home/u/.cache/nx2linux/aot-cache/lib/abc.so
#2  0x00007f0300000000 in aot_run () from /home/u/.cache/nx2linux/aot-cache/lib/abc.so
#3  0x00007f0400000000 in ffi_call_unix64 () from /usr/lib64/libffi.so.8
#4  0x00007f0500000000 in _PyEval_EvalFrameDefault () from /usr/lib64/libpython3.14.so.1.0

Thread 5 (Thread 0x7f02 (LWP 102) "guest-13"):
#0  0x00007f0600000000 in __futex_abstimed_wait_common () from /lib64/libc.so.6
#1  0x00007f0600000010 in pthread_cond_timedwait@@GLIBC_2.3.2 () from /lib64/libc.so.6
#2  0x00007f0500000020 in take_gil () from /usr/lib64/libpython3.14.so.1.0
#3  0x00007f0500000030 in PyEval_RestoreThread () from /usr/lib64/libpython3.14.so.1.0
#4  0x00007f0400000040 in ffi_call_unix64 () from /usr/lib64/libffi.so.8

Thread 4 (Thread 0x7f03 (LWP 103) "nvn-queue"):
#0  0x00007f0700000000 in nx2gpu_draw () from /home/u/.cache/nx2linux/build-host/libnx2linux_gpu.so
#1  0x00007f0700000010 in nvn_replay_stream () from /home/u/.cache/nx2linux/build-host/libnx2linux_gpu.so
#2  0x00007f0400000050 in ffi_call_unix64 () from /usr/lib64/libffi.so.8

Thread 3 (Thread 0x7f04 (LWP 104) "nvn-queue"):
#0  0x00007f0800000000 in ?? () from /usr/lib64/libnvidia-glcore.so.580
#1  0x00007f0800000010 in vkQueueSubmit () from /usr/lib64/libvulkan.so.1

Thread 2 (Thread 0x7f05 (LWP 105) "guest-14"):
#0  0x00007f0500000060 in _PyEval_EvalFrameDefault () from /usr/lib64/libpython3.14.so.1.0
#1  0x00007f0500000070 in PyEval_EvalCode () from /usr/lib64/libpython3.14.so.1.0

Thread 1 (Thread 0x7f06 (LWP 100) "python"):
#0  0x00007f0900000000 in __syscall_cancel_arch () from /lib64/libc.so.6
#1  0x00007f0900000010 in __internal_syscall_cancel () from /lib64/libc.so.6
#2  0x00007f0900000020 in clock_nanosleep@GLIBC_2.2.5 () from /lib64/libc.so.6
#3  0x00007f0900000030 in nanosleep () from /lib64/libc.so.6
"""


class Parsing(unittest.TestCase):
    def test_threads_names_and_frames(self):
        t = ss.parse(REAL)
        self.assertEqual([(x['num'], x['lwp'], x['name']) for x in t], [(4, 296694, 'spinner'), (3, 296695, 'waiter')])
        self.assertEqual([f[1] for f in t[0]['frames']], ['spin_here', 'spinner', 'start_thread', '__clone3'])
        self.assertEqual(t[0]['frames'][0][2], '/tmp/x/t.c:8'); self.assertEqual(t[1]['frames'][3][1], 'pthread_cond_wait')   # the version after @ is dropped
        self.assertEqual(t[1]['frames'][3][2], '/lib64/libc.so.6')

    def test_a_thread_without_a_name_and_noise_lines(self):
        t = ss.parse('[New LWP 5]\nUsing host libthread_db\nThread 1 (Thread 0x1 (LWP 7)):\n#0  0x1 in main ()\n[Inferior 1 (process 7) detached]\n')
        self.assertEqual(len(t), 1); self.assertEqual(t[0]['name'], ''); self.assertEqual(t[0]['frames'][0][1], 'main')

    def test_nothing_to_parse(self): self.assertEqual(ss.parse('gdb: no process'), [])


class Kinds(unittest.TestCase):
    def kinds(self, text): return {t['name'] + str(t['num']): ss.kind(t) for t in ss.parse(text)}

    def test_what_a_real_capture_says(self):
        k = self.kinds(REAL)
        self.assertEqual(k['spinner4'][0], 'running'); self.assertEqual(k['spinner4'][2], 'spin_here')
        self.assertEqual(k['waiter3'], ('waiting', 'libc', 'pthread_cond_wait'))   # the cancellation wrappers at the top do not hide the wait

    def test_the_host(self):
        k = self.kinds(HOST)
        self.assertEqual(k['guest-126'], ('running', 'recompiled game code', 'sub_8061a40'))
        self.assertEqual(k['guest-135'][0], 'waiting for the GIL')                      # parked in a condition wait inside take_gil: the GIL, not an event
        self.assertEqual(k['nvn-queue4'][:2], ('running', 'host GPU library'))
        self.assertEqual(k['nvn-queue3'][:2], ('running', 'Vulkan driver'))
        self.assertEqual(k['guest-142'][:2], ('running', 'Python and ctypes'))
        self.assertEqual(k['python1'], ('waiting', 'libc', 'nanosleep'))

    def test_the_aot_runtime_is_apart_from_the_recompiled_code(self):
        t = ss.parse('Thread 1 (Thread 0x1 (LWP 1) "guest-2"):\n#0  0x1 in __remill_read_memory_64 () from /x/lib/k.so\n#1  0x2 in sub_8000 () from /x/lib/k.so\n')
        self.assertEqual(ss.kind(t[0])[:2], ('running', 'AOT runtime'))

    def test_an_empty_stack_is_running_nowhere_known(self): self.assertEqual(ss.kind({'frames': [], 'name': 'x'})[:2], ('running', 'other'))

    def test_a_futex_without_a_known_wrapper_is_still_a_wait(self):
        t = ss.parse('Thread 1 (Thread 0x1 (LWP 1) "x"):\n#0  0x1 in __syscall_cancel_arch () from /lib64/libc.so.6\n#1  0x2 in futex_wait () from /lib64/libc.so.6\n')
        self.assertEqual(ss.kind(t[0])[0], 'waiting')


class Report(unittest.TestCase):
    def test_the_numbers_by_hand(self):
        res = ss.analyse([HOST, HOST])
        self.assertEqual(res['samples'], 2); self.assertEqual(res['thread_samples'], 12)
        self.assertEqual(res['states'], {'running': 8, 'waiting for the GIL': 2, 'waiting': 2})
        self.assertEqual(res['running_by_kind'], {'recompiled game code': 2, 'host GPU library': 2, 'Vulkan driver': 2, 'Python and ctypes': 2})
        self.assertEqual(res['by_thread_kind']['nvn-queue'], {'running': 4}); self.assertEqual(res['by_thread_kind']['guest-*'], {'running': 4, 'waiting for the GIL': 2})

    def test_the_table_says_what_it_needs_to(self):
        text = ss.table(ss.analyse([HOST]), {'gdb_seconds': [1.5, 2.5]})
        for s in ('waiting for the GIL', 'recompiled game code', 'Vulkan driver', 'sub_8061a40', 'guest-*', 'nvn-queue', 'stopped about 2.0 s per sample', 'orders of magnitude'): self.assertIn(s, text)

    def test_no_division_by_zero_on_nothing(self): self.assertIn('0 samples', ss.table(ss.analyse([])))

    def test_report_reads_a_folder_and_writes_both_files(self):
        with tempfile.TemporaryDirectory() as d:
            for i in (1, 2): (Path(d) / f'sample-{i:02d}.txt').write_text(HOST)
            (Path(d) / 'meta.json').write_text(json.dumps({'gdb_seconds': [1.0, 1.0]}))
            text = ss.do_report(d)
            self.assertIn('2 samples', text); self.assertEqual(json.loads((Path(d) / 'report.json').read_text())['samples'], 2); self.assertTrue((Path(d) / 'report.txt').is_file())

    def test_an_empty_folder_is_named(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(SystemExit) as e: ss.do_report(d)
        self.assertIn('no sample', str(e.exception))


class Choosing(unittest.TestCase):
    def test_a_process_is_found_by_its_command_line_and_not_the_tool_itself(self):
        with tempfile.TemporaryDirectory() as d:
            for pid, cmd in ((201, b'python\0-I\0horizon_run.py\0--modules\0x'), (202, b'bash\0-c\0ls'), (203, b'python\0tools/sample_stacks.py\0run\0--match\0horizon_run.py'), (204, b'gdb\0-p\0201\0horizon_run.py')):
                (Path(d) / str(pid)).mkdir(); (Path(d) / str(pid) / 'cmdline').write_bytes(cmd)
            self.assertEqual([p for p, _ in ss.find_pid('horizon_run.py', proc=d)], [201])


class Run(unittest.TestCase):
    def args(self, **kw):
        a = dict(pid=1234, match=None, samples=3, interval=2.0, depth=30, out=None, gdb='gdb'); a.update(kw)
        return type('A', (), a)()

    def test_samples_are_taken_in_turn_and_saved(self):
        with tempfile.TemporaryDirectory() as d:
            sleeps, said = [], []
            out = ss.do_run(self.args(out=d), say=said.append, sleep=sleeps.append, sample=lambda pid, depth, gdb: (REAL, 0.25))
            self.assertEqual(sorted(p.name for p in Path(out).glob('sample-*.txt')), ['sample-01.txt', 'sample-02.txt', 'sample-03.txt'])
            self.assertEqual(sleeps, [2.0, 2.0]); self.assertEqual(json.loads((Path(out) / 'meta.json').read_text())['gdb_seconds'], [0.25] * 3); self.assertEqual(len(said), 3)

    def test_several_matching_processes_are_not_guessed(self):
        orig = ss.find_pid
        ss.find_pid = lambda text: [(1, 'a'), (2, 'b')]
        try:
            with self.assertRaises(SystemExit) as e: ss.do_run(self.args(pid=None, match='x'))
            self.assertIn('more than one process', str(e.exception))
        finally: ss.find_pid = orig

    def test_no_matching_process_is_named(self):
        orig = ss.find_pid
        ss.find_pid = lambda text: []
        try:
            with self.assertRaises(SystemExit) as e: ss.do_run(self.args(pid=None, match='nothing_like_this'))
            self.assertIn("no process has 'nothing_like_this'", str(e.exception))
        finally: ss.find_pid = orig

    def test_gdb_being_refused_is_a_permission_error_with_the_reason(self):
        class P: stdout = ''; stderr = 'ptrace: Operation not permitted.'
        with self.assertRaises(PermissionError) as e: ss.one_sample(1, 30, run=lambda *a, **k: P())
        self.assertIn('Operation not permitted', str(e.exception))

    def test_gdb_printing_nothing_else_is_an_error(self):
        class P: stdout = ''; stderr = 'something odd'
        with self.assertRaises(RuntimeError): ss.one_sample(1, 30, run=lambda *a, **k: P())

    def test_the_gdb_command_does_not_ask_questions_or_download(self):
        seen = []
        class P: stdout = REAL; stderr = ''
        ss.one_sample(77, 25, gdb='gdb', run=lambda cmd, **k: (seen.append(cmd), P())[1])
        c = seen[0]
        for s in ('-batch', '-nx', 'set debuginfod enabled off', 'set pagination off', '77', 'thread apply all bt 25'): self.assertIn(s, ' '.join(c))


class CommandLine(unittest.TestCase):
    def test_report_from_the_command_line(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'sample-01.txt').write_text(HOST)
            r = subprocess.run([sys.executable, str(TOOLS / 'sample_stacks.py'), 'report', d], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr); self.assertIn('recompiled game code', r.stdout)

    def test_it_needs_a_process(self): self.assertNotEqual(subprocess.run([sys.executable, str(TOOLS / 'sample_stacks.py'), 'run'], capture_output=True, text=True, timeout=60).returncode, 0)

    def test_a_missing_gdb_is_said_and_not_installed(self):
        r = subprocess.run([sys.executable, str(TOOLS / 'sample_stacks.py'), 'run', '--pid', '1', '--gdb', 'no_such_gdb_here'], capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0); self.assertIn('is not installed', r.stderr); self.assertIn('I do not install it', r.stderr)


C_PROGRAM = textwrap.dedent("""
    #define _GNU_SOURCE
    #include <pthread.h>
    #include <stdio.h>
    #include <unistd.h>
    static pthread_mutex_t m = PTHREAD_MUTEX_INITIALIZER; static pthread_cond_t c = PTHREAD_COND_INITIALIZER;
    volatile unsigned long x;
    __attribute__((noinline)) void spin_here(void) { for (;;) x++; }
    void *spinner(void *a) { (void)a; pthread_setname_np(pthread_self(), "guest-1"); spin_here(); return 0; }
    void *waiter(void *a) { (void)a; pthread_setname_np(pthread_self(), "guest-2"); pthread_mutex_lock(&m); for (;;) pthread_cond_wait(&c, &m); return 0; }
    int main(void) { pthread_t t[2]; pthread_create(&t[0], 0, spinner, 0); pthread_create(&t[1], 0, waiter, 0); puts("up"); fflush(stdout); for (;;) sleep(1); }
""")


@unittest.skipUnless(shutil.which('gdb') and shutil.which('gcc'), 'needs gdb and gcc')
class RealGdb(unittest.TestCase):
    def test_a_real_process_is_sampled_and_its_threads_are_told_apart(self):
        with tempfile.TemporaryDirectory() as d:
            src, exe = Path(d) / 't.c', Path(d) / 't'
            src.write_text(C_PROGRAM)
            if subprocess.run(['gcc', '-g', '-O0', '-pthread', str(src), '-o', str(exe)], capture_output=True).returncode: self.skipTest('gcc could not build the test program')
            p = subprocess.Popen([str(exe)], stdout=subprocess.PIPE); p.stdout.readline()
            try:
                try: out = ss.do_run(type('A', (), dict(pid=p.pid, match=None, samples=2, interval=0.2, depth=20, out=str(Path(d) / 'o'), gdb='gdb'))(), say=lambda s: None)
                except PermissionError as e: self.skipTest(f'this system does not let gdb attach: {e}')
                res = ss.analyse([f.read_text() for f in sorted(Path(out).glob('sample-*.txt'))])
            finally: p.kill(); p.wait()
        self.assertEqual(res['samples'], 2)
        self.assertEqual(res['by_thread_kind']['guest-*'], {'running': 2, 'waiting': 2})   # one spins, one waits in a condition, in each sample
        self.assertIn(['other', 'spin_here', 2], res['top_running'])


if __name__ == '__main__': unittest.main()
