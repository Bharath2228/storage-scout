import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QSizePolicy

from src.main_window import LoadingDialog
from src.theme import apply_theme


class LoadingDialogLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def tearDown(self):
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, LoadingDialog):
                widget.close()
        self.app.processEvents()

    def test_detail_text_has_room_in_light_and_dark_themes(self):
        message = "Applying filters and preparing the table..."

        for theme_name in ("light", "dark"):
            with self.subTest(theme=theme_name):
                apply_theme(self.app, theme_name)
                dialog = LoadingDialog("Loading results", message)
                dialog.show()
                self.app.processEvents()

                detail = dialog.detail_label
                self.assertEqual(dialog.size().width(), 380)
                self.assertEqual(dialog.size().height(), 140)
                self.assertTrue(detail.wordWrap())
                self.assertGreaterEqual(detail.minimumHeight(), 24)
                self.assertEqual(
                    detail.sizePolicy().verticalPolicy(),
                    QSizePolicy.Policy.Minimum,
                )
                self.assertGreaterEqual(
                    detail.height(),
                    detail.fontMetrics().height() + 8,
                )
                wrapped_bounds = detail.fontMetrics().boundingRect(
                    detail.contentsRect(),
                    int(Qt.TextFlag.TextWordWrap),
                    message,
                )
                self.assertGreaterEqual(
                    detail.height(),
                    wrapped_bounds.height() + 8,
                )


if __name__ == "__main__":
    unittest.main()
