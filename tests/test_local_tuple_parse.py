import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.quick_settings import _tuple_parse


class TestTupleParse(unittest.TestCase):

    def test_normal_with_parens(self):
        result = _tuple_parse('(A=1,B="x,y",C=True)')
        self.assertEqual(result, [['A', '1'], ['B', '"x,y"'], ['C', 'True']])

    def test_without_parens(self):
        result = _tuple_parse('A=1,B=2')
        self.assertEqual(result, [['A', '1'], ['B', '2']])

    def test_empty_tuple(self):
        result = _tuple_parse('()')
        self.assertEqual(result, [])

    def test_single_pair(self):
        result = _tuple_parse('(A=1)')
        self.assertEqual(result, [['A', '1']])

    def test_whitespace_stripped(self):
        result = _tuple_parse(' ( A = 1 , B = 2 ) ')
        self.assertEqual(result, [['A', '1'], ['B', '2']])

    def test_value_with_equals(self):
        result = _tuple_parse('(A=1=2)')
        self.assertEqual(result, [['A', '1=2']])

    def test_quoted_value_preserved(self):
        result = _tuple_parse('(A="hello world")')
        self.assertEqual(result, [['A', '"hello world"']])


if __name__ == '__main__':
    unittest.main()
