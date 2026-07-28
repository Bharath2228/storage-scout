import unittest
from types import MethodType, SimpleNamespace
from unittest import mock

from PyQt6.QtWidgets import QSystemTrayIcon

from src.main_window import MainWindow


class CompletionNotificationTests(unittest.TestCase):
    def make_window(self, active=True, enabled=True):
        return SimpleNamespace(
            notifications_enabled=enabled,
            isActiveWindow=mock.Mock(return_value=active),
        )

    def test_long_scan_notifies_even_when_window_is_active(self):
        window = self.make_window(active=True)

        should_notify = MainWindow._should_show_completion_notification(
            window,
            elapsed_secs=8.1,
            cancelled=False,
        )

        self.assertTrue(should_notify)

    def test_short_scan_notifies_only_when_window_is_inactive(self):
        active_window = self.make_window(active=True)
        inactive_window = self.make_window(active=False)

        self.assertFalse(
            MainWindow._should_show_completion_notification(
                active_window,
                elapsed_secs=2,
                cancelled=False,
            )
        )
        self.assertTrue(
            MainWindow._should_show_completion_notification(
                inactive_window,
                elapsed_secs=2,
                cancelled=False,
            )
        )

    def test_cancelled_or_disabled_notifications_do_not_fire(self):
        cancelled_window = self.make_window(active=False)
        disabled_window = self.make_window(active=False, enabled=False)

        self.assertFalse(
            MainWindow._should_show_completion_notification(
                cancelled_window,
                elapsed_secs=20,
                cancelled=True,
            )
        )
        self.assertFalse(
            MainWindow._should_show_completion_notification(
                disabled_window,
                elapsed_secs=20,
                cancelled=False,
            )
        )

    def test_visible_tray_receives_five_second_information_message(self):
        tray_icon = mock.Mock()
        tray_icon.isVisible.return_value = True
        window = SimpleNamespace(
            notifications_enabled=True,
            tray_icon=tray_icon,
        )

        shown = MainWindow._show_system_notification(
            window,
            "Scan complete",
            "Scan complete in 00:09.",
        )

        self.assertTrue(shown)
        tray_icon.showMessage.assert_called_once_with(
            "Scan complete",
            "Scan complete in 00:09.",
            QSystemTrayIcon.MessageIcon.Information,
            5000,
        )

    def test_missing_hidden_or_failing_tray_degrades_silently(self):
        missing_window = SimpleNamespace(
            notifications_enabled=True,
            tray_icon=None,
        )
        hidden_tray = mock.Mock()
        hidden_tray.isVisible.return_value = False
        hidden_window = SimpleNamespace(
            notifications_enabled=True,
            tray_icon=hidden_tray,
        )
        failing_tray = mock.Mock()
        failing_tray.isVisible.return_value = True
        failing_tray.showMessage.side_effect = RuntimeError("unavailable")
        failing_window = SimpleNamespace(
            notifications_enabled=True,
            tray_icon=failing_tray,
        )

        self.assertFalse(
            MainWindow._show_system_notification(missing_window, "Title", "Message")
        )
        self.assertFalse(
            MainWindow._show_system_notification(hidden_window, "Title", "Message")
        )
        self.assertFalse(
            MainWindow._show_system_notification(failing_window, "Title", "Message")
        )

    def test_scan_stats_show_eta_on_the_same_line(self):
        window = SimpleNamespace(
            _format_scan_duration=MethodType(MainWindow._format_scan_duration, object()),
        )

        text = MainWindow._scan_stats_text(
            window,
            {
                "elapsed_secs": 12,
                "rate": 44.4,
                "scanned": 1200,
                "percent": 50,
                "eta_secs": 12,
            },
        )

        self.assertEqual(
            text,
            "1,200 items scanned - 44 items/sec - 00:12 elapsed - ETA 00:12",
        )
        self.assertNotIn("\n", text)

    def test_unavailable_system_tray_is_skipped(self):
        window = SimpleNamespace(tray_icon=object())
        with mock.patch.object(
            QSystemTrayIcon,
            "isSystemTrayAvailable",
            return_value=False,
        ):
            MainWindow._setup_tray_icon(window)

        self.assertIsNone(window.tray_icon)

    def test_scan_completion_reuses_status_text_for_notification(self):
        finished_thread = SimpleNamespace(
            scan_elapsed_secs=9.0,
            scan_indexed_count=1204,
            scan_scanned_count=1210,
            is_cancelled=False,
        )
        completion_message = (
            "Scan complete in 00:09. 1,204 items scanned. "
            "6 items skipped by exclusion rules."
        )
        content_stack = mock.Mock()
        content_stack.currentIndex.return_value = 1
        window = SimpleNamespace(
            scanner_thread=finished_thread,
            last_scan_elapsed_secs=0.0,
            last_scan_item_count=0,
            btn_rescan=mock.Mock(),
            is_scanning=True,
            _update_file_types_enabled=mock.Mock(),
            scan_progress_was_determinate=False,
            lbl_status=mock.Mock(),
            _scan_complete_status_text=mock.Mock(return_value=completion_message),
            current_scan_root=r"C:\scan",
            folder_cache=SimpleNamespace(running_total_size=100),
            _should_show_completion_notification=mock.Mock(return_value=True),
            _show_system_notification=mock.Mock(),
            content_stack=content_stack,
            current_lazy_show_all_tree=True,
            tree_model=None,
            scan_refresh_timer=mock.Mock(),
            _update_chips_sql=mock.Mock(),
            _set_total_summary_chip=mock.Mock(),
            _set_selected_summary_chip=mock.Mock(),
        )

        with mock.patch("src.main_window.record_scan_history"):
            MainWindow._on_scan_done(window)

        window.lbl_status.setText.assert_called_once_with(completion_message)
        window._should_show_completion_notification.assert_called_once_with(
            elapsed_secs=9.0,
            cancelled=False,
        )
        window._show_system_notification.assert_called_once_with(
            "Scan complete",
            completion_message,
        )


if __name__ == "__main__":
    unittest.main()
