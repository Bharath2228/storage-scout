import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QLabel

from src.main_window import (
    DeletePreviewDialog,
    DeletePreviewThread,
    DeleteAuthDialog,
    summarize_paths_batch,
)


class DeletePreviewDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.dialog = DeletePreviewDialog()

    def tearDown(self):
        self.dialog.close()
        self.app.processEvents()

    def test_completed_preview_shows_summary_and_hides_progress(self):
        self.dialog.apply_preview(
            {
                "paths": [r"C:\one", r"C:\two"],
                "total": 5,
                "delete_operations": 2,
                "folders": 1,
                "files": 4,
                "size": 1024,
            }
        )

        self.assertEqual(self.dialog.title_label.text(), "Review before deleting")
        self.assertEqual(self.dialog.primary_count_label.text(), "2 items to delete")
        self.assertTrue(self.dialog.primary_count_label.isVisibleTo(self.dialog))
        self.assertEqual(self.dialog.matched_value.text(), "5")
        self.assertEqual(self.dialog.recycle_count_value.text(), "2")
        self.assertEqual(self.dialog.folders_value.text(), "1")
        self.assertEqual(self.dialog.files_value.text(), "4")
        self.assertEqual(self.dialog.size_value.text(), "1.0 KB")
        self.assertTrue(self.dialog.summary_widget.isVisibleTo(self.dialog))
        self.assertTrue(self.dialog.preview_note.isVisibleTo(self.dialog))
        self.assertFalse(self.dialog.progress.isVisibleTo(self.dialog))
        self.assertTrue(self.dialog.delete_button.isEnabled())
        self.assertFalse(self.dialog.delete_button.icon().isNull())
        self.assertEqual(
            self.dialog.delete_button.cursor().shape(),
            Qt.CursorShape.PointingHandCursor,
        )
        self.assertEqual(
            self.dialog.cancel_button.cursor().shape(),
            Qt.CursorShape.PointingHandCursor,
        )
        self.assertFalse(self.dialog.delete_button.autoDefault())
        self.assertFalse(self.dialog.cancel_button.autoDefault())

    def test_enter_does_not_close_delete_preview(self):
        self.dialog.apply_preview(
            {
                "paths": [r"C:\one"],
                "total": 1,
                "delete_operations": 1,
                "folders": 0,
                "files": 1,
                "size": 10,
            }
        )

        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Return,
            Qt.KeyboardModifier.NoModifier,
        )
        with mock.patch.object(self.dialog, "accept") as accept, mock.patch.object(
            self.dialog,
            "reject",
        ) as reject:
            self.dialog.keyPressEvent(event)

        self.assertTrue(event.isAccepted())
        accept.assert_not_called()
        reject.assert_not_called()

    def test_equal_counts_do_not_show_explanatory_note(self):
        self.dialog.apply_preview(
            {
                "paths": [r"C:\one"],
                "total": 1,
                "delete_operations": 1,
                "folders": 0,
                "files": 1,
                "size": 10,
            }
        )

        self.assertFalse(self.dialog.preview_note.isVisibleTo(self.dialog))

    def test_calculating_and_error_states_keep_delete_disabled(self):
        self.dialog.apply_preview({"paths": [], "size": None})
        self.assertEqual(self.dialog.title_label.text(), "Preparing delete preview")
        self.assertFalse(self.dialog.primary_count_label.isVisibleTo(self.dialog))
        self.assertTrue(self.dialog.progress.isVisibleTo(self.dialog))
        self.assertFalse(self.dialog.delete_button.isEnabled())

        self.dialog.show_error("Database unavailable")
        self.assertEqual(self.dialog.title_label.text(), "Could not calculate preview")
        self.assertFalse(self.dialog.primary_count_label.isVisibleTo(self.dialog))
        self.assertEqual(self.dialog.error_label.text(), "Database unavailable")
        self.assertFalse(self.dialog.progress.isVisibleTo(self.dialog))
        self.assertFalse(self.dialog.delete_button.isEnabled())

    def test_cancel_interrupts_active_database_work(self):
        thread = DeletePreviewThread()
        connection = mock.Mock()
        thread._connection = connection

        thread.cancel()

        self.assertTrue(thread.is_cancelled)
        connection.interrupt.assert_called_once_with()
        with self.assertRaises(DeletePreviewThread._Cancelled):
            thread._raise_if_cancelled()

    def test_bulk_preview_omits_paths_unselected_from_a_page(self):
        cursor = mock.Mock()
        cursor.fetchall.return_value = [
            (r"C:\scan\page-one.txt", 0),
            (r"C:\scan\page-two.txt", 0),
        ]
        thread = DeletePreviewThread(
            bulk_scope={
                "where_sql": "",
                "params": [],
                "folder_delete_mode": "empty_only",
                "excluded_paths": [r"C:\scan\page-two.txt"],
            }
        )

        paths = thread._build_bulk_paths(cursor)

        self.assertEqual(paths, [r"C:\scan\page-one.txt"])

    def test_window_reject_uses_same_cancel_signal_as_cancel_button(self):
        thread = DeletePreviewThread()
        self.dialog.finished.connect(thread.cancel)

        self.dialog.reject()

        self.assertTrue(thread.is_cancelled)

    def test_unindexed_filesystem_selection_uses_live_counts_and_size(self):
        cursor = mock.Mock()
        cursor.fetchall.return_value = []
        with tempfile.TemporaryDirectory() as root:
            nested = os.path.join(root, "nested")
            os.makedirs(nested)
            with open(os.path.join(root, "one.bin"), "wb") as handle:
                handle.write(b"123")
            with open(os.path.join(nested, "two.bin"), "wb") as handle:
                handle.write(b"12345")

            _paths, folders, files, size = summarize_paths_batch(cursor, [root])

        self.assertEqual(folders, 2)
        self.assertEqual(files, 2)
        self.assertEqual(size, 8)


class DeleteAuthDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_auth_dialog_uses_recycle_bin_language_and_hand_cursors(self):
        dialog = DeleteAuthDialog(2, 642 * 1024)
        try:
            text = " ".join(label.text() for label in dialog.findChildren(QLabel))
            self.assertIn("to the Recycle Bin", text)
            self.assertNotIn("permanently", text.lower())
            self.assertEqual(
                dialog.cancel_button.cursor().shape(),
                Qt.CursorShape.PointingHandCursor,
            )
            self.assertEqual(
                dialog.submit_button.cursor().shape(),
                Qt.CursorShape.PointingHandCursor,
            )
        finally:
            dialog.close()

    def test_enter_key_authorizes_when_password_is_available(self):
        dialog = DeleteAuthDialog(1, 10)
        try:
            dialog.username_input.setText("admin")
            dialog.password_input.setText("secret")
            with mock.patch.object(dialog, "_submit") as submit:
                dialog._username_return_pressed()
                submit.assert_called_once_with()
        finally:
            dialog.close()

    def test_enter_from_username_moves_to_password_when_password_empty(self):
        dialog = DeleteAuthDialog(1, 10)
        try:
            dialog.show()
            self.app.processEvents()
            dialog.username_input.setText("admin")
            dialog.password_input.clear()
            with mock.patch.object(dialog, "_submit") as submit:
                dialog._username_return_pressed()
                submit.assert_not_called()
            self.assertIs(QApplication.focusWidget(), dialog.password_input)
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
