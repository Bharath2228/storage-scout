import os
import csv
import html
import subprocess
import send2trash
import time
from datetime import datetime
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QRadioButton, QSlider, QTreeView, QHeaderView,
    QMessageBox, QStyledItemDelegate, QButtonGroup, QApplication, QFileDialog,
    QSpinBox, QAbstractItemView, QStackedWidget, QStyleOptionViewItem,
    QMenu, QSizePolicy, QFrame, QStyle, QDialog, QProgressBar
)
from PyQt6.QtCore import Qt, QRect, QModelIndex, QPersistentModelIndex, QTimer, QEvent, QSignalBlocker, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QIcon, QFont

from .models import WatchdogTreeModel, WatchdogFilterProxyModel, format_size
from .scanner import ScannerThread

VIDEO_EXTENSIONS = (
    ".3g2", ".3gp", ".avi", ".divx", ".flv", ".m2ts", ".m4v",
    ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".ogv",
    ".rm", ".rmvb", ".ts", ".vob", ".webm", ".wmv",
)

EMPTY_FOLDER_SQL = (
    "is_folder = 1 AND NOT EXISTS ("
    "SELECT 1 FROM file_index child WHERE child.parent_path = file_index.path"
    ") AND lower(name) NOT IN ('.git', '__pycache__', 'venv', '.venv', 'node_modules')"
)


def escape_sql_like(value):
    return (
        value
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def descendant_like_patterns(path):
    bases = {
        str(path).rstrip("\\/"),
        os.path.normpath(path).rstrip("\\/"),
    }
    patterns = []
    for base in bases:
        if not base:
            continue
        for separator in ("\\", "/"):
            pattern = escape_sql_like(base + separator) + "%"
            if pattern not in patterns:
                patterns.append(pattern)
    return patterns


def descendant_like_sql(path, column="path"):
    patterns = descendant_like_patterns(path)
    if not patterns:
        return "1=0", []
    sql = " OR ".join(f"{column} LIKE ? ESCAPE '\\'" for _ in patterns)
    return sql, patterns


def case_insensitive_path_sql(column="path"):
    return f"{column} = ? COLLATE NOCASE"


def build_sort_order_clause(view_mode, sort_column, sort_desc):
    primary_dir = "DESC" if sort_desc else "ASC"
    age_dir = "ASC" if sort_desc else "DESC"

    if sort_column == 0:
        return f"lower(name) {primary_dir}, lower(path) ASC"
    if sort_column == 1:
        if view_mode == 'Tree':
            return f"is_folder {primary_dir}, lower(name) ASC, lower(path) ASC"
        return f"lower(parent_path) {primary_dir}, lower(name) ASC, lower(path) ASC"
    if sort_column == 2:
        return f"modified_time {primary_dir}, lower(path) ASC"
    if sort_column == 3:
        return f"modified_time {age_dir}, lower(path) ASC"
    if sort_column == 4:
        return f"size {primary_dir}, lower(path) ASC"
    if sort_column == 5:
        return f"modified_time {primary_dir}, is_folder DESC, lower(name) ASC"
    return f"lower(path) {primary_dir}"


def tree_sort_value(node, sort_column):
    if sort_column == 1:
        return 0 if node.get('is_dir') else 1
    if sort_column in (2, 3, 5):
        return node.get('last_modified', 0) or 0
    if sort_column == 4:
        return node.get('size', 0) or 0
    return (node.get('name', '') or '').lower()


def sort_tree_siblings(node, sort_column, sort_desc):
    children = node.get('children', [])
    for child in children:
        sort_tree_siblings(child, sort_column, sort_desc)

    children.sort(key=lambda child: (
        tree_sort_value(child, sort_column),
        (child.get('name', '') or '').lower(),
        (child.get('path', '') or '').lower(),
    ), reverse=sort_desc)


def prune_contained_paths(paths):
    unique_paths = list(dict.fromkeys(path for path in paths if path))
    sorted_paths = sorted(unique_paths, key=lambda value: len(os.path.normpath(value)))
    kept = []
    kept_keys = []

    for path in sorted_paths:
        normalized = os.path.normcase(os.path.normpath(path)).rstrip("\\/")
        is_contained = any(
            normalized.startswith(parent + "\\") or normalized.startswith(parent + "/")
            for parent in kept_keys
        )
        if is_contained:
            continue
        kept.append(path)
        kept_keys.append(normalized)

    return kept


def summarize_paths_batch(cursor, paths, cancel_check=None):
    def check_cancelled():
        if cancel_check:
            cancel_check()

    pruned_paths = prune_contained_paths(paths)
    if not pruned_paths:
        return [], 0, 0, 0

    rows_by_key = {}
    for start in range(0, len(pruned_paths), 900):
        check_cancelled()
        batch = pruned_paths[start:start + 900]
        placeholders = ",".join("?" * len(batch))
        cursor.execute(
            f"SELECT path, is_folder, size FROM file_index WHERE path COLLATE NOCASE IN ({placeholders})",
            batch,
        )
        for path, is_folder, size in cursor.fetchall():
            rows_by_key[os.path.normcase(os.path.normpath(path))] = (path, is_folder, size)

    folders = 0
    files = 0
    total_size = 0
    folder_paths = []

    for path in pruned_paths:
        check_cancelled()
        row = rows_by_key.get(os.path.normcase(os.path.normpath(path)))
        if not row:
            if os.path.isdir(path):
                folders += 1
                folder_paths.append(path)
            else:
                files += 1
            continue

        _, is_folder, size = row
        if is_folder:
            folder_paths.append(path)
        else:
            files += 1
            total_size += size or 0

    remaining_folder_paths = []
    for start in range(0, len(folder_paths), 900):
        check_cancelled()
        batch = folder_paths[start:start + 900]
        placeholders = ",".join("?" * len(batch))
        cursor.execute(
            f"""
            SELECT path, total_size, file_count, folder_count
            FROM folder_summary
            WHERE path COLLATE NOCASE IN ({placeholders})
            """,
            batch,
        )
        summary_by_key = {
            os.path.normcase(os.path.normpath(path)): (total_size, file_count, folder_count)
            for path, total_size, file_count, folder_count in cursor.fetchall()
        }
        for folder_path in batch:
            summary = summary_by_key.get(os.path.normcase(os.path.normpath(folder_path)))
            if summary:
                summary_size, summary_files, summary_folders = summary
                folders += summary_folders or 0
                files += summary_files or 0
                total_size += summary_size or 0
            else:
                remaining_folder_paths.append(folder_path)

    for start in range(0, len(remaining_folder_paths), 120):
        check_cancelled()
        batch = remaining_folder_paths[start:start + 120]
        clauses = []
        params = []
        for folder_path in batch:
            descendant_sql, descendant_params = descendant_like_sql(folder_path)
            clauses.append(f"({case_insensitive_path_sql()} OR ({descendant_sql}))")
            params.extend([folder_path, *descendant_params])
        cursor.execute(
            "SELECT "
            "COALESCE(SUM(CASE WHEN is_folder = 1 THEN 1 ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_folder = 0 THEN 1 ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_folder = 0 THEN size ELSE 0 END), 0) "
            "FROM file_index WHERE " + " OR ".join(clauses),
            params,
        )
        batch_folders, batch_files, batch_size = cursor.fetchone()
        folders += batch_folders or 0
        files += batch_files or 0
        total_size += batch_size or 0

    return pruned_paths, folders, files, total_size


class DeleteThread(QThread):
    delete_progress = pyqtSignal(int, int, str)
    delete_finished = pyqtSignal(int, list, bool, list)

    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = list(paths)
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        from src.file_index_tool import FileIndexTool

        deleted_count = 0
        errors = []
        deleted_paths = []
        total = len(self.paths)
        tool = FileIndexTool()

        try:
            for index, path in enumerate(self.paths):
                if self.is_cancelled:
                    break

                self.delete_progress.emit(index, total, path)
                try:
                    send2trash.send2trash(path)
                    tool.conn.execute(
                        f"DELETE FROM file_index WHERE {case_insensitive_path_sql()}",
                        (path,),
                    )
                    descendant_sql, descendant_params = descendant_like_sql(path)
                    tool.conn.execute(
                        f"DELETE FROM file_index WHERE {descendant_sql}",
                        descendant_params,
                    )
                    tool.conn.commit()
                    deleted_count += 1
                    deleted_paths.append(path)
                except Exception as exc:
                    errors.append(f"{path}: {exc}")

                self.delete_progress.emit(index + 1, total, path)
        finally:
            tool.close()

        self.delete_finished.emit(deleted_count, errors, self.is_cancelled, deleted_paths)


class DeleteProgressDialog(QDialog):
    cancel_requested = pyqtSignal()

    def __init__(self, total, parent=None):
        super().__init__(parent)
        self._allow_close = False
        self.setWindowTitle("Deleting items")
        self.setModal(True)
        self.setFixedSize(520, 190)
        self.setObjectName("deleteProgressDialog")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        self.title_label = QLabel("Moving items to the Recycle Bin")
        self.title_label.setObjectName("deleteProgressTitle")
        layout.addWidget(self.title_label)

        self.count_label = QLabel(f"Deleting 0 of {total}")
        self.count_label.setObjectName("deleteProgressCount")
        layout.addWidget(self.count_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, total)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setObjectName("deleteProgressBar")
        layout.addWidget(self.progress)

        self.path_label = QLabel("Preparing deletion...")
        self.path_label.setObjectName("deleteProgressPath")
        self.path_label.setWordWrap(True)
        self.path_label.setMinimumHeight(36)
        layout.addWidget(self.path_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.cancel_button = QPushButton("Cancel after current item")
        self.cancel_button.setObjectName("deleteProgressCancel")
        self.cancel_button.clicked.connect(self._cancel)
        btn_row.addWidget(self.cancel_button)
        layout.addLayout(btn_row)

    def _cancel(self):
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Stopping...")
        self.count_label.setText(self.count_label.text() + "  - stopping")
        self.cancel_requested.emit()

    def update_progress(self, done, total, path):
        self.progress.setMaximum(total)
        self.progress.setValue(done)
        self.count_label.setText(f"Deleting {done} of {total}")
        self.path_label.setText(path)

    def closeEvent(self, event):
        if self._allow_close:
            event.accept()
            return

        if self.cancel_button.isEnabled():
            self._cancel()
        event.ignore()


class LoadingDialog(QDialog):
    def __init__(self, title="Loading", detail="Working...", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedSize(360, 122)
        self.setObjectName("loadingDialog")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)

        title = QLabel(title)
        title.setObjectName("loadingTitle")
        layout.addWidget(title)

        detail_label = QLabel(detail)
        detail_label.setObjectName("loadingDetail")
        layout.addWidget(detail_label)

        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        bar.setObjectName("loadingBar")
        layout.addWidget(bar)


class DeletePreviewThread(QThread):
    preview_ready = pyqtSignal(dict)
    preview_failed = pyqtSignal(str)

    class _Cancelled(Exception):
        pass

    def __init__(self, paths=None, bulk_scope=None, excluded_paths=None, parent=None):
        super().__init__(parent)
        self.paths = list(paths or [])
        self.bulk_scope = dict(bulk_scope or {}) if bulk_scope else None
        self.excluded_paths = dict(excluded_paths or {})
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def _raise_if_cancelled(self):
        if self.is_cancelled:
            raise DeletePreviewThread._Cancelled()

    def _path_key(self, path):
        return os.path.normcase(os.path.normpath(path))

    def _prune_paths(self, paths):
        pruned_paths = []
        selected_roots = []
        for path in sorted(paths, key=lambda value: (len(os.path.normpath(value)), value.lower())):
            normalized = os.path.normcase(os.path.normpath(path))
            if any(
                normalized == root or normalized.startswith(root + os.sep)
                for root in selected_roots
            ):
                continue
            selected_roots.append(normalized)
            pruned_paths.append(path)
        return pruned_paths

    def _has_excluded_ancestor(self, path, include_self=True):
        normalized = self._path_key(path)
        for excluded_key in self.excluded_paths:
            if normalized == excluded_key:
                return include_self
            if normalized.startswith(excluded_key + os.sep):
                return True
        return False

    def _has_excluded_descendant(self, path):
        normalized = self._path_key(path)
        return any(
            excluded_key.startswith(normalized + os.sep)
            for excluded_key in self.excluded_paths
        )

    def _expand_paths_around_exclusions(self, cursor, selected_roots):
        if not self.excluded_paths:
            return selected_roots

        expanded = []
        for root_path in selected_roots:
            if self.is_cancelled:
                return []
            root_key = self._path_key(root_path)
            if not any(
                excluded_key == root_key or excluded_key.startswith(root_key + os.sep)
                for excluded_key in self.excluded_paths
            ):
                expanded.append(root_path)
                continue

            descendant_sql, descendant_params = descendant_like_sql(root_path)
            cursor.execute(
                f"""
                SELECT path, is_folder
                FROM file_index
                WHERE {case_insensitive_path_sql()} OR ({descendant_sql})
                ORDER BY length(path) ASC, lower(path) ASC
                """,
                (root_path, *descendant_params),
            )
            for path, is_folder in cursor.fetchall():
                if self.is_cancelled:
                    return []
                if self._has_excluded_ancestor(path):
                    continue
                if is_folder and self._has_excluded_descendant(path):
                    continue
                expanded.append(path)

        return self._prune_paths(expanded)

    def _count_selected_path_contents(self, cursor, path):
        cursor.execute(
            f"SELECT is_folder FROM file_index WHERE {case_insensitive_path_sql()}",
            (path,),
        )
        row = cursor.fetchone()
        if not row:
            return (1, 0) if os.path.isdir(path) else (0, 1)

        is_folder = row[0]
        if not is_folder:
            return 0, 1

        descendant_sql, descendant_params = descendant_like_sql(path)
        cursor.execute(
            f"""
            SELECT
                COALESCE(SUM(CASE WHEN is_folder = 1 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN is_folder = 0 THEN 1 ELSE 0 END), 0)
            FROM file_index
            WHERE {case_insensitive_path_sql()} OR ({descendant_sql})
            """,
            (path, *descendant_params),
        )
        folders, files = cursor.fetchone()
        return folders or 0, files or 0

    def _path_subtree_size(self, cursor, path):
        cursor.execute(
            f"SELECT is_folder, size FROM file_index WHERE {case_insensitive_path_sql()}",
            (path,),
        )
        row = cursor.fetchone()
        if not row:
            return 0

        is_folder, size = row
        if not is_folder:
            return size or 0

        descendant_sql, descendant_params = descendant_like_sql(path)
        cursor.execute(
            f"SELECT COALESCE(SUM(size), 0) FROM file_index WHERE is_folder = 0 AND ({descendant_sql})",
            descendant_params,
        )
        return cursor.fetchone()[0] or 0

    def _summarize_paths(self, cursor, paths):
        return summarize_paths_batch(
            cursor,
            paths,
            cancel_check=self._raise_if_cancelled,
        )

    def _build_bulk_paths(self, cursor):
        where_sql = self.bulk_scope.get('where_sql', '')
        params = self.bulk_scope.get('params', [])
        folder_delete_mode = self.bulk_scope.get('folder_delete_mode', 'empty_only')

        cursor.execute(
            "SELECT path, is_folder FROM file_index" + where_sql,
            params,
        )
        rows = cursor.fetchall()

        paths = []
        for path, is_folder in rows:
            if self.is_cancelled:
                return []
            if not is_folder:
                paths.append(path)
            elif folder_delete_mode == 'all':
                paths.append(path)
            elif folder_delete_mode == 'empty_only':
                cursor.execute(
                    "SELECT 1 FROM file_index WHERE parent_path = ? COLLATE NOCASE LIMIT 1",
                    (path,),
                )
                if cursor.fetchone() is None:
                    paths.append(path)
        return self._prune_paths(paths)

    def run(self):
        from src.file_index_tool import FileIndexTool

        tool = FileIndexTool()
        try:
            cursor = tool.conn.cursor()
            if self.bulk_scope:
                where_sql = self.bulk_scope.get('where_sql', '')
                params = self.bulk_scope.get('params', [])
                cursor.execute(
                    (
                        "SELECT COUNT(*), "
                        "SUM(CASE WHEN is_folder = 1 THEN 1 ELSE 0 END), "
                        "SUM(CASE WHEN is_folder = 0 THEN 1 ELSE 0 END) "
                        "FROM file_index"
                    ) + where_sql,
                    params,
                )
                total, folders, files = cursor.fetchone()
                paths = self._build_bulk_paths(cursor)
            else:
                total = len(self.paths)
                paths = self._expand_paths_around_exclusions(cursor, self._prune_paths(self.paths))
                folders = 0
                files = 0

            if self.is_cancelled:
                return

            if not self.bulk_scope:
                paths, folders, files, total_size = self._summarize_paths(cursor, paths)
            else:
                _, folders, files, total_size = self._summarize_paths(cursor, paths)

            self.preview_ready.emit({
                'paths': paths,
                'total': total or len(paths),
                'delete_operations': len(paths),
                'folders': folders or 0,
                'files': files or 0,
                'size': total_size or 0,
            })
        except DeletePreviewThread._Cancelled:
            return
        except Exception as exc:
            self.preview_failed.emit(str(exc))
        finally:
            tool.close()


class DeletePreviewDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Delete preview")
        self.setModal(True)
        self.setFixedSize(520, 230)
        self.setObjectName("deleteProgressDialog")
        self.preview_paths = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)

        self.title_label = QLabel("Preparing delete preview")
        self.title_label.setObjectName("deleteProgressTitle")
        layout.addWidget(self.title_label)

        self.detail_label = QLabel("Calculating selected items and size...")
        self.detail_label.setObjectName("deleteProgressCount")
        layout.addWidget(self.detail_label)

        self.path_label = QLabel("Please wait while we gather the delete summary.")
        self.path_label.setObjectName("deleteProgressPath")
        self.path_label.setWordWrap(True)
        self.path_label.setMinimumHeight(42)
        layout.addWidget(self.path_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setObjectName("deleteProgressBar")
        layout.addWidget(self.progress)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("deleteProgressCancel")
        self.cancel_button.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_button)
        self.delete_button = QPushButton("Delete Selected")
        self.delete_button.setObjectName("primaryBtn")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self.accept)
        btn_row.addWidget(self.delete_button)
        layout.addLayout(btn_row)

    def apply_preview(self, payload):
        self.preview_paths = payload.get('paths', [])
        total = payload.get('total', len(self.preview_paths))
        delete_operations = payload.get('delete_operations', len(self.preview_paths))
        folders = payload.get('folders', 0)
        files = payload.get('files', 0)
        size = payload.get('size')
        size_text = 'Calculating...' if size is None else ('0 B' if size == 0 else format_size(size))
        self.detail_label.setText(
            f"Delete operations: {delete_operations}    Total size: {size_text}"
        )
        if folders > 0:
            path_summary = (
                f"Selected folders will delete {folders} folders and {files} files inside them.\n"
                f"Matched items: {total}\n"
                f"Items sent to Recycle Bin: {delete_operations}"
            )
        else:
            path_summary = (
                f"Matched items: {total}\n"
                f"Items sent to Recycle Bin: {delete_operations}"
            )
        self.path_label.setText(
            f"{path_summary}\n"
            f"Folders: {folders}\n"
            f"Files: {files}"
        )
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.delete_button.setEnabled(size is not None)

    def show_error(self, message):
        self.detail_label.setText("Could not calculate delete preview.")
        self.path_label.setText(message)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.delete_button.setEnabled(False)


