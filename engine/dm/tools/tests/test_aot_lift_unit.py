"""aot_lift.py without Remill: the way it reads a linker's complaint about missing symbols (the English and the French wording; the French one has a space inside « »)."""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aot_lift


class MissingSymbols(unittest.TestCase):
    def test_english(self):
        self.assertEqual(aot_lift.missing_symbols("x.o: in function `f':\nundefined reference to `__remill_read_memory_f32'\nundefined reference to `__remill_error'"), ['__remill_error', '__remill_read_memory_f32'])

    def test_french(self):
        self.assertEqual(aot_lift.missing_symbols("lifted_code:(.text+0x1a) : référence indéfinie vers « __remill_read_memory_f32 »\nlifted_code:(.text+0x4e) : référence indéfinie vers « __remill_read_memory_f32 »"), ['__remill_read_memory_f32'])

    def test_nothing_missing(self):
        self.assertEqual(aot_lift.missing_symbols("warning: overriding the module target triple"), [])


class Limits(unittest.TestCase):
    def test_a_function_too_long_for_the_lifters_command_line_is_an_explicit_error(self):
        """remill-lift takes the code as one hex argument (128 KiB at most): found on a real module, where it stopped a whole build with 'Argument list too long'"""
        with self.assertRaisesRegex(aot_lift.LiftError, r'function 0x1000: 70000 bytes are more than the 60000'): aot_lift.lift_function(bytes(70000), 0x1000, '/nonexistent/x.ll')


if __name__ == '__main__':
    unittest.main()
