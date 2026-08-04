import csv
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from src.main_window import (
    DEFAULT_EXPORT_COLUMNS,
    EXPORT_COLUMNS,
    ExportDialog,
    ExportThread,
    _export_listing_row,
)


class _MemoryTool:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            """
            CREATE TABLE file_index (
                root TEXT,
                path TEXT,
                name TEXT,
                parent_path TEXT,
                is_folder INTEGER,
                size INTEGER,
                modified_time REAL,
                extension TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE folder_summary (
                path TEXT,
                total_size INTEGER,
                file_count INTEGER,
                folder_count INTEGER,
                child_count INTEGER DEFAULT 0,
                physical_child_count INTEGER DEFAULT 0
            )
            """
        )

    def extension_breakdown(self):
        return [(".txt", 30, 2), ("(no extension)", 5, 1)]

    def close(self):
        self.conn.close()


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings = QSettings(
            os.path.join(self.temp_dir.name, "settings.ini"),
            QSettings.Format.IniFormat,
        )

    def tearDown(self):
        self.temp_dir.cleanup()
        self.app.processEvents()

    def test_dialog_defaults_match_existing_current_page_export(self):
        dialog = ExportDialog(False, False, self.settings)
        self.assertEqual(dialog.export_type(), "listing")
        self.assertEqual(dialog.export_scope(), "current_page")
        self.assertEqual(dialog.selected_columns(), list(DEFAULT_EXPORT_COLUMNS))
        self.assertFalse(dialog.scope_buttons["selected"].isEnabled())
        self.assertFalse(dialog.scope_buttons["bulk_scope"].isEnabled())
        self.assertFalse(dialog.include_summary.isChecked())
        self.assertIn("file and folder rows", dialog.type_help.text())
        self.assertIn("current results page", dialog.scope_help.text())
        self.assertIn(
            "Select or check at least one item",
            dialog.scope_buttons["selected"].toolTip(),
        )
        self.assertIn("Unavailable", dialog.scope_buttons["selected"].text())
        self.assertTrue(dialog.scope_buttons["selected"].property("unavailable"))
        self.assertIn("Unavailable", dialog.scope_buttons["bulk_scope"].text())
        dialog.close()

    def test_dialog_help_updates_for_type_and_scope(self):
        dialog = ExportDialog(True, True, self.settings)
        dialog.scope_buttons["bulk_scope"].click()
        self.app.processEvents()
        self.assertIn("all-pages selection", dialog.scope_help.text())
        self.assertIn("bulk deletion", dialog.scope_buttons["bulk_scope"].toolTip())

        file_types_index = dialog.type_combo.findData("file_types")
        dialog.type_combo.setCurrentIndex(file_types_index)
        self.app.processEvents()
        self.assertIn("file extension", dialog.type_help.text())
        self.assertFalse(dialog.scope_frame.isVisible())
        dialog.close()

    def test_dialog_stays_within_screen_and_scrolls_on_small_height(self):
        dialog = ExportDialog(False, False, self.settings)
        available = self.app.primaryScreen().availableGeometry()
        self.assertLessEqual(dialog.width(), available.width())
        self.assertLessEqual(dialog.height(), available.height())

        dialog.resize(420, 420)
        dialog.show()
        self.app.processEvents()

        self.assertGreater(dialog.options_scroll.height(), 0)
        self.assertGreater(
            dialog.options_scroll.verticalScrollBar().maximum(),
            0,
        )
        self.assertLessEqual(
            dialog.continue_button.geometry().bottom(),
            dialog.contentsRect().bottom(),
        )
        dialog.close()

    def test_listing_row_supports_all_columns(self):
        columns = [
            "name", "path", "type", "location", "last_modified",
            "age", "size_bytes", "size_formatted", "extension", "status",
        ]
        values = _export_listing_row(
            (
                r"C:\root\report.csv",
                "report.csv",
                0,
                1024,
                1_700_000_000,
                r"C:\root",
                ".csv",
                0,
            ),
            columns,
            age_cutoff=1_800_000_000,
        )
        self.assertEqual(values[0:4], [
            "report.csv", r"C:\root\report.csv", "File", r"C:\root",
        ])
        self.assertEqual(values[6:10], [1024, "1.0 KB", ".csv", "Inactive"])

    def test_selected_export_batches_more_than_sqlite_parameter_limit(self):
        tool = _MemoryTool()
        rows = [
            (
                r"C:\root",
                rf"C:\root\file-{index}.txt",
                f"file-{index}.txt",
                r"C:\root",
                0,
                index,
                1_700_000_000,
                ".txt",
            )
            for index in range(1005)
        ]
        tool.conn.executemany(
            "INSERT INTO file_index "
            "(root, path, name, parent_path, is_folder, size, modified_time, extension) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        thread = ExportThread(
            os.path.join(self.temp_dir.name, "selected.csv"),
            {
                "export_type": "listing",
                "scope": "selected",
                "columns": ["path"],
                "selected_paths": [row[1] for row in rows],
                "age_cutoff": None,
            },
        )
        output = io.StringIO()
        written = thread._write_listing(tool, csv.writer(output))
        self.assertEqual(written, 1005)
        self.assertEqual(len(list(csv.reader(io.StringIO(output.getvalue())))), 1006)
        tool.conn.close()

    def test_cancel_removes_partial_file(self):
        target = os.path.join(self.temp_dir.name, "cancelled.csv")
        thread = ExportThread(
            target,
            {
                "export_type": "listing",
                "scope": "current_page",
                "columns": ["name"],
                "current_page_rows": [],
            },
        )
        with open(thread.temp_path, "w", encoding="utf-8") as handle:
            handle.write("partial")
        thread.cancel()
        thread._remove_partial()
        self.assertFalse(os.path.exists(thread.temp_path))

    def test_export_file_uses_system_excel_separator(self):
        target = os.path.join(self.temp_dir.name, "excel.csv")
        tool = _MemoryTool()
        thread = ExportThread(
            target,
            {
                "export_type": "listing",
                "scope": "current_page",
                "columns": ["name", "path"],
                "current_page_rows": [
                    (
                        r"C:\root\report.csv",
                        "report.csv",
                        0,
                        10,
                        1_700_000_000,
                        r"C:\root",
                        ".csv",
                        0,
                    )
                ],
                "age_cutoff": None,
                "include_summary": False,
            },
        )

        with (
            mock.patch("src.file_index_tool.FileIndexTool", return_value=tool),
            mock.patch("src.main_window._preferred_csv_delimiter", return_value=","),
        ):
            thread.run()

        with open(target, "r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle, delimiter=","))
        self.assertEqual(rows[0], ["Name", "Full Path"])
        self.assertEqual(rows[1], ["report.csv", r"C:\root\report.csv"])

    def test_all_listing_columns_are_separate_and_in_selected_order(self):
        target = os.path.join(self.temp_dir.name, "all-columns.csv")
        columns = [key for key, _label in EXPORT_COLUMNS]
        tool = _MemoryTool()
        thread = ExportThread(
            target,
            {
                "export_type": "listing",
                "scope": "current_page",
                "columns": columns,
                "current_page_rows": [
                    (
                        r"C:\root\report.csv",
                        "report.csv",
                        0,
                        1024,
                        1_700_000_000,
                        r"C:\root",
                        ".csv",
                        0,
                    )
                ],
                "age_cutoff": 1_800_000_000,
                "include_summary": False,
            },
        )

        with (
            mock.patch("src.file_index_tool.FileIndexTool", return_value=tool),
            mock.patch("src.main_window._preferred_csv_delimiter", return_value=","),
        ):
            thread.run()

        with open(target, "r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle, delimiter=","))
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[0]), 10)
        self.assertEqual(len(rows[1]), 10)
        self.assertEqual(rows[0][0:4], ["Name", "Full Path", "Type", "Location / Parent Path"])
        self.assertEqual(rows[1][0:4], [
            "report.csv", r"C:\root\report.csv", "File", r"C:\root",
        ])

    def test_each_listing_scope_writes_expected_rows(self):
        tool = _MemoryTool()
        rows = [
            (r"C:\root-a", r"C:\root-a\one.txt", "one.txt", r"C:\root-a", 0, 10, 1_700_000_000, ".txt"),
            (r"C:\root-a", r"C:\root-a\two.log", "two.log", r"C:\root-a", 0, 20, 1_700_000_000, ".log"),
            (r"C:\root-b", r"C:\root-b\three.txt", "three.txt", r"C:\root-b", 0, 30, 1_700_000_000, ".txt"),
        ]
        tool.conn.executemany(
            "INSERT INTO file_index "
            "(root, path, name, parent_path, is_folder, size, modified_time, extension) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

        cases = (
            ("all_matching", {"where_sql": " WHERE extension = ?", "params": [".txt"]}, 2),
            ("entire_scan", {"scan_root": r"C:\root-a"}, 2),
            (
                "bulk_scope",
                {
                    "bulk_where_sql": " WHERE root = ?",
                    "bulk_params": [r"C:\root-a"],
                    "folder_delete_mode": "empty_only",
                    "excluded_paths": [r"C:\root-a\two.log"],
                },
                1,
            ),
        )
        for scope, extra_config, expected in cases:
            with self.subTest(scope=scope):
                config = {
                    "export_type": "listing",
                    "scope": scope,
                    "columns": ["name", "size_bytes"],
                    **extra_config,
                }
                output = io.StringIO()
                written = ExportThread("unused.csv", config)._write_listing(
                    tool,
                    csv.writer(output, delimiter=";"),
                )
                parsed = list(csv.reader(io.StringIO(output.getvalue()), delimiter=";"))
                self.assertEqual(written, expected)
                self.assertEqual(len(parsed), expected + 1)
                self.assertTrue(all(len(row) == 2 for row in parsed))
        tool.close()

    def test_fixed_report_types_write_rectangular_csv_rows(self):
        tool = _MemoryTool()
        tool.conn.execute(
            "INSERT INTO folder_summary "
            "(path, total_size, file_count, folder_count) VALUES (?, ?, ?, ?)",
            (r"C:\root", 35, 3, 1),
        )
        thread = ExportThread("unused.csv", {})

        file_types = io.StringIO()
        thread._write_file_types(tool, csv.writer(file_types, delimiter=";"))
        file_type_rows = list(csv.reader(io.StringIO(file_types.getvalue()), delimiter=";"))
        self.assertTrue(all(len(row) == 4 for row in file_type_rows))

        folders = io.StringIO()
        thread._write_folder_summary(tool, csv.writer(folders, delimiter=";"))
        folder_rows = list(csv.reader(io.StringIO(folders.getvalue()), delimiter=";"))
        self.assertTrue(all(len(row) == 5 for row in folder_rows))

        audit_path = os.path.join(self.temp_dir.name, "delete_audit.log")
        with open(audit_path, "w", encoding="utf-8") as handle:
            handle.write(
                "2026-08-02T13:27:52  user=Admin  action=delete_completed  "
                "items=2  size=35  errors=0\n"
            )
        audit = io.StringIO()
        with mock.patch("src.auth.DELETE_AUDIT_LOG_PATH", audit_path):
            thread._write_delete_audit(csv.writer(audit, delimiter=";"))
        audit_rows = list(csv.reader(io.StringIO(audit.getvalue()), delimiter=";"))
        self.assertTrue(all(len(row) == 8 for row in audit_rows))

        history = io.StringIO()
        with mock.patch(
            "src.scan_history.load_scan_history",
            return_value={
                "root": {
                    "root": r"C:\root",
                    "item_count": 3,
                    "total_size": 35,
                    "last_scan_duration_secs": 1.5,
                    "last_scanned_at": 1_700_000_000,
                }
            },
        ):
            thread._write_scan_history(csv.writer(history, delimiter=";"))
        history_rows = list(csv.reader(io.StringIO(history.getvalue()), delimiter=";"))
        self.assertTrue(all(len(row) == 6 for row in history_rows))
        tool.close()


if __name__ == "__main__":
    unittest.main()
