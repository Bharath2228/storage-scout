import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from src.main_window import FileTypeBarDelegate, FileTypesDialog


class FileTypesDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.dialog = FileTypesDialog()

    def tearDown(self):
        self.dialog.close()
        self.app.processEvents()

    def test_loaded_rows_use_summary_metrics_and_numeric_size_sorting(self):
        self.dialog.set_rows(
            [
                ("json", 2_048, 2),
                (".db", 5_000_000, 1),
                ("(no extension)", 12, 3),
            ]
        )

        self.assertFalse(self.dialog.summary_strip.isHidden())
        self.assertTrue(self.dialog.detail_label.isHidden())
        self.assertEqual(self.dialog.summary_types_value.text(), "3")
        self.assertEqual(self.dialog.summary_files_value.text(), "6")
        self.assertEqual(self.dialog.table.item(0, 0).text(), ".db")
        self.assertEqual(
            self.dialog.table.horizontalHeader().sortIndicatorSection(),
            2,
        )
        self.assertEqual(
            self.dialog.table.horizontalHeader().sortIndicatorOrder(),
            Qt.SortOrder.DescendingOrder,
        )

    def test_display_prefix_does_not_change_extension_filter_value(self):
        self.dialog.set_rows([("json", 100, 1)])
        selected = []
        self.dialog.extension_selected.connect(selected.append)

        self.assertEqual(self.dialog.table.item(0, 0).text(), ".json")
        self.dialog._activate_row(0, 0)

        self.assertEqual(selected, ["json"])

    def test_share_labels_remain_readable_for_tiny_values(self):
        self.assertEqual(FileTypeBarDelegate.format_share(0), "0%")
        self.assertEqual(FileTypeBarDelegate.format_share(0.0001), "<0.1%")
        self.assertEqual(FileTypeBarDelegate.format_share(0.125), "12.5%")


if __name__ == "__main__":
    unittest.main()
