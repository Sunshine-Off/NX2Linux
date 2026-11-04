import sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import patch_platforms as pp


class Platforms(unittest.TestCase):
    def test_every_patch_of_the_series_is_classified(self):
        self.assertEqual(pp.problems(), [])

    def make(self, series, rows):
        d = Path(tempfile.mkdtemp()); self.addCleanup(__import__('shutil').rmtree, d, True)
        (d / 'patches').mkdir()
        (d / 'patches' / 'series').write_text(series)
        (d / 'patches' / 'platforms.tsv').write_text('file\tclass\tnote\n' + rows)
        return d

    def test_a_missing_extra_unknown_or_empty_row_is_reported(self):
        d = self.make('a.patch\nb.patch\n', 'a.patch\tmac\tx\nc.patch\tlinux\t \n')
        text = '\n'.join(pp.problems(d))
        self.assertIn('b.patch: no row', text)
        self.assertIn('c.patch: in platforms.tsv but not in the series', text)
        self.assertIn("a.patch: class 'mac'", text)
        self.assertIn('c.patch: empty note', text)

    def test_a_malformed_row_is_an_error(self):
        d = self.make('a.patch\n', 'a.patch\tlinux\n')
        with self.assertRaises(ValueError):
            pp.table(d)

    def repo(self, files, rows, series=None):
        d = Path(tempfile.mkdtemp()); self.addCleanup(__import__('shutil').rmtree, d, True)
        (d / 'patches').mkdir()
        (d / 'patches' / 'series').write_text(''.join(n + '\n' for n in files))
        (d / 'patches' / 'platforms.tsv').write_text('file\tclass\tnote\n' + ''.join(f'{n}\t{c}\tx\n' for n, c in rows.items()))
        for n, fs in files.items():
            (d / 'patches' / n).write_text(''.join(f'diff --git a/{f} b/{f}\n' for f in fs))
        return d

    def test_a_dependency_on_another_class_is_a_violation_and_common_is_not(self):
        d = self.repo({'1.patch': ['x.c'], '2.patch': ['x.c'], '3.patch': ['y.c'], '4.patch': ['y.c']},
                      {'1.patch': 'apple', '2.patch': 'linux', '3.patch': 'common', '4.patch': 'linux'})
        self.assertEqual(pp.violations(d), {('2.patch', '1.patch', 'x.c')})

    def test_the_check_wants_current_lists_and_a_listed_overlap(self):
        d = self.repo({'1.patch': ['x.c'], '2.patch': ['x.c']}, {'1.patch': 'apple', '2.patch': 'linux'})
        text = '\n'.join(pp.problems(d))
        self.assertIn('series.linux is not current', text)
        for cls in pp.CLASSES:
            (d / 'patches' / f'series.{cls}').write_text(pp.list_text(cls, d))
        self.assertIn('2.patch changes x.c', '\n'.join(pp.problems(d)))
        (d / 'patches' / 'platform-overlaps.tsv').write_text('patch\tearlier patch\tfile\n2.patch\t1.patch\tx.c\n')
        self.assertEqual(pp.problems(d), [])
        (d / 'patches' / 'platform-overlaps.tsv').write_text('patch\tearlier patch\tfile\n2.patch\t1.patch\tx.c\n2.patch\t1.patch\tz.c\n')
        self.assertIn('no longer an overlap', '\n'.join(pp.problems(d)))

    def test_the_lists_follow_the_series_order(self):
        d = self.repo({'b.patch': ['q'], 'a.patch': ['r'], 'c.patch': ['s']}, {'b.patch': 'linux', 'a.patch': 'apple', 'c.patch': 'linux'})
        self.assertEqual(pp.list_text('linux', d), 'b.patch\nc.patch\n')


if __name__ == '__main__':
    unittest.main()