class PageLoadThread(QThread):
    page_ready = pyqtSignal(int, dict)
    page_failed = pyqtSignal(int, str)

    def __init__(self, request_id, options, parent=None):
        super().__init__(parent)
        self.request_id = request_id
        self.options = options

    def _relative_display_location(self, folder_path):
        if not folder_path:
            return ""

        root = self.options.get('scan_root') or ""
        normalized_folder = os.path.normpath(folder_path)

        if root:
            try:
                relative = os.path.relpath(normalized_folder, root)
                if relative == ".":
                    return "."
                if not relative.startswith(".."):
                    return relative
            except ValueError:
                pass

        parts = normalized_folder.replace("/", "\\").split("\\")
        return "\\".join(parts[-3:]) if len(parts) > 3 else normalized_folder

    def run(self):
        try:
            self.page_ready.emit(self.request_id, self._load())
        except Exception as exc:
            self.page_failed.emit(self.request_id, str(exc))

    def _load(self):
        from src.file_index_tool import FileIndexTool

        limit = self.options['limit']
        offset = self.options['offset']
        paginated = self.options.get('paginated', True)
        view_mode = self.options['view_mode']
        status_filter = self.options['status_filter']
        age_cutoff = self.options['age_cutoff']
        videos_only = self.options['videos_only']
        lazy_show_all_tree = self.options.get('lazy_show_all_tree', False)
        filtered_expanded_tree = self.options.get('filtered_expanded_tree', False)

        if lazy_show_all_tree:
            return self._load_lazy_show_all_tree()

        query = "SELECT path, name, is_folder, size, modified_time, parent_path FROM file_index"
        count_query = "SELECT COUNT(*) FROM file_index"
        where_clauses = []
        params = []

        if status_filter == 'Inactive':
            if view_mode == 'Tree':
                where_clauses.append("is_folder = 0")
            if age_cutoff is not None:
                where_clauses.append("modified_time <= ?")
                params.append(age_cutoff)
            else:
                where_clauses.append("1=1")
        elif status_filter == 'Empty':
            if age_cutoff is not None:
                where_clauses.append("modified_time <= ?")
                params.append(age_cutoff)
            where_clauses.append(EMPTY_FOLDER_SQL)
        elif status_filter == 'Active':
            if age_cutoff is not None:
                where_clauses.append("modified_time > ?")
                params.append(age_cutoff)
            else:
                where_clauses.append("1=0")
        elif age_cutoff is not None and not videos_only:
            where_clauses.append("modified_time <= ?")
            params.append(age_cutoff)

        if videos_only:
            placeholders = ','.join('?' * len(VIDEO_EXTENSIONS))
            where_clauses.append("is_folder = 0")
            where_clauses.append(f"extension IN ({placeholders})")
            params.extend(VIDEO_EXTENSIONS)
            if age_cutoff is not None:
                where_clauses.append("modified_time <= ?")
                params.append(age_cutoff)

        if view_mode == 'Files':
            where_clauses.append("is_folder = 0")
        elif view_mode == 'Folders':
            where_clauses.append("is_folder = 1")

        scan_root = self.options.get('scan_root')
        if scan_root:
            where_clauses.append("path != ? COLLATE NOCASE")
            params.append(scan_root)

        if where_clauses:
            where_sql = " WHERE " + " AND ".join(where_clauses)
            query += where_sql
            count_query += where_sql

        tool = FileIndexTool()
        try:
            cursor = tool.conn.cursor()
            cursor.execute(count_query, params)
            total_matches = cursor.fetchone()[0]

            order_by_sql = build_sort_order_clause(
                view_mode,
                self.options['sort_column'],
                self.options['sort_desc'],
            )
            query += f" ORDER BY {order_by_sql}"
            if paginated:
                query += f" LIMIT {limit} OFFSET {offset}"
            cursor.execute(query, params)
            rows = cursor.fetchall()
            original_fetched_order = {
                os.path.normcase(os.path.normpath(row[0])): index
                for index, row in enumerate(rows)
            }

            fetched_paths = {row[0] for row in rows}
            missing_parents = set()
            for row in rows:
                parent_path = row[5]
                while parent_path and parent_path not in fetched_paths and parent_path not in missing_parents:
                    missing_parents.add(parent_path)
                    parent_path = os.path.dirname(parent_path) if '\\' in parent_path or '/' in parent_path else None

            original_fetched_paths = {row[0] for row in rows}
            original_fetched_path_keys = {
                os.path.normcase(os.path.normpath(path))
                for path in original_fetched_paths
            }

            if missing_parents and view_mode == 'Tree':
                parents_list = list(missing_parents)
                for index in range(0, len(parents_list), 900):
                    batch = parents_list[index:index + 900]
                    placeholders = ','.join('?' * len(batch))
                    cursor.execute(
                        f"SELECT path, name, is_folder, size, modified_time, parent_path FROM file_index WHERE path COLLATE NOCASE IN ({placeholders})",
                        batch,
                    )
                    rows.extend(cursor.fetchall())
        finally:
            tool.close()

        root_node = {'name': 'root', 'is_dir': True, 'path': 'C:/', 'status': 'Active', 'children': []}
        nodes_by_path = {}

        for path, name, is_folder, size, modified_time, parent_path in rows:
            is_stale = False
            if age_cutoff and modified_time <= age_cutoff:
                is_stale = True
            elif not age_cutoff:
                is_stale = True

            status = 'Inactive' if is_stale else 'Active'
            path_key = os.path.normcase(os.path.normpath(path))
            if status_filter == 'Empty' and is_folder and path_key in original_fetched_path_keys:
                status = 'Empty'

            is_context = (path_key not in original_fetched_path_keys) if view_mode == 'Tree' else False
            actual_parent = parent_path if view_mode == 'Tree' else None
            display_location = self._relative_display_location(parent_path) if view_mode != 'Tree' else None

            nodes_by_path[path] = {
                'name': name,
                'path': path,
                'is_dir': bool(is_folder),
                'size': size or 0,
                'last_modified': modified_time,
                'status': status,
                'children': [],
                '_children_loaded': bool(filtered_expanded_tree),
                '_page_order': original_fetched_order.get(path_key),
                '_parent_path': actual_parent,
                '_is_context_fetched': is_context,
                '_is_page_result': path_key in original_fetched_path_keys,
                'location': parent_path if view_mode != 'Tree' else None,
                'display_location': display_location,
            }

        for path, node in nodes_by_path.items():
            parent_path = node.pop('_parent_path', None)
            parent_node = nodes_by_path.get(parent_path)
            if parent_node:
                parent_node['children'].append(node)
            else:
                root_node['children'].append(node)

        def sort_tree_for_page(node):
            children = node.get('children', [])
            if not children:
                return node.get('_page_order', float('inf'))

            child_orders = [sort_tree_for_page(child) for child in children]
            node_order = node.get('_page_order')
            if node_order is None:
                node_order = min(child_orders) if child_orders else float('inf')
            node['_page_order'] = node_order

            children.sort(key=lambda child: (
                child.get('_page_order', float('inf')),
                1 if child.get('_is_context_fetched') else 0,
                str(child.get('path', '')).lower(),
            ))
            return node_order

        if view_mode == 'Tree':
            self._hide_scan_root_context(root_node)
            sort_tree_siblings(
                root_node,
                self.options['sort_column'],
                self.options['sort_desc'],
            )

        return {
            'root_node': root_node,
            'view_mode': view_mode,
            'rows_count': len(rows),
            'total_matches': total_matches,
            'limit': limit,
            'offset': offset,
            'page': self.options['page'],
            'paginated': paginated,
            'lazy_show_all_tree': False,
            'filtered_expanded_tree': filtered_expanded_tree,
        }

    def _hide_scan_root_context(self, root_node):
        scan_root = self.options.get('scan_root')
        if not scan_root:
            return

        scan_root_key = os.path.normcase(os.path.normpath(scan_root))
        children = root_node.get('children', [])
        replacement_children = []

        for child in children:
            child_path = child.get('path')
            if child_path and os.path.normcase(os.path.normpath(child_path)) == scan_root_key:
                replacement_children.extend(child.get('children', []))
            else:
                replacement_children.append(child)

        root_node['children'] = replacement_children

    def _load_lazy_show_all_tree(self):
        from src.file_index_tool import FileIndexTool

        root_path = self.options.get('scan_root') or ""
        root_path = os.path.normpath(root_path) if root_path else root_path
        cache = self.options.get('folder_cache')
        cached_children = None
        if cache and cache.child_count(root_path) > 0:
            cached_children = cache.children_for(
                root_path,
                self.options['sort_column'],
                self.options['sort_desc'],
            )
        if cached_children is not None:
            total_matches = max(cache.item_count() - 1, 0)
            root_node = {
                'name': 'root',
                'is_dir': True,
                'path': root_path,
                'status': 'Active',
                'children': cached_children,
                '_children_loaded': True,
            }
            return {
                'root_node': root_node,
                'view_mode': 'Tree',
                'rows_count': len(cached_children),
                'total_matches': total_matches,
                'limit': max(total_matches, len(cached_children), 1),
                'offset': 0,
                'page': 0,
                'paginated': False,
                'lazy_show_all_tree': True,
            }

        tool = FileIndexTool()
        try:
            cursor = tool.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM file_index")
            total_rows = cursor.fetchone()[0] or 0

            cursor.execute(
                """
                SELECT path
                FROM file_index
                WHERE path = ? COLLATE NOCASE OR root = ? COLLATE NOCASE
                ORDER BY CASE WHEN path = ? COLLATE NOCASE THEN 0 ELSE 1 END,
                         length(path) ASC
                LIMIT 1
                """,
                (root_path, root_path, root_path),
            )
            stored_root_row = cursor.fetchone()
            stored_root_path = stored_root_row[0] if stored_root_row else root_path

            cursor.execute(
                "SELECT 1 FROM file_index WHERE path = ? COLLATE NOCASE LIMIT 1",
                (stored_root_path,),
            )
            total_matches = max(total_rows - (1 if cursor.fetchone() else 0), 0)

            cursor.execute(
                "SELECT f.path, f.name, f.is_folder, f.size, f.modified_time, f.parent_path, "
                "(SELECT 1 FROM file_index child WHERE child.parent_path = f.path COLLATE NOCASE LIMIT 1) "
                "FROM file_index f "
                "WHERE f.parent_path = ? COLLATE NOCASE "
                f"ORDER BY {build_sort_order_clause('Tree', self.options['sort_column'], self.options['sort_desc'])}",
                (stored_root_path,),
            )
            rows = cursor.fetchall()
        finally:
            tool.close()

        root_node = {
            'name': 'root',
            'is_dir': True,
            'path': stored_root_path,
            'status': 'Active',
            'children': [],
            '_children_loaded': True,
        }

        for path, name, is_folder, size, modified_time, parent_path, has_child in rows:
            root_node['children'].append({
                'name': name,
                'path': path,
                'is_dir': bool(is_folder),
                'size': size or 0,
                'last_modified': modified_time,
                'status': 'Active',
                'children': [],
                '_children_loaded': not (is_folder and has_child),
                '_is_page_result': True,
                'location': parent_path,
                'display_location': parent_path,
            })

        self._hide_scan_root_context(root_node)

        return {
            'root_node': root_node,
            'view_mode': 'Tree',
            'rows_count': len(rows),
            'total_matches': total_matches,
            'limit': max(total_matches, len(rows), 1),
            'offset': 0,
            'page': 0,
            'paginated': False,
            'lazy_show_all_tree': True,
        }


class LazyChildrenLoadThread(QThread):
    children_ready = pyqtSignal(int, str, list)
    children_failed = pyqtSignal(int, str, str)

    def __init__(self, request_id, folder_path, sort_column=0, sort_desc=False, options=None, cache=None, parent=None):
        super().__init__(parent)
        self.request_id = request_id
        self.folder_path = folder_path
        self.sort_column = sort_column
        self.sort_desc = sort_desc
        self.options = dict(options or {})
        self.apply_filter_options = options is not None
        self.cache = cache

    def _child_paths_with_children(self, cursor, child_paths):
        folders_with_children = set()
        for start in range(0, len(child_paths), 900):
            batch = child_paths[start:start + 900]
            if not batch:
                continue
            placeholders = ",".join("?" * len(batch))
            cursor.execute(
                f"""
                SELECT DISTINCT parent_path
                FROM file_index
                WHERE parent_path COLLATE NOCASE IN ({placeholders})
                """,
                batch,
            )
            folders_with_children.update(
                os.path.normcase(os.path.normpath(path))
                for (path,) in cursor.fetchall()
                if path
            )
        return folders_with_children

    def _status_for_child(self, is_folder, modified_time, has_child):
        if not self.apply_filter_options:
            return 'Active'

        options = self.options
        age_cutoff = options.get('age_cutoff')

        is_stale = (modified_time <= age_cutoff) if age_cutoff else True
        status = 'Inactive' if is_stale else 'Active'
        if is_folder and not has_child:
            status = 'Empty'
        return status

    def _child_matches_options(self, name, is_folder, status):
        options = self.options
        status_filter = options.get('status_filter')
        videos_only = options.get('videos_only', False)

        if videos_only and not is_folder:
            ext = os.path.splitext(name)[1].lower()
            if ext not in VIDEO_EXTENSIONS:
                return False
        if status_filter == 'Inactive' and status == 'Active':
            return False
        if status_filter == 'Empty' and status != 'Empty':
            return False
        if status_filter == 'Active' and status != 'Active':
            return False
        return True

    def run(self):
        if self.cache and self.cache.has_children_for(self.folder_path):
            children = self.cache.children_for(self.folder_path, self.sort_column, self.sort_desc)
            if self.apply_filter_options:
                filtered = []
                for child in children:
                    status = self._status_for_child(
                        child.get('is_dir', False),
                        child.get('last_modified', 0),
                        not child.get('_children_loaded', True),
                    )
                    if self._child_matches_options(child.get('name', ''), child.get('is_dir', False), status):
                        child['status'] = status
                        filtered.append(child)
                children = filtered
            self.children_ready.emit(self.request_id, self.folder_path, children)
            return

        from src.file_index_tool import FileIndexTool

        tool = FileIndexTool()
        try:
            cursor = tool.conn.cursor()
            cursor.execute(
                "SELECT path, name, is_folder, size, modified_time, parent_path "
                "FROM file_index "
                "WHERE parent_path = ? COLLATE NOCASE "
                f"ORDER BY {build_sort_order_clause('Tree', self.sort_column, self.sort_desc)}",
                (self.folder_path,),
            )
            rows = cursor.fetchall()
            child_folder_paths = [path for path, _name, is_folder, _size, _modified_time, _parent_path in rows if is_folder]
            folders_with_children = self._child_paths_with_children(cursor, child_folder_paths)
        except Exception as exc:
            self.children_failed.emit(self.request_id, self.folder_path, str(exc))
            return
        finally:
            tool.close()

        children = []
        for path, name, is_folder, size, modified_time, parent_path in rows:
            is_folder = bool(is_folder)
            has_child = os.path.normcase(os.path.normpath(path)) in folders_with_children
            status = self._status_for_child(is_folder, modified_time, has_child)
            if not self._child_matches_options(name, is_folder, status):
                continue
            children.append({
                'name': name,
                'path': path,
                'is_dir': is_folder,
                'size': size or 0,
                'last_modified': modified_time,
                'status': status,
                'children': [],
                '_children_loaded': not (is_folder and has_child),
                '_is_page_result': True,
                'location': parent_path,
                'display_location': parent_path,
            })

        self.children_ready.emit(self.request_id, self.folder_path, children)


class BulkPageSelectThread(QThread):
    bulk_ready = pyqtSignal(int, dict)
    bulk_failed = pyqtSignal(int, str)

    def __init__(self, request_id, options, where_sql, params, parent=None):
        super().__init__(parent)
        self.request_id = request_id
        self.options = options
        self.where_sql = where_sql or ""
        self.params = list(params or [])
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def _raise_if_cancelled(self):
        if self.is_cancelled:
            raise RuntimeError("cancelled")

    def run(self):
        try:
            result = self._load()
            if result is not None:
                self.bulk_ready.emit(self.request_id, result)
        except Exception as exc:
            if str(exc) != "cancelled":
                self.bulk_failed.emit(self.request_id, str(exc))

    def _load(self):
        from src.file_index_tool import FileIndexTool

        self._raise_if_cancelled()
        limit = self.options['limit']
        offset = self.options['offset']
        view_mode = self.options['view_mode']
        sort_column = self.options['sort_column']
        sort_desc = self.options['sort_desc']

        query = "SELECT path FROM file_index"
        count_query = (
            "SELECT COUNT(*), "
            "SUM(CASE WHEN is_folder = 1 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN is_folder = 0 THEN 1 ELSE 0 END), "
            "COALESCE(SUM(size), 0) "
            "FROM file_index"
        )
        if self.where_sql:
            query += self.where_sql
            count_query += self.where_sql

        query += f" ORDER BY {build_sort_order_clause(view_mode, sort_column, sort_desc)} LIMIT ? OFFSET ?"

        tool = FileIndexTool()
        try:
            cursor = tool.conn.cursor()
            cursor.execute(count_query, self.params)
            total_matches, folders, files, total_size = cursor.fetchone()
            total_matches = total_matches or 0
            folders = folders or 0
            files = files or 0
            total_size = total_size or 0
            effective_total = total_matches
            effective_folders = folders
            effective_files = files
            effective_size = total_size
            if self.options.get('folder_delete_mode') == 'empty_only' and folders:
                folder_clause = (
                    "is_folder = 1 AND NOT EXISTS "
                    "(SELECT 1 FROM file_index child WHERE child.parent_path = file_index.path COLLATE NOCASE)"
                )
                effective_where = (
                    self.where_sql + " AND " + folder_clause
                    if self.where_sql else " WHERE " + folder_clause
                )
                self._raise_if_cancelled()
                cursor.execute("SELECT COUNT(*) FROM file_index" + effective_where, self.params)
                deletable_folders = cursor.fetchone()[0] or 0
                effective_total = files + deletable_folders
                effective_folders = deletable_folders

            self._raise_if_cancelled()
            cursor.execute(query, [*self.params, limit, offset])
            paths = [row[0] for row in cursor.fetchall()]
        finally:
            tool.close()

        return {
            'paths': paths,
            'total_matches': total_matches,
            'folders': folders,
            'files': files,
            'total_size': total_size,
            'effective_total': effective_total,
            'effective_folders': effective_folders,
            'effective_files': effective_files,
            'effective_size': effective_size,
            'current_page_count': len(paths),
            'where_sql': self.where_sql,
            'params': self.params,
        }


# ---------------------------------------------------------------------------
# Delegates
# ---------------------------------------------------------------------------

class TotalsThread(QThread):
    totals_ready = pyqtSignal(int, dict)
    totals_failed = pyqtSignal(int, str)

    class _Cancelled(Exception):
        pass

    def __init__(self, request_id, options, parent=None):
        super().__init__(parent)
        self.request_id = request_id
        self.options = options
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def _raise_if_cancelled(self):
        if self.is_cancelled:
            raise TotalsThread._Cancelled()

    def run(self):
        try:
            result = self._compute()
            if result is not None:
                self.totals_ready.emit(self.request_id, result)
        except Exception as exc:
            self.totals_failed.emit(self.request_id, str(exc))

    def _sum_paths_total_size(self, cursor, paths):
        paths = self._prune_contained_paths(paths)
        total_size = 0
        for path in paths:
            self._raise_if_cancelled()
            cursor.execute(
                f"SELECT is_folder, size FROM file_index WHERE {case_insensitive_path_sql()}",
                (path,),
            )
            row = cursor.fetchone()
            if not row:
                continue

            is_folder, size = row
            if not is_folder:
                total_size += size or 0
                continue

            self._raise_if_cancelled()
            descendant_sql, descendant_params = descendant_like_sql(path)
            cursor.execute(
                f"SELECT COALESCE(SUM(size), 0) FROM file_index WHERE is_folder = 0 AND ({descendant_sql})",
                descendant_params,
            )
            total_size += cursor.fetchone()[0] or 0
        return total_size

    def _prune_contained_paths(self, paths):
        unique_paths = list(dict.fromkeys(path for path in paths if path))
        sorted_paths = sorted(unique_paths, key=lambda value: len(os.path.normpath(value)))
        kept = []
        kept_keys = []

        for path in sorted_paths:
            normalized = os.path.normcase(os.path.normpath(path)).rstrip("\\/")
            is_contained = any(
                normalized.startswith(parent + "\\") or normalized.startswith(parent + "/")
                for parent in kept_keys
            )
            if is_contained:
                continue
            kept.append(path)
            kept_keys.append(normalized)

        return kept

    def _sum_file_paths_size(self, cursor, paths):
        unique_paths = list(dict.fromkeys(paths))
        if not unique_paths:
            return 0

        total_size = 0
        for start in range(0, len(unique_paths), 900):
            self._raise_if_cancelled()
            batch = unique_paths[start:start + 900]
            placeholders = ",".join("?" * len(batch))
            cursor.execute(
                f"SELECT COALESCE(SUM(size), 0) FROM file_index "
                f"WHERE is_folder = 0 AND path IN ({placeholders})",
                batch,
            )
            total_size += cursor.fetchone()[0] or 0
        return total_size

    def _browse_folder_total_size(self, cursor, root_path):
        if not root_path:
            return 0

        self._raise_if_cancelled()
        normalized = os.path.normpath(root_path).rstrip("\\/")
        cursor.execute(
            "SELECT total_size, file_count FROM folder_summary WHERE path = ? COLLATE NOCASE",
            (normalized,),
        )
        summary = cursor.fetchone()
        if summary:
            total, file_count = summary
            return total or 0, file_count or 0

        cursor.execute(
            "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM file_index "
            "WHERE is_folder = 0 AND root = ? COLLATE NOCASE",
            (normalized,),
        )
        file_count, total = cursor.fetchone()
        file_count = file_count or 0
        total = total or 0
        if total:
            return total, file_count

        descendant_sql, descendant_params = descendant_like_sql(normalized)
        cursor.execute(
            f"SELECT COUNT(*), COALESCE(SUM(size), 0) FROM file_index "
            f"WHERE is_folder = 0 AND ({case_insensitive_path_sql()} OR {descendant_sql})",
            (normalized, *descendant_params),
        )
        file_count, total = cursor.fetchone()
        file_count = file_count or 0
        total = total or 0
        if total or file_count:
            return total, file_count

        cursor.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM file_index WHERE is_folder = 0")
        file_count, total = cursor.fetchone()
        return total or 0, file_count or 0

    def _compute(self):
        from src.file_index_tool import FileIndexTool

        tool = FileIndexTool()
        cursor = tool.conn.cursor()
        try:
            self._raise_if_cancelled()
            age_cutoff = self.options['age_cutoff']

            if age_cutoff is not None:
                cursor.execute("SELECT COUNT(*) FROM file_index WHERE is_folder = 0 AND modified_time <= ?", (age_cutoff,))
            else:
                cursor.execute("SELECT COUNT(*) FROM file_index WHERE is_folder = 0")
            inactive_files = cursor.fetchone()[0] or 0

            self._raise_if_cancelled()
            if age_cutoff is not None:
                cursor.execute("SELECT COUNT(*) FROM file_index WHERE is_folder = 1 AND modified_time <= ?", (age_cutoff,))
            else:
                cursor.execute("SELECT COUNT(*) FROM file_index WHERE is_folder = 1")
            inactive_folders = cursor.fetchone()[0] or 0

            self._raise_if_cancelled()
            if age_cutoff is not None:
                cursor.execute(
                    f"SELECT COUNT(*) FROM file_index WHERE {EMPTY_FOLDER_SQL} AND modified_time <= ?",
                    (age_cutoff,),
                )
            else:
                cursor.execute(f"SELECT COUNT(*) FROM file_index WHERE {EMPTY_FOLDER_SQL}")
            empty_n = cursor.fetchone()[0] or 0

            if self.options['is_scanning']:
                folder_total = None
                folder_file_count = None
            elif self.options['cached_folder_total'] is not None:
                folder_total = self.options['cached_folder_total']
                folder_file_count = None
            else:
                self._raise_if_cancelled()
                folder_total, folder_file_count = self._browse_folder_total_size(cursor, self.options['root_path'])

        except TotalsThread._Cancelled:
            return None
        finally:
            tool.close()

        return {
            'empty_n': empty_n,
            'inactive_folders': inactive_folders,
            'inactive_files': inactive_files,
            'folder_total': folder_total,
            'folder_file_count': folder_file_count,
        }


