import sqlite3
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src import file_index_tool
from src.main_window import MainWindow, TotalsThread


class FilteredTotalsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute(
            """
            CREATE TABLE file_index (
                path TEXT,
                name TEXT,
                is_folder INTEGER,
                size INTEGER,
                modified_time REAL,
                parent_path TEXT,
                extension TEXT,
                root TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE folder_summary (
                path TEXT,
                total_size INTEGER,
                file_count INTEGER,
                folder_count INTEGER,
                child_count INTEGER
            )
            """
        )
        root = r"C:\scan"
        folder = root + r"\Projects"
        self.connection.executemany(
            "INSERT INTO file_index VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (root, "scan", 1, 0, 1, None, "", root),
                (folder, "Projects", 1, 0, 1, root, "", root),
                (folder + r"\report.csv", "report.csv", 0, 100, 1, folder, ".csv", root),
                (root + r"\other.txt", "other.txt", 0, 50, 1, root, ".txt", root),
            ],
        )
        self.options = {
            "age_cutoff": None,
            "is_scanning": False,
            "root_path": root,
            "cached_folder_total": 150,
        }

    def tearDown(self):
        self.connection.close()

    def _compute(self, **filtered_options):
        class FakeTool:
            def __init__(inner_self):
                inner_self.conn = self.connection

            def close(inner_self):
                pass

        options = dict(self.options)
        options.update(filtered_options)
        with mock.patch.object(file_index_tool, "FileIndexTool", FakeTool):
            return TotalsThread(1, options)._compute()

    def test_type_filter_total_sums_only_matching_files(self):
        result = self._compute(
            filtered_where_sql=" WHERE extension = ? COLLATE NOCASE",
            filtered_where_params=[".csv"],
            filtered_size_label="Type .csv Size",
        )

        self.assertEqual(result["filtered_total"], 100)
        self.assertEqual(result["filtered_size_label"], "Type .csv Size")

    def test_search_total_includes_files_inside_a_matching_folder(self):
        result = self._compute(
            filtered_where_sql=" WHERE name LIKE ? COLLATE NOCASE",
            filtered_where_params=["%Projects%"],
            filtered_size_label="Search Size",
        )

        self.assertEqual(result["filtered_total"], 100)

    def test_filtered_size_does_not_replace_overall_total_size(self):
        window = MainWindow()
        try:
            window.totals_request_id = 7
            window.is_scanning = False
            window.current_scan_root = r"C:\scan"
            window._on_totals_ready(
                7,
                {
                    "empty_n": 0,
                    "inactive_folders": 0,
                    "inactive_files": 0,
                    "folder_total": 150,
                    "folder_file_count": 2,
                    "filtered_total": 100,
                    "filtered_size_label": "Search Size",
                },
            )

            self.assertEqual(window.chip_browse_size.text(), "Total Size 150.0 B")
            self.assertEqual(window.chip_filtered_size.text(), "Search Size 100.0 B")
            self.assertFalse(window.chip_filtered_size.isHidden())
        finally:
            window.close()

    def test_scoped_folder_total_falls_back_to_filesystem_for_nas_content(self):
        with tempfile.TemporaryDirectory() as scope:
            os.makedirs(os.path.join(scope, "nested"))
            with open(os.path.join(scope, "one.bin"), "wb") as handle:
                handle.write(b"1234")
            with open(os.path.join(scope, "nested", "two.bin"), "wb") as handle:
                handle.write(b"123456")

            result = self._compute(
                folder_scope=scope,
                filtered_where_sql=None,
                filtered_where_params=(),
                filtered_size_label=None,
            )

        self.assertEqual(result["scoped_folder_total"], 10)


if __name__ == "__main__":
    unittest.main()
