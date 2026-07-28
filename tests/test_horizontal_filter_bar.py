import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QPushButton, QSplitter

from src.main_window import FilterPanel, FilterPopover, MainWindow


class HorizontalFilterBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.panel = FilterPanel()
        self.panel.resize(1400, self.panel.sizeHint().height())
        self.panel.show()
        self.app.processEvents()

    def tearDown(self):
        self.panel.close()
        self.app.processEvents()

    def test_main_controls_are_horizontal_and_close_button_is_removed(self):
        self.assertEqual(self.panel.objectName(), "filterBar")
        self.assertEqual(self.panel.main_row.height(), 56)
        self.assertEqual(self.panel.txt_search.height(), 36)
        self.assertEqual(self.panel.rb_all.height(), 36)
        self.assertEqual(self.panel.btn_age_toggle.height(), 36)
        self.assertEqual(self.panel.btn_rescan_exclusions.text(), "Re-scan")
        self.assertFalse(hasattr(self.panel, "btn_close"))
        self.assertIsInstance(
            self.panel.rb_all.parentWidget().layout().itemAt(0).layout(),
            QHBoxLayout,
        )
        self.assertIsInstance(
            self.panel.rb_view_tree.parentWidget().layout().itemAt(0).layout(),
            QHBoxLayout,
        )

    def test_only_one_popover_opens_at_a_time(self):
        self.panel._age_section_expanded = False
        self.panel._exclusions_section_expanded = False
        self.panel._sync_collapsible_sections()

        self.panel._toggle_collapsible_section("age")
        self.assertIsInstance(self.panel.age_popover, FilterPopover)
        self.assertTrue(self.panel.age_popover.isVisible())
        self.assertFalse(self.panel.exclusions_popover.isVisible())
        self.assertEqual(self.panel.age_popover.width(), 340)
        self.assertEqual(self.panel.height(), 56)

        self.panel._toggle_collapsible_section("exclusions")
        self.assertFalse(self.panel.age_popover.isVisible())
        self.assertTrue(self.panel.exclusions_popover.isVisible())
        self.assertEqual(self.panel.exclusions_popover.width(), 360)

    def test_escape_closes_popover_and_summaries_show_active_state(self):
        self.panel.applied_age_value = 6
        self.panel._update_popover_summaries()
        self.assertEqual(self.panel.btn_age_toggle.title.text(), "Age: 6mo")
        self.assertEqual(
            self.panel.btn_exclusions_toggle.title.text(),
            "Exclusions: Default",
        )

        self.panel._toggle_collapsible_section("age")
        QTest.keyClick(self.panel.age_popover, Qt.Key.Key_Escape)
        self.app.processEvents()

        self.assertFalse(self.panel.age_popover.isVisible())
        self.assertFalse(self.panel._age_section_expanded)

    def test_main_window_toggles_bar_without_width_animation(self):
        window = MainWindow()
        try:
            window.show()
            self.app.processEvents()
            self.assertFalse(window.fp.isVisible())
            window._toggle_filters()
            self.app.processEvents()
            self.assertTrue(window.fp.isVisible())
            self.assertTrue(window.btn_filter.isChecked())
            self.assertEqual(window.fp.width(), window.centralWidget().width())

            window._toggle_filters()
            self.app.processEvents()
            self.assertFalse(window.fp.isVisible())
            self.assertFalse(window.btn_filter.isChecked())
        finally:
            window.close()

    def test_classic_toolbar_is_single_and_filter_bar_has_no_gap(self):
        window = MainWindow()
        try:
            window.resize(1500, 820)
            window.show()
            window._toggle_filters()
            self.app.processEvents()

            self.assertFalse(hasattr(window, "toolbar_stack"))
            self.assertFalse(hasattr(window, "toolbar_style"))
            button_texts = {
                button.text()
                for button in window.topbar.findChildren(QPushButton)
            }
            self.assertTrue(
                {
                    "Browse",
                    "Re-scan",
                    "Folders",
                    "Filters",
                    "Types",
                    "Export CSV",
                    "Delete Selected",
                }.issubset(button_texts)
            )
            self.assertNotIn("Ribbon", button_texts)
            self.assertNotIn("Classic toolbar", button_texts)
            self.assertFalse(window.scan_stats.wordWrap())

            topbar_bottom = window.topbar.y() + window.topbar.height()
            self.assertEqual(window.fp.y(), topbar_bottom)
            folder_top = window.folder_browser.mapTo(
                window.centralWidget(),
                window.folder_browser.rect().topLeft(),
            ).y()
            self.assertEqual(folder_top, window.fp.y() + window.fp.height())
        finally:
            window.close()

    def test_folder_panel_uses_a_horizontal_resizable_splitter(self):
        window = MainWindow()
        try:
            window.resize(1500, 820)
            window.show()
            self.app.processEvents()

            self.assertIsInstance(window.main_splitter, QSplitter)
            self.assertEqual(window.main_splitter.orientation(), Qt.Orientation.Horizontal)
            initial_width = window.folder_browser.width()
            window.main_splitter.setSizes([360, 900])
            self.app.processEvents()

            self.assertGreater(window.folder_browser.width(), initial_width)
            self.assertGreaterEqual(window.folder_browser.width(), 340)
        finally:
            window.close()

    def test_files_only_view_hides_expand_all(self):
        window = MainWindow()
        try:
            window.fp.rb_inactive.setChecked(True)
            window.fp.rb_view_files.setChecked(True)
            window._update_expand_control_visibility()

            self.assertTrue(window.btn_expand.isHidden())
        finally:
            window.close()

    def test_selection_button_labels_do_not_move_the_controls(self):
        window = MainWindow()
        try:
            window.resize(1500, 820)
            window.show()
            window.controls_bar.setVisible(True)
            window.btn_current_page_selection.setVisible(True)
            window.btn_select_all.setVisible(True)
            window.btn_clear_selection.setVisible(False)
            self.app.processEvents()
            current_page_x = window.btn_current_page_selection.x()
            clear_x = window.btn_clear_selection.x()

            window.btn_select_all.setVisible(False)
            window.btn_clear_selection.setVisible(True)
            window.btn_current_page_selection.setText("Unselect Current Page")
            self.app.processEvents()

            self.assertEqual(window.btn_current_page_selection.x(), current_page_x)
            self.assertEqual(window.btn_clear_selection.x(), clear_x)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
