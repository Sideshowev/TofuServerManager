import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import game_defaults as G


class TestLocalLaunchFlags(unittest.TestCase):
    def test_has_flag_found(self):
        self.assertTrue(G._has("-fullscreen -windowed", "-fullscreen"))

    def test_has_flag_not_found(self):
        self.assertFalse(G._has("-fullscreen -windowed", "-borderless"))

    def test_has_case_insensitive(self):
        self.assertTrue(G._has("-Fullscreen", "-fullscreen"))

    def test_value_simple(self):
        self.assertEqual("1920x1080", G._value("-resolution 1920x1080", "-resolution"))

    def test_value_quoted_with_spaces(self):
        self.assertEqual(
            "My Game Folder",
            G._value('-gamepath "My Game Folder"', "-gamepath"),
        )

    def test_value_not_found(self):
        self.assertEqual("", G._value("-fullscreen", "-resolution"))

    def test_set_add_new_flag(self):
        result = G._set("-fullscreen", "-windowed", "1")
        self.assertIn("-windowed 1", result)

    def test_set_update_existing_flag(self):
        result = G._set("-resolution 1024x768", "-resolution", "1920x1080")
        self.assertIn("1920x1080", result)
        self.assertNotIn("1024x768", result)

    def test_set_quoted_value(self):
        result = G._set("", "-gamepath", "C:\\Program Files\\Game")
        self.assertIn('"C:\\Program Files\\Game"', result)


if __name__ == "__main__":
    unittest.main()