class PathSizeThread(QThread):
    total_ready = pyqtSignal(int, str, object)
    total_failed = pyqtSignal(int, str, str)

    class _Cancelled(Exception):
        pass

    def __init__(self, request_id, kind, paths, parent=None):
        super().__init__(parent)
        self.request_id = request_id
        self.kind = kind
        self.paths = list(paths)
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def _raise_if_cancelled(self):
        if self.is_cancelled:
            raise PathSizeThread._Cancelled()

    def _prune_contained_paths(self, paths):
        unique_paths = list(dict.fromkeys(path for path in paths if path))
        sorted_paths = sorted(unique_paths, key=lambda value: len(os.path.normpath(value)))
        kept = []
        kept_keys = []

        for path in sorted_paths:
            normalized = os.path.normcase(os.path.normpath(path)).rstrip("\\/")
            is_contained = any(
                normalized.startswith(parent + "\\") or normalized.startswith(parent + "/")
                for parent in kept_keys
            )
            if is_contained:
                continue
            kept.append(path)
            kept_keys.append(normalized)

        return kept

    def _summarize_paths(self, cursor, paths):
        _, folders, files, total_size = summarize_paths_batch(
            cursor,
            paths,
            cancel_check=self._raise_if_cancelled,
        )
        return {
            'folders': folders,
            'files': files,
            'size': total_size,
        }

    def run(self):
        from src.file_index_tool import FileIndexTool

        tool = FileIndexTool()
        try:
            total = self._summarize_paths(tool.conn.cursor(), self.paths)
            self._raise_if_cancelled()
            self.total_ready.emit(self.request_id, self.kind, total)
        except PathSizeThread._Cancelled:
            return
        except Exception as exc:
            self.total_failed.emit(self.request_id, self.kind, str(exc))
        finally:
            tool.close()


class BulkSelectThread(QThread):
    bulk_ready = pyqtSignal(int, dict)
    bulk_failed = pyqtSignal(int, str)

    class _Cancelled(Exception):
        pass

    def __init__(self, request_id, candidates, status=None, videos_only=False, include_context=False, exact_only=False, parent=None):
        super().__init__(parent)
        self.request_id = request_id
        self.candidates = candidates
        self.status = status
        self.videos_only = videos_only
        self.include_context = include_context
        self.exact_only = exact_only
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def _raise_if_cancelled(self):
        if self.is_cancelled:
            raise BulkSelectThread._Cancelled()

    def run(self):
        try:
            result = self._collect_targets()
            if result is not None:
                self.bulk_ready.emit(self.request_id, result)
        except Exception as exc:
            self.bulk_failed.emit(self.request_id, str(exc))

    def _collect_targets(self):
        targets = []
        seen_paths = set()
        all_checked = True

        for item in self.candidates:
            self._raise_if_cancelled()

            path = item.get('path')
            if not path or path in seen_paths:
                continue
            if not self.include_context and not item.get('is_page_result', True):
                continue
            if self.status is not None and item.get('status') != self.status:
                continue
            if self.videos_only and not item.get('is_video'):
                continue
            if self.exact_only and not item.get('is_exact_match'):
                continue

            seen_paths.add(path)
            targets.append(path)
            if not item.get('checked', False):
                all_checked = False

        return {
            'paths': targets,
            'all_checked': all_checked,
            'count': len(targets),
        }


