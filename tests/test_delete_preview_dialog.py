import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.main_window import (
    DeletePreviewDialog,
    DeletePreviewThread,
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
        self.assertEqual(self.dialog.matched_value.text(), "5")
        self.assertEqual(self.dialog.recycle_count_value.text(), "2")
        self.assertEqual(self.dialog.folders_value.text(), "1")
        self.assertEqual(self.dialog.files_value.text(), "4")
        self.assertEqual(self.dialog.size_value.text(), "1.0 KB")
        self.assertTrue(self.dialog.summary_widget.isVisibleTo(self.dialog))
        self.assertTrue(self.dialog.preview_note.isVisibleTo(self.dialog))
        self.assertFalse(self.dialog.progress.isVisibleTo(self.dialog))
        self.assertTrue(self.dialog.delete_button.isEnabled())

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
        self.assertTrue(self.dialog.progress.isVisibleTo(self.dialog))
        self.assertFalse(self.dialog.delete_button.isEnabled())

        self.dialog.show_error("Database unavailable")
        self.assertEqual(self.dialog.title_label.text(), "Could not calculate preview")
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


if __name__ == "__main__":
    unittest.main()
