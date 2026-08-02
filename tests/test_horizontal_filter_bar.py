import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
)

from src.main_window import FilterPanel, MainWindow
from src.scan_exclusions import ScanExclusions


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

    def test_filters_use_a_dedicated_grouped_drawer(self):
        self.assertEqual(self.panel.objectName(), "filterDrawer")
        self.assertEqual(self.panel.minimumWidth(), 240)
        self.assertEqual(self.panel.maximumWidth(), 520)
        self.assertGreaterEqual(self.panel.width(), self.panel.minimumWidth())
        self.assertLessEqual(self.panel.width(), self.panel.maximumWidth())
        self.assertEqual(self.panel.txt_search.height(), 36)
        self.assertEqual(self.panel.search_shell.objectName(), "searchFieldShell")
        self.assertEqual(self.panel.btn_search_submit.objectName(), "searchFieldAction")
        self.assertEqual(self.panel.btn_search_submit.width(), 32)
        self.assertEqual(
            self.panel.btn_rescan_exclusions.text(),
            "Apply and Re-scan",
        )
        self.assertEqual(
            self.panel.btn_rescan_exclusions.accessibleName(),
            "Apply and re-scan",
        )
        self.assertEqual(
            self.panel.btn_reset_exclusions.text(),
            "Reset to defaults",
        )
        self.assertEqual(
            self.panel.txt_excluded_extensions.placeholderText(),
            "Example: .tmp, .log, .iso",
        )
        self.assertTrue(hasattr(self.panel, "btn_close"))
        self.assertEqual(self.panel.btn_close.text(), "×")
        self.assertEqual(self.panel.btn_close.objectName(), "closeFilterDrawer")
        self.assertEqual(self.panel.btn_close.accessibleName(), "Close filters")

    def test_filter_drawer_reflows_controls_at_minimum_width(self):
        self.panel.resize(self.panel.minimumWidth(), self.panel.height())
        self.app.processEvents()

        segment_width = self.panel.rb_all.width()
        self.assertLess(segment_width, 142)
        self.assertGreaterEqual(segment_width, 72)
        self.assertEqual(self.panel.rb_inactive.width(), segment_width)
        self.assertEqual(self.panel.rb_view_tree.width(), segment_width)
        self.assertEqual(self.panel.rb_view_files.width(), segment_width)
        self.assertLessEqual(
            (2 * segment_width) + self.panel.display_grid.spacing(),
            self.panel.width() - (2 * 16),
        )
        self.assertLessEqual(self.panel.search_shell.width(), self.panel.width() - 24)
        self.assertLessEqual(self.panel.age_box.width(), self.panel.width() - 24)
        self.assertEqual(
            self.panel.age_summary_layout.direction(),
            QBoxLayout.Direction.TopToBottom,
        )
        self.assertEqual(
            self.panel.age_manual_row.direction(),
            QBoxLayout.Direction.LeftToRight,
        )
        self.assertEqual(
            self.panel.age_manual_label.text(),
            "More than 24 months? Enter here",
        )
        self.assertEqual(self.panel.age_manual_label.objectName(), "ageManualHint")
        self.assertEqual(
            self.panel.btn_apply_age.sizePolicy().horizontalPolicy(),
            self.panel.btn_apply_age.sizePolicy().Policy.Fixed,
        )
        self.assertEqual(
            self.panel.exclusions_size_row.direction(),
            QBoxLayout.Direction.TopToBottom,
        )
        self.assertEqual(
            self.panel.exclusions_actions_layout.direction(),
            QBoxLayout.Direction.TopToBottom,
        )

        self.panel._exclusions_section_expanded = True
        self.panel._sync_collapsible_sections()
        self.app.processEvents()
        for control in (
            self.panel.txt_excluded_folders,
            self.panel.txt_excluded_extensions,
            self.panel.min_size_input,
            self.panel.min_size_unit,
            self.panel.btn_reset_exclusions,
            self.panel.btn_rescan_exclusions,
        ):
            self.assertLessEqual(control.width(), control.parentWidget().width())

    def test_search_action_button_submits_the_search_field(self):
        submitted = []
        self.panel.txt_search.returnPressed.connect(lambda: submitted.append(True))

        QTest.mouseClick(
            self.panel.btn_search_submit,
            Qt.MouseButton.LeftButton,
        )
        self.app.processEvents()

        self.assertEqual(submitted, [True])

    def test_exclusions_popup_controls_are_evenly_aligned(self):
        self.panel.prepare_exclusions_popup_layout()
        self.panel.exclusions_box.resize(340, 280)
        self.panel.exclusions_box.show()
        self.app.processEvents()

        self.assertEqual(self.panel.min_size_input.height(), 36)
        self.assertEqual(self.panel.min_size_unit.height(), 36)
        self.assertEqual(self.panel.btn_reset_exclusions.height(), 36)
        self.assertEqual(self.panel.btn_rescan_exclusions.height(), 36)
        self.assertEqual(
            self.panel.min_size_input.sizePolicy().horizontalPolicy(),
            self.panel.min_size_input.sizePolicy().Policy.Ignored,
        )
        self.assertEqual(
            self.panel.btn_reset_exclusions.sizePolicy().horizontalPolicy(),
            self.panel.btn_reset_exclusions.sizePolicy().Policy.Ignored,
        )
        self.assertLessEqual(
            abs(self.panel.min_size_input.width() - self.panel.min_size_unit.width()),
            1,
        )
        self.assertLessEqual(
            abs(
                self.panel.btn_reset_exclusions.width()
                - self.panel.btn_rescan_exclusions.width()
            ),
            1,
        )

    def test_exclusions_always_restore_defaults_on_a_new_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = QSettings(
                os.path.join(temp_dir, "settings.ini"),
                QSettings.Format.IniFormat,
            )
            settings.setValue(
                "scan_exclusions",
                '{"folder_names":["Custom"],"extensions":[".iso"],'
                '"min_file_size_bytes":1024}',
            )
            self.panel.settings = settings

            self.panel._restore_scan_exclusions()

            self.assertEqual(
                self.panel.get_scan_exclusions().to_dict(),
                ScanExclusions().to_dict(),
            )
            self.assertFalse(settings.contains("scan_exclusions"))

    def test_exclusions_button_distinguishes_default_active_and_pending(self):
        window = MainWindow()
        try:
            default = ScanExclusions()
            custom = ScanExclusions(
                folder_names=[*default.folder_names, "Archive"],
                extensions=[".iso"],
                min_file_size_bytes=1024,
            )

            window.fp._set_scan_exclusions_controls(default)
            window.applied_scan_exclusions = None
            window._update_exclusions_indicator()
            self.assertEqual(window.btn_exclusions.text(), "Exclusions: Default")
            self.assertEqual(
                window.btn_exclusions.property("exclusionState"),
                "default",
            )
            self.assertEqual(
                window.lbl_exclusions_rule_count.text(),
                "4 active rules",
            )
            self.assertTrue(window.lbl_exclusions_pending.isHidden())

            window.fp.txt_excluded_extensions.setText(".iso")
            self.app.processEvents()
            self.assertEqual(
                window.btn_exclusions.property("exclusionState"),
                "pending",
            )
            self.assertFalse(window.lbl_exclusions_pending.isHidden())

            window.fp._set_scan_exclusions_controls(default)
            window._update_exclusions_indicator()

            window.fp._set_scan_exclusions_controls(custom)
            window._update_exclusions_indicator()
            self.assertEqual(window.btn_exclusions.text(), "Exclusions: Pending")
            self.assertEqual(
                window.btn_exclusions.property("exclusionState"),
                "pending",
            )
            self.assertEqual(
                window.lbl_exclusions_rule_count.text(),
                "7 active rules",
            )
            self.assertFalse(window.lbl_exclusions_pending.isHidden())

            window.applied_scan_exclusions = custom.to_dict()
            window._update_exclusions_indicator()
            self.assertEqual(window.btn_exclusions.text(), "Exclusions: 7 active")
            self.assertEqual(
                window.btn_exclusions.property("exclusionState"),
                "active",
            )
            self.assertTrue(window.lbl_exclusions_pending.isHidden())

            window.fp._set_scan_exclusions_controls(default)
            window._update_exclusions_indicator()
            self.assertEqual(window.btn_exclusions.text(), "Exclusions: Pending")
        finally:
            window.close()

    def test_only_one_advanced_drawer_section_opens_at_a_time(self):
        self.panel._age_section_expanded = True
        self.panel._exclusions_section_expanded = False
        self.panel._sync_collapsible_sections()

        self.panel._toggle_collapsible_section("age")
        self.assertTrue(self.panel.age_box.isVisible())
        self.assertFalse(self.panel.exclusions_box.isVisible())

        self.panel._toggle_collapsible_section("exclusions")
        self.assertTrue(self.panel.age_box.isVisible())
        self.assertTrue(self.panel.exclusions_box.isVisible())

    def test_section_summaries_show_active_state(self):
        self.panel.applied_age_value = 6
        self.panel._update_popover_summaries()
        self.assertIsInstance(self.panel.age_heading, QLabel)
        self.assertEqual(self.panel.age_heading.text(), "Age: 6mo")
        self.assertEqual(
            self.panel.age_heading.cursor().shape(),
            Qt.CursorShape.ArrowCursor,
        )
        self.assertFalse(hasattr(self.panel.age_heading, "clicked"))
        self.assertEqual(
            self.panel.btn_exclusions_toggle.title.text(),
            "Exclusions: Default",
        )

        self.panel._toggle_collapsible_section("age")
        self.assertTrue(self.panel._age_section_expanded)
        self.assertTrue(self.panel.age_box.isVisible())

    def test_main_window_starts_filter_drawer_at_minimum_width(self):
        window = MainWindow()
        try:
            window.resize(1500, 820)
            window.show()
            self.app.processEvents()
            self.assertFalse(window.fp.isVisible())
            window._toggle_filters()
            self.app.processEvents()
            self.assertTrue(window.fp.isVisible())
            self.assertTrue(window.btn_filter.isChecked())
            self.assertEqual(window.fp.width(), 240)
            self.assertEqual(window.main_splitter.handleWidth(), 3)

            initial_width = window.fp.width()
            window.main_splitter.setSizes([240, 780, 420])
            self.app.processEvents()

            self.assertGreater(window.fp.width(), initial_width)
            self.assertGreaterEqual(window.fp.width(), 240)
            self.assertLessEqual(window.fp.width(), 520)

            window._toggle_filters()
            self.app.processEvents()
            self.assertFalse(window.fp.isVisible())
            self.assertFalse(window.btn_filter.isChecked())
        finally:
            window.close()

    def test_actions_are_moved_into_secondary_horizontal_menu(self):
        window = MainWindow()
        try:
            window.resize(1500, 820)
            window.show()
            window._toggle_filters()
            self.app.processEvents()

            self.assertFalse(hasattr(window, "toolbar_stack"))
            self.assertFalse(hasattr(window, "toolbar_style"))
            self.assertIsInstance(window.topbar.layout(), QGridLayout)
            topbar_button_texts = {
                button.text()
                for button in window.topbar.findChildren(QPushButton)
            }
            self.assertNotIn("Filters", topbar_button_texts)
            button_texts = {
                button.text()
                for button in window.app_menu.findChildren(QPushButton)
            }
            self.assertTrue(
                {
                    "Folders Panel",
                    "Filters",
                    "File Extensions",
                    "Exclusions: Default",
                    "Export CSV",
                    "Delete Selected",
                }.issubset(button_texts)
            )
            self.assertNotIn("Ribbon", button_texts)
            self.assertNotIn("Classic toolbar", button_texts)
            self.assertFalse(window.scan_stats.wordWrap())
            self.assertEqual(window.path_input_shell.objectName(), "browsePathShell")
            self.assertEqual(window.txt_path.objectName(), "browsePathInput")
            self.assertEqual(window.txt_path.placeholderText(), "Choose a folder to scan")
            self.assertTrue(window.txt_path.isReadOnly())
            self.assertEqual(window.btn_browse.objectName(), "browsePathBtn")
            self.assertEqual(window.btn_browse.text(), "Browse")
            self.assertEqual(window.btn_browse.toolTip(), "Browse folder")
            self.assertIn("Rescan", topbar_button_texts)
            self.assertIs(window.btn_rescan.parentWidget(), window.topbar)
            self.assertEqual(window.btn_rescan.size().width(), 80)
            self.assertEqual(window.btn_folder_browser.text(), "Folders Panel")
            self.assertEqual(window.btn_folder_browser.minimumWidth(), 138)
            menu_layout = window.app_menu.layout()
            self.assertEqual(
                menu_layout.indexOf(window.btn_exclusions),
                menu_layout.indexOf(window.btn_file_types) + 1,
            )
            window._toggle_exclusions_popup()
            self.app.processEvents()
            self.assertTrue(window.exclusions_popup.isVisible())
            self.assertTrue(window.fp.exclusions_box.isVisible())
            self.assertTrue(window.btn_exclusions.isChecked())
            self.assertTrue(window.btn_export.property("menuAction"))
            self.assertTrue(window.btn_delete.property("menuAction"))
            self.assertFalse(window.btn_export.icon().isNull())
            self.assertFalse(window.btn_delete.icon().isNull())
            self.assertEqual(window.btn_export.width(), 138)
            self.assertEqual(window.btn_theme_toggle.objectName(), "themeToggleBtn")
            self.assertEqual(window.btn_theme_toggle.text(), "")
            self.assertEqual(window.btn_theme_toggle.size().width(), 38)
            self.assertEqual(window.btn_theme_toggle.size().height(), 38)

            self.assertEqual(window.app_menu.height(), 58)
            self.assertIsInstance(window.app_menu.layout(), QHBoxLayout)
            self.assertEqual(window.fp.width(), 240)
        finally:
            window.close()

    def test_topbar_typography_scales_down_in_narrow_windows(self):
        window = MainWindow()
        try:
            window.resize(1500, 820)
            window.show()
            self.app.processEvents()
            wide_title_size = window.lbl_app_title.font().pixelSize()
            wide_button_size = window.btn_browse.font().pixelSize()

            window.resize(980, 820)
            self.app.processEvents()

            self.assertLess(window.lbl_app_title.font().pixelSize(), wide_title_size)
            self.assertLess(window.btn_browse.font().pixelSize(), wide_button_size)
            self.assertEqual(window.btn_browse.text(), "Browse")
        finally:
            window.close()

    def test_theme_toggle_button_switches_theme(self):
        window = MainWindow()
        try:
            window.show()
            self.app.processEvents()
            starting_theme = window.current_theme_name
            starting_tooltip = window.btn_theme_toggle.toolTip()

            QTest.mouseClick(window.btn_theme_toggle, Qt.MouseButton.LeftButton)
            self.app.processEvents()

            self.assertNotEqual(window.current_theme_name, starting_theme)
            self.assertNotEqual(window.btn_theme_toggle.toolTip(), starting_tooltip)
            self.assertIn(
                window.current_theme_name,
                {"light", "dark"},
            )
        finally:
            window.close()

    def test_stop_state_uses_matching_action_button_shape(self):
        window = MainWindow()
        try:
            window._set_rescan_stop_ui()

            self.assertEqual(window.btn_rescan.objectName(), "stopScanBtn")
            self.assertEqual(window.btn_rescan.text(), "Stop")
            self.assertEqual(window.btn_rescan.toolTip(), "Stop the active scan")
            self.assertEqual(window.btn_rescan.size().width(), 80)
            self.assertEqual(window.btn_rescan.size().height(), 32)
            window._set_rescan_idle_ui()
            self.assertEqual(window.btn_rescan.text(), "Rescan")
            self.assertEqual(window.btn_rescan.size().width(), 80)
        finally:
            window.close()

    def test_delete_button_keeps_compact_copy_and_moves_counts_to_tooltip(self):
        window = MainWindow()
        try:
            window.resize(1500, 820)
            window.show()
            self.app.processEvents()

            window._update_delete_button_copy()
            window._set_delete_armed(False)
            self.app.processEvents()

            idle_width = window.btn_delete.width()
            self.assertEqual(idle_width, 176)
            window._update_delete_button_copy(250, all_pages=True)
            window._set_delete_armed(True)
            self.app.processEvents()

            self.assertEqual(window.btn_delete.width(), idle_width)
            self.assertEqual(window.btn_delete.text(), "Delete Selected")
            self.assertEqual(
                window.btn_delete.toolTip(),
                "Move 250 matching items across all pages to the Recycle Bin",
            )
        finally:
            window.close()

    def test_folder_panel_uses_a_horizontal_resizable_splitter(self):
        window = MainWindow()
        try:
            window.resize(1500, 820)
            window.show()
            self.app.processEvents()

            self.assertEqual(window.main_splitter.handleWidth(), 3)
            self.assertEqual(window.folder_browser.btn_close.text(), "Close Folders Panel")
            self.assertEqual(window.folder_browser.btn_close.height(), 32)
            title = window.folder_browser.findChild(QLabel, "filterDrawerTitle")
            self.assertIsNotNone(title)
            self.assertEqual(title.text(), "Folders Panel")
            self.assertEqual(window.folder_browser.minimumWidth(), 160)
            self.assertEqual(window.folder_browser.width(), 160)
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

    def test_selected_file_type_has_clear_visible_context(self):
        window = MainWindow()
        try:
            window.active_extension_filter = ".csv"
            window._update_type_filter_ui()

            self.assertFalse(window.type_filter_banner.isHidden())
            self.assertEqual(window.lbl_active_type_filter.text(), ".CSV files")
            self.assertEqual(
                window.lbl_active_type_filter_meta.text(),
                "Showing .CSV files in Files view.",
            )
            self.assertEqual(window.btn_file_types.text(), "Extension: .CSV")
            self.assertTrue(window.btn_file_types.isChecked())

            with mock.patch.object(window, "_apply_filters"):
                window._clear_type_filter()

            self.assertIsNone(window.active_extension_filter)
            self.assertTrue(window.type_filter_banner.isHidden())
            self.assertEqual(window.btn_file_types.text(), "File Extensions")
            self.assertFalse(window.btn_file_types.isChecked())
        finally:
            window.close()

    def test_no_extension_filter_uses_plain_language_in_banner(self):
        window = MainWindow()
        try:
            window.active_extension_filter = "(no extension)"
            window._update_type_filter_ui()

            self.assertEqual(
                window.lbl_active_type_filter.text(),
                "Files with no extension",
            )
            self.assertEqual(
                window.lbl_active_type_filter_meta.text(),
                "Showing files without an extension in Files view.",
            )
            self.assertEqual(window.btn_file_types.text(), "Extension: (no extension)")
        finally:
            window.close()

    def test_selection_controls_pack_visible_buttons_without_hidden_gaps(self):
        window = MainWindow()
        try:
            window.resize(1500, 820)
            window.show()
            window.controls_bar.setVisible(True)
            window._refresh_selection_buttons()
            self.app.processEvents()
            select_x = window.btn_select_all.x()
            inactive_gap = (
                window.btn_select_inactive.x()
                - (window.btn_expand.x() + window.btn_expand.width())
            )
            self.assertLessEqual(inactive_gap, window.controls_bar.layout().spacing() + 1)
            self.assertEqual(window.btn_expand.size().height(), 30)
            self.assertEqual(window.btn_select_all.size().width(), 104)
            self.assertEqual(window.btn_select_all.size().height(), 30)
            self.assertEqual(window.btn_current_page_selection.size().height(), 30)
            self.assertEqual(window.btn_clear_selection.size().height(), 30)
            self.assertEqual(window.btn_select_inactive.size().height(), 30)
            self.assertEqual(window.btn_select_empty.size().height(), 30)
            self.assertEqual(window.btn_prev_page.size().height(), 30)
            self.assertEqual(window.btn_next_page.size().height(), 30)
            self.assertEqual(window.lbl_page_info.size().height(), 30)
            self.assertEqual(window.controls_bar.layout().contentsMargins().top(), 0)
            self.assertEqual(window.controls_bar.layout().contentsMargins().bottom(), 0)

            selected_path = os.path.normpath(r"C:\scan\selected.txt")
            window.selected_paths[window._path_key(selected_path)] = selected_path
            window._refresh_selection_buttons()
            self.app.processEvents()

            self.assertEqual(window.btn_select_all.x(), select_x)
            self.assertEqual(window.btn_select_all.text(), "Unselect All")
            self.assertTrue(window.btn_select_all.isVisible())
            self.assertTrue(window.btn_clear_selection.isHidden())
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
