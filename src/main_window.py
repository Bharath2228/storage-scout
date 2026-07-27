import os
import csv
import html
import json
import subprocess
import send2trash
import time
from datetime import datetime
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QRadioButton, QSlider, QTreeView, QHeaderView, QGridLayout,
    QMessageBox, QStyledItemDelegate, QButtonGroup, QApplication, QFileDialog,
    QSpinBox, QAbstractItemView, QStackedWidget, QStyleOptionViewItem,
    QMenu, QSizePolicy, QFrame, QStyle, QDialog, QProgressBar,
    QTableWidget, QTableWidgetItem, QComboBox, QScrollArea, QAbstractScrollArea,
    QLayout, QSystemTrayIcon
)
from PyQt6.QtCore import Qt, QRect, QModelIndex, QPersistentModelIndex, QTimer, QEvent, QSignalBlocker, QThread, pyqtSignal, QSize, QSettings
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QIcon, QFont

from .models import WatchdogTreeModel, WatchdogFilterProxyModel, format_size
from .scanner import ScannerThread
from .scan_exclusions import ScanExclusions
from .scan_history import get_scan_history, record_scan_history
from .auth import AuthStore, append_delete_audit
from .theme import (
    FONT_FAMILY,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    SPACE_XL,
    SPACE_XS,
    TYPE_SCALE,
    apply_theme,
    current_palette,
    resolve_theme_name,
    safe_point_size,
)

VIDEO_EXTENSIONS = (
    ".3g2", ".3gp", ".avi", ".divx", ".flv", ".m2ts", ".m4v",
    ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".ogv",
    ".rm", ".rmvb", ".ts", ".vob", ".webm", ".wmv",
)

def paint_tree_row_border(painter, option):
    return

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
        self.setObjectName("modalDialog")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        layout.setSpacing(SPACE_MD)

        self.title_label = QLabel("Moving items to the Recycle Bin")
        self.title_label.setObjectName("modalTitle")
        layout.addWidget(self.title_label)

        self.count_label = QLabel(f"Deleting 0 of {total}")
        self.count_label.setObjectName("modalDetail")
        layout.addWidget(self.count_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, total)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setObjectName("deleteProgressBar")
        layout.addWidget(self.progress)

        self.path_label = QLabel("Preparing deletion...")
        self.path_label.setObjectName("modalSecondary")
        self.path_label.setWordWrap(True)
        self.path_label.setMinimumHeight(36)
        layout.addWidget(self.path_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.cancel_button = QPushButton("Cancel after current item")
        self.cancel_button.setObjectName("modalCancel")
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
        self.setObjectName("modalDialog")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        layout.setSpacing(SPACE_MD)

        title = QLabel(title)
        title.setObjectName("modalTitle")
        layout.addWidget(title)

        detail_label = QLabel(detail)
        detail_label.setObjectName("modalSecondary")
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
        self._connection = None

    def cancel(self):
        self.is_cancelled = True
        connection = self._connection
        if connection is not None:
            try:
                connection.interrupt()
            except Exception:
                pass

    def _raise_if_cancelled(self):
        if self.is_cancelled:
            raise DeletePreviewThread._Cancelled()

    def _path_key(self, path):
        return os.path.normcase(os.path.normpath(path))

    def _prune_paths(self, paths):
        pruned_paths = []
        selected_roots = []
        for path in sorted(paths, key=lambda value: (len(os.path.normpath(value)), value.lower())):
            self._raise_if_cancelled()
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
            self._raise_if_cancelled()
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
                self._raise_if_cancelled()
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
            self._raise_if_cancelled()
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
            self._connection = tool.conn
            self._raise_if_cancelled()
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

            self._raise_if_cancelled()

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
            if not self.is_cancelled:
                self.preview_failed.emit(str(exc))
        finally:
            self._connection = None
            tool.close()


class DeletePreviewDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Delete preview")
        self.setModal(True)
        self.setFixedSize(560, 350)
        self.setObjectName("modalDialog")
        self.preview_paths = []
        self.preview_payload = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        layout.setSpacing(SPACE_MD)

        self.title_label = QLabel("Preparing delete preview")
        self.title_label.setObjectName("modalTitle")
        layout.addWidget(self.title_label)

        self.detail_label = QLabel("Calculating selected items and size...")
        self.detail_label.setObjectName("modalDetail")
        layout.addWidget(self.detail_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setObjectName("deleteProgressBar")
        layout.addWidget(self.progress)

        self.summary_widget = QWidget()
        summary_layout = QGridLayout(self.summary_widget)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.setHorizontalSpacing(SPACE_XL)
        summary_layout.setVerticalSpacing(SPACE_SM)
        summary_layout.setColumnStretch(0, 1)

        matched_label = QLabel("Matched items")
        matched_label.setObjectName("modalSecondary")
        self.matched_value = QLabel("0")
        self.matched_value.setObjectName("modalDetail")
        self.matched_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        summary_layout.addWidget(matched_label, 0, 0)
        summary_layout.addWidget(self.matched_value, 0, 1)

        recycle_count_label = QLabel("Items sent to Recycle Bin")
        recycle_count_label.setObjectName("modalSecondary")
        self.recycle_count_value = QLabel("0")
        self.recycle_count_value.setObjectName("modalTitle")
        self.recycle_count_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        summary_layout.addWidget(recycle_count_label, 1, 0)
        summary_layout.addWidget(self.recycle_count_value, 1, 1)

        folders_label = QLabel("Folders")
        folders_label.setObjectName("modalSecondary")
        self.folders_value = QLabel("0")
        self.folders_value.setObjectName("modalDetail")
        self.folders_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        summary_layout.addWidget(folders_label, 2, 0)
        summary_layout.addWidget(self.folders_value, 2, 1)

        files_label = QLabel("Files")
        files_label.setObjectName("modalSecondary")
        self.files_value = QLabel("0")
        self.files_value.setObjectName("modalDetail")
        self.files_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        summary_layout.addWidget(files_label, 3, 0)
        summary_layout.addWidget(self.files_value, 3, 1)

        size_label = QLabel("Total size")
        size_label.setObjectName("modalSecondary")
        self.size_value = QLabel("0 B")
        self.size_value.setObjectName("modalDetail")
        self.size_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        summary_layout.addWidget(size_label, 4, 0)
        summary_layout.addWidget(self.size_value, 4, 1)

        self.summary_widget.setVisible(False)
        layout.addWidget(self.summary_widget)

        self.preview_note = QLabel("")
        self.preview_note.setObjectName("modalSecondary")
        self.preview_note.setWordWrap(True)
        self.preview_note.setVisible(False)
        layout.addWidget(self.preview_note)

        self.error_label = QLabel("")
        self.error_label.setObjectName("modalSecondary")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        layout.addStretch()

        btn_row = QHBoxLayout()
        self.recycle_bin_note = QLabel(
            "Items will be moved to the Recycle Bin and can be restored."
        )
        self.recycle_bin_note.setObjectName("modalSecondary")
        self.recycle_bin_note.setWordWrap(True)
        self.recycle_bin_note.setMaximumWidth(280)
        btn_row.addWidget(self.recycle_bin_note)
        btn_row.addStretch()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("modalCancel")
        self.cancel_button.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_button)
        self.delete_button = QPushButton("Delete Selected")
        self.delete_button.setObjectName("destructiveBtn")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self.accept)
        btn_row.addWidget(self.delete_button)
        layout.addLayout(btn_row)

    def apply_preview(self, payload):
        data = dict(payload or {})
        self.preview_payload = data
        self.preview_paths = list(data.get('paths', []) or [])
        total = int(data.get('total', len(self.preview_paths)) or 0)
        delete_operations = int(
            data.get('delete_operations', len(self.preview_paths)) or 0
        )
        folders = int(data.get('folders', 0) or 0)
        files = int(data.get('files', 0) or 0)
        size = data.get('size')

        if size is None:
            self.title_label.setText("Preparing delete preview")
            self.detail_label.setText("Calculating selected items and size...")
            self.summary_widget.setVisible(False)
            self.preview_note.setVisible(False)
            self.error_label.setVisible(False)
            self.progress.setRange(0, 0)
            self.progress.setVisible(True)
            self.delete_button.setEnabled(False)
            return

        self.title_label.setText("Review before deleting")
        self.detail_label.setText("Check the deletion summary before continuing.")
        self.matched_value.setText(f"{total:,}")
        self.recycle_count_value.setText(f"{delete_operations:,}")
        self.folders_value.setText(f"{folders:,}")
        self.files_value.setText(f"{files:,}")
        self.size_value.setText("0 B" if size == 0 else format_size(size))
        self.summary_widget.setVisible(True)
        self.error_label.setVisible(False)
        self.progress.setVisible(False)

        show_note = total != delete_operations
        self.preview_note.setText(
            "Non-empty folders outside your filter won't be deleted."
            if show_note
            else ""
        )
        self.preview_note.setVisible(show_note)
        self.delete_button.setEnabled(True)

    def show_error(self, message):
        self.title_label.setText("Could not calculate preview")
        self.detail_label.setText("The delete summary could not be prepared.")
        self.summary_widget.setVisible(False)
        self.preview_note.setVisible(False)
        self.error_label.setText(message)
        self.error_label.setVisible(True)
        self.progress.setVisible(False)
        self.delete_button.setEnabled(False)


class DeleteAuthDialog(QDialog):
    def __init__(self, item_count, total_size=0, parent=None):
        super().__init__(parent)
        self.authorized_username = None
        self.item_count = int(item_count or 0)
        self.total_size = int(total_size or 0)
        self.store = AuthStore()
        self.setWindowTitle("Delete authorization")
        self.setModal(True)
        self.setFixedSize(520, 250)
        self.setObjectName("modalDialog")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        layout.setSpacing(SPACE_MD)

        title = QLabel("Authorization required")
        title.setObjectName("modalTitle")
        layout.addWidget(title)

        size_text = format_size(self.total_size) if self.total_size else "0 B"
        detail = QLabel(
            f"Enter your credentials to permanently delete {self.item_count} "
            f"item{'s' if self.item_count != 1 else ''} ({size_text})."
        )
        detail.setObjectName("modalDetail")
        detail.setWordWrap(True)
        layout.addWidget(detail)

        self.message_label = QLabel("")
        self.message_label.setObjectName("authMessage")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        self.username_input = QLineEdit()
        self.username_input.setObjectName("filterSearch")
        self.username_input.setPlaceholderText("Username")
        self.username_input.setFixedHeight(34)
        layout.addWidget(self.username_input)

        self.password_input = QLineEdit()
        self.password_input.setObjectName("filterSearch")
        self.password_input.setPlaceholderText("Password")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setFixedHeight(34)
        layout.addWidget(self.password_input)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("modalCancel")
        self.cancel_button.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_button)
        self.submit_button = QPushButton("Authorize delete")
        self.submit_button.setObjectName("primaryBtn")
        self.submit_button.clicked.connect(self._submit)
        btn_row.addWidget(self.submit_button)
        layout.addLayout(btn_row)

        self.password_input.returnPressed.connect(self._submit)
        self.username_input.returnPressed.connect(self.password_input.setFocus)
        self.username_input.textChanged.connect(self._update_lockout_state)
        self.cooldown_timer = QTimer(self)
        self.cooldown_timer.setInterval(1000)
        self.cooldown_timer.timeout.connect(self._update_lockout_state)
        self._update_lockout_state()

    def _update_lockout_state(self):
        remaining = self.store.lockout_remaining(self.username_input.text())
        if remaining > 0:
            self.submit_button.setEnabled(False)
            self.message_label.setText(f"Too many failed attempts - try again in {remaining}s.")
            if not self.cooldown_timer.isActive():
                self.cooldown_timer.start()
            return

        self.cooldown_timer.stop()
        self.submit_button.setEnabled(True)
        if self.message_label.text().startswith("Too many failed attempts"):
            self.message_label.setText("")

    def _submit(self):
        if self.store.is_empty():
            QMessageBox.warning(
                self,
                "Delete authorization",
                "No authorized users are configured for this installation. Contact your administrator.",
            )
            return

        self._update_lockout_state()
        if not self.submit_button.isEnabled():
            return

        attempted_username = self.username_input.text()
        username = attempted_username.strip()
        password = self.password_input.text()
        if self.store.verify(attempted_username, password):
            self.authorized_username = self.store.canonical_username(username)
            append_delete_audit(
                "delete_authorized",
                username=self.authorized_username,
                items=self.item_count,
                size=self.total_size,
            )
            self.accept()
            return

        append_delete_audit(
            "delete_denied",
            username="UNKNOWN",
            items=self.item_count,
            size=self.total_size,
            reason="invalid_credentials",
            attempted_username=username or "UNKNOWN",
        )
        self.password_input.clear()
        self.message_label.setText("Invalid username or password.")
        self._update_lockout_state()


