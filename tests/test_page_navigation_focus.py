import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
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

    def test_current_page_selection_never_requests_all_pages(self):
        indices = [object(), object()]
        window = SimpleNamespace(
            tree_model=object(),
            _display_mode=mock.Mock(return_value="All"),
            _collect_bulk_target_indices=mock.Mock(return_value=indices),
            _are_all_indices_checked=mock.Mock(return_value=False),
            _refresh_selection_buttons=mock.Mock(),
            _begin_chunked_bulk_selection=mock.Mock(),
            _preserve_results_focus=False,
        )

        MainWindow._toggle_current_page_selection(window)

        window._begin_chunked_bulk_selection.assert_called_once_with(
            indices,
            Qt.CheckState.Checked,
            current_page_count=2,
            label="current page",
            offer_all_pages=False,
            status=None,
            videos_only=False,
            requested_scope="current",
        )

    def test_current_page_toggle_unselects_only_the_current_page(self):
        indices = [object()]
        window = SimpleNamespace(
            tree_model=object(),
            _display_mode=mock.Mock(return_value="All"),
            _collect_bulk_target_indices=mock.Mock(return_value=indices),
            _are_all_indices_checked=mock.Mock(return_value=True),
            _refresh_selection_buttons=mock.Mock(),
            _clear_current_page_checks=mock.Mock(),
        )

        MainWindow._toggle_current_page_selection(window)

        window._clear_current_page_checks.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
