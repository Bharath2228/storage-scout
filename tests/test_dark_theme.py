import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.theme import DARK_PALETTE, apply_theme


class DarkThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def tearDown(self):
        apply_theme(self.app, "light")

    def test_dark_theme_uses_high_contrast_controls_and_icons(self):
        apply_theme(self.app, "dark")
        stylesheet = self.app.styleSheet()

        self.assertIn(f"color: {DARK_PALETTE['text']}", stylesheet)
        self.assertIn("tree_chevron_right_dark.svg", stylesheet)
        self.assertIn("tree_chevron_down_dark.svg", stylesheet)
        self.assertIn("up_arrow_dark.svg", stylesheet)
        self.assertIn("QComboBox QAbstractItemView", stylesheet)
        self.assertEqual(DARK_PALETTE["on_accent"], "#07111f")


if __name__ == "__main__":
    unittest.main()
