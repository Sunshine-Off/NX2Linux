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


if __name__ == '__main__':
    unittest.main()