class FileTypeBreakdownThread(QThread):
    breakdown_ready = pyqtSignal(int, list)
    breakdown_failed = pyqtSignal(int, str)

    def __init__(self, request_id, limit=20, parent=None):
        super().__init__(parent)
        self.request_id = request_id
        self.limit = limit
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        from src.file_index_tool import FileIndexTool

        tool = FileIndexTool()
        try:
            if self.is_cancelled:
                return
            rows = tool.extension_breakdown(limit=self.limit)
            if not self.is_cancelled:
                self.breakdown_ready.emit(self.request_id, rows)
        except Exception as exc:
            if not self.is_cancelled:
                self.breakdown_failed.emit(self.request_id, str(exc))
        finally:
            tool.close()


class FileTypeBarDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        try:
            share = float(index.data(Qt.ItemDataRole.UserRole) or 0.0)
        except (TypeError, ValueError):
            share = 0.0

        rect = option.rect.adjusted(12, 10, -12, -10)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        palette = current_palette()
        painter.setBrush(QColor(palette["border"]))
        painter.drawRoundedRect(rect, 5, 5)

        if share > 0:
            width = max(3, int(rect.width() * min(1.0, share)))
            bar_rect = QRect(rect.left(), rect.top(), width, rect.height())
            painter.setBrush(QColor(palette["accent"]))
            painter.drawRoundedRect(bar_rect, 5, 5)
        painter.restore()


class FileTypesDialog(QDialog):
    extension_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("File Types")
        self.setModal(False)
        self.resize(620, 520)
        self.setObjectName("modalDialog")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        layout.setSpacing(SPACE_MD)

        title = QLabel("File Types")
        title.setObjectName("modalTitle")
        layout.addWidget(title)

        self.detail_label = QLabel("Calculating file type breakdown...")
        self.detail_label.setObjectName("modalDetail")
        layout.addWidget(self.detail_label)

        self.table = QTableWidget(0, 4)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.setHorizontalHeaderLabels(["Type", "Share", "Size", "Files"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setItemDelegateForColumn(1, FileTypeBarDelegate(self.table))
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 180)
        self.table.setColumnWidth(2, 110)
        self.table.setColumnWidth(3, 90)
        self.table.cellClicked.connect(self._activate_row)
        layout.addWidget(self.table, 1)

        hint = QLabel("Click a file type to show matching files.")
        hint.setObjectName("modalSecondary")
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setObjectName("modalCancel")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def set_loading(self):
        self.detail_label.setText("Calculating file type breakdown...")
        self.table.setRowCount(0)

    def set_error(self, message):
        self.detail_label.setText(f"Could not load file types: {message}")
        self.table.setRowCount(0)

    def set_rows(self, rows):
        total_size = sum(size for _label, size, _count in rows) or 0
        total_files = sum(count for _label, _size, count in rows) or 0
        self.detail_label.setText(
            f"{len(rows)} groups · {format_size(total_size)} · {total_files:,} files"
        )
        self.table.setRowCount(len(rows))
        for row, (label, total_size_for_type, file_count) in enumerate(rows):
            raw_extension = None
            if label == "(no extension)":
                raw_extension = ""
            elif label != "Other":
                raw_extension = label

            type_item = QTableWidgetItem(label)
            type_item.setData(Qt.ItemDataRole.UserRole, raw_extension)
            self.table.setItem(row, 0, type_item)

            share_item = QTableWidgetItem("")
            share_item.setData(
                Qt.ItemDataRole.UserRole,
                (total_size_for_type / total_size) if total_size else 0.0,
            )
            self.table.setItem(row, 1, share_item)

            self.table.setItem(row, 2, QTableWidgetItem(format_size(total_size_for_type)))
            self.table.setItem(row, 3, QTableWidgetItem(f"{file_count:,}"))
        self.table.resizeRowsToContents()

    def _activate_row(self, row, _column):
        item = self.table.item(row, 0)
        if item is None:
            return
        raw_extension = item.data(Qt.ItemDataRole.UserRole)
        if raw_extension is None:
            return
        self.extension_selected.emit(raw_extension)
        self.accept()


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
                    return "Root directory"
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
        name_filter = (self.options.get('name_filter') or '').strip()
        extension_filter = self.options.get('extension_filter')
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

        if name_filter:
            where_clauses.append("name LIKE ? ESCAPE '\\' COLLATE NOCASE")
            params.append(f"%{escape_sql_like(name_filter)}%")

        if extension_filter is not None:
            where_clauses.append("is_folder = 0")
            where_clauses.append("extension = ? COLLATE NOCASE")
            params.append(extension_filter)

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

        if name_filter and view_mode == 'Tree':
            for node in nodes_by_path.values():
                if node.get('children'):
                    node['_children_loaded'] = True

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
            'name_filter': name_filter,
            'extension_filter': extension_filter,
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
        if cached_children:
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
                'debug_info': f"show_all_source=cache children={len(cached_children)} total={total_matches}",
            }

        tool = FileIndexTool()
        try:
            cursor = tool.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM file_index")
            total_rows = cursor.fetchone()[0] or 0

            cursor.execute(
                """
                SELECT CASE
                           WHEN path = ? COLLATE NOCASE THEN path
                           ELSE root
                       END
                FROM file_index
                WHERE path = ? COLLATE NOCASE OR root = ? COLLATE NOCASE
                ORDER BY CASE WHEN path = ? COLLATE NOCASE THEN 0 ELSE 1 END,
                         length(path) ASC
                LIMIT 1
                """,
                (root_path, root_path, root_path, root_path),
            )
            stored_root_row = cursor.fetchone()
            if stored_root_row:
                stored_root_path = stored_root_row[0]
            else:
                cursor.execute(
                    """
                    SELECT root
                    FROM file_index
                    GROUP BY root
                    ORDER BY COUNT(*) DESC, length(root) ASC
                    LIMIT 1
                    """
                )
                fallback_root_row = cursor.fetchone()
                stored_root_path = fallback_root_row[0] if fallback_root_row else root_path

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
            load_source = "parent_path"
            if not rows and total_matches:
                rows = self._load_direct_children_from_root_index(cursor, stored_root_path)
                load_source = "root_index_direct"
            if not rows and total_matches:
                rows = self._load_direct_children_by_path(cursor, stored_root_path)
                load_source = "path_prefix_direct"
            if not rows and total_matches:
                rows = self._load_indexed_rows_for_root(cursor, stored_root_path)
                load_source = "root_index_all"
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
            'debug_info': f"show_all_source={load_source} rows={len(rows)} total={total_matches} root={stored_root_path}",
        }

    def _is_direct_child_path(self, root_path, child_path):
        root = os.path.normpath(str(root_path or "")).rstrip("\\/")
        child = os.path.normpath(str(child_path or "")).rstrip("\\/")
        if not root or not child:
            return False
        if os.path.normcase(root) == os.path.normcase(child):
            return False

        try:
            relative = os.path.relpath(child, root)
        except ValueError:
            relative = ""

        if relative and relative != "." and not relative.startswith(".."):
            return "\\" not in relative and "/" not in relative

        root_key = os.path.normcase(root)
        child_key = os.path.normcase(child)
        for separator in ("\\", "/"):
            prefix = root_key + separator
            if child_key.startswith(prefix):
                remainder = child_key[len(prefix):]
                return "\\" not in remainder and "/" not in remainder
        return False

    def _load_direct_children_from_root_index(self, cursor, root_path):
        cursor.execute(
            """
            SELECT f.path, f.name, f.is_folder, f.size, f.modified_time, f.parent_path,
                   (SELECT 1 FROM file_index child WHERE child.parent_path = f.path COLLATE NOCASE LIMIT 1)
            FROM file_index f
            WHERE f.root = ? COLLATE NOCASE
              AND f.path != ? COLLATE NOCASE
            """,
            (root_path, root_path),
        )

        rows = []
        seen = set()
        for row in cursor.fetchall():
            path = row[0]
            if not self._is_direct_child_path(root_path, path):
                continue
            key = os.path.normcase(os.path.normpath(path))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)

        return self._sort_show_all_rows(rows)

    def _load_indexed_rows_for_root(self, cursor, root_path):
        cursor.execute(
            """
            SELECT f.path, f.name, f.is_folder, f.size, f.modified_time, f.parent_path,
                   (SELECT 1 FROM file_index child WHERE child.parent_path = f.path COLLATE NOCASE LIMIT 1)
            FROM file_index f
            WHERE f.root = ? COLLATE NOCASE
              AND f.path != ? COLLATE NOCASE
            """,
            (root_path, root_path),
        )
        return self._sort_show_all_rows(cursor.fetchall())

    def _load_direct_children_by_path(self, cursor, root_path):
        rows = []
        seen = set()
        base = str(root_path).rstrip("\\/")

        for separator in ("\\", "/"):
            prefix = base + separator
            pattern = escape_sql_like(prefix) + "%"
            relative_start = len(prefix) + 1
            cursor.execute(
                """
                SELECT f.path, f.name, f.is_folder, f.size, f.modified_time, f.parent_path,
                       (SELECT 1 FROM file_index child WHERE child.parent_path = f.path COLLATE NOCASE LIMIT 1)
                FROM file_index f
                WHERE f.path LIKE ? ESCAPE '\\'
                  AND instr(substr(f.path, ?), '\\') = 0
                  AND instr(substr(f.path, ?), '/') = 0
                """,
                (pattern, relative_start, relative_start),
            )
            for row in cursor.fetchall():
                key = os.path.normcase(os.path.normpath(row[0]))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)

        return self._sort_show_all_rows(rows)

    def _sort_show_all_rows(self, rows):
        rows.sort(key=lambda row: (
            tree_sort_value(
                {
                    'path': row[0],
                    'name': row[1],
                    'is_dir': bool(row[2]),
                    'size': row[3] or 0,
                    'last_modified': row[4],
                    'status': 'Active',
                },
                self.options['sort_column'],
            ),
            (row[1] or '').lower(),
            (row[0] or '').lower(),
        ), reverse=self.options['sort_desc'])
        return rows


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


class TreeRowDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.backgroundBrush = QBrush(Qt.BrushStyle.NoBrush)
        super().paint(painter, opt, index)
        paint_tree_row_border(painter, option)

