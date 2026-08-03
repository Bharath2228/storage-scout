import sqlite3
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src import file_index_tool
from src.folder_cache import FolderCache
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
                child_count INTEGER,
                physical_child_count INTEGER DEFAULT -1
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
                    "inactive_folders": 5233,
                    "inactive_files": 50118,
                    "folder_total": 150,
                    "folder_file_count": 2,
                    "filtered_total": 100,
                    "filtered_size_label": "Search Size",
                },
            )

            self.assertEqual(window.chip_browse_size.text(), "Total Size: 150.0 B")
            self.assertEqual(window.chip_filtered_size.text(), "Search Size 100.0 B")
            self.assertEqual(
                window.chip_inactive_folders.text(),
                "Inactive: 5,233 folders · 50,118 files",
            )
            window._set_selected_summary_chip("1.5 GB", 12, 3456)
            self.assertEqual(
                window.chip_selected_size.text(),
                "Selected: 3,456 files, 12 folders · 1.5 GB",
            )
            self.assertFalse(window.chip_filtered_size.isHidden())
        finally:
            window.close()

    def test_empty_results_clear_stale_page_and_status_metrics(self):
        window = MainWindow()
        try:
            window.is_scanning = False
            window._set_total_summary_chip("1.1 GB")
            window._set_chip_text(
                window.chip_inactive_folders,
                "Inactive: 0 folders · 11 files",
            )
            window._set_chip_text(window.chip_page_size, "Current Page Size: calculating...")

            window._set_empty_result_metrics()

            self.assertEqual(
                window.chip_inactive_folders.text(),
                "Inactive: 0 folders · 0 files",
            )
            self.assertEqual(window.chip_page_size.text(), "Current Page Size: 0 B")
            self.assertEqual(
                window.chip_selected_size.text(),
                "Selected: 0 files, 0 folders · 0 B",
            )
            self.assertEqual(window.chip_browse_size.text(), "Total Size: 1.1 GB")
        finally:
            window.close()

    def test_show_all_files_view_shows_page_size_only_when_paginated(self):
        window = MainWindow()
        try:
            window.fp.rb_all.setChecked(True)
            window.fp.rb_view_files.setChecked(True)
            window.current_lazy_show_all_tree = False

            window.current_total_matches = 4001
            window._update_status_metrics_visibility()
            self.assertFalse(window.chip_page_size.isHidden())

            window.current_total_matches = 2000
            window._update_status_metrics_visibility()
            self.assertTrue(window.chip_page_size.isHidden())
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

    def test_selecting_folder_displays_completed_cached_size_immediately(self):
        root = os.path.normpath(r"Z:\scan")
        scope = os.path.join(root, "Projects")
        cache = FolderCache()
        cache.add_item(root, "scan", True, 0, 1, None)
        cache.add_item(scope, "Projects", True, 0, 1, root)
        cache.set_folder_summary(scope, 5 * 1024, 2, 1, 2, 2)
        window = MainWindow()
        try:
            window.current_scan_root = root
            window.folder_cache = cache

            window._set_folder_browser_scope(scope, reload=False)

            self.assertEqual(window.chip_folder_size.text(), "Folder Size 5.0 KB")
            self.assertFalse(window.chip_folder_size.isHidden())
        finally:
            window.close()

    def test_pending_refresh_keeps_completed_cache_totals_visible(self):
        root = os.path.normpath(r"Z:\scan")
        scope = os.path.join(root, "Projects")
        cache = FolderCache()
        cache.add_item(root, "scan", True, 0, 1, None)
        cache.add_item(scope, "Projects", True, 0, 1, root)
        cache.set_folder_summary(root, 12 * 1024, 3, 2, 1, 1)
        cache.set_folder_summary(scope, 5 * 1024, 2, 1, 2, 2)
        window = MainWindow()
        try:
            window.is_scanning = False
            window.current_scan_root = root
            window.folder_browser_scope = scope
            window.folder_cache = cache
            window.cached_folder_total = None
            window.cached_folder_total_root = None

            window._set_size_totals_pending(browse=True)

            self.assertEqual(window.chip_browse_size.text(), "Total Size: 12.0 KB")
            self.assertEqual(window.chip_folder_size.text(), "Folder Size 5.0 KB")
        finally:
            window.close()

    def test_latest_totals_failure_restores_known_values(self):
        root = os.path.normpath(r"Z:\scan")
        scope = os.path.join(root, "Projects")
        cache = FolderCache()
        cache.add_item(root, "scan", True, 0, 1, None)
        cache.add_item(scope, "Projects", True, 0, 1, root)
        cache.set_folder_summary(root, 12 * 1024, 3, 2, 1, 1)
        cache.set_folder_summary(scope, 5 * 1024, 2, 1, 2, 2)
        window = MainWindow()
        try:
            window.is_scanning = False
            window.current_scan_root = root
            window.folder_browser_scope = scope
            window.folder_cache = cache
            window.totals_request_id = 9
            window._set_total_summary_chip("--")
            window._set_chip_text(
                window.chip_folder_size,
                "Folder Size calculating...",
            )

            window._on_totals_failed(9, "temporary read error")

            self.assertEqual(window.chip_browse_size.text(), "Total Size: 12.0 KB")
            self.assertEqual(window.chip_folder_size.text(), "Folder Size 5.0 KB")
        finally:
            window.close()

    def test_totals_refresh_reuses_cached_scoped_folder_size(self):
        with mock.patch.object(
            TotalsThread,
            "_scoped_folder_total_size",
            side_effect=AssertionError("cached size should avoid scoped lookup"),
        ):
            result = self._compute(
                folder_scope=r"C:\scan\Projects",
                cached_scoped_folder_total=4096,
                filtered_where_sql=None,
                filtered_where_params=(),
                filtered_size_label=None,
            )

        self.assertEqual(result["scoped_folder_total"], 4096)


if __name__ == "__main__":
    unittest.main()
