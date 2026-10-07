import csv
import os
from datetime import datetime

from PyQt6.QtCore import (
    QThread,
    pyqtSignal,
)

from ...models import format_size
from ..constants import EMPTY_FOLDER_SQL, EXPORT_COLUMNS
from ..export_utils import _export_listing_row, _preferred_csv_delimiter
from ..path_utils import PathKeyIndex, _path_key, bulk_scope_excluded_keys


class ExportThread(QThread):
    progress = pyqtSignal(int, int, str)
    export_finished = pyqtSignal(int, int)
    export_failed = pyqtSignal(str)
    export_cancelled = pyqtSignal()

    LISTING_SELECT = (
        "SELECT path, name, is_folder, size, modified_time, parent_path, extension, "
        "CASE WHEN is_folder = 1 AND NOT EXISTS ("
        "SELECT 1 FROM file_index child WHERE child.parent_path = file_index.path COLLATE NOCASE"
        ") THEN 1 ELSE 0 END AS is_empty FROM file_index"
    )

    class _Cancelled(Exception):
        pass

    def __init__(self, target_path, config, parent=None):
        super().__init__(parent)
        self.target_path = os.path.normpath(target_path)
        self.config = dict(config)
        self.is_cancelled = False
        self.temp_path = self.target_path + ".part"

    def cancel(self):
        self.is_cancelled = True

    def _check_cancelled(self):
        if self.is_cancelled:
            raise self._Cancelled()

    def run(self):
        from src.file_index_tool import FileIndexTool

        tool = None
        row_count = 0
        try:
            os.makedirs(os.path.dirname(self.target_path) or ".", exist_ok=True)
            tool = FileIndexTool()
            with open(self.temp_path, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle, delimiter=_preferred_csv_delimiter())
                if self.config.get("include_summary"):
                    for key, value in self.config.get("metadata", {}).items():
                        writer.writerow([f"# {key}", value])
                    writer.writerow([])
                row_count = self._write_export(tool, writer)
            self._check_cancelled()
            os.replace(self.temp_path, self.target_path)
            self.export_finished.emit(row_count, os.path.getsize(self.target_path))
        except self._Cancelled:
            self._remove_partial()
            self.export_cancelled.emit()
        except Exception as exc:
            self._remove_partial()
            self.export_failed.emit(str(exc))
        finally:
            if tool is not None:
                tool.close()

    def _remove_partial(self):
        try:
            if os.path.exists(self.temp_path):
                os.remove(self.temp_path)
        except OSError:
            pass

    def _write_export(self, tool, writer):
        export_type = self.config["export_type"]
        if export_type == "listing":
            return self._write_listing(tool, writer)
        if export_type == "file_types":
            return self._write_file_types(tool, writer)
        if export_type == "folder_summary":
            return self._write_folder_summary(tool, writer)
        if export_type == "delete_audit":
            return self._write_delete_audit(writer)
        if export_type == "scan_history":
            return self._write_scan_history(writer)
        raise ValueError("Unsupported export type.")

    def _write_listing(self, tool, writer):
        columns = self.config["columns"]
        labels = dict(EXPORT_COLUMNS)
        writer.writerow([labels[column] for column in columns])
        scope = self.config["scope"]
        if scope == "current_page":
            rows = self.config.get("current_page_rows", [])
            total = len(rows)
            for index, row in enumerate(rows, 1):
                self._check_cancelled()
                writer.writerow(_export_listing_row(row, columns, self.config.get("age_cutoff")))
                if index % 100 == 0 or index == total:
                    self.progress.emit(index, total, "Writing current page")
            return total

        if scope == "selected":
            return self._write_selected_listing(tool, writer, columns)

        cursor = tool.conn.cursor()
        where_sql, params = self._listing_where()
        cursor.execute("SELECT COUNT(*) FROM file_index" + where_sql, params)
        total = int(cursor.fetchone()[0] or 0)
        prune_bulk_roots = (
            scope == "bulk_scope"
            and self.config.get("folder_delete_mode") == "all"
        )
        order_sql = " ORDER BY length(path), lower(path)" if prune_bulk_roots else " ORDER BY lower(path)"
        query = self.LISTING_SELECT + where_sql + order_sql
        cursor.execute(query, params)
        written = 0
        processed = 0
        retained_paths = PathKeyIndex()
        excluded_keys = (
            bulk_scope_excluded_keys(self.config)
            if scope == "bulk_scope"
            else set()
        )
        while True:
            self._check_cancelled()
            rows = cursor.fetchmany(1000)
            if not rows:
                break
            for row in rows:
                self._check_cancelled()
                processed += 1
                row_key = _path_key(row[0])
                if row_key in excluded_keys:
                    continue
                if prune_bulk_roots and retained_paths.has_ancestor(row_key, include_self=False):
                    continue
                writer.writerow(_export_listing_row(row, columns, self.config.get("age_cutoff")))
                if prune_bulk_roots:
                    retained_paths.add(row_key)
                written += 1
            self.progress.emit(processed, total, "Writing file and folder rows")
        return written

    def _write_selected_listing(self, tool, writer, columns):
        paths = list(self.config.get("selected_paths", []))
        total = len(paths)
        written = 0
        cursor = tool.conn.cursor()
        for start in range(0, total, 900):
            self._check_cancelled()
            batch = paths[start:start + 900]
            placeholders = ",".join("?" * len(batch))
            cursor.execute(
                self.LISTING_SELECT
                + f" WHERE path COLLATE NOCASE IN ({placeholders}) ORDER BY lower(path)",
                batch,
            )
            for row in cursor.fetchall():
                self._check_cancelled()
                writer.writerow(
                    _export_listing_row(row, columns, self.config.get("age_cutoff"))
                )
                written += 1
            self.progress.emit(written, total, "Writing selected items")
        return written

    def _listing_where(self):
        scope = self.config["scope"]
        if scope == "all_matching":
            return self.config.get("where_sql", ""), list(self.config.get("params", []))
        if scope == "entire_scan":
            root = self.config.get("scan_root")
            return (" WHERE root = ? COLLATE NOCASE", [root]) if root else ("", [])
        if scope == "bulk_scope":
            where_sql = self.config.get("bulk_where_sql", "")
            params = list(self.config.get("bulk_params", []))
            if self.config.get("folder_delete_mode") == "empty_only":
                folder_clause = f"(is_folder = 0 OR ({EMPTY_FOLDER_SQL}))"
                where_sql = (
                    where_sql + " AND " + folder_clause
                    if where_sql else " WHERE " + folder_clause
                )
            return where_sql, params
        raise ValueError("Unsupported export scope.")

    def _write_file_types(self, tool, writer):
        writer.writerow(["Extension", "Total Size (bytes)", "Total Size", "File Count"])
        rows = tool.extension_breakdown()
        for index, (extension, total_size, file_count) in enumerate(rows, 1):
            self._check_cancelled()
            writer.writerow([extension, total_size, format_size(total_size), file_count])
            self.progress.emit(index, len(rows), "Writing file type breakdown")
        return len(rows)

    def _write_folder_summary(self, tool, writer):
        writer.writerow(["Path", "Total Size (bytes)", "Total Size", "File Count", "Folder Count"])
        cursor = tool.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM folder_summary")
        total = int(cursor.fetchone()[0] or 0)
        cursor.execute(
            "SELECT path, total_size, file_count, folder_count "
            "FROM folder_summary ORDER BY lower(path)"
        )
        written = 0
        while True:
            self._check_cancelled()
            rows = cursor.fetchmany(1000)
            if not rows:
                break
            for path, total_size, file_count, folder_count in rows:
                writer.writerow([
                    path,
                    int(total_size or 0),
                    format_size(int(total_size or 0)),
                    int(file_count or 0),
                    int(folder_count or 0),
                ])
                written += 1
            self.progress.emit(written, total, "Writing folder summaries")
        return written

    def _write_delete_audit(self, writer):
        from src.auth import DELETE_AUDIT_LOG_PATH

        headers = [
            "Timestamp", "User", "Action", "Items", "Size (bytes)",
            "Errors", "Attempted User", "Reason",
        ]
        writer.writerow(headers)
        if not os.path.exists(DELETE_AUDIT_LOG_PATH):
            return 0
        written = 0
        with open(DELETE_AUDIT_LOG_PATH, "r", encoding="utf-8") as handle:
            for line in handle:
                self._check_cancelled()
                parts = [part.strip() for part in line.strip().split("  ") if part.strip()]
                if not parts:
                    continue
                fields = {"timestamp": parts[0]}
                for part in parts[1:]:
                    key, separator, value = part.partition("=")
                    if separator:
                        fields[key] = value
                writer.writerow([
                    fields.get("timestamp", ""),
                    fields.get("user", ""),
                    fields.get("action", ""),
                    fields.get("items", ""),
                    fields.get("size", ""),
                    fields.get("errors", ""),
                    fields.get("attempt", ""),
                    fields.get("reason", ""),
                ])
                written += 1
                if written % 250 == 0:
                    self.progress.emit(written, 0, "Writing delete audit log")
        self.progress.emit(written, written, "Writing delete audit log")
        return written

    def _write_scan_history(self, writer):
        from src.scan_history import load_scan_history

        writer.writerow([
            "Root", "Item Count", "Total Size (bytes)", "Total Size",
            "Last Scan Duration (seconds)", "Last Scanned At",
        ])
        history = load_scan_history()
        entries = [entry for entry in history.values() if isinstance(entry, dict)]
        entries.sort(key=lambda entry: str(entry.get("root", "")).casefold())
        for index, entry in enumerate(entries, 1):
            self._check_cancelled()
            timestamp = float(entry.get("last_scanned_at", 0) or 0)
            size = int(entry.get("total_size", 0) or 0)
            writer.writerow([
                entry.get("root", ""),
                int(entry.get("item_count", 0) or 0),
                size,
                format_size(size),
                float(entry.get("last_scan_duration_secs", 0) or 0),
                datetime.fromtimestamp(timestamp).isoformat(timespec="seconds") if timestamp else "",
            ])
            self.progress.emit(index, len(entries), "Writing scan history")
        return len(entries)