class StatusDelegate(QStyledItemDelegate):
    def _colors_for_status(self, status):
        palette = current_palette()
        mapping = {
            'Empty': (
                palette["status_danger"],
                palette["status_danger_bg"],
            ),
            'Inactive': (
                palette["status_warning"],
                palette["status_warning_bg"],
            ),
            'Pending': (
                palette["status_warning"],
                palette["status_warning_bg"],
            ),
            'Active': (
                palette["status_success"],
                palette["status_success_bg"],
            ),
            'Context': (
                palette["accent"],
                palette["accent_tint"],
            ),
        }
        return tuple(QColor(value) for value in mapping.get(
            status,
            (palette["text_muted"], palette["surface"]),
        ))

    def paint(self, painter, option, index):
        status = index.data(Qt.ItemDataRole.DisplayRole)
        if not status:
            paint_tree_row_border(painter, option)
            return
        text_color, bg_color = self._colors_for_status(status)
        rect = option.rect
        pill = QRect(rect.left() + (rect.width() - 64) // 2,
                     rect.top() + (rect.height() - 20) // 2, 64, 20)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(bg_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(pill, 10, 10)
        
        f = QFont(FONT_FAMILY)
        f.setPointSize(safe_point_size(TYPE_SCALE["muted"]["point_size"], 8))
        f.setWeight(QFont.Weight(TYPE_SCALE["body"]["weight"]))


        painter.setFont(f)
        painter.setPen(text_color)
        
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, status)
        painter.restore()
        paint_tree_row_border(painter, option)


class SizeBarDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)

    def paint(self, painter, option, index):
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.backgroundBrush = QBrush(Qt.BrushStyle.NoBrush)
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
        paint_tree_row_border(painter, option)

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
        palette = current_palette()
        
        if "queued" in text:
            # Draw red badge for queued items
            painter.setBrush(QBrush(QColor(palette["status_danger_bg"])))
            painter.setPen(QPen(QColor(palette["status_danger"]), 1))
            painter.drawRoundedRect(btn, 10, 10) # Rounded capsule
            f = QFont(FONT_FAMILY)
            f.setPointSize(safe_point_size(TYPE_SCALE["muted"]["point_size"], 8))
            f.setWeight(QFont.Weight(TYPE_SCALE["body"]["weight"]))


            painter.setFont(f)
            painter.setPen(QColor(palette["status_danger"]))
            painter.drawText(btn, Qt.AlignmentFlag.AlignCenter, "X Queued")
        else:
            if not is_hovered:
                painter.restore()
                return
            # Modern Small Outline Button for "Open"
            bg = QColor(palette["accent_tint"])
            painter.setBrush(QBrush(bg))
            painter.setPen(QPen(QColor(palette["accent"]), 1.2))
            painter.drawRoundedRect(btn, 5, 5)
            f = QFont(FONT_FAMILY)
            f.setPointSize(safe_point_size(TYPE_SCALE["muted"]["point_size"], 8))
            f.setWeight(QFont.Weight(TYPE_SCALE["body"]["weight"]))
            painter.setFont(f)
            painter.setPen(QColor(palette["accent"]))
            painter.drawText(btn, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

# ---------------------------------------------------------------------------
# Filter Panel (standalone widget)
# ---------------------------------------------------------------------------

class AccordionHeader(QFrame):
    clicked = pyqtSignal()

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setObjectName("accordionHeader")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_SM)

        self.chevron = QLabel()
        self.chevron.setObjectName("accordionChevron")
        self.chevron.setFixedWidth(18)
        self.chevron.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title = QLabel(text)
        self.title.setObjectName("accordionTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(self.chevron)
        layout.addWidget(self.title, 1)

    def set_expanded(self, expanded):
        self.chevron.setText("▾" if expanded else "▸")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class FilterPanel(QFrame):
    searchCleared = pyqtSignal()
    exclusionChanged = pyqtSignal()

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
        self.settings = QSettings("IBMS", "Watchdog")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("filterScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        scroll.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        scroll_content = QWidget()
        self.filter_scroll_content = scroll_content
        scroll_content.setObjectName("filterScrollContent")
        scroll_content.setMinimumHeight(0)
        scroll_content.setFixedWidth(self.width())
        scroll_content.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        body = QVBoxLayout(scroll_content)
        body.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        body.setContentsMargins(0, SPACE_LG, 0, SPACE_MD)
        body.setSpacing(0)
        scroll.setWidget(scroll_content)

        search_section = QWidget()
        search_section.setObjectName("searchSection")
        search_layout = QVBoxLayout(search_section)
        search_layout.setContentsMargins(SPACE_LG, SPACE_MD, SPACE_LG, SPACE_MD)
        search_layout.setSpacing(SPACE_MD)

        lbl_search = QLabel("SEARCH")
        lbl_search.setObjectName("searchHeader")
        search_layout.addWidget(lbl_search)

        self.txt_search = QLineEdit()
        self.txt_search.setObjectName("filterSearch")
        self.txt_search.setPlaceholderText("Search files and folders...")
        self.txt_search.setToolTip("Filter the current results by file or folder name")
        self.txt_search.setFixedHeight(36)
        clear_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCloseButton)
        self.search_clear_action = self.txt_search.addAction(
            clear_icon,
            QLineEdit.ActionPosition.TrailingPosition,
        )
        self.search_clear_action.setToolTip("Clear search")
        self.search_clear_action.setVisible(False)
        self.search_clear_action.triggered.connect(self.clear_search_text)
        self.txt_search.textChanged.connect(
            lambda text: self.search_clear_action.setVisible(bool(text))
        )
        self.txt_search.installEventFilter(self)
        search_layout.addWidget(self.txt_search)

        body.addWidget(search_section)
        body.addSpacing(SPACE_XL)

        self._age_section_expanded = False
        self._exclusions_section_expanded = False

        # Section 1: Display Mode
        display_section = QWidget()
        display_section.setObjectName("displayModeSection")
        display_layout = QVBoxLayout(display_section)
        display_layout.setContentsMargins(SPACE_LG, SPACE_MD, SPACE_LG, SPACE_MD)
        display_layout.setSpacing(SPACE_SM)

        lbl_display = QLabel("DISPLAY MODE")
        lbl_display.setObjectName("displayModeHeader")

        display_layout.addWidget(lbl_display)

        self.rb_all      = QRadioButton("Show all")
        self.rb_inactive = QRadioButton("Inactive only")
        self.rb_empty    = QRadioButton("Empty only")
        self.rb_videos   = QRadioButton("Videos only")
        self.rb_all.setChecked(True)
        self.bg = QButtonGroup()
        segment_width = (self.width() - (2 * SPACE_LG) - SPACE_SM) // 2
        display_grid = QGridLayout()
        display_grid.setContentsMargins(0, 0, 0, 0)
        display_grid.setSpacing(SPACE_SM)
        display_grid.setColumnStretch(0, 1)
        display_grid.setColumnStretch(1, 1)
        for rb in [self.rb_all, self.rb_inactive, self.rb_empty, self.rb_videos]:
            self.bg.addButton(rb)
            rb.setFixedWidth(segment_width)
            rb.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            rb.setProperty("segment", True)
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
        display_grid.addWidget(self.rb_all, 0, 0)
        display_grid.addWidget(self.rb_inactive, 0, 1)
        display_grid.addWidget(self.rb_empty, 1, 0)
        display_grid.addWidget(self.rb_videos, 1, 1)
        display_layout.addLayout(display_grid)
        
        body.addWidget(display_section)
        body.addSpacing(SPACE_XL)

        # Section 1.5: View Mode
        view_section = QWidget()
        view_section.setObjectName("viewModeSection")
        view_layout = QVBoxLayout(view_section)
        view_layout.setContentsMargins(SPACE_LG, SPACE_MD, SPACE_LG, SPACE_MD)
        view_layout.setSpacing(SPACE_SM)

        lbl_view = QLabel("VIEW MODE")
        lbl_view.setObjectName("viewModeHeader")
        view_layout.addWidget(lbl_view)

        self.rb_view_tree    = QRadioButton("Tree view")
        self.rb_view_files   = QRadioButton("Files only")
        self.rb_view_folders = QRadioButton("Folders only")
        self.rb_view_tree.setChecked(True)
        
        self.bg_view = QButtonGroup()
        view_grid = QGridLayout()
        view_grid.setContentsMargins(0, 0, 0, 0)
        view_grid.setSpacing(SPACE_SM)
        view_grid.setColumnStretch(0, 1)
        view_grid.setColumnStretch(1, 1)
        for rb in [self.rb_view_tree, self.rb_view_files, self.rb_view_folders]:
            self.bg_view.addButton(rb)
            rb.setFixedWidth(segment_width)
            rb.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            rb.setProperty("segment", True)
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
        view_grid.addWidget(self.rb_view_tree, 0, 0)
        view_grid.addWidget(self.rb_view_files, 0, 1)
        view_grid.addWidget(self.rb_view_folders, 1, 0)
        view_layout.addLayout(view_grid)
            
        body.addWidget(view_section)
        body.addSpacing(SPACE_XL)



        # Section 2: Date Range
        
        

        # Section 3: Stale Threshold
        age_section = QWidget()
        age_section.setObjectName("ageThresholdSection")
        age_outer_layout = QVBoxLayout(age_section)
        age_outer_layout.setContentsMargins(SPACE_LG, SPACE_MD, SPACE_LG, SPACE_MD)
        age_outer_layout.setSpacing(SPACE_MD)

        self.btn_age_toggle = self._make_accordion_header("AGE THRESHOLD")
        self.btn_age_toggle.clicked.connect(lambda: self._toggle_collapsible_section("age"))
        age_outer_layout.addWidget(self.btn_age_toggle)

        accordion_content_width = self.width() - (2 * SPACE_LG)
        self.age_box = QFrame()
        self.age_box.setObjectName("ageControl")
        self.age_box.setFixedWidth(accordion_content_width)
        age_layout = QVBoxLayout(self.age_box)
        age_layout.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)
        age_layout.setSpacing(SPACE_SM)

        age_hdr = QHBoxLayout()
        age_hdr.setSpacing(SPACE_SM)
        self.lbl_pill = QLabel()
        self.lbl_pill.setObjectName("agePill")
        self.lbl_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_pill.setFixedHeight(20)
        self.lbl_val = QLabel()
        self.lbl_val.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_val.setWordWrap(True)
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
        bot_row.setSpacing(SPACE_SM)
        lbl_manual = QLabel("Months")
        lbl_manual.setObjectName("manualLabel")

        bot_row.addWidget(lbl_manual)
        bot_row.addWidget(self.age_input)
        bot_row.addStretch()
        age_layout.addLayout(bot_row)
        age_outer_layout.addWidget(self.age_box)
        
        body.addWidget(age_section)
        body.addSpacing(SPACE_XL)

        exclusions_section = QWidget()
        exclusions_section.setObjectName("exclusionsSection")
        exclusions_outer_layout = QVBoxLayout(exclusions_section)
        exclusions_outer_layout.setContentsMargins(SPACE_LG, SPACE_MD, SPACE_LG, SPACE_MD)
        exclusions_outer_layout.setSpacing(SPACE_MD)

        self.btn_exclusions_toggle = self._make_accordion_header("EXCLUSIONS")
        self.btn_exclusions_toggle.clicked.connect(lambda: self._toggle_collapsible_section("exclusions"))
        exclusions_outer_layout.addWidget(self.btn_exclusions_toggle)

        self.exclusions_box = QWidget()
        self.exclusions_box.setFixedWidth(accordion_content_width)
        exclusions_layout = QVBoxLayout(self.exclusions_box)
        exclusions_layout.setContentsMargins(0, SPACE_MD, 0, SPACE_MD)
        exclusions_layout.setSpacing(SPACE_MD)

        lbl_folders = QLabel("Folders")
        lbl_folders.setObjectName("manualLabel")
        exclusions_layout.addWidget(lbl_folders)

        self.txt_excluded_folders = QLineEdit()
        self.txt_excluded_folders.setObjectName("scanExclusionInput")
        self.txt_excluded_folders.setPlaceholderText("node_modules, *.git*, Temp")
        self.txt_excluded_folders.setToolTip("Folder names or simple patterns to skip on the next scan")
        self.txt_excluded_folders.setFixedHeight(32)
        exclusions_layout.addWidget(self.txt_excluded_folders)

        lbl_extensions = QLabel("Extensions")
        lbl_extensions.setObjectName("manualLabel")
        exclusions_layout.addWidget(lbl_extensions)

        self.txt_excluded_extensions = QLineEdit()
        self.txt_excluded_extensions.setObjectName("scanExclusionInput")
        self.txt_excluded_extensions.setPlaceholderText(".tmp, .log, .iso")
        self.txt_excluded_extensions.setToolTip("File extensions to skip on the next scan")
        self.txt_excluded_extensions.setFixedHeight(32)
        exclusions_layout.addWidget(self.txt_excluded_extensions)

        size_row = QHBoxLayout()
        size_row.setSpacing(SPACE_SM)
        lbl_min_size = QLabel("Ignore under:")
        lbl_min_size.setObjectName("manualLabel")
        self.min_size_input = QSpinBox()
        self.min_size_input.setObjectName("scanExclusionSize")
        self.min_size_input.setRange(0, 999999)
        self.min_size_input.setValue(0)
        self.min_size_input.setFixedWidth(86)
        self.min_size_input.setCursor(Qt.CursorShape.PointingHandCursor)
        self.min_size_unit = QComboBox()
        self.min_size_unit.setObjectName("scanExclusionUnit")
        self.min_size_unit.addItems(["KB", "MB", "GB"])
        self.min_size_unit.setFixedWidth(72)
        self.min_size_unit.setCursor(Qt.CursorShape.PointingHandCursor)
        size_row.addWidget(lbl_min_size)
        size_row.addStretch()
        size_row.addWidget(self.min_size_input)
        size_row.addWidget(self.min_size_unit)
        exclusions_layout.addLayout(size_row)

        self.lbl_exclusions_hint = QLabel("Exclusion changes apply on next scan - click Re-scan to apply.")
        self.lbl_exclusions_hint.setObjectName("manualLabel")
        self.lbl_exclusions_hint.setWordWrap(True)
        self.lbl_exclusions_hint.setVisible(False)
        exclusions_layout.addWidget(self.lbl_exclusions_hint)

        self.btn_reset_exclusions = QPushButton("Reset exclusions")
        self.btn_reset_exclusions.setObjectName("resetExclusions")
        self.btn_reset_exclusions.setToolTip("Restore the default scan exclusions")
        self.btn_reset_exclusions.setCursor(Qt.CursorShape.PointingHandCursor)
        exclusions_layout.addWidget(self.btn_reset_exclusions, alignment=Qt.AlignmentFlag.AlignLeft)

        exclusions_outer_layout.addWidget(self.exclusions_box)
        body.addWidget(exclusions_section)

        self._update_age_label(self.slider.value())
        self._restore_scan_exclusions()
        self._age_section_expanded = self.age_input.value() != self.AGE_FILTER_DISABLED
        self._exclusions_section_expanded = self._scan_exclusions_from_controls().differs_from_default()
        self._sync_collapsible_sections()
        self.txt_excluded_folders.editingFinished.connect(self._normalize_and_save_scan_exclusions)
        self.txt_excluded_extensions.editingFinished.connect(self._normalize_and_save_scan_exclusions)
        self.min_size_input.valueChanged.connect(self._save_scan_exclusions_from_controls)
        self.min_size_unit.currentTextChanged.connect(self._save_scan_exclusions_from_controls)
        self.btn_reset_exclusions.clicked.connect(self.reset_scan_exclusions_to_defaults)
        body.addStretch()
        outer.addWidget(scroll, 1)

        # Section 4: Actions
        action_box = QWidget()
        action_box.setObjectName("sidebarActionBox")
        action_layout = QVBoxLayout(action_box)
        action_layout.setContentsMargins(SPACE_LG, SPACE_MD, SPACE_LG, SPACE_LG)
        action_layout.setSpacing(SPACE_SM)
        
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

    def _make_accordion_header(self, text):
        return AccordionHeader(text, self)

    def _toggle_collapsible_section(self, section):
        if section == "age":
            self._age_section_expanded = not self._age_section_expanded
        elif section == "exclusions":
            self._exclusions_section_expanded = not self._exclusions_section_expanded
        self._sync_collapsible_sections()

    def _sync_collapsible_sections(self):
        if hasattr(self, 'age_box'):
            self.age_box.setVisible(self._age_section_expanded)
            self.age_box.updateGeometry()
        if hasattr(self, 'btn_age_toggle'):
            self.btn_age_toggle.set_expanded(self._age_section_expanded)
            self.btn_age_toggle.updateGeometry()
        if hasattr(self, 'exclusions_box'):
            self.exclusions_box.setVisible(self._exclusions_section_expanded)
            self.exclusions_box.updateGeometry()
        if hasattr(self, 'btn_exclusions_toggle'):
            self.btn_exclusions_toggle.set_expanded(self._exclusions_section_expanded)
            self.btn_exclusions_toggle.updateGeometry()
        if hasattr(self, 'filter_scroll_content'):
            self.filter_scroll_content.setFixedWidth(self.width())
            layout = self.filter_scroll_content.layout()
            if layout is not None:
                layout.invalidate()
                layout.activate()
            self.filter_scroll_content.setFixedWidth(self.width())
            self.filter_scroll_content.updateGeometry()
        self.updateGeometry()

    def _update_age_label(self, value):
        palette = current_palette()
        if value == self.AGE_FILTER_DISABLED:
            self.lbl_pill.setText("Off")
            self.lbl_pill.setStyleSheet(f"background-color: {palette['text_muted']}; color: white;")
            self.lbl_val.setText("Age filtering is disabled")
        else:
            self.lbl_pill.setText(f"{value}m")
            self.lbl_pill.setStyleSheet(f"background-color: {palette['accent']}; color: white;")
            
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

    def get_scan_exclusions(self):
        return self._scan_exclusions_from_controls()

    def show_exclusions_rescan_hint(self, visible=True):
        self.lbl_exclusions_hint.setVisible(visible)

    def reset_scan_exclusions_to_defaults(self):
        self._set_scan_exclusions_controls(ScanExclusions())
        self._save_scan_exclusions_from_controls()

    def _restore_scan_exclusions(self):
        raw_value = self.settings.value("scan_exclusions", "")
        try:
            data = json.loads(raw_value) if raw_value else {}
        except (TypeError, ValueError):
            data = {}
        self._set_scan_exclusions_controls(ScanExclusions.from_dict(data))

    def _save_scan_exclusions_from_controls(self):
        exclusions = self._scan_exclusions_from_controls()
        self.settings.setValue("scan_exclusions", json.dumps(exclusions.to_dict()))
        self.exclusionChanged.emit()

    def _normalize_and_save_scan_exclusions(self):
        exclusions = self._scan_exclusions_from_controls()
        self._set_scan_exclusions_controls(exclusions)
        self._save_scan_exclusions_from_controls()

    def _scan_exclusions_from_controls(self):
        folders = [
            item.strip()
            for item in self.txt_excluded_folders.text().split(",")
            if item.strip()
        ]
        extensions = [
            item.strip()
            for item in self.txt_excluded_extensions.text().split(",")
            if item.strip()
        ]
        min_value = self.min_size_input.value()
        unit = self.min_size_unit.currentText()
        multiplier = {"KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}.get(unit, 1024)
        return ScanExclusions(
            folder_names=folders,
            extensions=extensions,
            min_file_size_bytes=min_value * multiplier if min_value > 0 else 0,
        )

    def _set_scan_exclusions_controls(self, exclusions):
        blockers = [
            QSignalBlocker(self.txt_excluded_folders),
            QSignalBlocker(self.txt_excluded_extensions),
            QSignalBlocker(self.min_size_input),
            QSignalBlocker(self.min_size_unit),
        ]
        try:
            exclusions = exclusions or ScanExclusions()
            data = exclusions.to_dict()
            self.txt_excluded_folders.setText(", ".join(data["folder_names"]))
            self.txt_excluded_extensions.setText(", ".join(data["extensions"]))
            size = data["min_file_size_bytes"]
            if size <= 0:
                self.min_size_input.setValue(0)
                self.min_size_unit.setCurrentText("MB")
            else:
                for unit, multiplier in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
                    if size % multiplier == 0:
                        self.min_size_input.setValue(size // multiplier)
                        self.min_size_unit.setCurrentText(unit)
                        break
                else:
                    self.min_size_input.setValue(max(1, size // 1024))
                    self.min_size_unit.setCurrentText("KB")
        finally:
            del blockers

    def apply_default_browse_preset(self):
        self.rb_all.setChecked(True)
        self.rb_view_tree.setChecked(True)
        self.slider.setValue(self.AGE_FILTER_DISABLED)
        
        blocker = QSignalBlocker(self.age_input)
        self.age_input.setValue(self.AGE_FILTER_DISABLED)
        del blocker
        
        self._update_age_label(self.AGE_FILTER_DISABLED)

    def eventFilter(self, obj, event):
        if (
            obj == self.txt_search
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
        ):
            self.clear_search_text()
            return True
        return super().eventFilter(obj, event)

    def clear_search_text(self):
        self.txt_search.clear()
        self.searchCleared.emit()

    # helpers
    def _make_section(self, title):
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(SPACE_MD)
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
        self.is_scanning = False
        self.folder_cache = None
        self.saved_age_threshold_value = 0
        self.age_controls_forced_disabled = False
        self.loading_dialog = None
        self.selection_loading_dialog = None
        self.selection_loading_min_visible_until = 0.0
        self.delete_preview_thread = None
        self.pending_delete_preview_dialog = None
        self.file_type_thread = None
        self.file_type_request_id = 0
        self.file_types_dialog = None
        self.active_extension_filter = None
        self.settings = QSettings("IBMS", "Watchdog")
        self.current_theme_name = resolve_theme_name(self.settings.value("theme", "light"))
        self.notifications_enabled = self.settings.value(
            "notifications_enabled",
            True,
            type=bool,
        )
        self.tray_icon = None
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
        self._preserve_results_focus = False
        self.applied_name_filter = ""
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
        self.last_scan_excluded_count = 0
        self.last_scan_elapsed_secs = 0.0
        self.last_scan_item_count = 0
        self.scan_progress_was_determinate = False

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

        self.search_debounce_timer = QTimer(self)
        self.search_debounce_timer.setSingleShot(True)
        self.search_debounce_timer.setInterval(275)
        self.search_debounce_timer.timeout.connect(self._on_filter_changed)

        self.loading_timer = QTimer(self)
        self.loading_timer.setSingleShot(True)
        self.loading_timer.setInterval(180)
        self.loading_timer.timeout.connect(self._show_loading_dialog)

        self.scan_refresh_timer = QTimer(self)
        self.scan_refresh_timer.setSingleShot(True)
        self.scan_refresh_timer.setInterval(1200)
        self.scan_refresh_timer.timeout.connect(self._refresh_pagination_only)

        self._build_ui()
        self._setup_tray_icon()
        QApplication.instance().installEventFilter(self)
        self.tree.header().sectionClicked.connect(self._on_header_sort_clicked)
        self._apply_sort_indicator()

    # -------------------------------------------------------------------------
    # UI
    # -------------------------------------------------------------------------

    def _setup_tray_icon(self):
        self.tray_icon = None
        try:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                return
            icon_path = os.path.join(
                os.path.dirname(__file__),
                "assets",
                "folder_blue.svg",
            )
            icon = QIcon(icon_path)
            if icon.isNull():
                return
            self.tray_icon = QSystemTrayIcon(icon, self)
            self.tray_icon.setToolTip("IBMS Folder Watchdog")
            self.tray_icon.show()
        except Exception:
            self.tray_icon = None

    def _should_show_completion_notification(self, elapsed_secs=None, cancelled=False):
        if cancelled or not getattr(self, 'notifications_enabled', True):
            return False
        if elapsed_secs is not None and elapsed_secs > 8:
            return True
        return not self.isActiveWindow()

    def _show_system_notification(self, title, message):
        tray_icon = getattr(self, 'tray_icon', None)
        if not getattr(self, 'notifications_enabled', True) or tray_icon is None:
            return False
        try:
            if not tray_icon.isVisible():
                return False
            tray_icon.showMessage(
                title,
                message,
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )
            return True
        except Exception:
            return False

    def _placeholder_icon(self, asset_name, muted=False):
        label = QLabel()
        label.setObjectName("emptyIcon")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_path = os.path.join(os.path.dirname(__file__), "assets", asset_name)
        pixmap = QIcon(icon_path).pixmap(QSize(56, 56))
        if muted:
            painter = QPainter(pixmap)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(pixmap.rect(), QColor(current_palette()["text_muted"]))
            painter.end()
        label.setPixmap(pixmap)
        label.setFixedSize(80, 80)
        return label

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
        tb.setContentsMargins(SPACE_LG, SPACE_MD, SPACE_LG, SPACE_MD)
        tb.setSpacing(SPACE_MD)

        lbl = QLabel("IBMS Watchdog")
        lbl.setObjectName("appTitle")
        tb.addWidget(lbl)

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

        self.btn_filter = QPushButton("Filters")
        self.btn_filter.setObjectName("ghostBtn")
        self.btn_filter.setCheckable(True)
        self.btn_filter.setToolTip("Toggle filter sidebar (Alt+F)")
        self.btn_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_filter.clicked.connect(self._toggle_filters)
        tb.addWidget(self.btn_filter)

        self.btn_file_types = QPushButton("Types")
        self.btn_file_types.setObjectName("ghostBtn")
        self.btn_file_types.setToolTip("File Types: show disk usage by extension")
        self.btn_file_types.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_file_types.setEnabled(False)
        self.btn_file_types.clicked.connect(self._show_file_types)
        tb.addWidget(self.btn_file_types)

        self.theme_selector = QComboBox()
        self.theme_selector.setObjectName("themeSelector")
        self.theme_selector.addItem("Light", "light")
        self.theme_selector.addItem("Dark", "dark")
        self.theme_selector.setToolTip("Switch theme")
        self.theme_selector.setCursor(Qt.CursorShape.PointingHandCursor)
        theme_index = self.theme_selector.findData(self.current_theme_name)
        self.theme_selector.setCurrentIndex(max(0, theme_index))
        self.theme_selector.currentIndexChanged.connect(self._on_theme_changed)
        tb.addWidget(self.theme_selector)

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
        self._set_delete_armed(False)
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
        self.fp.txt_search.returnPressed.connect(self._on_search_submitted)
        self.fp.searchCleared.connect(self._clear_search_filter)
        # Age controls: wait for the user to pause before applying.
        self.fp.slider.valueChanged.connect(self._on_age_slider_changed)
        self.fp.slider.sliderReleased.connect(self._on_age_slider_released)
        self.fp.age_input.editingFinished.connect(self._on_manual_age_finished)
        self.fp.exclusionChanged.connect(self._on_scan_exclusions_changed)
        
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
        right_v.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        right_v.setSpacing(SPACE_LG)

        # Controls row (above tree): expand / select all
        self.controls_bar = QWidget()
        controls_layout = QHBoxLayout(self.controls_bar)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(SPACE_MD)
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
        self.content_stack.setMinimumHeight(0)
        self.content_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Page 0: Empty state
        empty_page = QWidget()
        empty_page.setObjectName("emptyState")
        ep_layout = QVBoxLayout(empty_page)
        ep_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ep_layout.setSpacing(SPACE_LG)

        empty_icon = self._placeholder_icon("folder_blue.svg")

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
        ep_layout.addWidget(empty_icon, alignment=Qt.AlignmentFlag.AlignCenter)
        ep_layout.addWidget(title_lbl)
        ep_layout.addWidget(sub_lbl, alignment=Qt.AlignmentFlag.AlignCenter)
        ep_layout.addSpacing(SPACE_SM)
        ep_layout.addWidget(btn_browse_cta, alignment=Qt.AlignmentFlag.AlignCenter)
        ep_layout.addStretch()

        # Page 1: Scanning state
        scanning_page = QWidget()
        scanning_page.setObjectName("emptyState")
        sp_layout = QVBoxLayout(scanning_page)
        sp_layout.setContentsMargins(56, 56, 56, 56)
        sp_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sp_layout.setSpacing(0)

        scan_content = QWidget()
        scan_content.setObjectName("scanStateContent")
        scan_content.setMaximumWidth(720)
        scan_content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        scan_layout = QVBoxLayout(scan_content)
        scan_layout.setContentsMargins(0, 0, 0, 0)
        scan_layout.setSpacing(14)

        scan_icon = self._placeholder_icon("toolbar_refresh.svg", muted=True)

        sp_title = QLabel("Scanning in progress")
        sp_title.setObjectName("emptyTitle")
        sp_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.scan_detail = QLabel("Preparing the index and waiting for the first batch of results.")
        self.scan_detail.setObjectName("emptySub")
        self.scan_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scan_detail.setWordWrap(True)
        self.scan_detail.setMaximumWidth(680)
        self.scan_detail.setMinimumHeight(26)

        self.scan_stats = QLabel("Preparing scan...")
        self.scan_stats.setObjectName("scanStats")
        self.scan_stats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scan_stats.setWordWrap(True)
        self.scan_stats.setMaximumWidth(700)
        self.scan_stats.setMinimumHeight(30)

        self.scan_path = QLabel()
        self.scan_path.setObjectName("scanPathValue")
        self.scan_path.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scan_path.setWordWrap(True)
        self.scan_path.setMaximumWidth(700)
        self.scan_path.setMinimumHeight(46)
        self.scan_path.setTextFormat(Qt.TextFormat.RichText)
        self.scan_path.setVisible(False)

        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setTextVisible(False)
        self.scan_progress.setObjectName("loadingBar")
        self.scan_progress.setFixedWidth(480)
        self.scan_progress.setFixedHeight(12)

        sp_layout.addStretch()
        scan_layout.addWidget(scan_icon, alignment=Qt.AlignmentFlag.AlignCenter)
        scan_layout.addWidget(sp_title)
        scan_layout.addWidget(self.scan_detail, alignment=Qt.AlignmentFlag.AlignCenter)
        scan_layout.addSpacing(12)
        scan_layout.addWidget(self.scan_progress, alignment=Qt.AlignmentFlag.AlignCenter)
        scan_layout.addWidget(self.scan_stats, alignment=Qt.AlignmentFlag.AlignCenter)
        sp_layout.addWidget(scan_content, alignment=Qt.AlignmentFlag.AlignCenter)
        sp_layout.addStretch()

        # Page 1: Tree view
        self.tree = QTreeView()
        self.tree.setMinimumHeight(0)
        self.tree.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.tree.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tree.setAlternatingRowColors(False)
        self.tree.setSortingEnabled(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(False)
        self.tree.setRootIsDecorated(True)
        self.tree.setItemsExpandable(True)
        self.tree.setExpandsOnDoubleClick(True)
        self.tree.setIndentation(16)
        self.tree.setMouseTracking(True)
        self.tree.viewport().setMouseTracking(True)
        self.tree.setItemDelegate(TreeRowDelegate(self.tree))
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
            hdr.setSortIndicatorShown(False)
        except Exception:
            pass
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_status_column_visibility()

        # Page 2: No results (filter produced zero matches)
        no_results_page = QWidget()
        no_results_page.setObjectName("emptyState")
        nr_layout = QVBoxLayout(no_results_page)
        nr_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nr_layout.setSpacing(SPACE_LG)
        nr_title = QLabel("No matching items")
        nr_title.setObjectName("noResultsTitle")
        nr_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.nr_sub = QLabel("Try adjusting your filters or age threshold.")
        self.nr_sub.setObjectName("noResultsSub")
        self.nr_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.nr_sub.setWordWrap(True)
        self.nr_sub.setMaximumWidth(400)
        btn_reset_nr = QPushButton("Reset filters")
        btn_reset_nr.setObjectName("primaryBtn")
        btn_reset_nr.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_reset_nr.setFixedWidth(160)
        btn_reset_nr.clicked.connect(self._reset_filters)
        nr_layout.addStretch()
        nr_layout.addWidget(nr_title)
        nr_layout.addWidget(self.nr_sub, alignment=Qt.AlignmentFlag.AlignCenter)
        nr_layout.addSpacing(SPACE_MD)
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
        self.tree_container.setMinimumHeight(0)
        self.tree_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        tc_layout = QVBoxLayout(self.tree_container)
        tc_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        tc_layout.setContentsMargins(SPACE_XS, SPACE_XS, SPACE_XS, SPACE_XS) # Internal border gap
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
        sbl.setContentsMargins(SPACE_MD, SPACE_XS, SPACE_MD, SPACE_XS)
        sbl.setSpacing(SPACE_SM)
        self.lbl_status = QLabel("Ready - select a folder and click Re-scan")
        self.lbl_status.setObjectName("statusMessage")

        sbl.addWidget(self.lbl_status)
        sbl.addStretch()
        
        self.chip_empty = self._chip("Empty 0", "chipEmpty")
        self.chip_inactive_folders = self._chip("Inactive 0 folders", "chipInactive")
        self.chip_inactive_files = self._chip("0 files", "chipInactive")
        self.chip_browse_size = self._chip("Total Size --", "chipSpace")
        self.chip_page_size = self._chip("Page --", "chipSpace")
        self.chip_selected_size = self._chip("0 B selected · 0 folders, 0 files", "chipSpace")

        sbl.addWidget(self.chip_empty)
        sbl.addWidget(self.chip_inactive_folders)
        sbl.addWidget(self.chip_inactive_files)
        sbl.addWidget(self.chip_browse_size)
        sbl.addWidget(self.chip_page_size)
        sbl.addWidget(self.chip_selected_size)
        self._update_status_metrics_visibility()
        vbox.addWidget(sb)

        # Do NOT auto-start scan - let the user enter a path first

    def _on_theme_changed(self):
        theme_name = self.theme_selector.currentData() or "light"
        self.current_theme_name = resolve_theme_name(theme_name)
        self.settings.setValue("theme", self.current_theme_name)
        apply_theme(QApplication.instance(), self.current_theme_name)
        self.fp._update_age_label(self.fp.age_input.value())
        if self.scanner_thread and self.scanner_thread.isRunning():
            palette = current_palette()
            self.btn_rescan.setStyleSheet(
                f"background-color: {palette['status_danger']}; border-color: {palette['status_danger']};"
            )
        if hasattr(self, 'tree'):
            self._update_status_column_visibility()
            self.tree.viewport().update()
            self.tree.header().viewport().update()
        if self.file_types_dialog:
            self.file_types_dialog.update()

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

    def _format_chip_size(self, size):
        from .models import format_size

        if size is None:
            return "--"
        if isinstance(size, str):
            return size
        if size == 0:
            return "0 B"
        return format_size(int(size))

    def _set_total_summary_chip(self, text):
        if text is None:
            display_text = "Total Size --"
        elif isinstance(text, (int, float)):
            display_text = f"Total Size {self._format_chip_size(int(text))}"
        else:
            display_text = text if str(text).startswith("Total Size ") else f"Total Size {text}"
        self._set_chip_text(self.chip_browse_size, display_text)

    def _set_scanning_total_chip(self):
        running_total = 0
        if self.folder_cache is not None:
            running_total = getattr(self.folder_cache, "running_total_size", 0) or 0
        self._set_chip_text(self.chip_browse_size, f"Scanning… {self._format_chip_size(running_total)} so far")

    def _set_selected_summary_chip(self, size_text=None, folder_count=None, file_count=None):
        if self.is_scanning:
            self.chip_selected_size.setVisible(False)
            return

        size_part = "--" if size_text is None else size_text
        folders_part = "--" if folder_count is None else f"{folder_count:,}"
        files_part = "--" if file_count is None else f"{file_count:,}"
        text = f"{size_part} selected · {folders_part} folders, {files_part} files"
        self._set_chip_text(self.chip_selected_size, text)
        self.chip_selected_size.setVisible(True)

    def _scan_path_html(self, path):
        palette = current_palette()
        if not path:
            return f"<span style='color:{palette['text_muted']};'>Current folder<br>--</span>"

        escaped = html.escape(path)
        for separator in ("\\", "/", "_", "-", "."):
            escaped = escaped.replace(separator, f"{separator}<wbr>")
        return (
            f"<span style='font-weight:600;color:{palette['text_muted']};'>Current folder</span>"
            f"<br><span style='color:{palette['text_muted']};'>{escaped}</span>"
        )

    def _set_scanning_panel(self, path=None, detail=None):
        if hasattr(self, 'scan_path'):
            self.scan_path.setText(self._scan_path_html(path))
            self.scan_path.setToolTip(path or "")
        if detail and hasattr(self, 'scan_detail'):
            self.scan_detail.setText(detail)

    def _format_scan_duration(self, seconds):
        seconds = int(max(0, seconds or 0))
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _scan_stats_text(self, detail=None):
        if not detail:
            return "Preparing the index and waiting for the first batch of results."
        elapsed = 0.0 if not detail else detail.get("elapsed_secs", 0.0)
        rate = 0.0 if not detail else detail.get("rate", 0.0)
        scanned = 0 if not detail else detail.get("scanned", 0) or 0
        percent = None if not detail else detail.get("percent")
        eta_secs = None if not detail else detail.get("eta_secs")

        rate_text = "rate calculating..." if rate <= 0 else f"{rate:,.0f} items/sec"
        parts = [f"{scanned:,} items scanned", rate_text, f"{self._format_scan_duration(elapsed)} elapsed"]
        if percent is not None and eta_secs is not None:
            parts.append(f"~{self._format_scan_duration(eta_secs)} remaining ({percent:.0f}%)")
        return " - ".join(parts)

    def _set_scan_stats(self, detail=None):
        percent = None if not detail else detail.get("percent")

        if hasattr(self, 'scan_progress'):
            if percent is None:
                self.scan_progress.setRange(0, 0)
            else:
                self.scan_progress_was_determinate = True
                self.scan_progress.setRange(0, 100)
                self.scan_progress.setValue(max(0, min(99, int(percent))))

        if hasattr(self, 'scan_stats'):
            self.scan_stats.setText(self._scan_stats_text(detail))

    def _set_size_totals_pending(self, browse=False, page=False, selected=False):
        if browse:
            if self.is_scanning:
                self._set_scanning_total_chip()
            else:
                self._set_total_summary_chip("--")
        if page:
            self._set_chip_text(self.chip_page_size, "Page calculating...")
        if selected:
            if self.is_scanning:
                self.chip_selected_size.setVisible(False)
            elif self._selected_roots_for_delete():
                self._set_chip_text(self.chip_selected_size, "Selected calculating...")
            else:
                self.cached_selected_total = 0
                self._set_selected_summary_chip("0 B", 0, 0)

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

    def _restore_tree_scroll_position(self, vertical_value, horizontal_value=0):
        if not hasattr(self, 'tree'):
            return
        vbar = self.tree.verticalScrollBar()
        hbar = self.tree.horizontalScrollBar()
        vbar.setValue(max(vbar.minimum(), min(vertical_value, vbar.maximum())))
        hbar.setValue(max(hbar.minimum(), min(horizontal_value, hbar.maximum())))

    def _clear_path_focus_for_external_click(self, obj, event):
        if event.type() != QEvent.Type.MouseButtonPress:
            return
        if not hasattr(self, 'txt_path') or not self.txt_path.hasFocus():
            return
        if not isinstance(obj, QWidget):
            return
        if obj == self.txt_path or self.txt_path.isAncestorOf(obj):
            return

        self._clear_path_input_focus()

    def _clear_path_input_focus(self):
        if not hasattr(self, 'txt_path'):
            return
        self.txt_path.deselect()
        self.txt_path.clearFocus()

    def eventFilter(self, obj, event):
        self._clear_path_focus_for_external_click(obj, event)
        if hasattr(self, 'tree') and obj == self.tree.viewport():
            if event.type() == QEvent.Type.MouseMove:
                pos = event.position().toPoint() if hasattr(event, 'position') else event.pos()
                self._update_tree_cursor(self.tree.indexAt(pos))
            elif event.type() == QEvent.Type.Leave:
                self.tree.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        if hasattr(self, 'tree_container') and obj == self.tree_container:
            if event.type() == QEvent.Type.Resize:
                self._reposition_scroll_top_btn()
                QTimer.singleShot(0, self._fit_tree_columns_to_viewport)
        return super().eventFilter(obj, event)

    def _fit_tree_columns_to_viewport(self):
        if not hasattr(self, 'tree') or self.tree.model() is None:
            return

        header = self.tree.header()
        view_mode = self.fp.get_view_mode() if hasattr(self, 'fp') else 'Tree'
        viewport_width = max(0, self.tree.viewport().width() - 18)

        if view_mode == 'Tree':
            fixed_widths = {
                1: 96,
                2: 150,
                3: 86,
                4: 132,
                5: 118,
            }
            min_name_width = 420
        else:
            fixed_widths = {
                1: min(420, max(260, viewport_width // 4)),
                2: 150,
                3: 86,
                4: 132,
                5: 118,
            }
            min_name_width = 260

        visible_fixed = 0
        for column, width in fixed_widths.items():
            if self.tree.isColumnHidden(column):
                continue
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.tree.setColumnWidth(column, width)
            visible_fixed += width

        name_width = max(min_name_width, viewport_width - visible_fixed)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(0, name_width)

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
            name_filter=self.applied_name_filter,
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
        QTimer.singleShot(0, self._fit_tree_columns_to_viewport)

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
        if hasattr(self, 'chip_page_size'):
            self.chip_page_size.setVisible(not self.fp.rb_all.isChecked())
        if hasattr(self, 'chip_selected_size'):
            self.chip_selected_size.setVisible((not self.is_scanning) and show_status)

    def _set_page_controls_visible(self, visible):
        for widget in (
            getattr(self, 'btn_prev_page', None),
            getattr(self, 'lbl_page_info', None),
            getattr(self, 'btn_next_page', None),
        ):
            if widget is not None:
                widget.setVisible(visible)

    def _update_page_controls(self, total_matches):
        if not hasattr(self, 'lbl_page_info'):
            return

        limit = 2000
        has_multiple_pages = total_matches > limit and not self.current_lazy_show_all_tree
        self._set_page_controls_visible(has_multiple_pages)
        if not has_multiple_pages:
            return

        offset = self.current_page * limit
        end = min(offset + limit, total_matches)
        self.lbl_page_info.setText(f"{offset + 1}-{end} of {total_matches}")
        self.btn_prev_page.setEnabled(self.current_page > 0)
        self.btn_next_page.setEnabled(offset + limit < total_matches)

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
        self.search_debounce_timer.stop()
        self.applied_name_filter = ""
        self.active_extension_filter = None
        blocker = QSignalBlocker(self.fp.txt_search)
        self.fp.txt_search.clear()
        del blocker
        self.fp.search_clear_action.setVisible(False)
        self._apply_default_browse_preset()

    def _on_scan_exclusions_changed(self):
        if self._has_completed_scan_context():
            self.fp.show_exclusions_rescan_hint(True)

    def _on_scan_exclusions_summary(self, excluded_count):
        self.last_scan_excluded_count = excluded_count or 0

    def _has_completed_scan_context(self):
        return bool(getattr(self, 'current_scan_root', None)) and not self.is_scanning

    def _update_file_types_enabled(self):
        if hasattr(self, 'btn_file_types'):
            self.btn_file_types.setEnabled(self._has_completed_scan_context())

    def _show_file_types(self):
        if not self._has_completed_scan_context():
            return

        self.file_type_request_id += 1
        request_id = self.file_type_request_id

        if self.file_type_thread and self.file_type_thread.isRunning():
            self.file_type_thread.cancel()

        self.file_types_dialog = FileTypesDialog(self)
        self.file_types_dialog.extension_selected.connect(self._drill_down_file_type)
        self.file_types_dialog.set_loading()
        self.file_types_dialog.show()

        self.file_type_thread = FileTypeBreakdownThread(request_id, limit=20, parent=self)
        self.file_type_thread.breakdown_ready.connect(self._on_file_types_ready)
        self.file_type_thread.breakdown_failed.connect(self._on_file_types_failed)
        self.file_type_thread.finished.connect(self._cleanup_file_type_thread)
        self.file_type_thread.start()

    def _cleanup_file_type_thread(self):
        if self.sender() is self.file_type_thread:
            self.file_type_thread = None

    def _on_file_types_ready(self, request_id, rows):
        if request_id != self.file_type_request_id or not self.file_types_dialog:
            return
        self.file_types_dialog.set_rows(rows)

    def _on_file_types_failed(self, request_id, error):
        if request_id != self.file_type_request_id or not self.file_types_dialog:
            return
        self.file_types_dialog.set_error(error)

    def _drill_down_file_type(self, extension):
        self.active_extension_filter = extension
        self.search_debounce_timer.stop()
        self.applied_name_filter = ""
        self._cancel_running_bulk_select_thread()
        self._discard_current_page_selection()
        self.bulk_delete_scope = None
        self.current_page = 0

        blockers = [
            QSignalBlocker(self.fp.bg),
            QSignalBlocker(self.fp.bg_view),
            QSignalBlocker(self.fp.rb_all),
            QSignalBlocker(self.fp.rb_inactive),
            QSignalBlocker(self.fp.rb_empty),
            QSignalBlocker(self.fp.rb_videos),
            QSignalBlocker(self.fp.rb_view_tree),
            QSignalBlocker(self.fp.rb_view_files),
            QSignalBlocker(self.fp.rb_view_folders),
            QSignalBlocker(self.fp.txt_search),
        ]
        try:
            self.fp.rb_all.setChecked(True)
            self.fp.rb_view_files.setChecked(True)
            self.fp.txt_search.clear()
        finally:
            del blockers

        self.fp.search_clear_action.setVisible(False)
        self.sort_column = 4
        self.sort_order = Qt.SortOrder.DescendingOrder
        self._apply_sort_indicator()
        self._apply_filters()

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
                # Filtered pages are capped by the selected page size, so expandAll stays bounded.
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

    def _expand_loaded_search_branches(self):
        if not self.tree_model or self.proxy_model.sourceModel() is None:
            return

        self.is_programmatic_expand = True
        try:
            stack = [
                self.tree_model.index(row, 0, QModelIndex())
                for row in range(self.tree_model.rowCount(QModelIndex()))
            ]
            while stack:
                source_index = stack.pop()
                item = source_index.internalPointer()
                real_children = [
                    child
                    for child in item.childItems
                    if not child.itemData.get('_is_dummy')
                ]
                if not real_children:
                    continue

                proxy_index = self.proxy_model.mapFromSource(source_index)
                if proxy_index.isValid():
                    self.tree.setExpanded(proxy_index, True)
                for row in range(self.tree_model.rowCount(source_index)):
                    stack.append(self.tree_model.index(row, 0, source_index))
        finally:
            self.is_programmatic_expand = False

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
            options.get('name_filter', ''),
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

        self._preserve_results_focus = True
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
        self._set_delete_armed(False)
        self.btn_delete.setEnabled(False)

    def _on_filter_changed(self):
        # User manually changed a filter control - clear selections and apply
        self.active_extension_filter = None
        self._discard_current_page_selection()
        self._apply_filters()

    def _on_search_submitted(self):
        search_text = self.fp.txt_search.text().strip()
        if search_text == self.applied_name_filter:
            return
        self.applied_name_filter = search_text
        self._on_filter_changed()

    def _clear_search_filter(self):
        self.search_debounce_timer.stop()
        if not self.applied_name_filter:
            return
        self.applied_name_filter = ""
        self._on_filter_changed()

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

        name_filter = self.applied_name_filter
        if name_filter:
            where_clauses.append("name LIKE ? ESCAPE '\\' COLLATE NOCASE")
            params.append(f"%{escape_sql_like(name_filter)}%")

        if self.active_extension_filter is not None:
            where_clauses.append("is_folder = 0")
            where_clauses.append("extension = ? COLLATE NOCASE")
            params.append(self.active_extension_filter)

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

        focused_selection_mode = mode in ('Inactive', 'Empty')
        self.btn_select_all.setVisible(not has_selection and not focused_selection_mode)
        self.btn_clear_selection.setVisible(has_selection and not focused_selection_mode)
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
        self.btn_select_inactive.setVisible(mode == 'Inactive')
        self.btn_select_empty.setVisible(mode == 'Empty')

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
            thread = DeletePreviewThread(
                bulk_scope=bulk_scope,
                parent=self,
            )
        else:
            thread = DeletePreviewThread(
                paths or [],
                excluded_paths=self.excluded_paths,
                parent=self,
            )

        self.delete_preview_thread = thread
        thread.preview_ready.connect(dialog.apply_preview)
        thread.preview_failed.connect(dialog.show_error)
        dialog.finished.connect(thread.cancel)
        thread.start()

        self.pending_delete_preview_dialog = dialog
        dialog.finished.connect(lambda *_: setattr(self, 'pending_delete_preview_dialog', None))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        result = dialog.exec()
        thread.cancel()
        if thread.isRunning():
            thread.wait()
        if self.delete_preview_thread is thread:
            self.delete_preview_thread = None
        if result == QDialog.DialogCode.Accepted:
            payload = dict(dialog.preview_payload or {})
            payload['paths'] = list(dialog.preview_paths or [])
            return payload
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
        self.current_scan_root = None
        self.active_extension_filter = None

        # Reset pagination state
        self.current_page = 0
        self.current_total_matches = 0
        self.is_scanning  = False
        self.cached_folder_total = None
        self.cached_folder_total_root = None
        self.totals_request_id += 1

        # Reset status chips
        self.chip_empty.setText("Empty 0")
        self.chip_inactive_folders.setText("Inactive 0 folders")
        self.chip_inactive_files.setText("0 files")
        self._set_total_summary_chip("--")
        self._set_chip_text(self.chip_page_size, "Page --")
        self._set_selected_summary_chip("0 B", 0, 0)
        self._update_status_metrics_visibility()

        # Hide controls, show empty page
        self.controls_bar.setVisible(False)
        self.content_stack.setCurrentIndex(0)
        self.lbl_status.setText("Ready - select a folder and click Re-scan")
        self._update_file_types_enabled()

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
        
        self.current_page = 0
        self.total_scanned = 0
        self.last_scan_excluded_count = 0
        self.last_scan_elapsed_secs = 0.0
        self.last_scan_item_count = 0
        self.scan_progress_was_determinate = False
        self.is_scanning = True
        self.active_extension_filter = None
        self.current_scan_root = os.path.normcase(os.path.normpath(path))
        self.cached_folder_total = None
        self.cached_folder_total_root = self.current_scan_root
        self.totals_request_id += 1
        self._cancel_running_totals_thread()

        self.lbl_status.setText("Scanning...")
        self.btn_rescan.setText("Stop")
        palette = current_palette()
        self.btn_rescan.setStyleSheet(
            f"background-color: {palette['status_danger']}; border-color: {palette['status_danger']};"
        )
        self._set_size_totals_pending(browse=True, page=True, selected=True)
        self.content_stack.setCurrentIndex(3)
        self._update_file_types_enabled()
        self._set_scanning_panel(
            path=path,
            detail="Preparing the index and waiting for the first batch of results.",
        )
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setValue(0)
        self._set_scan_stats(None)

        self.fp.show_exclusions_rescan_hint(False)
        history = get_scan_history(self.current_scan_root)
        estimated_total_items = None
        if history:
            estimated_total_items = history.get("item_count") or None
        self.scanner_thread = ScannerThread(
            path,
            stale_months=self.fp.get_stale_months_for_scan(),
            exclusions=self.fp.get_scan_exclusions(),
            estimated_total_items=estimated_total_items,
        )
        self.folder_cache = self.scanner_thread.cache
        self.scanner_thread.scan_started.connect(self._on_scan_started)
        self.scanner_thread.scan_finished.connect(self._on_scan_done)
        self.scanner_thread.scan_exclusions_summary.connect(self._on_scan_exclusions_summary)
        self.scanner_thread.scan_progress.connect(self._on_progress)
        self.scanner_thread.scan_progress_detail.connect(self._on_progress_detail)
        self.scanner_thread.first_batch_ready.connect(self._on_first_batch_ready)
        self.scanner_thread.batch_ready.connect(self._on_batch_ready)
        self.scanner_thread.start()

    def _on_progress(self, path):
        s = ("..." + path[-72:]) if len(path) > 75 else path
        if self.is_scanning:
            self._set_scanning_total_chip()
        if self.content_stack.currentIndex() == 3:
            self._set_scanning_panel(path=s)

    def _on_progress_detail(self, detail):
        self.last_scan_elapsed_secs = detail.get("elapsed_secs", 0.0)
        self.last_scan_item_count = detail.get("scanned", 0) or 0
        self._set_scan_stats(detail)
        self.lbl_status.setText(f"Scanning - {self._scan_stats_text(detail)}")

    def _on_scan_started(self):
        if self.is_scanning:
            self._set_scanning_total_chip()
            self.chip_selected_size.setVisible(False)

    def _set_match_status(self, total_matches):
        if self.is_scanning:
            return
        else:
            self.lbl_status.setText(self._scan_complete_status_text())

    def _scan_complete_status_text(self):
        parts = [
            f"Scan complete in {self._format_scan_duration(getattr(self, 'last_scan_elapsed_secs', 0.0))}.",
            f"{getattr(self, 'last_scan_item_count', 0):,} items scanned.",
        ]
        if getattr(self, 'last_scan_excluded_count', 0):
            parts.append(f"{self.last_scan_excluded_count:,} items skipped by exclusion rules.")
        return " ".join(parts)

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
        self._update_page_controls(total_matches or 0)
        
        self._set_match_status(total_matches)

    def _on_scan_done(self):
        finished_thread = self.scanner_thread
        if finished_thread:
            self.last_scan_elapsed_secs = getattr(finished_thread, "scan_elapsed_secs", 0.0) or 0.0
            self.last_scan_item_count = (
                getattr(finished_thread, "scan_indexed_count", 0)
                or getattr(finished_thread, "scan_scanned_count", 0)
                or 0
            )
        self.btn_rescan.setEnabled(True)
        self.btn_rescan.setText("Re-scan")
        self.btn_rescan.setStyleSheet("") # reset style
        self.is_scanning = False
        self._update_file_types_enabled()
        
        if finished_thread and finished_thread.is_cancelled:
            self.lbl_status.setText("Scan stopped by user.")
        else:
            if self.scan_progress_was_determinate:
                self.scan_progress.setRange(0, 100)
                self.scan_progress.setValue(100)
            completion_message = self._scan_complete_status_text()
            self.lbl_status.setText(completion_message)
            record_scan_history(
                self.current_scan_root,
                getattr(finished_thread, "scan_scanned_count", 0) or self.last_scan_item_count,
                getattr(self.folder_cache, "running_total_size", 0) if self.folder_cache else 0,
                self.last_scan_elapsed_secs,
            )
            if self._should_show_completion_notification(
                elapsed_secs=self.last_scan_elapsed_secs,
                cancelled=False,
            ):
                self._show_system_notification("Scan complete", completion_message)

        if self.content_stack.currentIndex() in (0, 2, 3):
            self._load_page()
        elif self.content_stack.currentIndex() == 1:
            if self.current_lazy_show_all_tree:
                if self.tree_model:
                    self.tree_model.update_sizes_from_cache(self.folder_cache)
            else:
                self.scan_refresh_timer.start()
            self._update_chips_sql()
        if self.folder_cache is not None:
            self._set_total_summary_chip(getattr(self.folder_cache, "running_total_size", 0) or 0)
        self._set_selected_summary_chip("0 B", 0, 0)
            
    def _prev_page(self):
        if self.current_page > 0:
            self._preserve_results_focus = True
            self.current_page -= 1
            self._update_pending_pagination_state()
            self._load_page()
            self._clear_path_input_focus()

    def _next_page(self):
        self._preserve_results_focus = True
        self.current_page += 1
        self._update_pending_pagination_state()
        self._load_page()
        self._clear_path_input_focus()

    def _update_pending_pagination_state(self):
        if not hasattr(self, 'lbl_page_info'):
            return

        limit = 2000
        total_matches = self.current_total_matches or 0
        self._update_page_controls(total_matches)
        if self.current_lazy_show_all_tree or total_matches <= 0:
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
        hdr.setSortIndicatorShown(False)
        del blocker
        model = getattr(self, 'tree_model', None)
        if model is not None and hasattr(model, 'set_sort_header_state'):
            model.set_sort_header_state(self.sort_column, self.sort_order)

    def _focus_results_view(self):
        self._clear_path_input_focus()
        self.tree.setFocus(Qt.FocusReason.OtherFocusReason)

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
        self._preserve_results_focus = True
        self.sort_column = col
        self.sort_order = order
        self._apply_sort_indicator()
        self.current_page = 0 # Reset to first page when sorting changes
        self._load_page()

    def _page_load_options(self):
        view_mode = self.fp.get_view_mode()
        paginated = True
        name_filter = self.applied_name_filter
        extension_filter = self.active_extension_filter

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
            and not name_filter
            and extension_filter is None
        )
        filtered_expanded_tree = (
            view_mode == 'Tree'
            and not name_filter
            and extension_filter is None
            and (
                status_filter in ('Inactive', 'Empty')
                or self.fp.rb_videos.isChecked()
            )
        )
        defer_tree_load_until_scan_done = (
            lazy_show_all_tree
            or filtered_expanded_tree
            or bool(name_filter)
            or extension_filter is not None
        )
        return {
            'limit': limit,
            'offset': self.current_page * limit if paginated else 0,
            'page': self.current_page if paginated else 0,
            'paginated': paginated,
            'view_mode': view_mode,
            'status_filter': status_filter,
            'age_cutoff': age_cutoff,
            'videos_only': self.fp.rb_videos.isChecked(),
            'name_filter': name_filter,
            'extension_filter': extension_filter,
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
                "Working...",
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
        self._refresh_selection_buttons()
        self._do_recount()
        self._focus_results_view()
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
            self._refresh_selection_buttons()
            self._do_recount()
            self._focus_results_view()
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
            self._refresh_selection_buttons()
            self._do_recount()
            self._focus_results_view()
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
                self._set_selected_summary_chip("0 B", 0, 0)
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
            self.btn_prev_page,
            self.btn_next_page,
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
        self._update_file_types_enabled()

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
                self.content_stack.setCurrentIndex(3)
                self._set_scanning_panel(
                    detail="Indexing is still running. The first batch will replace this panel as soon as it is ready.",
                )
            else:
                self.content_stack.setCurrentIndex(2)
                self.controls_bar.setVisible(False)
                self._set_page_controls_visible(False)
                debug_info = result.get('debug_info')
                self.lbl_status.setText(
                    f"No matching items found ({debug_info})"
                    if debug_info else "No matching items found"
                )
            return

        self.tree_model = WatchdogTreeModel(result['root_node'])
        self.tree_model.view_mode = view_mode
        self.tree_model.options = self._page_load_options()
        self.proxy_model.setSourceModel(self.tree_model)
        self.tree.setModel(self.proxy_model)
        self._rebuild_source_index_map()
        self._restore_persistent_selection_to_model()
        self._restore_bulk_scope_selection_to_model()

        self._update_page_controls(total_matches or 0)

        self.content_stack.setCurrentIndex(1)
        self.controls_bar.setVisible(True)

        hdr = self.tree.header()
        for index in range(0, 6):
            hdr.setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(False)
        self._apply_sort_indicator()
        self._fit_tree_columns_to_viewport()

        if result.get('name_filter'):
            self._set_expand_state(False)
            self._expand_loaded_search_branches()
        else:
            should_expand_loaded_tree = view_mode == 'Tree' and not lazy_show_all_tree
            self._set_expand_state(should_expand_loaded_tree)
        self._restore_tree_refresh_state(self.tree_model.options)
        self._update_expand_control_visibility()
        self._update_status_column_visibility()
        self._update_status_metrics_visibility()
        self._set_match_status(total_matches)
        self._update_chips_sql()
        self._do_recount()
        self._refresh_selection_buttons()
        self.tree_model.dataChanged.connect(self._on_checked)
        self.tree_model.layoutChanged.connect(self._on_checked)
        if self._preserve_results_focus:
            self._focus_results_view()
            self._preserve_results_focus = False

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
        if self.is_scanning:
            self._set_scanning_total_chip()
            return
        if result['folder_total'] is None:
            self._set_total_summary_chip("--")
        elif folder_total_is_unknown_zero:
            self.cached_folder_total = None
            self._set_total_summary_chip("--")
        else:
            folder_total = result['folder_total']
            folder_total_text = "0 B" if folder_total_is_real_zero else format_size(folder_total)
            self._set_total_summary_chip(folder_total_text)
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
        self._set_selected_summary_chip(format_size(size), folders, files)
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
        self._set_selected_summary_chip("0 B", 0, 0)

    def _on_totals_failed(self, request_id, error):
        if request_id != self.totals_request_id:
            return

        if self.is_scanning:
            self._set_scanning_total_chip()
        else:
            self._set_total_summary_chip("--")

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
            self._set_delete_armed(False)
            self.btn_delete.setEnabled(False)
            self._refresh_selection_buttons()
            return
        if self.bulk_delete_scope:
            scope = self.bulk_delete_scope
            armed = scope['total'] > 0
            self.btn_delete.setText(
                f"Delete Selected (All pages: {scope['total']} items)"
            )
            self._set_delete_armed(armed)
            self.btn_delete.setEnabled(armed)
            self._refresh_selection_buttons()
            self._update_chips_sql()
            return
        selected_paths = self._selected_roots_for_delete()
        total = len(selected_paths)
        if total:
            self.btn_delete.setText(f"Delete Selected ({total} item{'s' if total != 1 else ''})")
        else:
            self.btn_delete.setText("Delete Selected")
        armed = total > 0
        self._set_delete_armed(armed)
        self.btn_delete.setEnabled(armed)
        self._set_size_totals_pending(selected=True)
        self._refresh_selection_buttons()
        self._update_chips_sql()

    def _set_delete_armed(self, armed):
        self.btn_delete.setProperty("armed", bool(armed))
        self.btn_delete.style().unpolish(self.btn_delete)
        self.btn_delete.style().polish(self.btn_delete)
        self.btn_delete.update()

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
                    return "Root directory"
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
        QTimer.singleShot(0, self._fit_tree_columns_to_viewport)

    def _on_tree_collapsed(self, proxy_index):
        QTimer.singleShot(0, self._fit_tree_columns_to_viewport)

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

        expanded_path_keys = set()
        stack = [QModelIndex()]
        while stack:
            parent = stack.pop()
            for row in range(self.proxy_model.rowCount(parent)):
                proxy_index = self.proxy_model.index(row, 0, parent)
                if not self.tree.isExpanded(proxy_index):
                    continue
                source_for_expanded = self.proxy_model.mapToSource(proxy_index)
                item_data = self.tree_model.data(source_for_expanded, Qt.ItemDataRole.UserRole) or {}
                if item_data.get('path'):
                    expanded_path_keys.add(self._path_key(item_data['path']))
                stack.append(proxy_index)
        expanded_path_keys.add(self._path_key(folder_path))

        source_index = QModelIndex(persistent_index)
        vbar = self.tree.verticalScrollBar()
        hbar = self.tree.horizontalScrollBar()
        previous_vertical = vbar.value()
        previous_horizontal = hbar.value()
        self.tree.setUpdatesEnabled(False)
        try:
            self.proxy_model.setSourceModel(None)
            try:
                self.tree_model.finish_async_child_load(source_index, children)
            finally:
                self.proxy_model.setSourceModel(self.tree_model)
                self.tree.setModel(self.proxy_model)
                self._update_status_column_visibility()
        finally:
            self.tree.setUpdatesEnabled(True)
        self._rebuild_source_index_map()
        self._restore_persistent_selection_to_model()
        self._restore_bulk_scope_selection_to_model()
        self._refresh_selection_buttons()
        self.is_programmatic_expand = True
        try:
            for path_key in sorted(expanded_path_keys, key=lambda key: key.count(os.sep)):
                loaded_source_index = self.source_index_by_path.get(path_key, QModelIndex())
                proxy_index = self.proxy_model.mapFromSource(loaded_source_index)
                if not proxy_index.isValid():
                    continue
                self.tree.setExpanded(proxy_index, True)
        finally:
            self.is_programmatic_expand = False
        self._restore_tree_scroll_position(previous_vertical, previous_horizontal)
        QTimer.singleShot(
            0,
            lambda v=previous_vertical, h=previous_horizontal: self._restore_tree_scroll_position(v, h),
        )
        QTimer.singleShot(0, self._update_status_column_visibility)
        QTimer.singleShot(0, self._fit_tree_columns_to_viewport)

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
        palette = current_palette()
        menu.setStyleSheet(f"""
            QMenu {{ background:{palette['surface']}; color:{palette['text']}; border:1px solid {palette['border']};
                    border-radius:6px; padding:{SPACE_XS}px; }}
            QMenu::item {{ padding:{SPACE_SM}px {SPACE_LG}px; border-radius:4px; }}
            QMenu::item:selected {{ background:{palette['accent']}; color:white; }}
            QMenu::separator {{ background:{palette['border']}; height:1px; margin:{SPACE_XS}px 0; }}
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
            try:
                _paths, _folders, _files, total_size = self._summarize_delete_paths([path])
            except Exception:
                total_size = 0
            authorized_by = self._authorize_delete(1, total_size)
            if authorized_by:
                self._start_delete([path], authorized_by=authorized_by, audit_size=total_size)

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
        if hasattr(self, 'btn_file_types'):
            self.btn_file_types.setEnabled(enabled and self._has_completed_scan_context())
        self.controls_bar.setEnabled(enabled)
        self.tree.setEnabled(enabled)

    def _authorize_delete(self, item_count, total_size=0):
        dialog = DeleteAuthDialog(item_count, total_size, self)
        if dialog.store.is_empty():
            QMessageBox.warning(
                self,
                "Delete authorization",
                "No authorized users are configured for this installation. Contact your administrator.",
            )
            return None

        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.authorized_username
        return None

    def _start_delete(self, paths, authorized_by=None, audit_size=0):
        if not paths:
            return
        if not authorized_by:
            QMessageBox.warning(
                self,
                "Delete authorization",
                "Delete authorization is required before items can be deleted.",
            )
            return

        self.delete_authorized_by = authorized_by
        self.delete_audit_items = len(paths)
        self.delete_audit_size = int(audit_size or 0)
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
        append_delete_audit(
            "delete_completed",
            username=getattr(self, 'delete_authorized_by', "UNKNOWN"),
            items=getattr(self, 'delete_audit_items', deleted_count),
            size=getattr(self, 'delete_audit_size', None),
            errors=len(errors or []),
        )
        self.delete_authorized_by = None
        self.delete_audit_items = 0
        self.delete_audit_size = 0
        if deleted_paths:
            if self.folder_cache is not None:
                for path in deleted_paths:
                    self.folder_cache.remove_path(path)
                self.cached_folder_total = None
                self.cached_folder_total_root = None
            self._remove_deleted_paths_from_selection(deleted_paths)
        self._set_delete_controls_enabled(True)
        self._load_page()
        if deleted_paths:
            self._start_totals_refresh()

        if cancelled:
            title = "Deletion stopped"
            message = f"Deleted {deleted_count} item(s) before stopping."
        else:
            title = "Deletion complete"
            message = f"Deleted {deleted_count} item(s)."

        if errors:
            message += f"\n\nFailed: {len(errors)}"

        if self._should_show_completion_notification(
            elapsed_secs=None,
            cancelled=cancelled,
        ):
            self._show_system_notification(title, message)

        if errors:
            QMessageBox.warning(self, title, message + "\n\n" + "\n".join(errors[:10]))
        else:
            QMessageBox.information(self, title, message)

    def _delete_selected(self):
        if not self.tree_model:
            return
        if self.bulk_delete_scope:
            scope = self.bulk_delete_scope
            preview = self._show_delete_preview(bulk_scope=scope)
            if preview:
                paths = preview.get('paths', [])
                total_size = preview.get('size', 0) or 0
                item_count = preview.get('delete_operations', len(paths))
                authorized_by = self._authorize_delete(item_count, total_size)
                if authorized_by:
                    self._start_delete(paths, authorized_by=authorized_by, audit_size=total_size)
            return

        paths = self._selected_roots_for_delete()
        if not paths:
            QMessageBox.information(self, "Delete", "No items selected.")
            return
        preview = self._show_delete_preview(paths=paths)
        if preview:
            preview_paths = preview.get('paths', [])
            total_size = preview.get('size', 0) or 0
            item_count = preview.get('delete_operations', len(preview_paths))
            authorized_by = self._authorize_delete(item_count, total_size)
            if authorized_by:
                self._start_delete(preview_paths, authorized_by=authorized_by, audit_size=total_size)

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