class StatusDelegate(QStyledItemDelegate):
    # (text_color, bg_color, border_color)
    _COLORS = {
        'Empty':    (QColor("#991b1b"), QColor("#fef2f2"), QColor("#fee2e2")), # Danger Red
        'Inactive': (QColor("#92400e"), QColor("#fffbeb"), QColor("#fef3c7")), # Warning Amber
        'Pending':  (QColor("#92400e"), QColor("#fffbeb"), QColor("#fef3c7")), # Warning Amber
        'Active':   (QColor("#065f46"), QColor("#f0fdf4"), QColor("#d1fae5")), # Success Emerald
        'Context':  (QColor("#1e40af"), QColor("#eff6ff"), QColor("#dbeafe")), # Info Blue
    }

    def paint(self, painter, option, index):
        status = index.data(Qt.ItemDataRole.DisplayRole)
        if not status:
            return
        colors = self._COLORS.get(status, (QColor(0x57, 0x60, 0x6a), QColor(0xf6, 0xf8, 0xfa), QColor(0xd0, 0xd7, 0xde)))
        text_color, bg_color, border_color = colors
        rect = option.rect
        pill = QRect(rect.left() + (rect.width() - 64) // 2,
                     rect.top() + (rect.height() - 20) // 2, 64, 20)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(bg_color))
        painter.setPen(QPen(border_color, 1))
        painter.drawRoundedRect(pill, 10, 10)

        
        # Colored dot
        dot_size = 6
        dot_rect = QRect(pill.left() + 8, pill.top() + (pill.height() - dot_size) // 2, dot_size, dot_size)
        painter.setBrush(QBrush(text_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(dot_rect)
        
        f = QFont("Segoe UI", 8)
        f.setBold(True)


        painter.setFont(f)
        painter.setPen(text_color)
        
        text_rect = QRect(pill.left() + 18, pill.top(), pill.width() - 18, pill.height())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, status)
        painter.restore()


class SizeBarDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)

    def paint(self, painter, option, index):
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""

        widget = opt.widget
        style = widget.style() if widget else QApplication.style()

        painter.save()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        display_font = index.data(Qt.ItemDataRole.FontRole) or opt.font
        painter.setFont(display_font)

        content_rect = opt.rect.adjusted(10, 5, -18, -5)

        text_color = (
            opt.palette.color(opt.palette.ColorRole.HighlightedText)
            if opt.state & QStyle.StateFlag.State_Selected
            else opt.palette.color(opt.palette.ColorRole.Text)
        )
        painter.setPen(text_color)
        painter.drawText(
            content_rect,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            text,
        )
        painter.restore()

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        hint.setHeight(max(hint.height(), 34))
        return hint


class ActionDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return
        rect = option.rect
        
        # Determine if hovered
        is_hovered = option.state & QStyle.StateFlag.State_MouseOver

        # Smaller professional button: 64x22
        btn_w, btn_h = 64, 22
        btn = QRect(rect.left() + (rect.width() - btn_w) // 2,
                    rect.top() + (rect.height() - btn_h) // 2, btn_w, btn_h)
        
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        if "queued" in text:
            # Draw red badge for queued items
            painter.setBrush(QBrush(QColor("#fef2f2")))
            painter.setPen(QPen(QColor("#ef4444"), 1))
            painter.drawRoundedRect(btn, 10, 10) # Rounded capsule
            f = QFont("Segoe UI", 8)
            f.setBold(True)


            painter.setFont(f)
            painter.setPen(QColor("#ef4444"))
            painter.drawText(btn, Qt.AlignmentFlag.AlignCenter, "X Queued")
        else:
            if not is_hovered:
                painter.restore()
                return
            # Modern Small Outline Button for "Open"
            bg = QColor("#eff6ff")
            painter.setBrush(QBrush(bg))
            painter.setPen(QPen(QColor("#2563eb"), 1.2))
            painter.drawRoundedRect(btn, 5, 5)
            f = QFont("Segoe UI", 8)
            f.setBold(True)
            painter.setFont(f)
            painter.setPen(QColor("#2563eb"))
            painter.drawText(btn, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

# ---------------------------------------------------------------------------
# Filter Panel (standalone widget)
# ---------------------------------------------------------------------------

class FilterPanel(QFrame):
    DEFAULT_STALE_MONTHS = 3
    AGE_FILTER_DISABLED = 0
    MAX_STALE_MONTHS = 24
    MAX_MANUAL_STALE_MONTHS = 240
    DEFAULT_STATUS_FILTER = "Inactive"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setVisible(False)
        self.setFixedWidth(280)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 16, 0, 16)
        outer.setSpacing(4)

        # Section 1: Display Mode
        display_section = QWidget()
        display_section.setObjectName("displayModeSection")
        display_layout = QVBoxLayout(display_section)
        display_layout.setContentsMargins(0, 0, 0, 0)
        display_layout.setSpacing(4)

        lbl_display = QLabel("Display Mode")
        lbl_display.setObjectName("displayModeHeader")

        display_layout.addWidget(lbl_display)

        self.rb_all      = QRadioButton("Show all")
        self.rb_inactive = QRadioButton("Inactive only")
        self.rb_empty    = QRadioButton("Empty only")
        self.rb_videos   = QRadioButton("Videos only")
        self.rb_all.setChecked(True)
        self.bg = QButtonGroup()
        for rb in [self.rb_all, self.rb_inactive, self.rb_empty, self.rb_videos]:
            self.bg.addButton(rb)
            display_layout.addWidget(rb)
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
        
        outer.addWidget(display_section)
        outer.addWidget(self._hline())

        # Section 1.5: View Mode
        view_section = QWidget()
        view_section.setObjectName("viewModeSection")
        view_layout = QVBoxLayout(view_section)
        view_layout.setContentsMargins(0, 0, 0, 0)
        view_layout.setSpacing(4)

        lbl_view = QLabel("View Mode")
        lbl_view.setObjectName("viewModeHeader")
        view_layout.addWidget(lbl_view)

        self.rb_view_tree    = QRadioButton("Tree view")
        self.rb_view_files   = QRadioButton("Files only")
        self.rb_view_folders = QRadioButton("Folders only")
        self.rb_view_tree.setChecked(True)
        
        self.bg_view = QButtonGroup()
        for rb in [self.rb_view_tree, self.rb_view_files, self.rb_view_folders]:
            self.bg_view.addButton(rb)
            view_layout.addWidget(rb)
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
            
        outer.addWidget(view_section)
        outer.addWidget(self._hline())



        # Section 2: Date Range
        
        

        # Section 3: Stale Threshold
        age_section = QWidget()
        age_section.setObjectName("ageThresholdSection")
        age_outer_layout = QVBoxLayout(age_section)
        age_outer_layout.setContentsMargins(0, 0, 0, 0)
        age_outer_layout.setSpacing(4)

        lbl_age = QLabel("Age Threshold")
        lbl_age.setObjectName("ageThresholdHeader")

        age_outer_layout.addWidget(lbl_age)

        age_box = QWidget()
        age_layout = QVBoxLayout(age_box)
        age_layout.setContentsMargins(16, 4, 16, 4)
        age_layout.setSpacing(8)

        age_hdr = QHBoxLayout()
        age_hdr.setSpacing(6)
        self.lbl_pill = QLabel()
        self.lbl_pill.setObjectName("agePill")
        self.lbl_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_pill.setFixedHeight(20)
        self.lbl_val = QLabel()
        self.lbl_val.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        age_hdr.addWidget(self.lbl_pill)
        age_hdr.addWidget(self.lbl_val)
        age_hdr.addStretch()
        age_layout.addLayout(age_hdr)
        
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(self.AGE_FILTER_DISABLED, self.MAX_STALE_MONTHS)
        self.slider.setValue(self.AGE_FILTER_DISABLED)
        self.slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider.setTickInterval(1)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider.valueChanged.connect(self._update_age_label)
        self.slider.valueChanged.connect(self._sync_manual_age_from_slider)
        age_layout.addWidget(self.slider)

        self.age_input = QSpinBox()
        self.age_input.setRange(self.AGE_FILTER_DISABLED, self.MAX_MANUAL_STALE_MONTHS)
        self.age_input.setValue(self.AGE_FILTER_DISABLED)
        self.age_input.setSuffix(" months")
        self.age_input.setCursor(Qt.CursorShape.PointingHandCursor)
        self.age_input.valueChanged.connect(self._sync_slider_from_manual_age)

        bot_row = QHBoxLayout()
        bot_row.setSpacing(8)
        lbl_manual = QLabel("Manual:")
        lbl_manual.setObjectName("manualLabel")

        bot_row.addWidget(lbl_manual)
        bot_row.addWidget(self.age_input)
        bot_row.addStretch()
        age_layout.addLayout(bot_row)
        age_outer_layout.addWidget(age_box)
        
        outer.addWidget(age_section)

        self._update_age_label(self.slider.value())
        outer.addStretch()

        # Section 4: Actions
        action_box = QWidget()
        action_layout = QVBoxLayout(action_box)
        action_layout.setContentsMargins(16, 16, 16, 16)
        action_layout.setSpacing(10)
        
        self.btn_reset = QPushButton("Reset all filters")
        self.btn_reset.setObjectName("resetFilters")

        self.btn_reset.setToolTip("Reset all filters to their default values")
        self.btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close = QPushButton("Close sidebar")
        self.btn_close.setObjectName("closeSidebar")
        self.btn_close.setToolTip("Hide the filter sidebar")
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        action_layout.addWidget(self.btn_reset)
        action_layout.addWidget(self.btn_close)
        outer.addWidget(action_box)

    def _update_age_label(self, value):
        if value == self.AGE_FILTER_DISABLED:
            self.lbl_pill.setText("Off")
            self.lbl_pill.setStyleSheet("background-color: #64748b; color: white;")
            self.lbl_val.setText("Age filtering is disabled")
        else:
            self.lbl_pill.setText(f"{value}m")
            self.lbl_pill.setStyleSheet("background-color: #2563eb; color: white;")
            
            years = value // 12
            months = value % 12
            if years > 0 and months > 0:
                text = f"Older than {years}y {months}m"
            elif years > 0:
                text = f"Older than {years}y"
            else:
                text = f"Older than {value} month{'s' if value > 1 else ''}"
            self.lbl_val.setText(text)

    def _hline(self):
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        f.setObjectName("sidebarDivider")
        f.setFixedHeight(1)
        return f

    def _sync_manual_age_from_slider(self, value):
        blocker = QSignalBlocker(self.age_input)
        self.age_input.setValue(value)
        del blocker

    def _sync_slider_from_manual_age(self, value):
        blocker = QSignalBlocker(self.slider)
        self.slider.setValue(min(value, self.MAX_STALE_MONTHS))
        del blocker
        self._update_age_label(value)

    def get_older_than_secs(self):
        """Returns seconds threshold or None if age filtering is disabled."""
        v = self.age_input.value()
        if v == self.AGE_FILTER_DISABLED:
            return None
        return v * 30 * 24 * 3600  # months to seconds (approximate)

    def get_stale_months_for_scan(self):
        value = self.age_input.value()
        return value if value > 0 else self.DEFAULT_STALE_MONTHS

    def get_view_mode(self):
        """Returns 'Tree', 'Files', or 'Folders'."""
        if self.rb_view_files.isChecked(): return 'Files'
        if self.rb_view_folders.isChecked(): return 'Folders'
        return 'Tree'

    def apply_default_browse_preset(self):
        self.rb_all.setChecked(True)
        self.rb_view_tree.setChecked(True)
        self.slider.setValue(self.AGE_FILTER_DISABLED)
        
        blocker = QSignalBlocker(self.age_input)
        self.age_input.setValue(self.AGE_FILTER_DISABLED)
        del blocker
        
        self._update_age_label(self.AGE_FILTER_DISABLED)

    # helpers
    def _make_section(self, title):
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)
        lbl = QLabel(title)
        lbl.setObjectName("sectionLabel")
        col.addWidget(lbl)
        return col

    def _vline(self):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFixedWidth(1)
        line.setObjectName("divider")
        return line


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("IBMS Folder Watchdog - Exchange Drive Scanner")
        self.resize(1200, 720)

        self.scanner_thread = None
        self.delete_thread = None
        self.delete_progress = None
        self.page_load_thread = None
        self.page_load_threads = []
        self.page_load_request_id = 0
        self.lazy_child_threads = {}
        self.lazy_child_request_id = 0
        self.totals_thread = None
        self.totals_threads = []
        self.totals_request_id = 0
        self.totals_refresh_pending = False
        self.totals_refresh_options = None
        self.current_page_total_thread = None
        self.selected_total_thread = None
        self.path_total_threads = []
        self.cached_folder_total = None
        self.cached_folder_total_root = None
        self.cached_selected_total = None
        self.folder_cache = None
        self.saved_age_threshold_value = 0
        self.age_controls_forced_disabled = False
        self.loading_dialog = None
        self.selection_loading_dialog = None
        self.selection_loading_min_visible_until = 0.0
        self.delete_preview_thread = None
        self.pending_delete_preview_dialog = None
        self.bulk_select_thread = None
        self.bulk_select_request_id = 0
        self.bulk_select_active = False
        self.bulk_select_queue = []
        self.bulk_select_state = Qt.CheckState.Unchecked
        self.bulk_select_offer_all_pages = False
        self.bulk_select_current_page_count = 0
        self.bulk_select_label = ""
        self.bulk_select_status = None
        self.bulk_select_videos_only = False
        self.bulk_select_precomputed_scope = None
        self.bulk_select_requested_scope = 'current'
        self.bulk_select_forced_state = None
        self.source_index_by_path = {}
        self.bulk_select_batch_size = 250
        self.bulk_select_batch_delay_ms = 0
        self.tree_model     = None
        self.proxy_model    = WatchdogFilterProxyModel()
        self.bulk_delete_scope = None
        self.selected_paths = {}
        self.page_only_selected_paths = {}
        self.excluded_paths = {}
        self.page_only_selection_page = None
        self.current_total_matches = 0
        self.current_lazy_show_all_tree = False
        self.refresh_tree_state_key = None
        self.refresh_collapsed_tree_paths = set()

        self.sort_column = 3 # Default sort by Age
        self.sort_order = Qt.SortOrder.DescendingOrder

        self.recount_timer = QTimer(self)
        self.recount_timer.setSingleShot(True)
        self.recount_timer.timeout.connect(self._do_recount)

        # Debounce timer for age threshold so slider drags do not reload on every step.
        self.filter_debounce_timer = QTimer(self)
        self.filter_debounce_timer.setSingleShot(True)
        self.filter_debounce_timer.setInterval(700)
        self.filter_debounce_timer.timeout.connect(self._on_filter_changed)

        self.loading_timer = QTimer(self)
        self.loading_timer.setSingleShot(True)
        self.loading_timer.setInterval(180)
        self.loading_timer.timeout.connect(self._show_loading_dialog)

        self.scan_refresh_timer = QTimer(self)
        self.scan_refresh_timer.setSingleShot(True)
        self.scan_refresh_timer.setInterval(1200)
        self.scan_refresh_timer.timeout.connect(self._refresh_pagination_only)

        self._build_ui()
        self.tree.header().sectionClicked.connect(self._on_header_sort_clicked)
        self._apply_sort_indicator()

    # -------------------------------------------------------------------------
    # UI
    # -------------------------------------------------------------------------

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        vbox = QVBoxLayout(root)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        # Top bar
        topbar = QWidget()
        topbar.setObjectName("topbar")
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(16, 12, 16, 12)
        tb.setSpacing(12)

        lbl = QLabel("IBMS Watchdog")
        lbl.setObjectName("appTitle")
        tb.addWidget(lbl)
        tb.addWidget(self._vbar())

        self.txt_path = QLineEdit()
        self.txt_path.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.txt_path.setMinimumWidth(240)
        self.txt_path.setPlaceholderText("Enter or browse a folder path...")
        
        # Add folder icon to the left of the path input
        path_icon = QIcon.fromTheme("folder-open", QIcon.fromTheme("folder"))
        self.txt_path.addAction(path_icon, QLineEdit.ActionPosition.LeadingPosition)

        btn_browse = QPushButton("Browse")
        btn_browse.setObjectName("primaryBtn")
        btn_browse.setToolTip("Browse folder")
        btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_browse.clicked.connect(self._browse)
        tb.addWidget(self.txt_path)
        tb.addWidget(btn_browse)

        self.btn_rescan = QPushButton("Re-scan")
        self.btn_rescan.setObjectName("primaryBtn")
        self.btn_rescan.setToolTip("Start scanning the selected folder path")
        self.btn_rescan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_rescan.clicked.connect(self.start_scan)
        tb.addWidget(self.btn_rescan)

        tb.addWidget(self._vbar())

        self.btn_filter = QPushButton("Filters")
        self.btn_filter.setObjectName("ghostBtn")
        self.btn_filter.setCheckable(True)
        self.btn_filter.setToolTip("Toggle filter sidebar (Alt+F)")
        self.btn_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_filter.clicked.connect(self._toggle_filters)
        tb.addWidget(self.btn_filter)

        self.btn_expand = QPushButton("Expand All")
        self.btn_expand.setObjectName("collapseAll")
        self.btn_expand.setCheckable(True)
        self.btn_expand.setToolTip("Expand or collapse all folders in the view")
        self.btn_expand.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_expand.clicked.connect(self._toggle_expand)

        # Push Export + Delete to the far right
        tb.addStretch()
        tb.addWidget(self._vbar())

        btn_export = QPushButton("Export CSV")
        btn_export.setObjectName("primaryBtn")
        btn_export.setToolTip("Export the current view to CSV")
        btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_export.clicked.connect(self._export_csv)
        tb.addWidget(btn_export)

        self.btn_delete = QPushButton("Delete Selected")
        self.btn_delete.setObjectName("deleteBtn")
        self.btn_delete.setToolTip("Permanently delete selected items")
        self.btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_delete.clicked.connect(self._delete_selected)
        self.btn_delete.setEnabled(False)
        tb.addWidget(self.btn_delete)

        vbox.addWidget(topbar)

        # Main Content Area (Sidebar + Content)
        main_area = QHBoxLayout()
        main_area.setContentsMargins(0, 0, 0, 0)
        main_area.setSpacing(0)

        # Left Sidebar (Filters)
        self.fp = FilterPanel()
        self.fp.btn_close.clicked.connect(self._toggle_filters)
        self.fp.btn_reset.clicked.connect(self._reset_filters)
        # Dynamic filtering
        self.fp.bg.buttonClicked.connect(lambda _btn: self._on_filter_changed())
        self.fp.rb_videos.toggled.connect(self._on_videos_mode_toggled)
        # Age controls: wait for the user to pause before applying.
        self.fp.slider.valueChanged.connect(self._on_age_slider_changed)
        self.fp.slider.sliderReleased.connect(self._on_age_slider_released)
        self.fp.age_input.editingFinished.connect(self._on_manual_age_finished)
        
        # View mode connections
        self.fp.rb_view_tree.toggled.connect(self._on_filter_changed)
        self.fp.rb_view_files.toggled.connect(self._on_filter_changed)
        self.fp.rb_view_folders.toggled.connect(self._on_filter_changed)
        
        self._apply_default_browse_preset(apply_now=False)
        self._update_age_controls_enabled()
        self._update_expand_control_visibility()
        main_area.addWidget(self.fp)

        # Right Content Area
        self.right_content = QWidget()
        self.right_content.setObjectName("contentArea")
        right_v = QVBoxLayout(self.right_content)
        right_v.setContentsMargins(24, 24, 24, 24)
        right_v.setSpacing(16)

        # Controls row (above tree): expand / select all
        self.controls_bar = QWidget()
        controls_layout = QHBoxLayout(self.controls_bar)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(12)
        controls_layout.addWidget(self.btn_expand)
        self.btn_select_all = QPushButton("Select All")
        self.btn_select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select_all.clicked.connect(self._select_all)
        controls_layout.addWidget(self.btn_select_all)

        self.btn_clear_selection = QPushButton("Unselect All")
        self.btn_clear_selection.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_selection.clicked.connect(self._unselect_all)
        self.btn_clear_selection.setVisible(False)
        controls_layout.addWidget(self.btn_clear_selection)
        
        self.btn_select_inactive = QPushButton("Select All Inactive")
        self.btn_select_inactive.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select_inactive.clicked.connect(self._select_inactive)
        self.btn_select_inactive.setEnabled(False)
        controls_layout.addWidget(self.btn_select_inactive)

        self.btn_select_empty = QPushButton("Select All Empty")
        self.btn_select_empty.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select_empty.setToolTip("No empty folders are available in the current view.")
        self.btn_select_empty.clicked.connect(self._select_empty)
        self.btn_select_empty.setEnabled(False)
        controls_layout.addWidget(self.btn_select_empty)

        controls_layout.addStretch()

        # Integrated Pagination
        self.btn_prev_page = QPushButton("<")
        self.btn_prev_page.setObjectName("pageNavBtn")
        self.btn_prev_page.setFixedSize(34, 34)
        self.btn_prev_page.setToolTip("Previous Page")
        self.btn_prev_page.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_prev_page.clicked.connect(self._prev_page)
        controls_layout.addWidget(self.btn_prev_page)

        self.lbl_page_info = QLabel("Page 1")
        self.lbl_page_info.setObjectName("pageInfo")
        self.lbl_page_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_page_info.setMinimumWidth(150)
        controls_layout.addWidget(self.lbl_page_info)

        self.btn_next_page = QPushButton(">")
        self.btn_next_page.setObjectName("pageNavBtn")
        self.btn_next_page.setFixedSize(34, 34)
        self.btn_next_page.setToolTip("Next Page")
        self.btn_next_page.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next_page.clicked.connect(self._next_page)
        controls_layout.addWidget(self.btn_next_page)

        self.controls_bar.setVisible(False)
        right_v.addWidget(self.controls_bar)

        # Empty state + Tree + Scanning state wrapped in a stacked widget
        self.content_stack = QStackedWidget()

        # Page 0: Empty state
        empty_page = QWidget()
        empty_page.setObjectName("emptyState")
        ep_layout = QVBoxLayout(empty_page)
        ep_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ep_layout.setSpacing(16)

        title_lbl = QLabel("Choose a folder to scan")
        title_lbl.setObjectName("emptyTitle")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub_lbl = QLabel("Find old files, empty folders, videos, and large cleanup targets in one place.")
        sub_lbl.setObjectName("emptySub")
        sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub_lbl.setWordWrap(True)
        sub_lbl.setMaximumWidth(420)

        btn_browse_cta = QPushButton("Browse folder...")
        btn_browse_cta.setObjectName("primaryBtn")
        btn_browse_cta.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_browse_cta.setFixedWidth(180)
        btn_browse_cta.clicked.connect(self._browse)

        ep_layout.addStretch()
        ep_layout.addWidget(title_lbl)
        ep_layout.addWidget(sub_lbl, alignment=Qt.AlignmentFlag.AlignCenter)
        ep_layout.addSpacing(8)
        ep_layout.addWidget(btn_browse_cta, alignment=Qt.AlignmentFlag.AlignCenter)
        ep_layout.addStretch()

        # Page 1: Scanning state
        scanning_page = QWidget()
        scanning_page.setObjectName("emptyState")
        sp_layout = QVBoxLayout(scanning_page)
        sp_layout.setContentsMargins(24, 36, 24, 36)
        sp_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sp_layout.setSpacing(18)

        sp_title = QLabel("Scanning in progress")
        sp_title.setObjectName("emptyTitle")
        sp_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.scan_detail = QLabel("Preparing the index and waiting for the first batch of results.")
        self.scan_detail.setObjectName("emptySub")
        self.scan_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scan_detail.setWordWrap(True)
        self.scan_detail.setMaximumWidth(520)
        self.scan_detail.setMinimumHeight(52)

        self.scan_path = QLabel()
        self.scan_path.setObjectName("scanPathValue")
        self.scan_path.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.scan_path.setWordWrap(True)
        self.scan_path.setMaximumWidth(760)
        self.scan_path.setMinimumHeight(72)
        self.scan_path.setTextFormat(Qt.TextFormat.RichText)

        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setTextVisible(False)
        self.scan_progress.setObjectName("loadingBar")
        self.scan_progress.setFixedWidth(360)
        self.scan_progress.setFixedHeight(18)

        scan_hint = QLabel("You can keep this window open while the scan runs in the background.")
        scan_hint.setObjectName("emptySub")
        scan_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scan_hint.setWordWrap(True)
        scan_hint.setMaximumWidth(520)

        sp_layout.addStretch()
        sp_layout.addWidget(sp_title)
        sp_layout.addWidget(self.scan_detail, alignment=Qt.AlignmentFlag.AlignCenter)
        sp_layout.addWidget(self.scan_path, alignment=Qt.AlignmentFlag.AlignCenter)
        sp_layout.addSpacing(4)
        sp_layout.addWidget(self.scan_progress, alignment=Qt.AlignmentFlag.AlignCenter)
        sp_layout.addSpacing(4)
        sp_layout.addWidget(scan_hint, alignment=Qt.AlignmentFlag.AlignCenter)
        sp_layout.addStretch()

        # Page 1: Tree view
        self.tree = QTreeView()
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(False)
        self.tree.setRootIsDecorated(True)
        self.tree.setItemsExpandable(True)
        self.tree.setExpandsOnDoubleClick(True)
        self.tree.setIndentation(16)
        self.tree.setMouseTracking(True)
        self.tree.viewport().setMouseTracking(True)
        self.tree.setItemDelegateForColumn(4, SizeBarDelegate(self.tree))
        self.tree.setItemDelegateForColumn(5, StatusDelegate(self.tree))
        self.tree.clicked.connect(self._on_click)
        self.tree.doubleClicked.connect(self._on_double_click)
        self.tree.expanded.connect(self._on_tree_expanded)
        self.tree.collapsed.connect(self._on_tree_collapsed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self.tree.viewport().installEventFilter(self)
        self.tree.setIconSize(QSize(18, 18))
        hdr = self.tree.header()
        hdr.setSectionsMovable(False)
        hdr.setStretchLastSection(False)
        hdr.setSectionsClickable(True)
        try:
            hdr.setSortIndicatorShown(True)
            hdr.setSortIndicator(3, Qt.SortOrder.DescendingOrder) # Default sort by Age
        except Exception:
            pass
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_status_column_visibility()

        # Page 2: No results (filter produced zero matches)
        no_results_page = QWidget()
        no_results_page.setObjectName("emptyState")
        nr_layout = QVBoxLayout(no_results_page)
        nr_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nr_layout.setSpacing(14)
        nr_icon = QLabel("[Search]")
        nr_icon.setObjectName("emptyIcon")
        nr_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nr_title = QLabel("No matching items")
        nr_title.setObjectName("emptyTitle")
        nr_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.nr_sub = QLabel("Try adjusting your filters or age threshold.")
        self.nr_sub.setObjectName("emptySub")
        self.nr_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.nr_sub.setWordWrap(True)
        self.nr_sub.setMaximumWidth(400)
        btn_reset_nr = QPushButton("Reset filters")
        btn_reset_nr.setObjectName("primaryBtn")
        btn_reset_nr.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_reset_nr.setFixedWidth(160)
        btn_reset_nr.clicked.connect(self._reset_filters)
        nr_layout.addStretch()
        nr_layout.addWidget(nr_icon)
        nr_layout.addWidget(nr_title)
        nr_layout.addWidget(self.nr_sub, alignment=Qt.AlignmentFlag.AlignCenter)
        nr_layout.addSpacing(8)
        nr_layout.addWidget(btn_reset_nr, alignment=Qt.AlignmentFlag.AlignCenter)
        nr_layout.addStretch()

        self.content_stack.addWidget(empty_page)      # index 0
        self.content_stack.addWidget(self.tree)       # index 1
        self.content_stack.addWidget(no_results_page) # index 2
        self.content_stack.addWidget(scanning_page)   # index 3

        self.content_stack.setCurrentIndex(0)

        # Wrap content_stack in container so we can overlay the floating button
        self.tree_container = QWidget()
        self.tree_container.setObjectName("tableCard")
        tc_layout = QVBoxLayout(self.tree_container)
        tc_layout.setContentsMargins(1, 1, 1, 1) # Internal border gap
        tc_layout.setSpacing(0)
        tc_layout.addWidget(self.content_stack)


        # Floating scroll-to-top button (child of tree_container for z-order)
        self.btn_scroll_top = QPushButton("^")
        self.btn_scroll_top.setObjectName("scrollTopBtn")
        self.btn_scroll_top.setParent(self.tree_container)
        self.btn_scroll_top.setFixedSize(38, 38)
        self.btn_scroll_top.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scroll_top.setToolTip("Back to top")
        self.btn_scroll_top.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_scroll_top.setVisible(False)
        self.btn_scroll_top.clicked.connect(self._scroll_to_top)
        self.btn_scroll_top.raise_()

        # Show/hide based on scroll position
        self.tree.verticalScrollBar().valueChanged.connect(self._on_tree_scroll)
        self.tree_container.installEventFilter(self)

        right_v.addWidget(self.tree_container, 1)
        main_area.addWidget(self.right_content, 1)

        vbox.addLayout(main_area, 1)

        # Status bar
        sb = QWidget()
        sb.setObjectName("statusbar")
        sb.setFixedHeight(40)
        sbl = QHBoxLayout(sb)
        sbl.setContentsMargins(12, 5, 12, 5)
        sbl.setSpacing(8)
        self.lbl_status = QLabel("Ready - select a folder and click Re-scan")
        self.lbl_status.setObjectName("statusMessage")

        sbl.addWidget(self.lbl_status)
        sbl.addStretch()
        
        self.chip_empty = self._chip("Empty 0", "chipEmpty")
        self.chip_inactive_folders = self._chip("Inactive 0 folders", "chipInactive")
        self.chip_inactive_files = self._chip("0 files", "chipInactive")
        self.chip_browse_size = self._chip("Folder --", "chipSpace")
        self.chip_page_size = self._chip("Page --", "chipSpace")
        self.chip_selected_size = self._chip("Selected --", "chipSpace")

        sbl.addWidget(self.chip_empty)
        sbl.addWidget(self.chip_inactive_folders)
        sbl.addWidget(self.chip_inactive_files)
        sbl.addWidget(self.chip_browse_size)
        sbl.addWidget(self.chip_page_size)
        sbl.addWidget(self.chip_selected_size)
        self._update_status_metrics_visibility()
        vbox.addWidget(sb)

        # Do NOT auto-start scan - let the user enter a path first

    def _sep(self):
        l = QLabel("-")
        l.setObjectName("statusSeparator")
        return l


    def _vbar(self):
        f = QFrame()
        f.setFrameShape(QFrame.Shape.VLine)
        f.setFixedWidth(1)
        f.setFixedHeight(24)
        f.setObjectName("divider")
        return f

    def _chip(self, text, obj_name):
        l = QLabel(text)
        l.setObjectName(obj_name)
        l.setToolTip(text)
        if obj_name == "chipSpace":
            l.setMinimumWidth(112)
            l.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        return l

    def _set_chip_text(self, chip, text):
        chip.setText(text)
        chip.setToolTip(text)

    def _scan_path_html(self, path):
        if not path:
            return (
                "<div><span style='font-weight:600;color:#0f172a;'>Current folder</span></div>"
                "<div style='margin-top:6px;color:#64748b;'>--</div>"
            )

        escaped = html.escape(path)
        for separator in ("\\", "/", "_", "-", "."):
            escaped = escaped.replace(separator, f"{separator}<wbr>")
        return (
            "<div><span style='font-weight:600;color:#0f172a;'>Current folder</span></div>"
            f"<div style='margin-top:6px;color:#475569;'>{escaped}</div>"
        )

    def _set_scanning_panel(self, path=None, detail=None):
        if hasattr(self, 'scan_path'):
            self.scan_path.setText(self._scan_path_html(path))
            self.scan_path.setToolTip(path or "")
        if detail and hasattr(self, 'scan_detail'):
            self.scan_detail.setText(detail)

    def _set_size_totals_pending(self, browse=False, page=False, selected=False):
        if browse:
            self._set_chip_text(self.chip_browse_size, "Folder calculating...")
        if page:
            self._set_chip_text(self.chip_page_size, "Page calculating...")
        if selected:
            if self._selected_roots_for_delete():
                self._set_chip_text(self.chip_selected_size, "Selected calculating...")
            else:
                self.cached_selected_total = 0
                self._set_chip_text(self.chip_selected_size, "Selected 0 B (0F 0f)")

    def _on_tree_scroll(self, value):
        """Show/hide the scroll-to-top button based on vertical scroll position."""
        visible = value > 80
        self.btn_scroll_top.setVisible(visible)
        if visible:
            self._reposition_scroll_top_btn()

    def _reposition_scroll_top_btn(self):
        """Keep the floating button pinned to the bottom-right of the tree container."""
        btn = self.btn_scroll_top
        c = self.tree_container
        margin = 18
        x = c.width() - btn.width() - margin
        y = c.height() - btn.height() - margin
        btn.move(x, y)
        btn.raise_()

    def _scroll_to_top(self):
        self.tree.scrollToTop()

    def eventFilter(self, obj, event):
        if hasattr(self, 'tree') and obj == self.tree.viewport():
            if event.type() == QEvent.Type.MouseMove:
                pos = event.position().toPoint() if hasattr(event, 'position') else event.pos()
                self._update_tree_cursor(self.tree.indexAt(pos))
            elif event.type() == QEvent.Type.Leave:
                self.tree.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        if hasattr(self, 'tree_container') and obj == self.tree_container:
            if event.type() == QEvent.Type.Resize:
                self._reposition_scroll_top_btn()
        return super().eventFilter(obj, event)

    # -------------------------------------------------------------------------
    # Filter panel
    # -------------------------------------------------------------------------

    def _toggle_filters(self):
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
        
        is_visible = self.fp.isVisible()
        target_width = 280 if not is_visible else 0
        
        # Ensure it's ready for animation
        if not is_visible:
            self.fp.setVisible(True)
            self.fp.setMinimumWidth(0)
            self.fp.setMaximumWidth(0)
            
        self.sidebar_anim = QPropertyAnimation(self.fp, b"maximumWidth")
        self.sidebar_anim.setDuration(250)
        self.sidebar_anim.setStartValue(self.fp.width())
        self.sidebar_anim.setEndValue(target_width)
        self.sidebar_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        
        if is_visible:
            self.sidebar_anim.finished.connect(lambda: self.fp.setVisible(False))
            
        self.sidebar_anim.start()
        self.btn_filter.setChecked(not is_visible)

    def _apply_filters(self):
        # 1. Update proxy model so it can format the Status column correctly
        self._cancel_running_bulk_select_thread()
        self._update_age_controls_enabled()
        age_secs = None if self.fp.rb_all.isChecked() else self.fp.get_older_than_secs()
        if hasattr(self.fp, 'rb_all') and self.fp.rb_all.isChecked():
            status_filter = None
        elif self.fp.rb_empty.isChecked():
            status_filter = 'Empty'
        elif self.fp.rb_videos.isChecked():
            status_filter = None
        else:
            status_filter = 'Inactive'

        if self.content_stack.currentIndex() != 0:
            self.proxy_model.setSourceModel(None)
            self.tree_model = None

        self.proxy_model.set_filters(
            empty_only=False,
            older_than_secs=age_secs,
            status_filter=status_filter,
            view_mode=self.fp.get_view_mode(),
        )
        self._update_expand_control_visibility()
        self._update_status_column_visibility()
        self._update_status_metrics_visibility()

        # 2. Fetch the paginated data from SQL
        self.current_page = 0
        if self.content_stack.currentIndex() != 0:
            self._load_page()

    def _apply_default_browse_preset(self, apply_now=True):
        blockers = [
            QSignalBlocker(self.fp.bg),
            QSignalBlocker(self.fp.slider),
            QSignalBlocker(self.fp.age_input),
        ]
        try:
            self.fp.apply_default_browse_preset()
        finally:
            del blockers

        if apply_now:
            self._cancel_running_bulk_select_thread()
            self._discard_current_page_selection()
            self._apply_filters()

    def _update_expand_control_visibility(self):
        if not hasattr(self, 'btn_expand') or not hasattr(self, 'fp'):
            return
        self.btn_expand.setVisible(not self.fp.rb_all.isChecked())

    def _update_status_column_visibility(self):
        if not hasattr(self, 'tree') or not hasattr(self, 'fp'):
            return

        show_status = not (self.fp.rb_all.isChecked() or self.fp.rb_videos.isChecked())
        self.tree.setColumnHidden(5, not show_status)

    def _update_status_metrics_visibility(self):
        if not hasattr(self, 'chip_empty'):
            return

        show_status = not (self.fp.rb_all.isChecked() or self.fp.rb_videos.isChecked())
        for widget in (
            self.chip_empty,
            self.chip_inactive_folders,
            self.chip_inactive_files,
        ):
            widget.setVisible(show_status)

    def _update_age_controls_enabled(self):
        if not hasattr(self, 'fp'):
            return

        show_all = self.fp.rb_all.isChecked()
        disabled_value = self.fp.AGE_FILTER_DISABLED
        current_value = self.fp.age_input.value()

        if show_all:
            if current_value != disabled_value:
                self.saved_age_threshold_value = current_value
            if current_value != disabled_value or self.fp.slider.value() != disabled_value:
                blockers = [
                    QSignalBlocker(self.fp.slider),
                    QSignalBlocker(self.fp.age_input),
                ]
                try:
                    self.fp.slider.setValue(disabled_value)
                    self.fp.age_input.setValue(disabled_value)
                finally:
                    del blockers
                self.fp._update_age_label(disabled_value)
            self.age_controls_forced_disabled = True
        else:
            restore_value = getattr(self, 'saved_age_threshold_value', disabled_value)
            if (
                getattr(self, 'age_controls_forced_disabled', False)
                and current_value == disabled_value
                and restore_value != disabled_value
            ):
                blockers = [
                    QSignalBlocker(self.fp.slider),
                    QSignalBlocker(self.fp.age_input),
                ]
                try:
                    self.fp.slider.setValue(min(restore_value, self.fp.MAX_STALE_MONTHS))
                    self.fp.age_input.setValue(restore_value)
                finally:
                    del blockers
                self.fp._update_age_label(restore_value)
            self.age_controls_forced_disabled = False

        enabled = not show_all
        self.fp.slider.setEnabled(enabled)
        self.fp.age_input.setEnabled(enabled)
        self.fp.lbl_pill.setEnabled(enabled)
        self.fp.lbl_val.setEnabled(enabled)
        cursor = Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor
        self.fp.slider.setCursor(cursor)
        self.fp.age_input.setCursor(cursor)

    def _reset_filters(self):
        self._apply_default_browse_preset()

    def _toggle_expand(self, checked):
        self._set_expand_state(checked)

    def _update_content_page(self):
        """Switch between tree (page 1) and no-results (page 2) based on current proxy row count.
        Only acts when data is loaded (page 0 = no scan yet is handled separately)."""
        if self.content_stack.currentIndex() == 0:
            return  # still on the welcome page, no scan done yet
        if self._has_visible_rows():
            self.content_stack.setCurrentIndex(1)
        else:
            self.content_stack.setCurrentIndex(2)

    def _has_visible_rows(self):
        if not self.proxy_model:
            return False
        return self.proxy_model.rowCount(QModelIndex()) > 0

    def _set_expand_state(self, expanded):
        if not hasattr(self, 'tree'):
            return

        if expanded:
            if self.proxy_model and self.proxy_model.rowCount() > 0:
                # Pages are capped at 2000 rows so expandAll is safe
                self.is_programmatic_expand = True
                try:
                    self.tree.expandAll()
                finally:
                    self.is_programmatic_expand = False
        else:
            self.is_programmatic_expand = True
            try:
                self.tree.collapseAll()
            finally:
                self.is_programmatic_expand = False

        if hasattr(self, 'btn_expand'):
            blocker = QSignalBlocker(self.btn_expand)
            self.btn_expand.setChecked(expanded)
            self.btn_expand.setText("Collapse All" if expanded else "Expand All")
            self.btn_expand.setEnabled(self._has_visible_rows())
            del blocker

    def _tree_refresh_state_signature(self, options=None):
        options = options or self._page_load_options()
        filter_mode = 'all'
        age_value = None
        if hasattr(self, 'fp'):
            if self.fp.rb_empty.isChecked():
                filter_mode = 'empty'
            elif self.fp.rb_videos.isChecked():
                filter_mode = 'videos'
            elif self.fp.rb_all.isChecked():
                filter_mode = 'all'
            else:
                filter_mode = 'inactive'
            if hasattr(self.fp, 'slider'):
                age_value = self.fp.slider.value()
        return (
            options.get('scan_root'),
            options.get('view_mode'),
            filter_mode,
            age_value,
            options.get('page'),
            bool(options.get('lazy_show_all_tree')),
            bool(options.get('filtered_expanded_tree')),
        )

    def _should_preserve_tree_refresh_state(self, options=None):
        options = options or self._page_load_options()
        return (
            options.get('view_mode') == 'Tree'
            and not options.get('lazy_show_all_tree')
        )

    def _capture_tree_refresh_state(self, options=None):
        options = options or self._page_load_options()
        if not self._should_preserve_tree_refresh_state(options):
            self.refresh_tree_state_key = None
            self.refresh_collapsed_tree_paths = set()
            return
        if not self.tree_model or self.proxy_model.sourceModel() is None:
            self.refresh_tree_state_key = self._tree_refresh_state_signature(options)
            self.refresh_collapsed_tree_paths = set()
            return

        collapsed_paths = set()
        stack = [QModelIndex()]
        while stack:
            parent = stack.pop()
            for row in range(self.proxy_model.rowCount(parent)):
                proxy_index = self.proxy_model.index(row, 0, parent)
                source_index = self.proxy_model.mapToSource(proxy_index)
                if not source_index.isValid():
                    continue
                item_data = self.tree_model.data(source_index, Qt.ItemDataRole.UserRole) or {}
                if not item_data.get('is_dir') or not item_data.get('path'):
                    continue
                if self.tree.isExpanded(proxy_index):
                    stack.append(proxy_index)
                else:
                    collapsed_paths.add(self._path_key(item_data['path']))

        self.refresh_tree_state_key = self._tree_refresh_state_signature(options)
        self.refresh_collapsed_tree_paths = collapsed_paths

    def _restore_tree_refresh_state(self, options=None):
        options = options or self._page_load_options()
        if not self._should_preserve_tree_refresh_state(options):
            self.refresh_tree_state_key = None
            self.refresh_collapsed_tree_paths = set()
            return
        if self.refresh_tree_state_key != self._tree_refresh_state_signature(options):
            return
        if not self.tree_model or self.proxy_model.sourceModel() is None:
            return

        self.is_programmatic_expand = True
        try:
            for path_key in sorted(
                self.refresh_collapsed_tree_paths,
                key=lambda key: key.count(os.sep),
            ):
                source_index = self.source_index_by_path.get(path_key)
                if source_index is None or not source_index.isValid():
                    continue
                proxy_index = self.proxy_model.mapFromSource(source_index)
                if proxy_index.isValid():
                    self.tree.setExpanded(proxy_index, False)
        finally:
            self.is_programmatic_expand = False

    def _select_all(self):
        if not self.tree_model:
            return

        videos_only = self._display_mode() == 'Videos'
        selecting = not self.btn_select_all.text().startswith("Deselect")
        self._begin_page_select_flow(
            label="videos" if videos_only else "items",
            videos_only=videos_only,
            selecting=selecting,
        )

    def _clear_all_checks(self):
        self.bulk_delete_scope = None
        self.selected_paths.clear()
        self.page_only_selected_paths.clear()
        self.excluded_paths.clear()
        self.page_only_selection_page = None
        self.cached_selected_total = None
        if not self.tree_model:
            self._do_recount()
            return
        indices = []

        def collect(parent=QModelIndex()):
            for r in range(self.tree_model.rowCount(parent)):
                idx = self.tree_model.index(r, 0, parent)
                indices.append(idx)
                if self.tree_model.hasChildren(idx):
                    collect(idx)

        collect()
        if indices:
            self.tree_model.set_indices_check_state_direct(indices, Qt.CheckState.Unchecked, explicit=False)
        self._do_recount()

    def _clear_current_page_checks(self):
        self.bulk_delete_scope = None
        self.cached_selected_total = None
        if not self.tree_model:
            self._do_recount()
            return

        indices = self._collect_checked_source_indices()
        if indices:
            self.tree_model.set_indices_check_state_direct(indices, Qt.CheckState.Unchecked, explicit=False)
        visible_keys = {
            self._path_key(self.tree_model.data(idx, Qt.ItemDataRole.UserRole).get('path', ''))
            for idx in indices
            if self.tree_model.data(idx, Qt.ItemDataRole.UserRole)
        }
        self.selected_paths = {
            key: path for key, path in self.selected_paths.items()
            if self._path_key(path) not in visible_keys
        }
        for key in visible_keys:
            path = self.source_index_by_path.get(key)
            item_data = self.tree_model.data(path, Qt.ItemDataRole.UserRole) if path and path.isValid() else None
            if item_data and self._has_any_selected_ancestor(item_data.get('path', ''), include_self=False):
                self.excluded_paths[key] = item_data['path']
        self.page_only_selected_paths.clear()
        self.page_only_selection_page = None
        self._do_recount()

    def _unselect_all(self):
        if not self.tree_model or not (
            self.bulk_delete_scope
            or self.selected_paths
            or self.page_only_selected_paths
        ):
            return

        if not (hasattr(self, 'lbl_page_info') and self.lbl_page_info.isVisible()):
            self._clear_all_checks()
            return

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle("Clear selection")
        msg.setText("Clear the current page or clear everything selected?")
        msg.setInformativeText(
            "Current page only clears the checked rows that are visible right now.\n"
            "Clear everything removes the full selection across all pages."
        )
        btn_page = msg.addButton("Current page only", QMessageBox.ButtonRole.RejectRole)
        btn_all = msg.addButton("Clear everything", QMessageBox.ButtonRole.AcceptRole)
        btn_cancel = msg.addButton("Cancel", QMessageBox.ButtonRole.DestructiveRole)
        btn_cancel.hide()
        msg.setEscapeButton(btn_cancel)
        msg.setDefaultButton(btn_page)
        msg.exec()

        if msg.clickedButton() == btn_all:
            self._clear_all_checks()
        elif msg.clickedButton() == btn_page:
            self._clear_current_page_checks()

    def _discard_current_page_selection(self):
        self.bulk_delete_scope = None
        self.selected_paths.clear()
        self.page_only_selected_paths.clear()
        self.excluded_paths.clear()
        self.page_only_selection_page = None
        self.cached_selected_total = None
        self.btn_delete.setText("Delete Selected")
        self.btn_delete.setEnabled(False)

    def _on_filter_changed(self):
        # User manually changed a filter control - clear selections and apply
        self._discard_current_page_selection()
        self._apply_filters()

    def _on_manual_age_finished(self):
        if hasattr(self.fp, 'age_input'):
            self.fp.age_input.interpretText()
        self._on_filter_changed()

    def _on_age_slider_changed(self, _value):
        if not hasattr(self, 'fp') or not self.fp.slider.isEnabled():
            return
        self.filter_debounce_timer.start()

    def _on_age_slider_released(self):
        if not hasattr(self, 'fp') or not self.fp.slider.isEnabled():
            return
        self.filter_debounce_timer.start()

    def _on_videos_mode_toggled(self, checked):
        self._update_age_controls_enabled()

    def _is_video_item(self, item_data):
        if not item_data or item_data.get('is_dir', False):
            return False
        return os.path.splitext(item_data.get('name', ''))[1].lower() in VIDEO_EXTENSIONS

    def _collect_bulk_target_indices(self, status=None, videos_only=False, include_context=False):
        if not self.tree_model or self.proxy_model.sourceModel() is None:
            return []

        targets = []
        seen_paths = set()
        exact_only = self.proxy_model.has_active_filters() or status is not None or videos_only

        def walk(parent=QModelIndex()):
            for row in range(self.proxy_model.rowCount(parent)):
                proxy_index = self.proxy_model.index(row, 0, parent)
                source_index = self.proxy_model.mapToSource(proxy_index)
                item_data = self.tree_model.data(source_index, Qt.ItemDataRole.UserRole)
                if item_data:
                    path = item_data.get('path')
                    is_exact_match = self.proxy_model.matches_source_index(source_index)
                    is_page_result = item_data.get(
                        '_is_page_result',
                        not item_data.get('_is_context_fetched', False),
                    )
                    if (
                        path and path not in seen_paths
                        and (include_context or is_page_result)
                        and (status is None or item_data.get('status') == status)
                        and (not videos_only or self._is_video_item(item_data))
                        and (not exact_only or is_exact_match)
                    ):
                        seen_paths.add(path)
                        targets.append(source_index)
                if self.proxy_model.hasChildren(proxy_index):
                    walk(proxy_index)

        walk()
        return targets

    def _are_all_indices_checked(self, indices):
        if not indices:
            return False
        for index in indices:
            item_data = self.tree_model.data(index, Qt.ItemDataRole.UserRole)
            if not item_data or not item_data.get('path'):
                return False
            if not self._is_effectively_selected(item_data['path']):
                return False
        return True

    def _path_key(self, path):
        return os.path.normcase(os.path.normpath(path))

    def _has_selected_ancestor(self, path, include_self=True):
        normalized = self._path_key(path)
        for selected_key in self.selected_paths:
            if normalized == selected_key:
                return include_self
            if normalized.startswith(selected_key + os.sep):
                return True
        return False

    def _has_any_selected_ancestor(self, path, include_self=True):
        return self._has_selected_ancestor(path, include_self=include_self)

    def _is_descendant_of_selected_path(self, path):
        return self._has_any_selected_ancestor(path, include_self=False)

    def _has_excluded_ancestor(self, path, include_self=True):
        normalized = self._path_key(path)
        for excluded_key in self.excluded_paths:
            if normalized == excluded_key:
                return include_self
            if normalized.startswith(excluded_key + os.sep):
                return True
        return False

    def _has_excluded_descendant(self, path):
        normalized = self._path_key(path)
        return any(
            excluded_key.startswith(normalized + os.sep)
            for excluded_key in self.excluded_paths
        )

    def _is_effectively_selected(self, path):
        return (
            self._has_selected_ancestor(path)
            and not self._has_excluded_ancestor(path)
        )

    def _is_persistable_selection_index(self, index):
        if not index.isValid():
            return False
        item_data = self.tree_model.data(index, Qt.ItemDataRole.UserRole)
        if not item_data or not item_data.get('path'):
            return False
        is_context_fetched = item_data.get('_is_context_fetched', False)
        if self.proxy_model.sourceModel() is self.tree_model:
            if self.proxy_model.is_context_only(index) or is_context_fetched:
                return False
        return True

    def _sync_persistent_selection_from_model(self):
        if not self.tree_model:
            return

        stack = [QModelIndex()]
        while stack:
            parent = stack.pop()
            for row in range(self.tree_model.rowCount(parent)):
                index = self.tree_model.index(row, 0, parent)
                item_data = self.tree_model.data(index, Qt.ItemDataRole.UserRole)
                if item_data and item_data.get('path') and self._is_persistable_selection_index(index):
                    key = self._path_key(item_data['path'])
                    state = self.tree_model.data(index, Qt.ItemDataRole.CheckStateRole)
                    if state == Qt.CheckState.Checked:
                        self.excluded_paths.pop(key, None)
                        self.excluded_paths = {
                            excluded_key: excluded_path
                            for excluded_key, excluded_path in self.excluded_paths.items()
                            if not excluded_key.startswith(key + os.sep)
                        }
                        if not self._is_descendant_of_selected_path(item_data['path']):
                            self.selected_paths[key] = item_data['path']
                    elif state == Qt.CheckState.Unchecked:
                        if self._has_any_selected_ancestor(item_data['path'], include_self=False):
                            self.excluded_paths[key] = item_data['path']
                        self.selected_paths.pop(key, None)
                if self.tree_model.hasChildren(index):
                    stack.append(index)

    def _restore_persistent_selection_to_model(self):
        if not self.tree_model or not self.selected_paths:
            return

        indices = []
        stack = [QModelIndex()]
        while stack:
            parent = stack.pop()
            for row in range(self.tree_model.rowCount(parent)):
                index = self.tree_model.index(row, 0, parent)
                item_data = self.tree_model.data(index, Qt.ItemDataRole.UserRole)
                if (
                    item_data and item_data.get('path')
                    and self._is_effectively_selected(item_data['path'])
                    and self._is_persistable_selection_index(index)
                ):
                    indices.append(index)
                if self.tree_model.hasChildren(index):
                    stack.append(index)

        if indices:
            self.tree_model.set_indices_check_state_direct(indices, Qt.CheckState.Checked, explicit=True)

    def _restore_bulk_scope_selection_to_model(self):
        if not self.tree_model or not self.bulk_delete_scope:
            return

        indices = self._collect_bulk_target_indices(
            status=self.bulk_delete_scope.get('status'),
            videos_only=self.bulk_delete_scope.get('videos_only', False),
        )
        indices = [
            index for index in indices
            if self._is_persistable_selection_index(index)
        ]
        if indices:
            self.tree_model.set_indices_check_state_direct(indices, Qt.CheckState.Checked, explicit=True)

    def _promote_current_page_selection_to_page_only(self):
        if not self.tree_model:
            return

        checked_indices = self._collect_checked_source_indices()
        if not checked_indices:
            return

        for index in checked_indices:
            item_data = self.tree_model.data(index, Qt.ItemDataRole.UserRole)
            if not item_data or not item_data.get('path'):
                continue
            key = self._path_key(item_data['path'])
            if key not in self.selected_paths:
                self.selected_paths[key] = item_data['path']

    def _set_indices_checked(self, indices, state, recursive=True):
        if not indices:
            return
        to_change = [idx for idx in indices if self.tree_model.data(idx, Qt.ItemDataRole.CheckStateRole) != state]
        if to_change:
            self.cached_selected_total = None
            if recursive:
                self.tree_model.set_indices_check_state(to_change, state, explicit=True)
            else:
                self.tree_model.set_indices_check_state_direct(to_change, state, explicit=True)
            self._sync_persistent_selection_from_model()

    def _build_bulk_where(self, status=None, videos_only=False):
        where_clauses = []
        params = []
        age_secs = None if self.fp.rb_all.isChecked() else self.fp.get_older_than_secs()
        age_cutoff = (datetime.now().timestamp() - age_secs) if age_secs is not None else None
        view_mode = self.fp.get_view_mode()

        if status == 'Inactive':
            if view_mode == 'Tree':
                where_clauses.append("is_folder = 0")
            if age_cutoff is not None:
                where_clauses.append("modified_time <= ?")
                params.append(age_cutoff)
            else:
                where_clauses.append("1=1")
        elif status == 'Empty':
            if age_cutoff is not None:
                where_clauses.append("modified_time <= ?")
                params.append(age_cutoff)
            where_clauses.append(EMPTY_FOLDER_SQL)
        elif videos_only:
            placeholders = ','.join('?' * len(VIDEO_EXTENSIONS))
            where_clauses.append("is_folder = 0")
            where_clauses.append(f"extension IN ({placeholders})")
            params.extend(VIDEO_EXTENSIONS)
            if age_cutoff is not None:
                where_clauses.append("modified_time <= ?")
                params.append(age_cutoff)
        else:
            if self.fp.rb_empty.isChecked():
                if age_cutoff is not None:
                    where_clauses.append("modified_time <= ?")
                    params.append(age_cutoff)
                where_clauses.append(EMPTY_FOLDER_SQL)
            elif self.fp.rb_inactive.isChecked():
                if view_mode == 'Tree':
                    where_clauses.append("is_folder = 0")
                if age_cutoff is not None:
                    where_clauses.append("modified_time <= ?")
                    params.append(age_cutoff)
                else:
                    where_clauses.append("1=1")
            elif self.fp.rb_videos.isChecked():
                placeholders = ','.join('?' * len(VIDEO_EXTENSIONS))
                where_clauses.append("is_folder = 0")
                where_clauses.append(f"extension IN ({placeholders})")
                params.extend(VIDEO_EXTENSIONS)
                if age_cutoff is not None:
                    where_clauses.append("modified_time <= ?")
                    params.append(age_cutoff)
            elif age_cutoff is not None:
                where_clauses.append("modified_time <= ?")
                params.append(age_cutoff)

        if view_mode == 'Files':
            where_clauses.append("is_folder = 0")
        elif view_mode == 'Folders':
            where_clauses.append("is_folder = 1")

        scan_root = getattr(self, 'current_scan_root', None) or os.path.normpath(self.txt_path.text().strip() or "")
        if scan_root:
            where_clauses.append("path != ? COLLATE NOCASE")
            params.append(scan_root)

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        return where_sql, params

    def _bulk_count(self, status=None, videos_only=False):
        from src.file_index_tool import FileIndexTool
        where_sql, params = self._build_bulk_where(status=status, videos_only=videos_only)
        tool = FileIndexTool()
        try:
            cursor = tool.conn.cursor()
            cursor.execute(
                (
                    "SELECT COUNT(*), "
                    "SUM(CASE WHEN is_folder = 1 THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN is_folder = 0 THEN 1 ELSE 0 END), "
                    "COALESCE(SUM(size), 0) "
                    "FROM file_index"
                ) + where_sql,
                params,
            )
            total, folders, files, total_size = cursor.fetchone()
        finally:
            tool.close()
        return total or 0, folders or 0, files or 0, total_size or 0, where_sql, params

    def _effective_bulk_counts(self, where_sql, params, folder_delete_mode, total, folders, files, total_size):
        if folder_delete_mode != 'empty_only' or not folders:
            return total, folders, files, total_size

        from src.file_index_tool import FileIndexTool

        folder_clause = "is_folder = 1 AND NOT EXISTS (SELECT 1 FROM file_index child WHERE child.parent_path = file_index.path COLLATE NOCASE)"
        effective_where = (where_sql + " AND " + folder_clause) if where_sql else (" WHERE " + folder_clause)
        tool = FileIndexTool()
        try:
            cursor = tool.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM file_index" + effective_where, params)
            deletable_folders = cursor.fetchone()[0] or 0
        finally:
            tool.close()

        return files + deletable_folders, deletable_folders, files, total_size

    def _bulk_folder_delete_mode(self, status=None, videos_only=False):
        if videos_only:
            return 'none'
        if status == 'Empty' or self.fp.rb_empty.isChecked():
            return 'all'
        return 'empty_only'

    def _offer_all_pages_selection(self, label, current_page_count, status=None, videos_only=False, precomputed=None):
        if not (hasattr(self, 'lbl_page_info') and self.lbl_page_info.isVisible()):
            self.bulk_delete_scope = None
            return

        if precomputed:
            total = precomputed.get('total_matches', 0)
            folders = precomputed.get('folders', 0)
            files = precomputed.get('files', 0)
            total_size = precomputed.get('total_size', 0)
            where_sql = precomputed.get('where_sql', "")
            params = precomputed.get('params', [])
        else:
            total, folders, files, total_size, where_sql, params = self._bulk_count(status=status, videos_only=videos_only)
        if total <= current_page_count:
            self.bulk_delete_scope = None
            return

        folder_delete_mode = self._bulk_folder_delete_mode(status=status, videos_only=videos_only)
        effective_total, effective_folders, effective_files, effective_size = self._effective_bulk_counts(
            where_sql,
            params,
            folder_delete_mode,
            total,
            folders,
            files,
            total_size,
        )
        safety_note = ""
        if folder_delete_mode == 'empty_only' and folders:
            safety_note = (
                "\n\nSafety: non-empty matching folders will not be deleted in all-pages mode "
                "because they may contain files that do not match the current filter."
            )

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle("Select across all pages?")
        msg.setText(f"You selected {current_page_count} {label} on this page.")
        msg.setInformativeText(
            "There are more matching items across all pages.\n\n"
            f"All pages: {total} items\n"
            f"Delete targets: {effective_total}\n"
            f"Folders: {effective_folders}\n"
            f"Files: {effective_files}\n\n"
            "Choose whether Delete Selected should apply only to this page or to every matching item."
            f"{safety_note}"
        )
        btn_current = msg.addButton("Current page only", QMessageBox.ButtonRole.RejectRole)
        btn_all = msg.addButton(f"All {total} matching", QMessageBox.ButtonRole.AcceptRole)
        msg.setDefaultButton(btn_current)
        msg.exec()

        if msg.clickedButton() == btn_all:
            self._set_all_pages_selection_scope(
                label,
                status=status,
                videos_only=videos_only,
                precomputed=precomputed,
            )
        else:
            self._promote_current_page_selection_to_page_only()
            self.bulk_delete_scope = None

    def _set_all_pages_selection_scope(self, label, status=None, videos_only=False, precomputed=None):
        if precomputed:
            total = precomputed.get('total_matches', 0)
            folders = precomputed.get('folders', 0)
            files = precomputed.get('files', 0)
            total_size = precomputed.get('total_size', 0)
            where_sql = precomputed.get('where_sql', "")
            params = precomputed.get('params', [])
        else:
            total, folders, files, total_size, where_sql, params = self._bulk_count(status=status, videos_only=videos_only)

        folder_delete_mode = self._bulk_folder_delete_mode(status=status, videos_only=videos_only)
        if precomputed and 'effective_total' in precomputed:
            effective_total = precomputed.get('effective_total', total)
            effective_folders = precomputed.get('effective_folders', folders)
            effective_files = precomputed.get('effective_files', files)
            effective_size = precomputed.get('effective_size', total_size)
        else:
            effective_total, effective_folders, effective_files, effective_size = self._effective_bulk_counts(
                where_sql,
                params,
                folder_delete_mode,
                total,
                folders,
                files,
                total_size,
            )
        self.bulk_delete_scope = {
            'label': label,
            'total': effective_total,
            'folders': effective_folders,
            'files': effective_files,
            'size': effective_size,
            'matched_total': total,
            'where_sql': where_sql,
            'params': params,
            'folder_delete_mode': folder_delete_mode,
            'status': status,
            'videos_only': videos_only,
        }

    def _choose_select_scope(self, label, current_page_count=0):
        if not (hasattr(self, 'lbl_page_info') and self.lbl_page_info.isVisible()):
            return 'current'

        total = self.current_total_matches or current_page_count
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle("Select across all pages?")
        msg.setText(f"You selected {current_page_count} {label} on this page.")
        msg.setInformativeText(
            "There are more matching items across all pages.\n\n"
            f"All pages: {total} items\n\n"
            "Choose whether Delete Selected should apply only to this page or to every matching item.\n"
            "If selection takes a moment, a selecting dialog will stay visible until it finishes."
        )
        btn_current = msg.addButton("Current page only", QMessageBox.ButtonRole.RejectRole)
        btn_all = msg.addButton(f"All {total} matching", QMessageBox.ButtonRole.AcceptRole)
        btn_cancel = msg.addButton("Cancel", QMessageBox.ButtonRole.DestructiveRole)
        btn_cancel.hide()
        msg.setEscapeButton(btn_cancel)
        msg.setDefaultButton(btn_current)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked == btn_all:
            return 'all'
        if clicked == btn_current:
            return 'current'
        return None

    def _begin_page_select_flow(self, label, status=None, videos_only=False, selecting=True):
        target_state = Qt.CheckState.Checked if selecting else Qt.CheckState.Unchecked
        indices = self._collect_bulk_target_indices(status=status, videos_only=videos_only)
        if not indices:
            self._refresh_selection_buttons()
            return
        scope = self._choose_select_scope(label, current_page_count=len(indices)) if selecting else 'current'
        if scope is None:
            return

        self._begin_chunked_bulk_selection(
            indices,
            target_state,
            current_page_count=len(indices),
            label=label,
            offer_all_pages=False,
            status=status,
            videos_only=videos_only,
            requested_scope=scope,
        )

    def _finish_immediate_page_selection(self, label):
        self._set_bulk_selection_busy(False)
        if label == "videos":
            self.lbl_status.setText("Video selection complete.")
        else:
            self.lbl_status.setText("Selection complete.")
        self._refresh_selection_buttons()
        self._do_recount()

    def _set_status_filter(self, status):
        if status == 'Inactive' and not self.fp.rb_inactive.isChecked():
            self.fp.rb_inactive.setChecked(True)
            return True
        if status == 'Empty' and not self.fp.rb_empty.isChecked():
            self.fp.rb_empty.setChecked(True)
            return True
        if status is None and hasattr(self.fp, 'rb_all') and not self.fp.rb_all.isChecked():
            self.fp.rb_all.setChecked(True)
            return True
        return False

    def _display_mode(self):
        if self.fp.rb_inactive.isChecked():
            return 'Inactive'
        if self.fp.rb_empty.isChecked():
            return 'Empty'
        if self.fp.rb_videos.isChecked():
            return 'Videos'
        return 'All'

    def _refresh_selection_buttons(self):
        if not hasattr(self, 'btn_select_all'):
            return

        mode = self._display_mode()
        has_selection = bool(self.bulk_delete_scope or self.selected_paths or self.page_only_selected_paths)
        all_targets, inactive_targets, empty_targets = self._collect_selection_button_targets(mode)

        self.btn_select_all.setVisible(not has_selection)
        self.btn_clear_selection.setVisible(has_selection)
        self.btn_clear_selection.setEnabled(has_selection)

        if mode == 'Videos':
            self.btn_select_all.setText("Select All Videos")
        elif self._are_all_indices_checked(all_targets):
            self.btn_select_all.setText("Deselect All")
        else:
            self.btn_select_all.setText("Select All")

        self.btn_select_inactive.setText(
            "Deselect All Inactive"
            if self._are_all_indices_checked(inactive_targets)
            else "Select All Inactive"
        )
        self.btn_select_empty.setText(
            "Deselect All Empty"
            if self._are_all_indices_checked(empty_targets)
            else "Select All Empty"
        )

        self.btn_select_all.setEnabled(bool(all_targets) and mode in ('All', 'Videos'))
        self.btn_select_inactive.setEnabled(bool(inactive_targets) and mode == 'Inactive')
        empty_enabled = bool(empty_targets) and mode in ('All', 'Empty')
        self.btn_select_empty.setEnabled(empty_enabled)
        self.btn_select_empty.setToolTip(
            "Select every empty folder in the current view."
            if empty_enabled
            else "No empty folders are available in the current view."
        )
        self.btn_select_inactive.setVisible(mode != 'All')

    def _collect_selection_button_targets(self, mode):
        if not self.tree_model or self.proxy_model.sourceModel() is None:
            return [], [], []

        all_targets = []
        inactive_targets = []
        empty_targets = []
        exact_all = self.proxy_model.has_active_filters() or mode == 'Videos'
        seen_all = set()
        seen_inactive = set()
        seen_empty = set()

        def add_target(targets, seen, source_index, path):
            if path in seen:
                return
            seen.add(path)
            targets.append(source_index)

        def walk(parent=QModelIndex()):
            for row in range(self.proxy_model.rowCount(parent)):
                proxy_index = self.proxy_model.index(row, 0, parent)
                source_index = self.proxy_model.mapToSource(proxy_index)
                item_data = self.tree_model.data(source_index, Qt.ItemDataRole.UserRole)
                if item_data:
                    path = item_data.get('path')
                    if path:
                        is_exact_match = self.proxy_model.matches_source_index(source_index)
                        is_page_result = item_data.get(
                            '_is_page_result',
                            not item_data.get('_is_context_fetched', False),
                        )
                        if is_page_result:
                            if (
                                (not exact_all or is_exact_match)
                                and (mode != 'Videos' or self._is_video_item(item_data))
                            ):
                                add_target(all_targets, seen_all, source_index, path)
                            if item_data.get('status') == 'Inactive' and is_exact_match:
                                add_target(inactive_targets, seen_inactive, source_index, path)
                            if item_data.get('status') == 'Empty' and is_exact_match:
                                add_target(empty_targets, seen_empty, source_index, path)
                if self.proxy_model.hasChildren(proxy_index):
                    walk(proxy_index)

        walk()
        return all_targets, inactive_targets, empty_targets

    def _collect_checked_source_indices(self):
        if not self.tree_model:
            return []

        checked_indices = []
        stack = [QModelIndex()]
        while stack:
            parent = stack.pop()
            for row in range(self.tree_model.rowCount(parent)):
                index = self.tree_model.index(row, 0, parent)
                if self.tree_model.data(index, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked:
                    checked_indices.append(index)
                if self.tree_model.hasChildren(index):
                    stack.append(index)
        return checked_indices

    def _prune_paths(self, paths):
        pruned_paths = []
        selected_roots = []
        for path in sorted(paths, key=lambda value: (len(os.path.normpath(value)), value.lower())):
            normalized = os.path.normcase(os.path.normpath(path))
            if any(
                normalized == root or normalized.startswith(root + os.sep)
                for root in selected_roots
            ):
                continue
            selected_roots.append(normalized)
            pruned_paths.append(path)
        return pruned_paths

    def _checked_delete_paths(self):
        pruned = self._selected_roots_for_delete()
        if not pruned or not self.excluded_paths:
            return pruned
        return self._expand_selected_paths_around_exclusions(pruned)

    def _selected_roots_for_delete(self):
        combined = list(self.selected_paths.values()) + list(self.page_only_selected_paths.values())
        return self._prune_paths(combined)

    def _expand_selected_paths_around_exclusions(self, selected_roots):
        from src.file_index_tool import FileIndexTool

        expanded = []
        tool = FileIndexTool()
        try:
            cursor = tool.conn.cursor()
            for root_path in selected_roots:
                root_key = self._path_key(root_path)
                if not any(
                    excluded_key == root_key or excluded_key.startswith(root_key + os.sep)
                    for excluded_key in self.excluded_paths
                ):
                    expanded.append(root_path)
                    continue

                descendant_sql, descendant_params = descendant_like_sql(root_path)
                cursor.execute(
                    f"""
                    SELECT path, is_folder
                    FROM file_index
                    WHERE {case_insensitive_path_sql()} OR ({descendant_sql})
                    ORDER BY length(path) ASC, lower(path) ASC
                    """,
                    (root_path, *descendant_params),
                )
                for path, is_folder in cursor.fetchall():
                    key = self._path_key(path)
                    if self._has_excluded_ancestor(path):
                        continue
                    if is_folder and self._has_excluded_descendant(path):
                        continue
                    expanded.append(path)
        finally:
            tool.close()

        return self._prune_paths(expanded)

    def _summarize_delete_paths(self, paths):
        from src.file_index_tool import FileIndexTool

        pruned_paths = self._prune_paths(paths)
        if not pruned_paths:
            return [], 0, 0, 0

        tool = FileIndexTool()
        try:
            cursor = tool.conn.cursor()
            pruned_paths, total_folders, total_files, total_size = summarize_paths_batch(cursor, pruned_paths)
        finally:
            tool.close()

        return pruned_paths, total_folders, total_files, total_size

    def _count_selected_path_contents(self, cursor, path):
        cursor.execute(
            f"SELECT is_folder FROM file_index WHERE {case_insensitive_path_sql()}",
            (path,),
        )
        row = cursor.fetchone()
        if not row:
            return (1, 0) if os.path.isdir(path) else (0, 1)

        is_folder = row[0]
        if not is_folder:
            return 0, 1

        descendant_sql, descendant_params = descendant_like_sql(path)
        cursor.execute(
            f"""
            SELECT
                COALESCE(SUM(CASE WHEN is_folder = 1 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN is_folder = 0 THEN 1 ELSE 0 END), 0)
            FROM file_index
            WHERE {case_insensitive_path_sql()} OR ({descendant_sql})
            """,
            (path, *descendant_params),
        )
        folders, files = cursor.fetchone()
        return folders or 0, files or 0

    def _checked_delete_summary(self):
        """Return delete targets and effective folder/file totals."""
        target_paths = self._checked_delete_paths()
        return self._summarize_delete_paths(target_paths)

    def _remove_deleted_paths_from_selection(self, deleted_paths):
        if not deleted_paths:
            return

        deleted_keys = [self._path_key(path) for path in deleted_paths if path]
        if not deleted_keys:
            return

        def is_deleted(path):
            normalized = self._path_key(path)
            for deleted_key in deleted_keys:
                if normalized == deleted_key or normalized.startswith(deleted_key + os.sep):
                    return True
            return False

        self.selected_paths = {
            key: path for key, path in self.selected_paths.items()
            if not is_deleted(path)
        }
        self.page_only_selected_paths = {
            key: path for key, path in self.page_only_selected_paths.items()
            if not is_deleted(path)
        }
        self.excluded_paths = {
            key: path for key, path in self.excluded_paths.items()
            if not is_deleted(path)
        }

    def _show_delete_preview(self, paths=None, bulk_scope=None):
        dialog = DeletePreviewDialog(self)
        dialog.preview_mode = 'bulk' if bulk_scope else 'direct'
        if bulk_scope:
            self.delete_preview_thread = DeletePreviewThread(
                bulk_scope=bulk_scope,
                parent=self,
            )
            self.delete_preview_thread.preview_ready.connect(dialog.apply_preview)
            self.delete_preview_thread.preview_failed.connect(dialog.show_error)
            dialog.finished.connect(self.delete_preview_thread.cancel)
            self.delete_preview_thread.finished.connect(lambda: setattr(self, 'delete_preview_thread', None))
            self.delete_preview_thread.start()
        else:
            self.delete_preview_thread = DeletePreviewThread(
                paths or [],
                excluded_paths=self.excluded_paths,
                parent=self,
            )
            self.delete_preview_thread.preview_ready.connect(dialog.apply_preview)
            self.delete_preview_thread.preview_failed.connect(dialog.show_error)
            dialog.finished.connect(self.delete_preview_thread.cancel)
            self.delete_preview_thread.finished.connect(lambda: setattr(self, 'delete_preview_thread', None))
            self.delete_preview_thread.start()

        self.pending_delete_preview_dialog = dialog
        dialog.finished.connect(lambda *_: setattr(self, 'pending_delete_preview_dialog', None))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        result = dialog.exec()
        if result == QDialog.DialogCode.Accepted:
            return dialog.preview_paths
        return None

    def _build_direct_delete_preview(self, paths):
        pruned_paths = self._prune_paths(paths)
        folder_count = 0
        file_count = 0
        if pruned_paths:
            from src.file_index_tool import FileIndexTool

            tool = FileIndexTool()
            try:
                cursor = tool.conn.cursor()
                _, folder_count, file_count, _total_size = summarize_paths_batch(cursor, pruned_paths)
            finally:
                tool.close()
        return {
            'paths': pruned_paths,
            'total': len(pruned_paths),
            'folders': folder_count,
            'files': file_count,
            'size': self.cached_selected_total,
        }

    def _current_page_file_paths(self):
        if not self.tree_model or self.proxy_model.sourceModel() is None:
            return []

        paths = []
        exact_only = self.proxy_model.has_active_filters()
        view_mode = self.fp.get_view_mode()

        def walk(parent=QModelIndex()):
            for row in range(self.proxy_model.rowCount(parent)):
                proxy_index = self.proxy_model.index(row, 0, parent)
                source_index = self.proxy_model.mapToSource(proxy_index)
                item_data = self.tree_model.data(source_index, Qt.ItemDataRole.UserRole)
                if item_data and item_data.get('path'):
                    is_page_result = item_data.get(
                        '_is_page_result',
                        not item_data.get('_is_context_fetched', False),
                    )
                    is_exact_match = self.proxy_model.matches_source_index(source_index)
                    is_chargeable = is_page_result and (not exact_only or is_exact_match)
                    if is_chargeable:
                        paths.append(item_data['path'])
                        if view_mode == 'Tree' and item_data.get('is_dir', False):
                            continue

                if view_mode == 'Tree' and self.proxy_model.hasChildren(proxy_index):
                    walk(proxy_index)

        walk()
        return list(dict.fromkeys(paths))

    def _path_subtree_size(self, cursor, path):
        cursor.execute(
            f"SELECT is_folder, size FROM file_index WHERE {case_insensitive_path_sql()}",
            (path,),
        )
        row = cursor.fetchone()
        if not row:
            return 0

        is_folder, size = row
        if not is_folder:
            return size or 0

        descendant_sql, descendant_params = descendant_like_sql(path)
        cursor.execute(
            f"SELECT COALESCE(SUM(size), 0) FROM file_index WHERE is_folder = 0 AND ({descendant_sql})",
            descendant_params,
        )
        return cursor.fetchone()[0] or 0

    def _sum_paths_total_size(self, paths):
        from src.file_index_tool import FileIndexTool

        pruned_paths = self._prune_paths(paths)
        if not pruned_paths:
            return 0

        tool = FileIndexTool()
        cursor = tool.conn.cursor()
        try:
            _, _, _, total_size = summarize_paths_batch(cursor, pruned_paths)
        finally:
            tool.close()
        return total_size

    def _browse_folder_total_size(self, cursor):
        root_path = getattr(self, 'current_scan_root', None) or os.path.normpath(self.txt_path.text().strip() or "")
        if not root_path:
            return 0

        normalized = os.path.normpath(root_path).rstrip("\\/")
        cursor.execute(
            "SELECT total_size FROM folder_summary WHERE path = ? COLLATE NOCASE",
            (normalized,),
        )
        summary = cursor.fetchone()
        if summary:
            return summary[0] or 0

        descendant_sql, descendant_params = descendant_like_sql(normalized)
        cursor.execute(
            f"SELECT COALESCE(SUM(size), 0) FROM file_index WHERE is_folder = 0 AND ({case_insensitive_path_sql()} OR {descendant_sql})",
            (normalized, *descendant_params),
        )
        return cursor.fetchone()[0] or 0

    def _select_by_status(self, status):
        if not self.tree_model:
            return

        button = self.btn_select_inactive if status == 'Inactive' else self.btn_select_empty
        selecting = not button.text().startswith("Deselect")
        self._begin_page_select_flow(
            label=f"{status.lower()} items",
            status=status,
            selecting=selecting,
        )

    def _select_inactive(self):
        self._select_by_status('Inactive')

    def _select_empty(self):
        self._select_by_status('Empty')

    # -------------------------------------------------------------------------
    # Scan
    # -------------------------------------------------------------------------

    def _reset_view(self):
        """Clear all displayed data and return to the empty/welcome page."""
        # Stop any running scan first
        if self.scanner_thread and self.scanner_thread.isRunning():
            self.scanner_thread.cancel()
        self._cancel_running_bulk_select_thread()

        # Clear the tree model
        self.tree_model = None
        self.proxy_model.setSourceModel(None)
        self.bulk_delete_scope = None
        self.page_load_request_id += 1
        self.scan_refresh_timer.stop()
        self._hide_loading_dialog()
        self._cancel_running_totals_thread()
        self.totals_refresh_pending = False
        self.totals_refresh_options = None
        self.selected_paths.clear()
        self.page_only_selected_paths.clear()
        self.excluded_paths.clear()
        self.page_only_selection_page = None
        self.source_index_by_path = {}
        self.lazy_child_request_id += 1
        self.current_lazy_show_all_tree = False
        self.refresh_tree_state_key = None
        self.refresh_collapsed_tree_paths = set()
        self.folder_cache = None

        # Reset pagination state
        self.current_page = 0
        self.is_scanning  = False
        self.cached_folder_total = None
        self.cached_folder_total_root = None
        self.totals_request_id += 1

        # Reset status chips
        self.chip_empty.setText("Empty 0")
        self.chip_inactive_folders.setText("Inactive 0 folders")
        self.chip_inactive_files.setText("0 files")
        self._set_chip_text(self.chip_browse_size, "Folder --")
        self._set_chip_text(self.chip_page_size, "Page --")
        self._set_chip_text(self.chip_selected_size, "Selected --")
        self._update_status_metrics_visibility()

        # Hide controls, show empty page
        self.controls_bar.setVisible(False)
        self.content_stack.setCurrentIndex(0)
        self.lbl_status.setText("Ready - select a folder and click Re-scan")

    def _browse(self):
        # Use native Windows Explorer dialog
        start_dir = self.txt_path.text().strip() or ""
        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder", start_dir
        )
        if folder:
            # Clear any previous scan results immediately
            self._reset_view()

            # Preserve UNC paths; normpath mangles \\server to \server
            if folder.startswith("//") or folder.startswith("\\\\"):
                self.txt_path.setText(folder.replace("/", "\\"))
            else:
                self.txt_path.setText(os.path.normpath(folder))
            self._apply_default_browse_preset()
            self.fp.setVisible(True)
            self.btn_filter.setChecked(True)
            # Automatically start a scan once the user has selected a folder
            self.start_scan()

    def start_scan(self):
        # If currently scanning, this button acts as a Stop button
        if self.scanner_thread and self.scanner_thread.isRunning():
            self.scanner_thread.cancel()
            self.lbl_status.setText("Stopping scan...")
            self.btn_rescan.setEnabled(False)
            return

        path = self.txt_path.text().strip()
        if not path:
            self.lbl_status.setText("Please enter a folder path.")
            return
        
        # Normalize slashes but preserve UNC prefix (\\server\share)
        if path.startswith("\\\\") or path.startswith("//"):
            # UNC path - keep as-is but normalize forward slashes to back
            path = path.replace("/", "\\")
        else:
            path = os.path.normpath(path)

        # Probe the path - use scandir which works reliably for both local and UNC
        try:
            with os.scandir(path):
                pass   # path is accessible
        except PermissionError:
            self.lbl_status.setText(f"Access denied: {path}")
            return
        except Exception:
            self.lbl_status.setText(f"Path not found or not accessible: {path}")
            return
        
        self.lbl_status.setText("Scanning...")
        self.btn_rescan.setText("Stop")
        self.btn_rescan.setStyleSheet("background-color: #da3633; border-color: #f85149;")
        self._set_size_totals_pending(browse=True, page=True, selected=True)
        self.content_stack.setCurrentIndex(3)
        self._set_scanning_panel(
            path=path,
            detail="Preparing the index and waiting for the first batch of results.",
        )

        self.current_page = 0
        self.total_scanned = 0
        self.is_scanning = True
        self.current_scan_root = os.path.normcase(os.path.normpath(path))
        self.cached_folder_total = None
        self.cached_folder_total_root = self.current_scan_root
        self.totals_request_id += 1
        self._cancel_running_totals_thread()

        self.scanner_thread = ScannerThread(path, stale_months=self.fp.get_stale_months_for_scan())
        self.folder_cache = self.scanner_thread.cache
        self.scanner_thread.scan_finished.connect(self._on_scan_done)
        self.scanner_thread.scan_progress.connect(self._on_progress)
        self.scanner_thread.first_batch_ready.connect(self._on_first_batch_ready)
        self.scanner_thread.batch_ready.connect(self._on_batch_ready)
        self.scanner_thread.start()

    def _on_progress(self, path):
        s = ("..." + path[-72:]) if len(path) > 75 else path
        self.lbl_status.setText(f"Scanning: {s}")
        if self.content_stack.currentIndex() == 3:
            self._set_scanning_panel(path=s)

    def _set_match_status(self, total_matches):
        if self.is_scanning:
            self.lbl_status.setText(f"Scanning... Found {total_matches} matching items")
        else:
            self.lbl_status.setText(f"Found {total_matches} matching items")

    def _on_first_batch_ready(self):
        options = self._page_load_options()
        if options.get('defer_tree_load_until_scan_done') and not self._can_live_load_from_cache(options):
            return
        if self.current_page == 0 and self.content_stack.currentIndex() in (0, 3):
            self._load_page()

    def _on_batch_ready(self):
        """Called during scanning when a new batch of items is indexed."""
        # Counting matches can be expensive on large scans, so throttle it.
        if self.content_stack.currentIndex() == 3:
            options = self._page_load_options()
            if self._can_live_load_from_cache(options):
                self._load_page()
            return
        if self.content_stack.currentIndex() == 1 and not self.scan_refresh_timer.isActive():
            self.scan_refresh_timer.start()

    def _can_live_load_from_cache(self, options):
        cache = options.get('folder_cache')
        root_path = options.get('scan_root')
        return bool(
            options.get('lazy_show_all_tree')
            and cache
            and root_path
            and cache.has_children_for(root_path)
            and cache.child_count(root_path) > 0
        )

    def _refresh_pagination_only(self):
        """Update pagination buttons/info without reloading the whole tree."""
        if self.page_load_thread and self.page_load_thread.isRunning():
            return

        from src.file_index_tool import FileIndexTool
        tool = FileIndexTool()
        try:
            cursor = tool.conn.cursor()

            # We need the same WHERE clause as _load_page
            where_clauses = []
            params = []
            # Determine status filter (simplified mirror of _load_page logic)
            if hasattr(self.fp, 'rb_all') and self.fp.rb_all.isChecked(): status_filter = None
            elif self.fp.rb_empty.isChecked(): status_filter = 'Empty'
            elif self.fp.rb_videos.isChecked(): status_filter = None
            else: status_filter = 'Inactive'

            view_mode = self.fp.get_view_mode()
            age_secs = None if self.fp.rb_all.isChecked() else self.fp.get_older_than_secs()
            age_cutoff = (datetime.now().timestamp() - age_secs) if age_secs is not None else None

            if status_filter == 'Inactive':
                if view_mode == 'Tree':
                    where_clauses.append("is_folder = 0")
                if age_cutoff is not None:
                    where_clauses.append("modified_time <= ?")
                    params.append(age_cutoff)
                else: where_clauses.append("1=1")
            elif status_filter == 'Empty':
                if age_cutoff is not None:
                    where_clauses.append("modified_time <= ?")
                    params.append(age_cutoff)
            elif status_filter == 'Active':
                if age_cutoff is not None:
                    where_clauses.append("modified_time > ?")
                    params.append(age_cutoff)
                else: where_clauses.append("1=0")

            if status_filter == 'Empty': where_clauses.append(EMPTY_FOLDER_SQL)

            if self.fp.rb_videos.isChecked():
                placeholders = ','.join('?' * len(VIDEO_EXTENSIONS))
                where_clauses.append("is_folder = 0")
                where_clauses.append(f"extension IN ({placeholders})")
                params.extend(VIDEO_EXTENSIONS)
                if age_cutoff is not None:
                    where_clauses.append("modified_time <= ?")
                    params.append(age_cutoff)

            if view_mode == 'Files':
                where_clauses.append("is_folder = 0")
            elif view_mode == 'Folders':
                where_clauses.append("is_folder = 1")

            scan_root = getattr(self, 'current_scan_root', None) or os.path.normpath(self.txt_path.text().strip() or "")
            if scan_root:
                where_clauses.append("path != ? COLLATE NOCASE")
                params.append(scan_root)

            where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
            cursor.execute("SELECT COUNT(*) FROM file_index" + where_sql, params)
            total_matches = cursor.fetchone()[0]
        finally:
            tool.close()

        # Update UI controls
        self.current_total_matches = total_matches or 0
        limit = 2000
        offset = self.current_page * limit
        has_multiple_pages = total_matches > limit and not self._page_load_options().get('lazy_show_all_tree')
        self.btn_prev_page.setVisible(has_multiple_pages)
        self.btn_next_page.setVisible(has_multiple_pages)
        self.lbl_page_info.setVisible(has_multiple_pages)
        
        if has_multiple_pages:
            self.lbl_page_info.setText(f"{offset + 1}-{min(offset + limit, total_matches)} of {total_matches}")
            self.btn_prev_page.setEnabled(self.current_page > 0)
            self.btn_next_page.setEnabled(offset + limit < total_matches)
        
        self._set_match_status(total_matches)

    def _on_scan_done(self):
        self.btn_rescan.setEnabled(True)
        self.btn_rescan.setText("Re-scan")
        self.btn_rescan.setStyleSheet("") # reset style
        self.is_scanning = False
        
        if self.scanner_thread and self.scanner_thread.is_cancelled:
            self.lbl_status.setText("Scan stopped by user.")
        else:
            self.lbl_status.setText("Scan complete.")

        if self.content_stack.currentIndex() in (0, 3):
            self._load_page()
        elif self.content_stack.currentIndex() == 1:
            if self.current_lazy_show_all_tree:
                if self.tree_model:
                    self.tree_model.update_sizes_from_cache(self.folder_cache)
            else:
                self.scan_refresh_timer.start()
            self._update_chips_sql()
            
    def _prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self._update_pending_pagination_state()
            self._load_page()

    def _next_page(self):
        self.current_page += 1
        self._update_pending_pagination_state()
        self._load_page()

    def _update_pending_pagination_state(self):
        if not hasattr(self, 'lbl_page_info'):
            return

        limit = 2000
        total_matches = self.current_total_matches or 0
        has_multiple_pages = total_matches > limit and not self.current_lazy_show_all_tree
        self.btn_prev_page.setVisible(has_multiple_pages)
        self.btn_next_page.setVisible(has_multiple_pages)
        self.lbl_page_info.setVisible(has_multiple_pages)
        if not has_multiple_pages:
            return

        offset = self.current_page * limit
        end = min(offset + limit, total_matches) if total_matches else offset + limit
        self.lbl_page_info.setText(f"{offset + 1}-{end} of {total_matches}")
        self.btn_prev_page.setEnabled(self.current_page > 0)
        self.btn_next_page.setEnabled(offset + limit < total_matches)

    def _default_sort_order_for_column(self, col):
        if col in (2, 3, 4, 5):
            return Qt.SortOrder.DescendingOrder
        return Qt.SortOrder.AscendingOrder

    def _apply_sort_indicator(self):
        hdr = self.tree.header()
        blocker = QSignalBlocker(hdr)
        hdr.setSortIndicatorShown(True)
        hdr.setSortIndicator(self.sort_column, self.sort_order)
        del blocker

    def _on_header_sort_clicked(self, col):
        if col >= 6:
            return
        if self.fp.get_view_mode() != 'Tree' and col == 1:
            return

        if col == self.sort_column:
            order = (
                Qt.SortOrder.AscendingOrder
                if self.sort_order == Qt.SortOrder.DescendingOrder
                else Qt.SortOrder.DescendingOrder
            )
        else:
            order = self._default_sort_order_for_column(col)

        self._on_sort_changed(col, order)

    def _on_sort_changed(self, col, order):
        if col >= 6:
            return

        self.bulk_delete_scope = None
        self.sort_column = col
        self.sort_order = order
        self._apply_sort_indicator()
        self.current_page = 0 # Reset to first page when sorting changes
        self._load_page()

    def _page_load_options(self):
        view_mode = self.fp.get_view_mode()
        paginated = True

        if hasattr(self.fp, 'rb_all') and self.fp.rb_all.isChecked():
            status_filter = None
        elif self.fp.rb_empty.isChecked():
            status_filter = 'Empty'
        elif self.fp.rb_videos.isChecked():
            status_filter = None
        else:
            status_filter = 'Inactive'

        age_secs = None if self.fp.rb_all.isChecked() else self.fp.get_older_than_secs()
        age_cutoff = (datetime.now().timestamp() - age_secs) if age_secs is not None else None

        limit = 2000
        lazy_show_all_tree = (
            view_mode == 'Tree'
            and status_filter is None
            and not self.fp.rb_videos.isChecked()
            and age_cutoff is None
        )
        filtered_expanded_tree = (
            view_mode == 'Tree'
            and (
                status_filter in ('Inactive', 'Empty')
                or self.fp.rb_videos.isChecked()
            )
        )
        defer_tree_load_until_scan_done = lazy_show_all_tree or filtered_expanded_tree
        return {
            'limit': limit,
            'offset': self.current_page * limit if paginated else 0,
            'page': self.current_page if paginated else 0,
            'paginated': paginated,
            'view_mode': view_mode,
            'status_filter': status_filter,
            'age_cutoff': age_cutoff,
            'videos_only': self.fp.rb_videos.isChecked(),
            'sort_column': self.sort_column,
            'sort_desc': self.sort_order == Qt.SortOrder.DescendingOrder,
            'scan_root': getattr(self, 'current_scan_root', os.path.normpath(self.txt_path.text().strip() or "")),
            'folder_cache': self.folder_cache,
            'lazy_show_all_tree': lazy_show_all_tree,
            'filtered_expanded_tree': filtered_expanded_tree,
            'defer_tree_load_until_scan_done': defer_tree_load_until_scan_done,
        }

    def _show_loading_dialog(self):
        if self.page_load_thread and self.page_load_thread.isRunning():
            if self.loading_dialog is None:
                self.loading_dialog = LoadingDialog(
                    "Loading results",
                    "Applying filters and preparing the table...",
                    self,
                )
            self.loading_dialog.show()
            QApplication.processEvents()

    def _hide_loading_dialog(self):
        self.loading_timer.stop()
        if self.loading_dialog:
            self.loading_dialog.hide()
            self.loading_dialog.close()
            self.loading_dialog = None

    def _show_selection_loading_dialog(self, label):
        if self.selection_loading_dialog is None:
            detail = (
                "Matching videos and updating the current page..."
                if label == "videos"
                else "Matching items and updating the current page..."
            )
            self.selection_loading_dialog = LoadingDialog(
                "Selecting items",
                detail,
                self,
            )
        self.selection_loading_min_visible_until = time.monotonic() + 0.25
        self.selection_loading_dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.selection_loading_dialog.show()
        self.selection_loading_dialog.raise_()
        self.selection_loading_dialog.activateWindow()
        QApplication.processEvents()

    def _hide_selection_loading_dialog(self):
        if self.selection_loading_dialog:
            remaining = self.selection_loading_min_visible_until - time.monotonic()
            if remaining > 0:
                QTimer.singleShot(int(remaining * 1000), self._hide_selection_loading_dialog)
                return
            self.selection_loading_dialog.hide()
            self.selection_loading_dialog.close()
            self.selection_loading_dialog = None

    def _cleanup_page_thread(self, thread):
        if thread in self.page_load_threads:
            self.page_load_threads.remove(thread)

    def _cleanup_lazy_child_thread(self, request_id):
        self.lazy_child_threads.pop(request_id, None)

    def _cleanup_totals_thread(self, thread):
        if thread in self.totals_threads:
            self.totals_threads.remove(thread)
        if thread is self.totals_thread:
            self.totals_thread = None

    def _cleanup_path_total_thread(self, thread):
        if thread in self.path_total_threads:
            self.path_total_threads.remove(thread)
        if thread is self.current_page_total_thread:
            self.current_page_total_thread = None
        if thread is self.selected_total_thread:
            self.selected_total_thread = None

    def _cleanup_bulk_select_thread(self, thread):
        if thread is self.bulk_select_thread:
            self.bulk_select_thread = None

    def _cancel_running_bulk_select_thread(self):
        self.bulk_select_request_id += 1
        if self.bulk_select_thread and self.bulk_select_thread.isRunning():
            self.bulk_select_thread.cancel()
        if self.bulk_select_active:
            self.bulk_select_active = False
            self.bulk_select_queue = []
            self.bulk_select_offer_all_pages = False
            self.bulk_select_current_page_count = 0
            self.bulk_select_label = ""
            self.bulk_select_status = None
            self.bulk_select_videos_only = False
            self.bulk_select_precomputed_scope = None
            self.bulk_select_requested_scope = 'current'
            self.bulk_select_forced_state = None
            self._set_bulk_selection_busy(False)

    def _cancel_running_totals_thread(self):
        if self.totals_thread and self.totals_thread.isRunning():
            self.totals_thread.cancel()
        if self.current_page_total_thread and self.current_page_total_thread.isRunning():
            self.current_page_total_thread.cancel()
        if self.selected_total_thread and self.selected_total_thread.isRunning():
            self.selected_total_thread.cancel()

    def _set_bulk_selection_busy(self, busy, label=""):
        self.bulk_select_active = busy
        if hasattr(self, 'fp'):
            self.fp.setEnabled(not busy)
        self._set_delete_controls_enabled(not busy)
        if busy:
            self.bulk_select_label = label
            self.lbl_status.setText(f"Selecting {label}..." if label else "Selecting...")
            self._show_selection_loading_dialog(label)
        else:
            self._hide_selection_loading_dialog()

    def _rebuild_source_index_map(self):
        self.source_index_by_path = {}
        if not self.tree_model:
            return

        stack = [QModelIndex()]
        while stack:
            parent = stack.pop()
            for row in range(self.tree_model.rowCount(parent)):
                index = self.tree_model.index(row, 0, parent)
                item_data = self.tree_model.data(index, Qt.ItemDataRole.UserRole)
                if item_data and item_data.get('path'):
                    self.source_index_by_path[self._path_key(item_data['path'])] = index
                if self.tree_model.hasChildren(index):
                    stack.append(index)

    def _snapshot_bulk_candidates(self):
        candidates = []
        if not self.tree_model or self.proxy_model.sourceModel() is None:
            return candidates

        def walk(parent=QModelIndex()):
            for row in range(self.proxy_model.rowCount(parent)):
                proxy_index = self.proxy_model.index(row, 0, parent)
                source_index = self.proxy_model.mapToSource(proxy_index)
                item_data = self.tree_model.data(source_index, Qt.ItemDataRole.UserRole) or {}
                path = item_data.get('path')
                if not path:
                    continue
                candidates.append({
                    'path': path,
                    'status': item_data.get('status'),
                    'checked': self.tree_model.data(source_index, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked,
                    'is_video': self._is_video_item(item_data),
                    'is_page_result': item_data.get('_is_page_result', not item_data.get('_is_context_fetched', False)),
                    'is_exact_match': self.proxy_model.matches_source_index(source_index),
                })
                if self.proxy_model.hasChildren(proxy_index):
                    walk(proxy_index)

        walk()
        return candidates

    def _paths_to_indices(self, paths):
        indices = []
        seen = set()
        for path in paths:
            key = self._path_key(path)
            if key in seen:
                continue
            seen.add(key)
            index = self.source_index_by_path.get(key)
            if index and index.isValid():
                indices.append(index)
        return indices

    def _begin_chunked_bulk_selection(self, indices, state, current_page_count=0, label="items", offer_all_pages=False, status=None, videos_only=False, requested_scope='ask_after'):
        if not indices:
            self.bulk_delete_scope = None
            self._set_bulk_selection_busy(False)
            return

        self.bulk_select_active = True
        self.bulk_select_queue = list(indices)
        self.bulk_select_state = Qt.CheckState(state)
        self.bulk_select_current_page_count = current_page_count
        self.bulk_select_offer_all_pages = offer_all_pages
        self.bulk_select_label = label
        self.bulk_select_status = status
        self.bulk_select_videos_only = videos_only
        self.bulk_select_requested_scope = requested_scope
        self._set_bulk_selection_busy(True, label)
        QTimer.singleShot(0, self._apply_bulk_selection_batch)

    def _apply_bulk_selection_batch(self):
        if not self.bulk_select_active or not self.tree_model:
            return

        batch = self.bulk_select_queue[:self.bulk_select_batch_size]
        self.bulk_select_queue = self.bulk_select_queue[self.bulk_select_batch_size:]
        if batch:
            self.tree_model.set_indices_check_state_direct(batch, self.bulk_select_state, explicit=True)

        if self.bulk_select_queue:
            QTimer.singleShot(self.bulk_select_batch_delay_ms, self._apply_bulk_selection_batch)
        else:
            self._finish_bulk_selection()

    def _finish_bulk_selection(self):
        if not self.bulk_select_active:
            return

        self.bulk_select_active = False
        self.bulk_select_queue = []
        self._sync_persistent_selection_from_model()
        if self.bulk_select_state == Qt.CheckState.Checked:
            if self.bulk_select_requested_scope == 'all':
                self._start_page_bulk_select_async(
                    status=self.bulk_select_status,
                    videos_only=self.bulk_select_videos_only,
                    label=self.bulk_select_label,
                    requested_scope='scope_only',
                )
                return
            elif self.bulk_select_requested_scope == 'ask_after' and self.bulk_select_offer_all_pages:
                self._offer_all_pages_selection(
                    label=self.bulk_select_label,
                    current_page_count=self.bulk_select_current_page_count,
                    status=self.bulk_select_status,
                    videos_only=self.bulk_select_videos_only,
                    precomputed=self.bulk_select_precomputed_scope,
                )
            elif self.bulk_select_requested_scope == 'ask_after':
                self._promote_current_page_selection_to_page_only()
            else:
                self.bulk_delete_scope = None
        else:
            self.bulk_delete_scope = None
        self._set_bulk_selection_busy(False)
        if self.bulk_select_label == "videos":
            self.lbl_status.setText("Video selection complete.")
        else:
            self.lbl_status.setText("Selection complete.")
        self._refresh_selection_buttons()
        self._do_recount()
        self.bulk_select_precomputed_scope = None
        self.bulk_select_forced_state = None
        self.bulk_select_requested_scope = 'current'

    def _start_page_bulk_select_async(self, status=None, videos_only=False, label="items", requested_scope='current', forced_state=None):
        if self.bulk_select_thread and self.bulk_select_thread.isRunning():
            return

        where_sql, params = self._build_bulk_where(status=status, videos_only=videos_only)

        options = self._page_load_options()
        options['limit'] = 2000
        options['offset'] = self.current_page * options['limit']
        options['folder_delete_mode'] = self._bulk_folder_delete_mode(status=status, videos_only=videos_only)

        self.bulk_select_request_id += 1
        request_id = self.bulk_select_request_id
        self.bulk_select_label = label
        self.bulk_select_status = status
        self.bulk_select_videos_only = videos_only
        self.bulk_select_requested_scope = requested_scope
        self.bulk_select_forced_state = forced_state
        self._set_bulk_selection_busy(True, label)

        thread = BulkPageSelectThread(request_id, options, where_sql, params, parent=self)
        self.bulk_select_thread = thread
        thread.bulk_ready.connect(self._on_bulk_page_select_ready)
        thread.bulk_failed.connect(self._on_bulk_page_select_failed)
        thread.finished.connect(lambda thread=thread: self._cleanup_bulk_select_thread(thread))
        thread.start()

    def _on_bulk_page_select_ready(self, request_id, result):
        if request_id != self.bulk_select_request_id:
            return

        if self.bulk_select_requested_scope == 'scope_only':
            self.bulk_select_precomputed_scope = result
            self._set_all_pages_selection_scope(
                self.bulk_select_label,
                status=self.bulk_select_status,
                videos_only=self.bulk_select_videos_only,
                precomputed=result,
            )
            self._set_bulk_selection_busy(False)
            self.lbl_status.setText("Selection complete.")
            self._refresh_selection_buttons()
            self._do_recount()
            self.bulk_select_precomputed_scope = None
            self.bulk_select_forced_state = None
            self.bulk_select_requested_scope = 'current'
            return

        paths = result.get('paths', [])
        if not paths:
            self._set_bulk_selection_busy(False)
            self.bulk_select_precomputed_scope = None
            self.bulk_select_forced_state = None
            self.bulk_select_requested_scope = 'current'
            self.lbl_status.setText("No matching items on this page.")
            self._refresh_selection_buttons()
            self._do_recount()
            return

        if self.bulk_select_forced_state is not None:
            target_state = Qt.CheckState(self.bulk_select_forced_state)
        else:
            all_checked = all(self._is_effectively_selected(path) for path in paths)
            target_state = Qt.CheckState.Unchecked if all_checked else Qt.CheckState.Checked
        indices = self._paths_to_indices(result.get('paths', []))
        self.bulk_select_precomputed_scope = result
        self._begin_chunked_bulk_selection(
            indices,
            target_state,
            current_page_count=result.get('current_page_count', len(indices)),
            label=self.bulk_select_label,
            offer_all_pages=(target_state == Qt.CheckState.Checked),
            status=self.bulk_select_status,
            videos_only=self.bulk_select_videos_only,
            requested_scope=self.bulk_select_requested_scope,
        )

    def _on_bulk_page_select_failed(self, request_id, error):
        if request_id != self.bulk_select_request_id:
            return

        self._set_bulk_selection_busy(False)
        self.bulk_select_precomputed_scope = None
        self.bulk_select_forced_state = None
        self.bulk_select_requested_scope = 'current'
        self.lbl_status.setText("Selection failed.")
        self._refresh_selection_buttons()
        self._do_recount()
        QMessageBox.critical(self, "Selection Error", error)

    def _build_totals_refresh_options(self):
        age_secs = None if self.fp.rb_all.isChecked() else self.fp.get_older_than_secs()
        age_cutoff = (datetime.now().timestamp() - age_secs) if age_secs is not None else None
        root_path = getattr(self, 'current_scan_root', None) or os.path.normpath(self.txt_path.text().strip() or "")
        root_key = os.path.normcase(os.path.normpath(root_path)) if root_path else None
        cached_folder_total = self.cached_folder_total if root_key and root_key == self.cached_folder_total_root else None

        return {
            'age_cutoff': age_cutoff,
            'is_scanning': self.is_scanning,
            'root_path': root_path,
            'cached_folder_total': cached_folder_total,
            'current_page_file_paths': self._current_page_file_paths(),
            'selected_paths': self._selected_roots_for_delete(),
        }

    def _start_totals_thread(self, request_id, options):
        thread = TotalsThread(request_id, options)
        self.totals_thread = thread
        self.totals_threads.append(thread)
        thread.totals_ready.connect(self._on_totals_ready)
        thread.totals_failed.connect(self._on_totals_failed)
        thread.finished.connect(lambda thread=thread: self._on_totals_thread_finished(thread))
        thread.start()

    def _start_path_total_thread(self, request_id, kind, paths):
        if not paths:
            if kind == 'page':
                self._set_chip_text(self.chip_page_size, "Page 0 B")
            else:
                self.cached_selected_total = 0
                self._set_chip_text(self.chip_selected_size, "Selected 0 B (0F 0f)")
            return

        thread = PathSizeThread(request_id, kind, paths, parent=self)
        if kind == 'page':
            self.current_page_total_thread = thread
        else:
            self.selected_total_thread = thread
        self.path_total_threads.append(thread)
        thread.total_ready.connect(self._on_path_total_ready)
        thread.total_failed.connect(self._on_path_total_failed)
        thread.finished.connect(lambda thread=thread: self._cleanup_path_total_thread(thread))
        thread.start()

    def _on_totals_thread_finished(self, thread):
        self._cleanup_totals_thread(thread)
        self.totals_refresh_pending = False
        self.totals_refresh_options = None

    def _start_totals_refresh(self):
        self.totals_request_id += 1
        request_id = self.totals_request_id
        options = self._build_totals_refresh_options()
        self._cancel_running_totals_thread()
        self.totals_refresh_pending = False
        self.totals_refresh_options = None
        self._start_totals_thread(request_id, options)
        self._start_path_total_thread(request_id, 'page', options['current_page_file_paths'])
        self._start_path_total_thread(request_id, 'selected', options['selected_paths'])

    def _set_loading_controls_enabled(self, enabled):
        self.tree.setEnabled(enabled)
        for button in (
            self.btn_expand,
            self.btn_select_all,
            self.btn_clear_selection,
            self.btn_select_inactive,
            self.btn_select_empty,
        ):
            button.setEnabled(enabled and button.isVisible())
        self.btn_delete.setEnabled(enabled and self.btn_delete.isEnabled())

    def _load_page(self):
        self._cancel_running_bulk_select_thread()
        self.page_load_request_id += 1
        request_id = self.page_load_request_id
        options = self._page_load_options()
        self._capture_tree_refresh_state(options)

        if self.page_load_thread and self.page_load_thread.isRunning():
            try:
                self.page_load_thread.page_ready.disconnect()
                self.page_load_thread.page_failed.disconnect()
            except TypeError:
                pass

        self.lbl_status.setText("Loading results...")
        root_path = getattr(self, 'current_scan_root', None) or os.path.normpath(self.txt_path.text().strip() or "")
        root_key = os.path.normcase(os.path.normpath(root_path)) if root_path else None
        has_cached_folder_total = bool(root_key and root_key == self.cached_folder_total_root and self.cached_folder_total is not None)
        self._set_size_totals_pending(
            browse=self.is_scanning or not has_cached_folder_total,
            page=True,
            selected=True,
        )
        self._set_loading_controls_enabled(False)
        self.loading_timer.start()

        self.page_load_thread = PageLoadThread(request_id, options)
        self.page_load_threads.append(self.page_load_thread)
        self.page_load_thread.page_ready.connect(self._on_page_load_ready)
        self.page_load_thread.page_failed.connect(self._on_page_load_failed)
        self.page_load_thread.finished.connect(lambda: self._cleanup_page_thread(self.sender()))
        self.page_load_thread.start()
        return

    def _on_page_load_ready(self, request_id, result):
        if request_id != self.page_load_request_id:
            return

        self._hide_loading_dialog()
        self._set_loading_controls_enabled(True)
        self.page_load_thread = None

        total_matches = result['total_matches']
        self.current_total_matches = total_matches or 0
        rows_count = result['rows_count']
        limit = result['limit']
        offset = result['offset']
        view_mode = result['view_mode']
        lazy_show_all_tree = result.get('lazy_show_all_tree', False)
        self.current_lazy_show_all_tree = lazy_show_all_tree

        if rows_count == 0 and self.current_page > 0:
            self.current_page -= 1
            self._load_page()
            return

        if rows_count == 0 and self.current_page == 0:
            if self.is_scanning:
                self.lbl_status.setText("Scanning... waiting for matching results")
                self.content_stack.setCurrentIndex(3)
                self._set_scanning_panel(
                    detail="Indexing is still running. The first batch will replace this panel as soon as it is ready.",
                )
            else:
                self.content_stack.setCurrentIndex(2)
                self.controls_bar.setVisible(False)
                self.lbl_status.setText("No matching items found")
            return

        self.tree_model = WatchdogTreeModel(result['root_node'])
        self.tree_model.view_mode = view_mode
        self.tree_model.options = self._page_load_options()
        self.proxy_model.setSourceModel(self.tree_model)
        self.tree.setModel(self.proxy_model)
        self._rebuild_source_index_map()
        self._restore_persistent_selection_to_model()
        self._restore_bulk_scope_selection_to_model()

        has_multiple_pages = (total_matches > limit) and not lazy_show_all_tree
        self.btn_prev_page.setVisible(has_multiple_pages)
        self.btn_next_page.setVisible(has_multiple_pages)
        self.lbl_page_info.setVisible(has_multiple_pages)

        if has_multiple_pages:
            self.lbl_page_info.setText(f"{offset + 1}-{min(offset + limit, total_matches)} of {total_matches}")
            self.btn_prev_page.setEnabled(self.current_page > 0)
            self.btn_next_page.setEnabled(offset + limit < total_matches)

        self.content_stack.setCurrentIndex(1)
        self.controls_bar.setVisible(True)

        hdr = self.tree.header()
        for index in range(0, 6):
            hdr.setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._apply_sort_indicator()

        if view_mode == 'Tree':
            self.tree.setColumnWidth(1, 78)
        else:
            self.tree.setColumnWidth(1, 380)
        self.tree.setColumnWidth(2, 150)
        self.tree.setColumnWidth(3, 82)
        self.tree.setColumnWidth(4, 132)
        self.tree.setColumnWidth(5, 116)

        self._set_expand_state(False if lazy_show_all_tree else True)
        self._restore_tree_refresh_state(self.tree_model.options)
        self._update_status_column_visibility()
        self._update_status_metrics_visibility()
        self._set_match_status(total_matches)
        self._update_chips_sql()
        self._do_recount()
        self._refresh_selection_buttons()
        self.tree_model.dataChanged.connect(self._on_checked)
        self.tree_model.layoutChanged.connect(self._on_checked)

    def _on_page_load_failed(self, request_id, error):
        if request_id != self.page_load_request_id:
            return

        self._hide_loading_dialog()
        self._set_loading_controls_enabled(True)
        self.page_load_thread = None
        self.lbl_status.setText("Failed to load results")
        QMessageBox.critical(self, "Load Error", error)

    def _update_chips_sql(self):
        self._start_totals_refresh()

    def _on_totals_ready(self, request_id, result):
        from .models import format_size

        if request_id != self.totals_request_id:
            return

        root_path = getattr(self, 'current_scan_root', None) or os.path.normpath(self.txt_path.text().strip() or "")
        root_key = os.path.normcase(os.path.normpath(root_path)) if root_path else None
        folder_file_count = result.get('folder_file_count')
        folder_total_is_real_zero = result['folder_total'] == 0 and folder_file_count == 0
        folder_total_is_unknown_zero = result['folder_total'] == 0 and folder_file_count not in (0, None)
        if result['folder_total'] is not None and root_key and not folder_total_is_unknown_zero:
            self.cached_folder_total = result['folder_total']
            self.cached_folder_total_root = root_key

        self._set_chip_text(self.chip_empty, f"Empty {result['empty_n']}")
        self._set_chip_text(self.chip_inactive_folders, f"Inactive {result['inactive_folders']} folders")
        self._set_chip_text(self.chip_inactive_files, f"{result['inactive_files']} files")
        if result['folder_total'] is None:
            self._set_chip_text(self.chip_browse_size, "Folder calculating...")
        elif folder_total_is_unknown_zero:
            self.cached_folder_total = None
            self._set_chip_text(self.chip_browse_size, "Folder calculating...")
        else:
            folder_total = result['folder_total']
            folder_total_text = "0 B" if folder_total_is_real_zero else format_size(folder_total)
            self._set_chip_text(self.chip_browse_size, f"Folder {folder_total_text}")
    def _on_path_total_ready(self, request_id, kind, total):
        from .models import format_size

        if request_id != self.totals_request_id:
            return

        if isinstance(total, dict):
            size = total.get('size', 0)
            folders = total.get('folders', 0)
            files = total.get('files', 0)
        else:
            size = total
            folders = 0
            files = 0

        if kind == 'page':
            self._set_chip_text(self.chip_page_size, f"Page {format_size(size)}")
            return

        self.cached_selected_total = size
        self._set_chip_text(
            self.chip_selected_size,
            f"Selected {format_size(size)} ({folders}F {files}f)",
        )
        if (
            self.pending_delete_preview_dialog
            and getattr(self.pending_delete_preview_dialog, 'preview_mode', '') == 'direct'
        ):
            self.pending_delete_preview_dialog.apply_preview(self._build_direct_delete_preview(self._selected_roots_for_delete()))

    def _on_path_total_failed(self, request_id, kind, error):
        if request_id != self.totals_request_id:
            return

        if kind == 'page':
            self._set_chip_text(self.chip_page_size, "Page --")
            return

        self.cached_selected_total = None
        self._set_chip_text(self.chip_selected_size, "Selected --")

    def _on_totals_failed(self, request_id, error):
        if request_id != self.totals_request_id:
            return

        self._set_chip_text(self.chip_browse_size, "Folder --")

    # -------------------------------------------------------------------------
    # Selection count (debounced)
    # -------------------------------------------------------------------------

    def _on_checked(self, tl=None, br=None, roles=None):
        if roles is None or Qt.ItemDataRole.CheckStateRole in roles:
            if self.bulk_select_active:
                return
            self._sync_persistent_selection_from_model()
            self._set_size_totals_pending(selected=True)
            self.recount_timer.start(80)

    def _do_recount(self):
        if not self.tree_model:
            self.btn_delete.setText("Delete Selected")
            self.btn_delete.setEnabled(False)
            self._refresh_selection_buttons()
            return
        if self.bulk_delete_scope:
            scope = self.bulk_delete_scope
            self.btn_delete.setText(
                f"Delete Selected (All pages: {scope['total']} items)"
            )
            self.btn_delete.setEnabled(scope['total'] > 0)
            self._refresh_selection_buttons()
            self._update_chips_sql()
            return
        selected_paths = self._selected_roots_for_delete()
        total = len(selected_paths)
        if total:
            self.btn_delete.setText(f"Delete Selected ({total} item{'s' if total != 1 else ''})")
        else:
            self.btn_delete.setText("Delete Selected")
        self.btn_delete.setEnabled(total > 0)
        self._set_size_totals_pending(selected=True)
        self._refresh_selection_buttons()
        self._update_chips_sql()

    # -------------------------------------------------------------------------
    # Tree interactions
    # -------------------------------------------------------------------------

    def _item_data(self, proxy_index):
        if not self.tree_model:
            return None
        src = self.proxy_model.mapToSource(proxy_index)
        return self.tree_model.data(src, Qt.ItemDataRole.UserRole)

    def _relative_display_location(self, folder_path):
        if not folder_path:
            return ""

        root = getattr(self, 'current_scan_root', None)
        normalized_folder = os.path.normpath(folder_path)
        if not root:
            root = os.path.normpath(self.txt_path.text().strip() or "")

        if root:
            try:
                relative = os.path.relpath(normalized_folder, root)
                if relative == ".":
                    return "."
                if not relative.startswith(".."):
                    return relative
            except ValueError:
                pass

        parts = normalized_folder.replace("/", "\\").split("\\")
        return "\\".join(parts[-3:]) if len(parts) > 3 else normalized_folder

    def _is_tree_index_clickable(self, index):
        if not index.isValid():
            return False
        return index.column() == 0

    def _update_tree_cursor(self, index):
        cursor = (
            Qt.CursorShape.PointingHandCursor
            if self._is_tree_index_clickable(index)
            else Qt.CursorShape.ArrowCursor
        )
        self.tree.viewport().setCursor(cursor)

    def _on_click(self, index):
        return

    def _on_double_click(self, index):
        if not self._is_tree_index_clickable(index):
            return
        d = self._item_data(index)
        if d:
            self._open(d['path'], d.get('is_dir', True))

    def _on_tree_expanded(self, proxy_index):
        if getattr(self, 'is_programmatic_expand', False):
            return
        if not self.tree_model or self.proxy_model.sourceModel() is None:
            return
        source_index = self.proxy_model.mapToSource(proxy_index)
        if source_index.isValid():
            self._start_lazy_child_load(source_index)

    def _on_tree_collapsed(self, proxy_index):
        pass

    def _start_lazy_child_load(self, source_index):
        folder_path = self.tree_model.begin_async_child_load(source_index)
        if not folder_path:
            return

        self.lazy_child_request_id += 1
        request_id = self.lazy_child_request_id
        thread = LazyChildrenLoadThread(
            request_id,
            folder_path,
            sort_column=self.sort_column,
            sort_desc=self.sort_order == Qt.SortOrder.DescendingOrder,
            options=None if self.current_lazy_show_all_tree else getattr(self.tree_model, 'options', {}),
            cache=self.folder_cache,
            parent=self,
        )
        self.lazy_child_threads[request_id] = {
            'thread': thread,
            'index': QPersistentModelIndex(source_index),
            'path': folder_path,
            'model': self.tree_model,
        }
        thread.children_ready.connect(self._on_lazy_children_ready)
        thread.children_failed.connect(self._on_lazy_children_failed)
        thread.finished.connect(lambda request_id=request_id: self._cleanup_lazy_child_thread(request_id))
        thread.start()

    def _on_lazy_children_ready(self, request_id, folder_path, children):
        state = self.lazy_child_threads.get(request_id)
        if not state or state.get('path') != folder_path:
            return
        persistent_index = state.get('index')
        if (
            not persistent_index
            or not persistent_index.isValid()
            or not self.tree_model
            or state.get('model') is not self.tree_model
        ):
            return

        self.tree_model.finish_async_child_load(QModelIndex(persistent_index), children)
        self._rebuild_source_index_map()
        self._restore_persistent_selection_to_model()
        self._restore_bulk_scope_selection_to_model()
        self._refresh_selection_buttons()
        proxy_index = self.proxy_model.mapFromSource(QModelIndex(persistent_index))
        if proxy_index.isValid():
            self.is_programmatic_expand = True
            try:
                self.tree.setExpanded(proxy_index, True)
            finally:
                self.is_programmatic_expand = False

    def _on_lazy_children_failed(self, request_id, folder_path, error):
        state = self.lazy_child_threads.get(request_id)
        if not state or state.get('path') != folder_path:
            return
        persistent_index = state.get('index')
        if (
            persistent_index
            and persistent_index.isValid()
            and self.tree_model
            and state.get('model') is self.tree_model
        ):
            self.tree_model.fail_async_child_load(QModelIndex(persistent_index))
        self.lbl_status.setText("Failed to load folder contents.")

    def _context_menu(self, pos):
        idx = self.tree.indexAt(pos)
        if not idx.isValid():
            return
        d = self._item_data(idx)
        if not d:
            return
        menu = QMenu()
        menu.setStyleSheet("""
            QMenu { background:#1e293b; color:#c9d1d9; border:1px solid #334155;
                    border-radius:6px; padding:4px; }
            QMenu::item { padding:6px 16px; border-radius:4px; }
            QMenu::item:selected { background:#3b82f6; }
            QMenu::separator { background:#334155; height:1px; margin:4px 0; }
        """)
        a_open   = menu.addAction("Open Location")
        a_copy   = menu.addAction("Copy Path")
        menu.addSeparator()
        a_delete = menu.addAction("Delete (Recycle Bin)")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen == a_open:
            self._open(d['path'], d.get('is_dir', True))
        elif chosen == a_copy:
            QApplication.clipboard().setText(d['path'])
        elif chosen == a_delete:
            self._delete_one(d['path'])

    def _open(self, path, is_dir=True):
        if not os.path.exists(path):
            return
        path = os.path.normpath(path)
        try:
            if is_dir:
                os.startfile(path)
            else:
                subprocess.Popen(f'explorer /select,"{path}"')
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Delete
    # -------------------------------------------------------------------------

    def _remove_path_from_db(self, path):
        from src.file_index_tool import FileIndexTool
        tool = FileIndexTool()
        try:
            tool.conn.execute(
                f"DELETE FROM file_index WHERE {case_insensitive_path_sql()}",
                (path,),
            )
            descendant_sql, descendant_params = descendant_like_sql(path)
            tool.conn.execute(
                f"DELETE FROM file_index WHERE {descendant_sql}",
                descendant_params,
            )
            tool.conn.commit()
        finally:
            tool.close()

    def _delete_one(self, path):
        r = QMessageBox.question(self, "Confirm Delete",
            f"Send to Recycle Bin?\n\n{path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if r == QMessageBox.StandardButton.Yes:
            self._start_delete([path])

    def _bulk_delete_paths(self):
        if not self.bulk_delete_scope:
            return []

        from src.file_index_tool import FileIndexTool
        tool = FileIndexTool()
        cursor = tool.conn.cursor()
        try:
            cursor.execute(
                "SELECT path, is_folder FROM file_index" + self.bulk_delete_scope['where_sql'],
                self.bulk_delete_scope['params'],
            )
            rows = cursor.fetchall()

            paths = []
            folder_delete_mode = self.bulk_delete_scope.get('folder_delete_mode', 'empty_only')
            for path, is_folder in rows:
                if not is_folder:
                    paths.append(path)
                elif folder_delete_mode == 'all':
                    paths.append(path)
                elif folder_delete_mode == 'empty_only':
                    cursor.execute(
                        "SELECT 1 FROM file_index WHERE parent_path = ? COLLATE NOCASE LIMIT 1",
                        (path,),
                    )
                    if cursor.fetchone() is None:
                        paths.append(path)
        finally:
            tool.close()
        return self._prune_paths(paths)

    def _short_path_for_progress(self, path):
        return ("..." + path[-96:]) if len(path) > 100 else path

    def _set_delete_controls_enabled(self, enabled):
        if not enabled:
            self.btn_delete.setEnabled(False)
        self.btn_rescan.setEnabled(enabled)
        self.btn_filter.setEnabled(enabled)
        self.controls_bar.setEnabled(enabled)
        self.tree.setEnabled(enabled)

    def _start_delete(self, paths):
        if not paths:
            return

        self._set_delete_controls_enabled(False)
        self.lbl_status.setText(f"Deleting 0 of {len(paths)}...")

        self.delete_progress = DeleteProgressDialog(len(paths), self)

        self.delete_thread = DeleteThread(paths)
        self.delete_thread.delete_progress.connect(self._on_delete_progress)
        self.delete_thread.delete_finished.connect(self._on_delete_finished)
        self.delete_progress.cancel_requested.connect(self.delete_thread.cancel)
        self.delete_thread.start()
        self.delete_progress.show()

    def _on_delete_progress(self, done, total, path):
        if self.delete_progress:
            current = self._short_path_for_progress(path)
            self.delete_progress.update_progress(done, total, current)
        self.lbl_status.setText(f"Deleting {done} of {total}...")

    def _on_delete_finished(self, deleted_count, errors, cancelled, deleted_paths):

        if self.delete_progress:
            self.delete_progress._allow_close = True
            self.delete_progress.hide()
            self.delete_progress.close()
            self.delete_progress = None

        self.delete_thread = None
        self.bulk_delete_scope = None
        if deleted_paths:
            self._remove_deleted_paths_from_selection(deleted_paths)
        self._set_delete_controls_enabled(True)
        self._load_page()

        if cancelled:
            title = "Deletion stopped"
            message = f"Deleted {deleted_count} item(s) before stopping."
        else:
            title = "Deletion complete"
            message = f"Deleted {deleted_count} item(s)."

        if errors:
            message += f"\n\nFailed: {len(errors)}"
            QMessageBox.warning(self, title, message + "\n\n" + "\n".join(errors[:10]))
        else:
            QMessageBox.information(self, title, message)

    def _delete_selected(self):
        if not self.tree_model:
            return
        if self.bulk_delete_scope:
            scope = self.bulk_delete_scope
            paths = self._show_delete_preview(bulk_scope=scope)
            if paths:
                self._start_delete(paths)
            return

        paths = self._selected_roots_for_delete()
        if not paths:
            QMessageBox.information(self, "Delete", "No items selected.")
            return
        preview_paths = self._show_delete_preview(paths=paths)
        if preview_paths:
            self._start_delete(preview_paths)

    # -------------------------------------------------------------------------
    # Export CSV
    # -------------------------------------------------------------------------

    def _export_csv(self):
        if not self.proxy_model or not self.proxy_model.sourceModel():
            QMessageBox.information(self, "Export", "Nothing to export - run a scan first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Report", "", "CSV Files (*.csv)")
        if not path:
            return
        # Columns to exclude from export: 5 = Status
        _SKIP_COLS = {5}
        try:
            with open(path, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow([self.proxy_model.headerData(i, Qt.Orientation.Horizontal)
                            for i in range(self.proxy_model.columnCount())
                            if i not in _SKIP_COLS])
                def write_rows(parent):
                    for r in range(self.proxy_model.rowCount(parent)):
                        row_data = [self.proxy_model.data(self.proxy_model.index(r, c, parent))
                                    for c in range(self.proxy_model.columnCount())
                                    if c not in _SKIP_COLS]
                        w.writerow(row_data)
                        child = self.proxy_model.index(r, 0, parent)
                        if self.proxy_model.hasChildren(child):
                            write_rows(child)
                write_rows(QModelIndex())
            QMessageBox.information(self, "Exported", "CSV report saved.")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))
