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

    def test_show_all_preserves_pending_value_without_applying_it(self):
        self.panel.applied_age_value = 6
        self.panel.age_input.setValue(8)
        window = SimpleNamespace(
            fp=self.panel,
            saved_age_threshold_value=0,
            age_controls_forced_disabled=False,
        )

        self.panel.rb_all.setChecked(True)
        MainWindow._update_age_controls_enabled(window)
        self.assertEqual(self.panel.age_input.value(), 0)
        self.assertFalse(self.panel.btn_apply_age.isEnabled())

        self.panel.rb_inactive.setChecked(True)
        MainWindow._update_age_controls_enabled(window)

        self.assertEqual(self.panel.age_input.value(), 8)
        self.assertEqual(self.panel.applied_age_value, 6)
        self.assertTrue(self.panel.btn_apply_age.isEnabled())

    def test_default_preset_clears_pending_and_applied_age(self):
        self.panel.applied_age_value = 6
        self.panel.age_input.setValue(8)

        self.panel.apply_default_browse_preset()

        self.assertEqual(self.panel.age_input.value(), 0)
        self.assertEqual(self.panel.applied_age_value, 0)
        self.assertFalse(self.panel.btn_apply_age.isEnabled())


if __name__ == "__main__":
    unittest.main()
