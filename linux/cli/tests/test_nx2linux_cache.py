"""`nx2linux.py cache-prune` (issue #18): the old finished libraries of an AOT cache, listed first and removed only on request.
A cache folder holds obj/ (one object per lifted function) and lib/ (one finished library per build key, and nothing removes the old ones).
  · it refuses a folder that is not a cache, such as a folder of modules;
  · by default it only lists what it would remove, and removes nothing;
  · with --delete it removes the libraries older than the newest --keep, with their .json, and never touches obj/;
  · it only touches files named like a build key (64 hex digits), never a symbolic link and never anything else.
The caches are built in a temporary folder by the tests, from nothing of any game."""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'linux' / 'cli'))

import nx2linux_cache as nc

class Key:
    @staticmethod
    def format(n): return (f'{n:x}' * 64)[:64]   # 64 hex digits that differ from the first one on


KEY = Key


def make_cache(root, libs):
    """libs: [(key number, size, age in seconds)]. Returns the cache folder"""
    cache = Path(root) / 'cache'
    (cache / 'obj' / 'ab').mkdir(parents=True)
    (cache / 'lib').mkdir()
    (cache / 'obj' / 'ab' / 'abcd.o').write_bytes(b'o' * 100)
    now = int(time.time())
    for n, size, age in libs:
        so, js = cache / 'lib' / (KEY.format(n) + '.so'), cache / 'lib' / (KEY.format(n) + '.json')
        so.write_bytes(b's' * size); js.write_text('{}')
        for p in (so, js): os.utime(p, (now - age, now - age))
    return cache


def run(**kw):
    out = io.StringIO()
    ns = dict(cache=None, keep=2, delete=False)
    ns.update(kw)
    import argparse
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try: code = nc.run(argparse.Namespace(**ns))
        except SystemExit as e: code = e.code if isinstance(e.code, int) else 2; out.write(str(e.code))
    return code, out.getvalue()


class Prune(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self): self.dir.cleanup()

    def libs(self, cache): return sorted(p.name for p in (cache / 'lib').iterdir())

    def test_a_folder_that_is_not_a_cache_is_refused(self):
        modules = self.root / 'aot-xc2c'; modules.mkdir(); (modules / 'main.elf').write_bytes(b'x')
        code, text = run(cache=str(modules), delete=True)
        self.assertNotEqual(code, 0); self.assertIn('not an AOT cache', text); self.assertTrue((modules / 'main.elf').exists())

    def test_by_default_it_lists_and_removes_nothing(self):
        cache = make_cache(self.root, [(1, 1000, 400), (2, 2000, 300), (3, 3000, 200), (4, 4000, 100)])
        before = self.libs(cache)
        code, text = run(cache=str(cache))
        self.assertEqual(code, 0); self.assertEqual(self.libs(cache), before)
        self.assertIn('nothing was removed', text); self.assertIn('--delete', text)
        self.assertIn('remove  ' + KEY.format(1)[:12], text); self.assertIn('remove  ' + KEY.format(2)[:12], text)   # the two oldest would go
        self.assertNotIn('remove  ' + KEY.format(4)[:12], text); self.assertIn('keep    ' + KEY.format(4)[:12], text)
        self.assertIn('3,004 bytes', text)                                                   # (1000 + 2) + (2000 + 2) can be freed, the .json files counted

    def test_delete_removes_the_older_libraries_with_their_json_and_keeps_the_newest(self):
        cache = make_cache(self.root, [(1, 10, 400), (2, 10, 300), (3, 10, 200), (4, 10, 100)])
        code, text = run(cache=str(cache), keep=2, delete=True)
        self.assertEqual(code, 0)
        self.assertEqual(self.libs(cache), sorted([KEY.format(3) + '.so', KEY.format(3) + '.json', KEY.format(4) + '.so', KEY.format(4) + '.json']))
        self.assertTrue((cache / 'obj' / 'ab' / 'abcd.o').exists(), 'the objects are never touched')

    def test_keep_zero_removes_every_library_but_not_the_objects(self):
        cache = make_cache(self.root, [(1, 10, 200), (2, 10, 100)])
        run(cache=str(cache), keep=0, delete=True)
        self.assertEqual(self.libs(cache), []); self.assertTrue((cache / 'obj' / 'ab' / 'abcd.o').exists())

    def test_fewer_libraries_than_keep_is_nothing_to_do(self):
        cache = make_cache(self.root, [(1, 10, 100)])
        code, text = run(cache=str(cache), keep=2, delete=True)
        self.assertEqual(code, 0); self.assertIn('nothing to remove', text); self.assertEqual(len(self.libs(cache)), 2)

    def test_it_only_touches_files_named_like_a_build_key_and_never_a_link(self):
        cache = make_cache(self.root, [(1, 10, 300), (2, 10, 200), (3, 10, 100)])
        (cache / 'lib' / 'notes.txt').write_text('mine')
        (cache / 'lib' / 'old-build.so').write_bytes(b'x' * 5)
        target = self.root / 'outside.so'; target.write_bytes(b'precious')
        os.symlink(target, cache / 'lib' / (KEY.format(0) + '.so'))   # the oldest by name, but a link
        run(cache=str(cache), keep=1, delete=True)
        names = self.libs(cache)
        for kept in ('notes.txt', 'old-build.so', KEY.format(0) + '.so', KEY.format(3) + '.so'): self.assertIn(kept, names)
        self.assertEqual(target.read_bytes(), b'precious')
        self.assertNotIn(KEY.format(1) + '.so', names); self.assertNotIn(KEY.format(2) + '.so', names)

    def test_a_negative_keep_is_refused(self):
        cache = make_cache(self.root, [(1, 10, 100)])
        code, text = run(cache=str(cache), keep=-1, delete=True)
        self.assertNotEqual(code, 0); self.assertEqual(len(self.libs(cache)), 2)

    def test_the_command_line_has_the_subcommand(self):
        cache = make_cache(self.root, [(1, 10, 300), (2, 10, 200), (3, 10, 100)])
        r = subprocess.run([sys.executable, str(ROOT / 'linux' / 'cli' / 'nx2linux.py'), 'cache-prune', str(cache), '--keep', '1'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn('nothing was removed', r.stdout)
        self.assertEqual(len(self.libs(cache)), 6)


if __name__ == '__main__':
    unittest.main()
