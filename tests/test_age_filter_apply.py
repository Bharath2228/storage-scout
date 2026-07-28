import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.main_window import FilterPanel, MainWindow


class AgeFilterApplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.panel = FilterPanel()
        self.panel.rb_inactive.setChecked(True)
        self.panel.slider.setEnabled(True)
        self.panel.age_input.setEnabled(True)
        self.panel._update_age_apply_state()

    def tearDown(self):
        self.panel.close()
        self.app.processEvents()

    def test_age_change_stays_pending_until_go_is_clicked(self):
        self.panel.slider.setValue(6)

        self.assertEqual(self.panel.age_input.value(), 6)
        self.assertEqual(self.panel.applied_age_value, 0)
        self.assertIsNone(self.panel.get_older_than_secs())
        self.assertTrue(self.panel.btn_apply_age.isEnabled())

        window = SimpleNamespace(
            fp=self.panel,
            _on_filter_changed=mock.Mock(),
        )
        MainWindow._on_age_filter_apply_clicked(window)

        self.assertEqual(self.panel.applied_age_value, 6)
        self.assertEqual(self.panel.get_older_than_secs(), 6 * 30 * 24 * 3600)
        self.assertFalse(self.panel.btn_apply_age.isEnabled())
        window._on_filter_changed.assert_called_once_with()

    def test_go_disables_when_value_matches_last_applied_value(self):
        self.panel.applied_age_value = 6
        self.panel.age_input.setValue(8)
        self.assertTrue(self.panel.btn_apply_age.isEnabled())

        self.panel.age_input.setValue(6)

        self.assertFalse(self.panel.btn_apply_age.isEnabled())

    def test_show_all_keeps_age_filter_controls_available(self):
        self.panel.applied_age_value = 6
        self.panel.age_input.setValue(8)
        window = SimpleNamespace(
            fp=self.panel,
        )

        self.panel.rb_all.setChecked(True)
        MainWindow._update_age_controls_enabled(window)
        self.assertEqual(self.panel.age_input.value(), 8)
        self.assertEqual(self.panel.applied_age_value, 6)
        self.assertTrue(self.panel.age_input.isEnabled())
        self.assertTrue(self.panel.slider.isEnabled())
        self.assertTrue(self.panel.btn_apply_age.isEnabled())

    def test_show_all_page_query_uses_applied_age(self):
        self.panel.rb_all.setChecked(True)
        self.panel.applied_age_value = 6
        window = SimpleNamespace(
            fp=self.panel,
            applied_name_filter="",
            active_extension_filter=None,
            folder_browser_scope=None,
            current_page=0,
            sort_column=0,
            sort_order=0,
            current_scan_root=r"C:\scan",
            folder_cache=None,
            txt_path=SimpleNamespace(text=lambda: r"C:\scan"),
        )

        options = MainWindow._page_load_options(window)

        self.assertIsNotNone(options["age_cutoff"])
        self.assertFalse(options["lazy_show_all_tree"])

    def test_exclusions_rescan_saves_closes_and_restarts(self):
        window = SimpleNamespace(
            fp=SimpleNamespace(
                _normalize_and_save_scan_exclusions=mock.Mock(),
                close_popovers=mock.Mock(),
            ),
            start_scan=mock.Mock(),
        )

        MainWindow._rescan_from_exclusions(window)

        window.fp._normalize_and_save_scan_exclusions.assert_called_once_with()
        window.fp.close_popovers.assert_called_once_with()
        window.start_scan.assert_called_once_with()

    def test_default_preset_clears_pending_and_applied_age(self):
        self.panel.applied_age_value = 6
        self.panel.age_input.setValue(8)

        self.panel.apply_default_browse_preset()

        self.assertEqual(self.panel.age_input.value(), 0)
        self.assertEqual(self.panel.applied_age_value, 0)
        self.assertFalse(self.panel.btn_apply_age.isEnabled())


if __name__ == "__main__":
    unittest.main()
