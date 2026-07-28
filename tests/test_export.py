import csv
import io
import os
import sqlite3
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from src.main_window import (
    DEFAULT_EXPORT_COLUMNS,
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


if __name__ == "__main__":
    unittest.main()
