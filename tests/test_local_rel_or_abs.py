import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.mods_common import rel_or_abs


class TestRelOrAbs(unittest.TestCase):

    def test_relative_path_joins_with_base(self):
        base = os.path.join(tempfile.gettempdir(), "mods")
        result = rel_or_abs(base, "some_mod")
        expected = os.path.join(base, "some_mod")
        self.assertEqual(result, expected)

    def test_absolute_path_returned_unchanged(self):
        abs_path = tempfile.gettempdir()
        base = os.path.join(abs_path, "other")
        result = rel_or_abs(base, abs_path)
        self.assertEqual(result, abs_path)

    def test_empty_string_joins_with_base(self):
        base = os.path.join(tempfile.gettempdir(), "mods")
        result = rel_or_abs(base, "")
        expected = os.path.join(base, "")
        self.assertEqual(result, expected)

    def test_none_joins_with_base(self):
        base = os.path.join(tempfile.gettempdir(), "mods")
        result = rel_or_abs(base, None)
        expected = os.path.join(base, "")
        self.assertEqual(result, expected)

    def test_quoted_relative_path_strips_quotes_and_joins(self):
        base = os.path.join(tempfile.gettempdir(), "mods")
        result = rel_or_abs(base, '"some_mod"')
        expected = os.path.join(base, "some_mod")
        self.assertEqual(result, expected)

    def test_quoted_absolute_path_strips_quotes_and_returns(self):
        abs_path = tempfile.gettempdir()
        base = os.path.join(abs_path, "other")
        result = rel_or_abs(base, f'"{abs_path}"')
        self.assertEqual(result, abs_path)


if __name__ == "__main__":
    unittest.main()
