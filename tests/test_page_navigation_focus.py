import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLineEdit, QWidget

from src.main_window import MainWindow


class PageNavigationFocusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_clear_path_input_focus_removes_selection_and_focus(self):
        parent = QWidget()
        path_input = QLineEdit(parent)
        path_input.setText(r"C:\important\folder")
        parent.show()
        path_input.setFocus()
        path_input.selectAll()
        self.app.processEvents()
        window = SimpleNamespace(txt_path=path_input)

        MainWindow._clear_path_input_focus(window)
        self.app.processEvents()

        self.assertEqual(path_input.selectedText(), "")
        self.assertFalse(path_input.hasFocus())
        parent.close()

    def test_previous_page_clears_path_focus_after_loading_starts(self):
        window = SimpleNamespace(
            current_page=2,
            _preserve_results_focus=False,
            _update_pending_pagination_state=mock.Mock(),
            _load_page=mock.Mock(),
            _clear_path_input_focus=mock.Mock(),
        )

        MainWindow._prev_page(window)

        self.assertEqual(window.current_page, 1)
        self.assertTrue(window._preserve_results_focus)
        window._load_page.assert_called_once_with()
        window._clear_path_input_focus.assert_called_once_with()

    def test_next_page_clears_path_focus_after_loading_starts(self):
        window = SimpleNamespace(
            current_page=0,
            _preserve_results_focus=False,
            _update_pending_pagination_state=mock.Mock(),
            _load_page=mock.Mock(),
            _clear_path_input_focus=mock.Mock(),
        )

        MainWindow._next_page(window)

        self.assertEqual(window.current_page, 1)
        self.assertTrue(window._preserve_results_focus)
        window._load_page.assert_called_once_with()
        window._clear_path_input_focus.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
