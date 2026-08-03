import os
import csv
import html
import json
import subprocess
import send2trash
import time
from bisect import bisect_left, insort
from datetime import datetime
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QRadioButton, QSlider, QTreeView, QHeaderView, QGridLayout,
    QMessageBox, QStyledItemDelegate, QButtonGroup, QApplication, QFileDialog,
    QSpinBox, QAbstractItemView, QStackedWidget, QStyleOptionViewItem,
    QMenu, QSizePolicy, QFrame, QStyle, QDialog, QProgressBar,
    QTableWidget, QTableWidgetItem, QComboBox, QAbstractScrollArea, QBoxLayout,
    QLayout, QSystemTrayIcon, QGraphicsDropShadowEffect, QCheckBox, QSplitter,
    QScrollArea, QToolButton
)
from PyQt6.QtCore import Qt, QRect, QRectF, QModelIndex, QPersistentModelIndex, QTimer, QEvent, QSignalBlocker, QThread, pyqtSignal, QSize, QSettings, QAbstractItemModel
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QIcon, QFont, QFontMetrics

from .models import WatchdogTreeModel, WatchdogFilterProxyModel, format_size, format_age
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

PERF_DEBUG = os.environ.get("WATCHDOG_PERF_DEBUG", "").strip().lower() in {
    "1", "true", "yes", "on",
}


def _path_key(path):
    return os.path.normcase(os.path.normpath(path))


def bulk_scope_excluded_keys(scope):
    return {
        _path_key(path)
        for path in (scope or {}).get("excluded_paths", [])
        if path
    }


def equivalent_path_variants(path):
    if not path:
        return []
    normalized = os.path.normpath(path)
    stripped = normalized.rstrip("\\/")
    variants = {
        path,
        normalized,
        stripped,
        stripped + "\\",
        stripped + "/",
    }
    variants.update(value.replace("\\", "/") for value in list(variants))
    variants.update(value.replace("/", "\\") for value in list(variants))
    return [value for value in variants if value]


def folder_is_physically_empty(cursor, path):
    try:
        cursor.execute(
            "SELECT physical_child_count FROM folder_summary "
            "WHERE path = ? COLLATE NOCASE",
            (path,),
        )
        row = cursor.fetchone()
    except Exception:
        row = None
    if row and row[0] is not None and row[0] >= 0:
        return row[0] == 0
    try:
        with os.scandir(path) as entries:
            return next(entries, None) is None
    except OSError:
        return False


def filesystem_folder_has_visible_entries(
    folder_path,
    exclusions,
    cancel_check=None,
    visited=None,
):
    if cancel_check:
        cancel_check()
    visited = visited if visited is not None else set()
    folder_key = _path_key(folder_path)
    if folder_key in visited:
        return False
    visited.add(folder_key)

    try:
        with os.scandir(folder_path) as entries:
            folder_entries = list(entries)
    except OSError:
        return False
    if not folder_entries:
        return True

    for entry in folder_entries:
        if cancel_check:
            cancel_check()
        try:
            is_folder = entry.is_dir(follow_symlinks=True)
            size = 0 if is_folder else entry.stat(follow_symlinks=True).st_size
        except OSError:
            continue
        if exclusions is not None:
            if is_folder and exclusions.matches_excluded_folder(entry.name):
                continue
            if not is_folder and (
                exclusions.matches_excluded_extension(
                    os.path.splitext(entry.name)[1].lower()
                )
                or (
                    exclusions.min_file_size_bytes > 0
                    and size < exclusions.min_file_size_bytes
                )
            ):
                continue
        if not is_folder:
            return True
        if filesystem_folder_has_visible_entries(
            entry.path,
            exclusions,
            cancel_check=cancel_check,
            visited=visited,
        ):
            return True
    return False


class PathKeyIndex:
    """Indexed normalized paths with depth-based ancestor and bisected descendant lookup."""

    def __init__(self, keys=()):
        self._keys = set(keys)
        self._sorted_keys = sorted(self._keys)

    def add(self, key):
        if key in self._keys:
            return
        self._keys.add(key)
        insort(self._sorted_keys, key)

    def discard(self, key):
        if key not in self._keys:
            return
        self._keys.remove(key)
        position = bisect_left(self._sorted_keys, key)
        if position < len(self._sorted_keys) and self._sorted_keys[position] == key:
            self._sorted_keys.pop(position)

    def clear(self):
        self._keys.clear()
        self._sorted_keys.clear()

    def has_ancestor(self, key, include_self=True):
        current = key if include_self else os.path.dirname(key)
        while current:
            if current in self._keys:
                return True
            parent = os.path.dirname(current.rstrip("\\/"))
            if not parent or parent == current:
                break
            current = parent
        return False

    def descendant_keys(self, key, include_self=False):
        matches = []
        if include_self and key in self._keys:
            matches.append(key)
        prefix = key if key.endswith(("\\", "/")) else key + os.sep
        position = bisect_left(self._sorted_keys, prefix)
        while position < len(self._sorted_keys):
            candidate = self._sorted_keys[position]
            if not candidate.startswith(prefix):
                break
            if candidate != key:
                matches.append(candidate)
            position += 1
        return matches

    def has_descendant(self, key):
        prefix = key if key.endswith(("\\", "/")) else key + os.sep
        position = bisect_left(self._sorted_keys, prefix)
        while (
            position < len(self._sorted_keys)
            and self._sorted_keys[position] == key
        ):
            position += 1
        return (
            position < len(self._sorted_keys)
            and self._sorted_keys[position].startswith(prefix)
        )

    def remove_many(self, keys):
        removed = set(keys)
        if not removed:
            return
        self._keys.difference_update(removed)
        self._sorted_keys = [
            key for key in self._sorted_keys
            if key not in removed
        ]


class IndexedPathDict(dict):
    """Dictionary that keeps a path-prefix index synchronized with key mutations."""

    _MISSING = object()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.path_index = PathKeyIndex(self.keys())

    def __setitem__(self, key, value):
        is_new = key not in self
        super().__setitem__(key, value)
        if is_new:
            self.path_index.add(key)

    def __delitem__(self, key):
        super().__delitem__(key)
        self.path_index.discard(key)

    def pop(self, key, default=_MISSING):
        if key in self:
            value = super().pop(key)
            self.path_index.discard(key)
            return value
        if default is self._MISSING:
            raise KeyError(key)
        return default

    def clear(self):
        super().clear()
        self.path_index.clear()

    def update(self, *args, **kwargs):
        values = dict(*args, **kwargs)
        for key, value in values.items():
            self[key] = value

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]

    def popitem(self):
        key, value = super().popitem()
        self.path_index.discard(key)
        return key, value

    def __ior__(self, other):
        self.update(other)
        return self

    def remove_descendants(self, key, include_self=False):
        keys = self.path_index.descendant_keys(key, include_self=include_self)
        for descendant_key in keys:
            dict.__delitem__(self, descendant_key)
        self.path_index.remove_many(keys)


def _perf_log(label, started_at, **counts):
    if not PERF_DEBUG or started_at is None:
        return
    details = " ".join(f"{name}={value}" for name, value in counts.items())
    print(f"[watchdog-perf] {label}: {(time.perf_counter() - started_at) * 1000:.1f} ms {details}".rstrip())


def paint_tree_row_border(painter, option):
    return

EMPTY_FOLDER_SQL = (
    "is_folder = 1 AND EXISTS ("
    "SELECT 1 FROM folder_summary summary "
    "WHERE summary.path = file_index.path COLLATE NOCASE "
    "AND summary.physical_child_count = 0"
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


def descendant_scope_sql(path, column="path"):
    prefix_sql, prefix_params = descendant_like_sql(path, column)
    normalized = os.path.normpath(str(path)).rstrip("\\/")
    variants = list(dict.fromkeys((
        str(path).rstrip("\\/"),
        normalized,
        normalized.replace("\\", "/"),
        normalized.replace("/", "\\"),
    )))
    variants = [variant for variant in variants if variant]
    if not variants:
        return prefix_sql, prefix_params

    placeholders = ",".join("?" * len(variants))
    hierarchy_sql = (
        f"{column} IN ("
        "WITH RECURSIVE scoped_paths(path) AS ("
        f"SELECT seed.path FROM file_index seed "
        f"WHERE seed.path COLLATE NOCASE IN ({placeholders}) "
        "UNION "
        "SELECT child.path FROM file_index child "
        "JOIN scoped_paths parent "
        "ON child.parent_path = parent.path COLLATE NOCASE"
        ") "
        f"SELECT path FROM scoped_paths WHERE path COLLATE NOCASE NOT IN ({placeholders})"
        ")"
    )
    return f"(({prefix_sql}) OR ({hierarchy_sql}))", [
        *prefix_params,
        *variants,
        *variants,
    ]


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


def prune_contained_paths(paths, cancel_check=None, sort_alpha=False):
    unique_paths = list(dict.fromkeys(path for path in paths if path))
    if sort_alpha:
        sort_key = lambda value: (len(os.path.normpath(value)), value.lower())
    else:
        sort_key = lambda value: len(os.path.normpath(value))
    sorted_paths = sorted(unique_paths, key=sort_key)
    kept = []
    kept_keys = set()

    for path in sorted_paths:
        if cancel_check:
            cancel_check()
        normalized = _path_key(path)
        current = normalized
        is_contained = False
        while current:
            if current in kept_keys:
                is_contained = True
                break
            parent = os.path.dirname(current.rstrip("\\/"))
            if not parent or parent == current:
                break
            current = parent
        if is_contained:
            continue
        kept.append(path)
        kept_keys.add(normalized)

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

    def summarize_filesystem_path(path):
        check_cancelled()
        if os.path.isfile(path):
            try:
                return 0, 1, os.path.getsize(path)
            except OSError:
                return 0, 1, 0
        if not os.path.isdir(path):
            return 0, 1, 0

        folder_count = 1
        file_count = 0
        size = 0
        for current, dir_names, file_names in os.walk(path):
            check_cancelled()
            folder_count += len(dir_names)
            file_count += len(file_names)
            for file_name in file_names:
                try:
                    size += os.path.getsize(os.path.join(current, file_name))
                except OSError:
                    continue
        return folder_count, file_count, size

    for path in pruned_paths:
        check_cancelled()
        row = rows_by_key.get(os.path.normcase(os.path.normpath(path)))
        if not row:
            path_folders, path_files, path_size = summarize_filesystem_path(path)
            folders += path_folders
            files += path_files
            total_size += path_size
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

    def _refresh_parent_folder_summaries(self, connection, parent_paths):
        for parent_path in parent_paths:
            if not parent_path:
                continue
            try:
                with os.scandir(parent_path) as entries:
                    physical_child_count = sum(1 for _entry in entries)
            except OSError:
                # Unknown is safer than falsely marking a folder empty.
                physical_child_count = -1
            cursor = connection.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM file_index "
                "WHERE parent_path = ? COLLATE NOCASE",
                (parent_path,),
            )
            visible_child_count = cursor.fetchone()[0] or 0
            connection.execute(
                "UPDATE folder_summary "
                "SET child_count = ?, physical_child_count = ? "
                "WHERE path = ? COLLATE NOCASE",
                (
                    visible_child_count,
                    physical_child_count,
                    parent_path,
                ),
            )

    def run(self):
        from src.file_index_tool import FileIndexTool

        deleted_count = 0
        errors = []
        deleted_paths = []
        affected_parent_paths = set()
        total = len(self.paths)
        tool = FileIndexTool()

        try:
            for index, path in enumerate(self.paths):
                if self.is_cancelled:
                    break

                self.delete_progress.emit(index, total, path)
                try:
                    parent_path = os.path.dirname(os.path.normpath(path))
                    try:
                        cursor = tool.conn.cursor()
                        cursor.execute(
                            "SELECT parent_path FROM file_index "
                            f"WHERE {case_insensitive_path_sql()} LIMIT 1",
                            (path,),
                        )
                        parent_row = cursor.fetchone()
                        if parent_row and parent_row[0]:
                            parent_path = parent_row[0]
                    except sqlite3.Error:
                        # Summary refresh is additive and must not block deletion.
                        pass
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
                    if parent_path:
                        affected_parent_paths.add(parent_path)
                except Exception as exc:
                    errors.append(f"{path}: {exc}")

                self.delete_progress.emit(index + 1, total, path)
            if affected_parent_paths:
                self._refresh_parent_folder_summaries(
                    tool.conn,
                    affected_parent_paths,
                )
                tool.conn.commit()
        finally:
            tool.close()

        self.delete_finished.emit(deleted_count, errors, self.is_cancelled, deleted_paths)


class DeleteProgressDialog(QDialog):
    cancel_requested = pyqtSignal()

    def __init__(self, total, parent=None):
        super().__init__(parent)
        self._allow_close = False
        self.total = int(total or 0)
        self.done = 0
        self.current_path = ""
        self.started_at = time.time()
        self.current_started_at = self.started_at
        self.cancel_requested_flag = False
        self.setWindowTitle("Deleting items")
        self.setModal(True)
        self.setFixedSize(620, 310)
        self.setObjectName("modalDialog")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        layout.setSpacing(SPACE_MD)

        item_word = "item" if self.total == 1 else "items"
        self.title_label = QLabel(f"Moving {self.total:,} {item_word} to the Recycle Bin")
        self.title_label.setObjectName("modalTitle")
        layout.addWidget(self.title_label)

        self.count_label = QLabel(f"Moved 0 of {self.total:,} {item_word}")
        self.count_label.setObjectName("modalDetail")
        layout.addWidget(self.count_label)

        self.remaining_label = QLabel(f"Remaining: {self.total:,} {item_word}")
        self.remaining_label.setObjectName("modalSecondary")
        layout.addWidget(self.remaining_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, self.total)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setObjectName("deleteProgressBar")
        layout.addWidget(self.progress)

        details = QFrame()
        details.setObjectName("modalSection")
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(SPACE_LG, SPACE_MD, SPACE_LG, SPACE_MD)
        details_layout.setSpacing(SPACE_XS)

        self.current_item_label = QLabel("Current item: Preparing deletion...")
        self.current_item_label.setObjectName("modalDetail")
        self.current_item_label.setWordWrap(True)
        details_layout.addWidget(self.current_item_label)

        self.location_label = QLabel("Location: --")
        self.location_label.setObjectName("modalSecondary")
        self.location_label.setWordWrap(True)
        details_layout.addWidget(self.location_label)

        self.elapsed_label = QLabel("Elapsed: 00:00")
        self.elapsed_label.setObjectName("modalSecondary")
        details_layout.addWidget(self.elapsed_label)

        self.working_label = QLabel("Working: preparing current item...")
        self.working_label.setObjectName("modalSecondary")
        details_layout.addWidget(self.working_label)

        layout.addWidget(details)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.cancel_button = QPushButton("Cancel after current item")
        self.cancel_button.setObjectName("modalCancel")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self._cancel)
        btn_row.addWidget(self.cancel_button)
        layout.addLayout(btn_row)

        self.status_timer = QTimer(self)
        self.status_timer.setInterval(1000)
        self.status_timer.timeout.connect(self._refresh_live_status)
        self.status_timer.start()

    def _format_elapsed(self, seconds):
        seconds = max(0, int(seconds or 0))
        minutes, secs = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _refresh_live_status(self):
        elapsed = self._format_elapsed(time.time() - self.started_at)
        current_elapsed = self._format_elapsed(time.time() - self.current_started_at)
        self.elapsed_label.setText(f"Elapsed: {elapsed}")
        if self.done >= self.total and self.total:
            self.working_label.setText("Working: finalizing...")
        elif self.current_path:
            self.working_label.setText(f"Working on current item: {current_elapsed}")
        else:
            self.working_label.setText(f"Working: preparing current item... {elapsed}")

    def _cancel(self):
        self.cancel_requested_flag = True
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Cancel requested")
        self.working_label.setText("Cancel requested - finishing current item...")
        self.cancel_requested.emit()

    def update_progress(self, done, total, path):
        path = path or ""
        if path != self.current_path:
            self.current_path = path
            self.current_started_at = time.time()
        self.done = int(done or 0)
        self.total = int(total or 0)
        self.progress.setMaximum(total)
        self.progress.setValue(done)
        item_word = "item" if self.total == 1 else "items"
        remaining = max(0, self.total - self.done)
        remaining_word = "item" if remaining == 1 else "items"
        self.count_label.setText(f"Moved {self.done:,} of {self.total:,} {item_word}")
        self.remaining_label.setText(f"Remaining: {remaining:,} {remaining_word}")

        normalized = os.path.normpath(path) if path else ""
        item_name = os.path.basename(normalized) if normalized else "Preparing deletion..."
        location = os.path.dirname(normalized) if normalized else "--"
        self.current_item_label.setText(f"Current item: {item_name or normalized}")
        self.current_item_label.setToolTip(path)
        self.location_label.setText(f"Location: {location or '--'}")
        self.location_label.setToolTip(path)
        if self.cancel_requested_flag:
            self.working_label.setText("Cancel requested - finishing current item...")
        else:
            self._refresh_live_status()

    def closeEvent(self, event):
        if hasattr(self, 'status_timer'):
            self.status_timer.stop()
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
        self.setFixedSize(380, 140)
        self.setObjectName("modalDialog")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        layout.setSpacing(SPACE_MD)

        title = QLabel(title)
        title.setObjectName("modalTitle")
        layout.addWidget(title)

        self.detail_label = QLabel(detail)
        self.detail_label.setObjectName("modalSecondary")
        self.detail_label.setWordWrap(True)
        self.detail_label.setMinimumHeight(24)
        self.detail_label.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Minimum,
        )
        layout.addWidget(self.detail_label)

        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        bar.setObjectName("loadingBar")
        layout.addWidget(bar)


EXPORT_COLUMNS = (
    ("name", "Name"),
    ("path", "Full Path"),
    ("type", "Type"),
    ("location", "Location / Parent Path"),
    ("last_modified", "Last Modified"),
    ("age", "Age"),
    ("size_bytes", "Size (bytes)"),
    ("size_formatted", "Size (formatted)"),
    ("extension", "Extension"),
    ("status", "Status"),
)
DEFAULT_EXPORT_COLUMNS = (
    "name",
    "type",
    "last_modified",
    "age",
    "size_formatted",
)


def _preferred_csv_delimiter():
    """Use the separator configured for spreadsheet lists on Windows."""
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Control Panel\International",
            ) as key:
                delimiter = str(winreg.QueryValueEx(key, "sList")[0] or "")
                if delimiter in {",", ";", "\t", "|"}:
                    return delimiter
        except (OSError, ValueError):
            pass
    return ","


def _export_status(is_folder, modified_time, is_empty, age_cutoff):
    if is_folder and is_empty:
        return "Empty"
    if not modified_time:
        return "Active"
    if age_cutoff is None:
        return "Inactive"
    return "Inactive" if modified_time <= age_cutoff else "Active"


def _export_listing_row(row, columns, age_cutoff=None):
    path, name, is_folder, size, modified_time, parent_path, extension, is_empty = row
    size = int(size or 0)
    modified_time = float(modified_time or 0)
    values = {
        "name": name or os.path.basename(path),
        "path": path,
        "type": "Folder" if is_folder else "File",
        "location": parent_path or "",
        "last_modified": (
            datetime.fromtimestamp(modified_time).strftime("%b %d, %Y")
            if modified_time else ""
        ),
        "age": format_age(modified_time),
        "size_bytes": size,
        "size_formatted": format_size(size),
        "extension": "" if is_folder else (extension or ""),
        "status": _export_status(bool(is_folder), modified_time, bool(is_empty), age_cutoff),
    }
    return [values[column] for column in columns]


class ExportDialog(QDialog):
    SCOPE_OPTIONS = (
        ("current_page", "Current page only"),
        ("all_matching", "All items matching current filters"),
        ("entire_scan", "Entire scan"),
        ("selected", "Selected / checked items only"),
        ("bulk_scope", "Current bulk delete scope"),
    )
    TYPE_OPTIONS = (
        ("listing", "Files and folders"),
        ("file_types", "File type breakdown"),
        ("folder_summary", "Folder summary"),
        ("delete_audit", "Delete audit log"),
        ("scan_history", "Scan history"),
    )
    TYPE_DESCRIPTIONS = {
        "listing": "Export file and folder rows with the columns you choose below.",
        "file_types": "Export one summary row per file extension, including size and file count.",
        "folder_summary": "Export folder paths with their total size, file count, and folder count.",
        "delete_audit": "Export saved delete authorization and completion history.",
        "scan_history": "Export one summary row for each previously scanned root folder.",
    }
    SCOPE_DESCRIPTIONS = {
        "current_page": "Only export the rows visible on the current results page.",
        "all_matching": "Export every item matching the current filters and folder scope across all pages.",
        "entire_scan": "Export the complete indexed scan, regardless of the current filters or page.",
        "selected": "Only export items you have manually selected or checked.",
        "bulk_scope": "Export the complete all-pages selection currently prepared for bulk deletion.",
    }

    def __init__(self, has_selection=False, has_bulk_scope=False, settings=None, parent=None):
        super().__init__(parent)
        self.settings = settings or QSettings("IBMS", "Watchdog")
        self.setWindowTitle("Export CSV")
        self.setModal(True)
        self.setObjectName("modalDialog")
        self.setMinimumSize(400, 360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
        layout.setSpacing(SPACE_MD)

        title = QLabel("Export data")
        title.setObjectName("modalTitle")
        layout.addWidget(title)

        detail = QLabel("Choose the report, scope, and columns to include.")
        detail.setObjectName("modalDetail")
        detail.setWordWrap(True)
        layout.addWidget(detail)

        self.options_scroll = QScrollArea()
        self.options_scroll.setObjectName("exportOptionsScroll")
        self.options_scroll.setWidgetResizable(True)
        self.options_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.options_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.options_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.options_content = QWidget()
        self.options_content.setObjectName("exportOptionsContent")
        options_layout = QVBoxLayout(self.options_content)
        options_layout.setContentsMargins(0, 0, SPACE_XS, 0)
        options_layout.setSpacing(SPACE_MD)
        self.options_scroll.setWidget(self.options_content)
        layout.addWidget(self.options_scroll, 1)

        type_row = QHBoxLayout()
        type_label = QLabel("Export type")
        type_label.setObjectName("sectionLabel")
        self.type_combo = QComboBox()
        for key, label in self.TYPE_OPTIONS:
            self.type_combo.addItem(label, key)
            self.type_combo.setItemData(
                self.type_combo.count() - 1,
                self.TYPE_DESCRIPTIONS[key],
                Qt.ItemDataRole.ToolTipRole,
            )
        type_row.addWidget(type_label)
        type_row.addWidget(self.type_combo, 1)
        options_layout.addLayout(type_row)
        self.type_help = QLabel()
        self.type_help.setObjectName("exportOptionHelp")
        self.type_help.setWordWrap(True)
        options_layout.addWidget(self.type_help)

        self.scope_frame = QFrame()
        self.scope_frame.setObjectName("modalSection")
        scope_layout = QVBoxLayout(self.scope_frame)
        scope_layout.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)
        scope_layout.setSpacing(SPACE_SM)
        scope_title = QLabel("Scope")
        scope_title.setObjectName("sectionLabel")
        scope_layout.addWidget(scope_title)
        self.scope_group = QButtonGroup(self)
        self.scope_buttons = {}
        for index, (key, label) in enumerate(self.SCOPE_OPTIONS):
            button = QRadioButton(label)
            button.setChecked(index == 0)
            button.setToolTip(self.SCOPE_DESCRIPTIONS[key])
            button.setProperty("exportScope", True)
            self.scope_group.addButton(button)
            self.scope_buttons[key] = button
            scope_layout.addWidget(button)
        self.scope_buttons["selected"].setEnabled(has_selection)
        self.scope_buttons["bulk_scope"].setEnabled(has_bulk_scope)
        if not has_selection:
            self.scope_buttons["selected"].setText(
                "Selected / checked items only (Unavailable)"
            )
            self.scope_buttons["selected"].setProperty("unavailable", True)
            self.scope_buttons["selected"].setToolTip(
                "Select or check at least one item before using this export scope."
            )
        if not has_bulk_scope:
            self.scope_buttons["bulk_scope"].setText(
                "Current bulk delete scope (Unavailable)"
            )
            self.scope_buttons["bulk_scope"].setProperty("unavailable", True)
            self.scope_buttons["bulk_scope"].setToolTip(
                "Create an all-pages bulk selection before using this export scope."
            )
        self.scope_help = QLabel()
        self.scope_help.setObjectName("exportOptionHelp")
        self.scope_help.setWordWrap(True)
        scope_layout.addWidget(self.scope_help)
        options_layout.addWidget(self.scope_frame)

        self.columns_frame = QFrame()
        self.columns_frame.setObjectName("modalSection")
        columns_layout = QGridLayout(self.columns_frame)
        columns_layout.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)
        columns_layout.setHorizontalSpacing(SPACE_LG)
        columns_layout.setVerticalSpacing(SPACE_SM)
        columns_title = QLabel("Columns")
        columns_title.setObjectName("sectionLabel")
        columns_layout.addWidget(columns_title, 0, 0, 1, 2)
        stored = self.settings.value("export_columns", list(DEFAULT_EXPORT_COLUMNS))
        if isinstance(stored, str):
            stored = [part for part in stored.split(",") if part]
        stored = set(stored or DEFAULT_EXPORT_COLUMNS)
        self.column_checks = {}
        for index, (key, label) in enumerate(EXPORT_COLUMNS):
            checkbox = QCheckBox(label)
            checkbox.setChecked(key in stored)
            self.column_checks[key] = checkbox
            columns_layout.addWidget(checkbox, 1 + index // 2, index % 2)
        options_layout.addWidget(self.columns_frame)

        self.include_summary = QCheckBox("Include export summary header")
        self.include_summary.setChecked(False)
        options_layout.addWidget(self.include_summary)

        self.validation_label = QLabel("")
        self.validation_label.setObjectName("authMessage")
        self.validation_label.setVisible(False)
        options_layout.addWidget(self.validation_label)
        options_layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setObjectName("modalCancel")
        cancel.clicked.connect(self.reject)
        self.continue_button = QPushButton("Choose file...")
        self.continue_button.setObjectName("primaryBtn")
        self.continue_button.clicked.connect(self._accept_if_valid)
        buttons.addWidget(cancel)
        buttons.addWidget(self.continue_button)
        layout.addLayout(buttons)

        self.type_combo.currentIndexChanged.connect(self._update_type_state)
        self.scope_group.buttonClicked.connect(self._update_option_help)
        for checkbox in self.column_checks.values():
            checkbox.toggled.connect(self._update_validation)
        self._fit_to_available_screen()
        self._update_type_state()

    def _fit_to_available_screen(self):
        screen = self.parentWidget().screen() if self.parentWidget() else None
        screen = screen or QApplication.primaryScreen()
        if screen is None:
            self._listing_height = 720
            self.resize(560, self._listing_height)
            return

        available = screen.availableGeometry()
        max_width = max(360, available.width() - (2 * SPACE_XL))
        max_height = max(360, available.height() - (2 * SPACE_XL))
        self.setMinimumSize(min(400, max_width), min(360, max_height))
        self._listing_height = min(720, max_height)
        self.resize(min(560, max_width), self._listing_height)

    def export_type(self):
        return self.type_combo.currentData()

    def export_scope(self):
        for key, button in self.scope_buttons.items():
            if button.isChecked():
                return key
        return "current_page"

    def selected_columns(self):
        return [
            key for key, _label in EXPORT_COLUMNS
            if self.column_checks[key].isChecked()
        ]

    def _update_type_state(self):
        is_listing = self.export_type() == "listing"
        self.scope_frame.setVisible(is_listing)
        self.columns_frame.setVisible(is_listing)
        self._update_option_help()
        self._update_validation()
        target_height = self._listing_height if is_listing else min(360, self._listing_height)
        self.resize(self.width(), target_height)

    def _update_option_help(self, _button=None):
        export_type = self.export_type()
        self.type_help.setText(self.TYPE_DESCRIPTIONS.get(export_type, ""))
        scope = self.export_scope()
        self.scope_help.setText(self.SCOPE_DESCRIPTIONS.get(scope, ""))

    def _update_validation(self):
        valid = self.export_type() != "listing" or bool(self.selected_columns())
        self.continue_button.setEnabled(valid)
        self.validation_label.setText("" if valid else "Select at least one column.")
        self.validation_label.setVisible(not valid)

    def _accept_if_valid(self):
        if self.export_type() == "listing" and not self.selected_columns():
            self._update_validation()
            return
        self.settings.setValue("export_columns", self.selected_columns())
        self.accept()


class ExportProgressDialog(QDialog):
    cancel_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._allow_close = False
        self.setWindowTitle("Exporting")
        self.setModal(True)
        self.setFixedSize(520, 190)
        self.setObjectName("modalDialog")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        layout.setSpacing(SPACE_MD)
        title = QLabel("Creating CSV report")
        title.setObjectName("modalTitle")
        layout.addWidget(title)
        self.detail_label = QLabel("Preparing export...")
        self.detail_label.setObjectName("modalDetail")
        layout.addWidget(self.detail_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setObjectName("loadingBar")
        layout.addWidget(self.progress)
        row = QHBoxLayout()
        row.addStretch()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("modalCancel")
        self.cancel_button.clicked.connect(self._cancel)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)

    def _cancel(self):
        if not self.cancel_button.isEnabled():
            return
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Stopping...")
        self.cancel_requested.emit()

    def update_progress(self, done, total, detail):
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(min(done, total))
            self.detail_label.setText(f"Exported {done:,} of {total:,} rows")
        else:
            self.progress.setRange(0, 0)
            self.detail_label.setText(detail or f"Exported {done:,} rows")

    def closeEvent(self, event):
        if self._allow_close:
            event.accept()
            return
        self._cancel()
        event.ignore()


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
        self.excluded_path_index = PathKeyIndex(self.excluded_paths)
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
        return _path_key(path)

    def _prune_paths(self, paths):
        return prune_contained_paths(
            paths,
            cancel_check=self._raise_if_cancelled,
            sort_alpha=True,
        )

    def _has_excluded_ancestor(self, path, include_self=True):
        return self.excluded_path_index.has_ancestor(
            self._path_key(path),
            include_self=include_self,
        )

    def _has_excluded_descendant(self, path):
        return self.excluded_path_index.has_descendant(self._path_key(path))

    def _expand_paths_around_exclusions(self, cursor, selected_roots):
        if not self.excluded_paths:
            return selected_roots

        expanded = []
        for root_path in selected_roots:
            self._raise_if_cancelled()
            root_key = self._path_key(root_path)
            if (
                root_key not in self.excluded_paths
                and not self.excluded_path_index.has_descendant(root_key)
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
        excluded_keys = bulk_scope_excluded_keys(self.bulk_scope)
        for path, is_folder in rows:
            self._raise_if_cancelled()
            if self._path_key(path) in excluded_keys:
                continue
            if not is_folder:
                paths.append(path)
            elif folder_delete_mode == 'all':
                paths.append(path)
            elif folder_delete_mode == 'empty_only':
                if folder_is_physically_empty(cursor, path):
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
                total = max(0, (total or 0) - len(bulk_scope_excluded_keys(self.bulk_scope)))
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
        self.setFixedSize(600, 390)
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

        self.primary_count_label = QLabel("")
        self.primary_count_label.setObjectName("fileTypesMetricValue")
        self.primary_count_label.setVisible(False)
        layout.addWidget(self.primary_count_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setObjectName("deleteProgressBar")
        layout.addWidget(self.progress)

        self.summary_widget = QFrame()
        self.summary_widget.setObjectName("modalSection")
        summary_layout = QGridLayout(self.summary_widget)
        summary_layout.setContentsMargins(SPACE_LG, SPACE_MD, SPACE_LG, SPACE_MD)
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

        note_row = QHBoxLayout()
        note_row.setSpacing(SPACE_SM)
        note_row.setContentsMargins(0, 0, 0, 0)
        recycle_icon = QLabel()
        recycle_icon.setPixmap(
            QIcon(
                os.path.join(
                    os.path.dirname(__file__),
                    "assets",
                    "toolbar_trash.svg",
                )
            ).pixmap(QSize(18, 18))
        )
        recycle_icon.setFixedSize(22, 22)
        recycle_icon.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        note_row.addWidget(recycle_icon)
        self.recycle_bin_note = QLabel(
            "Items will be moved to the Recycle Bin and can be restored."
        )
        self.recycle_bin_note.setObjectName("modalSecondary")
        self.recycle_bin_note.setWordWrap(True)
        note_row.addWidget(self.recycle_bin_note, 1)
        layout.addLayout(note_row)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("modalCancel")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.setDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_button)
        self.delete_button = QPushButton("Delete Selected")
        self.delete_button.setObjectName("destructiveBtn")
        self.delete_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_button.setAutoDefault(False)
        self.delete_button.setDefault(False)
        self.delete_button.setIcon(
            QIcon(
                os.path.join(
                    os.path.dirname(__file__),
                    "assets",
                    "toolbar_trash_white.svg",
                )
            )
        )
        self.delete_button.setIconSize(QSize(18, 18))
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self.accept)
        btn_row.addWidget(self.delete_button)
        layout.addLayout(btn_row)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
            return
        super().keyPressEvent(event)

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
            self.primary_count_label.setVisible(False)
            self.summary_widget.setVisible(False)
            self.preview_note.setVisible(False)
            self.error_label.setVisible(False)
            self.progress.setRange(0, 0)
            self.progress.setVisible(True)
            self.delete_button.setEnabled(False)
            return

        self.title_label.setText("Review before deleting")
        self.detail_label.setText("Check the deletion summary before continuing.")
        item_word = "item" if delete_operations == 1 else "items"
        self.primary_count_label.setText(f"{delete_operations:,} {item_word} to delete")
        self.primary_count_label.setVisible(True)
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
        self.primary_count_label.setVisible(False)
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
            f"Enter your credentials to move {self.item_count} "
            f"item{'s' if self.item_count != 1 else ''} ({size_text}) "
            "to the Recycle Bin."
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
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_button)
        self.submit_button = QPushButton("Authorize delete")
        self.submit_button.setObjectName("primaryBtn")
        self.submit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.submit_button.clicked.connect(self._submit)
        btn_row.addWidget(self.submit_button)
        layout.addLayout(btn_row)

        self.password_input.returnPressed.connect(self._submit)
        self.username_input.returnPressed.connect(self._username_return_pressed)
        self.username_input.textChanged.connect(self._update_lockout_state)
        self.cooldown_timer = QTimer(self)
        self.cooldown_timer.setInterval(1000)
        self.cooldown_timer.timeout.connect(self._update_lockout_state)
        self._update_lockout_state()

    def _username_return_pressed(self):
        if self.password_input.text():
            self._submit()
        else:
            self.password_input.setFocus()

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

    def __init__(self, request_id, parent=None):
        super().__init__(parent)
        self.request_id = request_id
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        from src.file_index_tool import FileIndexTool

        tool = FileIndexTool()
        try:
            if self.is_cancelled:
                return
            rows = tool.extension_breakdown()
            if not self.is_cancelled:
                self.breakdown_ready.emit(self.request_id, rows)
        except Exception as exc:
            if not self.is_cancelled:
                self.breakdown_failed.emit(self.request_id, str(exc))
        finally:
            tool.close()


class FileTypeBarDelegate(QStyledItemDelegate):
    @staticmethod
    def format_share(share):
        if share <= 0:
            return "0%"
        if share < 0.001:
            return "<0.1%"
        return f"{share * 100:.1f}%"

    def paint(self, painter, option, index):
        painter.save()
        try:
            share = float(index.data(Qt.ItemDataRole.UserRole) or 0.0)
        except (TypeError, ValueError):
            share = 0.0

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = current_palette()

        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor(palette["accent_tint"]))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(option.rect, QColor(palette["surface_hover"]))

        content_rect = option.rect.adjusted(12, 0, -12, 0)
        percentage_width = 54
        track_rect = QRect(
            content_rect.left(),
            content_rect.center().y() - 4,
            max(0, content_rect.width() - percentage_width - SPACE_SM),
            8,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(palette["border"]))
        painter.drawRoundedRect(track_rect, 4, 4)

        if share > 0:
            width = max(2, int(track_rect.width() * min(1.0, share)))
            bar_rect = QRect(
                track_rect.left(),
                track_rect.top(),
                width,
                track_rect.height(),
            )
            painter.setBrush(QColor(palette["accent"]))
            painter.drawRoundedRect(bar_rect, 4, 4)

        percentage_rect = QRect(
            track_rect.right() + SPACE_SM,
            option.rect.top(),
            percentage_width,
            option.rect.height(),
        )
        painter.setPen(QColor(palette["text_muted"]))
        painter.drawText(
            percentage_rect,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            self.format_share(share),
        )
        painter.restore()


class NumericTableWidgetItem(QTableWidgetItem):
    def __init__(self, text, numeric_value):
        super().__init__(text)
        self.numeric_value = numeric_value

    def __lt__(self, other):
        if isinstance(other, NumericTableWidgetItem):
            return self.numeric_value < other.numeric_value
        return super().__lt__(other)


class FileTypesDialog(QDialog):
    extension_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("File Types")
        self.setModal(False)
        self.resize(700, 560)
        self.setMinimumSize(600, 420)
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

        self.summary_strip = QFrame()
        self.summary_strip.setObjectName("fileTypesSummary")
        summary_layout = QHBoxLayout(self.summary_strip)
        summary_layout.setContentsMargins(SPACE_LG, SPACE_SM, SPACE_LG, SPACE_SM)
        summary_layout.setSpacing(SPACE_LG)
        self.summary_types_value, types_metric = self._make_summary_metric("Types")
        self.summary_files_value, files_metric = self._make_summary_metric("Files")
        self.summary_size_value, size_metric = self._make_summary_metric("Total size")
        summary_layout.addWidget(types_metric)
        summary_layout.addWidget(self._summary_divider())
        summary_layout.addWidget(files_metric)
        summary_layout.addWidget(self._summary_divider())
        summary_layout.addWidget(size_metric)
        summary_layout.addStretch(1)
        self.summary_strip.setVisible(False)
        layout.addWidget(self.summary_strip)

        self.table = QTableWidget(0, 4)
        self.table.setObjectName("fileTypesTable")
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.setHorizontalHeaderLabels(["Type", "Share", "Size", "Files"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.setShowGrid(False)
        self.table.setMouseTracking(True)
        self.table.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self.table.setItemDelegateForColumn(1, FileTypeBarDelegate(self.table))
        header = self.table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 200)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 80)
        self.table.setSortingEnabled(True)
        header.setSortIndicator(2, Qt.SortOrder.DescendingOrder)
        self.table.cellClicked.connect(self._activate_row)
        layout.addWidget(self.table, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(SPACE_MD)
        self.hint_label = QLabel("Select a type to show its matching files.")
        self.hint_label.setObjectName("modalSecondary")
        footer.addWidget(self.hint_label, 1)
        self.close_btn = QPushButton("Close")
        self.close_btn.setObjectName("modalCancel")
        self.close_btn.setFixedSize(100, 36)
        self.close_btn.clicked.connect(self.reject)
        footer.addWidget(self.close_btn)
        layout.addLayout(footer)

    @staticmethod
    def _summary_divider():
        divider = QFrame()
        divider.setObjectName("fileTypesSummaryDivider")
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFixedWidth(1)
        return divider

    @staticmethod
    def _make_summary_metric(label_text):
        container = QWidget()
        metric_layout = QVBoxLayout(container)
        metric_layout.setContentsMargins(0, 0, 0, 0)
        metric_layout.setSpacing(0)
        value = QLabel("--")
        value.setObjectName("fileTypesMetricValue")
        label = QLabel(label_text)
        label.setObjectName("fileTypesMetricLabel")
        metric_layout.addWidget(value)
        metric_layout.addWidget(label)
        return value, container

    def set_loading(self):
        self.detail_label.setText("Calculating file type breakdown...")
        self.detail_label.setVisible(True)
        self.summary_strip.setVisible(False)
        self.table.setRowCount(0)

    def set_error(self, message):
        self.detail_label.setText(f"Could not load file types: {message}")
        self.detail_label.setVisible(True)
        self.summary_strip.setVisible(False)
        self.table.setRowCount(0)

    def set_rows(self, rows):
        total_size = sum(size for _label, size, _count in rows) or 0
        total_files = sum(count for _label, _size, count in rows) or 0
        self.detail_label.setVisible(False)
        self.summary_types_value.setText(f"{len(rows):,}")
        self.summary_files_value.setText(f"{total_files:,}")
        self.summary_size_value.setText(format_size(total_size))
        self.summary_strip.setVisible(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for row, (label, total_size_for_type, file_count) in enumerate(rows):
            raw_extension = None
            if label == "(no extension)":
                raw_extension = ""
            else:
                raw_extension = label

            display_label = label
            if label != "(no extension)" and not label.startswith("."):
                display_label = f".{label}"
            type_item = QTableWidgetItem(display_label)
            type_item.setData(Qt.ItemDataRole.UserRole, raw_extension)
            type_item.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            self.table.setItem(row, 0, type_item)

            share = (total_size_for_type / total_size) if total_size else 0.0
            share_item = NumericTableWidgetItem("", share)
            share_item.setData(Qt.ItemDataRole.UserRole, share)
            self.table.setItem(row, 1, share_item)

            size_item = NumericTableWidgetItem(
                format_size(total_size_for_type),
                total_size_for_type,
            )
            size_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.table.setItem(row, 2, size_item)
            files_item = NumericTableWidgetItem(f"{file_count:,}", file_count)
            files_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.table.setItem(row, 3, files_item)
        self.table.setSortingEnabled(True)
        self.table.sortItems(2, Qt.SortOrder.DescendingOrder)

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

    class _Cancelled(Exception):
        pass

    def __init__(self, request_id, options, parent=None):
        super().__init__(parent)
        self.request_id = request_id
        self.options = options
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
            raise PageLoadThread._Cancelled()

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
        except PageLoadThread._Cancelled:
            return
        except Exception as exc:
            if not self.is_cancelled:
                self.page_failed.emit(self.request_id, str(exc))

    def _load(self):
        from src.file_index_tool import FileIndexTool

        self._raise_if_cancelled()
        limit = self.options['limit']
        offset = self.options['offset']
        paginated = self.options.get('paginated', True)
        view_mode = self.options['view_mode']
        status_filter = self.options['status_filter']
        age_cutoff = self.options['age_cutoff']
        videos_only = self.options['videos_only']
        name_filter = (self.options.get('name_filter') or '').strip()
        extension_filter = self.options.get('extension_filter')
        folder_scope = self.options.get('folder_scope')
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

        if folder_scope:
            scope_sql, scope_params = descendant_scope_sql(folder_scope)
            where_clauses.append(f"({scope_sql})")
            params.extend(scope_params)

        scan_root = self.options.get('scan_root')
        if scan_root:
            root_variants = equivalent_path_variants(scan_root)
            placeholders = ",".join("?" * len(root_variants))
            where_clauses.append(
                f"path COLLATE NOCASE NOT IN ({placeholders})"
            )
            params.extend(root_variants)

        if where_clauses:
            where_sql = " WHERE " + " AND ".join(where_clauses)
            query += where_sql
            count_query += where_sql

        folders_with_children = set()
        filesystem_scope_fallback = False
        folder_cache = self.options.get('folder_cache')
        cached_scope_result = None
        if (
            folder_scope
            and folder_cache is not None
            and hasattr(folder_cache, 'summary_descendant_count')
            and folder_cache.summary_descendant_count(folder_scope) is not None
        ):
            cached_scope_result = self._load_filtered_filesystem_scope(folder_scope)

        if cached_scope_result is not None:
            (
                rows,
                original_fetched_order,
                original_fetched_path_keys,
                folders_with_children,
                total_matches,
            ) = cached_scope_result
            filesystem_scope_fallback = True
        else:
            tool = FileIndexTool()
            self._connection = tool.conn
            try:
                cursor = tool.conn.cursor()
                self._raise_if_cancelled()
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
                self._raise_if_cancelled()
                if scan_root:
                    scan_root_key = _path_key(scan_root)
                    filtered_rows = [
                        row for row in rows
                        if _path_key(row[0]) != scan_root_key
                    ]
                    total_matches = max(
                        0,
                        total_matches - (len(rows) - len(filtered_rows)),
                    )
                    rows = filtered_rows
                if not rows and total_matches == 0 and folder_scope:
                    filesystem_result = self._load_filtered_filesystem_scope(folder_scope)
                    if filesystem_result is not None:
                        (
                            rows,
                            original_fetched_order,
                            original_fetched_path_keys,
                            folders_with_children,
                            total_matches,
                        ) = filesystem_result
                        filesystem_scope_fallback = True

                if not filesystem_scope_fallback:
                    original_fetched_order = {
                        _path_key(row[0]): index
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
                        _path_key(path)
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

                    folder_paths = [row[0] for row in rows if row[2]]
                    for index in range(0, len(folder_paths), 900):
                        batch = folder_paths[index:index + 900]
                        placeholders = ",".join("?" * len(batch))
                        cursor.execute(
                            f"SELECT DISTINCT parent_path FROM file_index "
                            f"WHERE parent_path COLLATE NOCASE IN ({placeholders})",
                            batch,
                        )
                        folders_with_children.update(
                            _path_key(parent_path)
                            for (parent_path,) in cursor.fetchall()
                            if parent_path
                        )
            finally:
                self._connection = None
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
            if is_folder and folder_cache is not None:
                size = getattr(folder_cache, 'folder_sizes', {}).get(path_key, size or 0)
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
                '_children_loaded': (
                    (bool(filtered_expanded_tree) and not filesystem_scope_fallback)
                    or not (is_folder and path_key in folders_with_children)
                ),
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

        if filesystem_scope_fallback and view_mode == 'Tree':
            for node in nodes_by_path.values():
                if node.get('children'):
                    node['_children_loaded'] = True

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
            if folder_scope:
                self._hide_folder_scope_context(root_node, folder_scope)
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
            'debug_info': "filtered_source=filesystem" if filesystem_scope_fallback else None,
            'filesystem_scope_fallback': filesystem_scope_fallback,
        }

    def _load_filtered_filesystem_scope(self, folder_scope):
        cache = self.options.get('folder_cache')
        exclusions = self.options.get('scan_exclusions')
        exclusion_key = json.dumps(
            exclusions.to_dict() if exclusions is not None else {},
            sort_keys=True,
        )
        snapshot_key = (_path_key(folder_scope), exclusion_key)
        cached_snapshot = None
        if cache is not None and hasattr(cache, 'filesystem_snapshot'):
            cached_snapshot = cache.filesystem_snapshot(snapshot_key)
        if (
            cached_snapshot is None
            and cache is not None
            and hasattr(cache, 'snapshot_subtree')
        ):
            cached_snapshot = cache.snapshot_subtree(folder_scope)
            if (
                cached_snapshot is not None
                and hasattr(cache, 'store_filesystem_snapshot')
            ):
                cache.store_filesystem_snapshot(snapshot_key, cached_snapshot)
        if cached_snapshot is not None:
            all_rows, physical_child_keys, visible_child_keys = cached_snapshot
        else:
            all_rows = {}
            physical_child_keys = set()
            visible_child_keys = set()
            pending = [os.path.normpath(folder_scope)]
            visited = set()

            while pending:
                self._raise_if_cancelled()
                folder_path = pending.pop()
                folder_key = _path_key(folder_path)
                if folder_key in visited:
                    continue
                visited.add(folder_key)
                try:
                    with os.scandir(folder_path) as entries:
                        folder_entries = list(entries)
                except OSError:
                    continue

                if folder_entries:
                    physical_child_keys.add(folder_key)
                for entry in folder_entries:
                    self._raise_if_cancelled()
                    try:
                        is_folder = entry.is_dir(follow_symlinks=True)
                        stat_result = entry.stat(follow_symlinks=True)
                    except OSError:
                        continue
                    if self._filesystem_entry_is_excluded(
                        entry.name,
                        is_folder,
                        0 if is_folder else stat_result.st_size,
                    ):
                        continue
                    visible_child_keys.add(folder_key)
                    row = (
                        entry.path,
                        entry.name,
                        int(is_folder),
                        0 if is_folder else stat_result.st_size,
                        stat_result.st_mtime,
                        folder_path,
                    )
                    all_rows[_path_key(entry.path)] = row
                    if is_folder:
                        pending.append(entry.path)

            all_rows = self._prune_exclusion_only_folders(
                all_rows,
                physical_child_keys,
            )
            visible_child_keys = {
                _path_key(row[5])
                for row in all_rows.values()
                if row[5]
            }
            folder_sizes = {
                key: 0
                for key, row in all_rows.items()
                if row[2]
            }
            for key, row in sorted(
                all_rows.items(),
                key=lambda item: item[0].count(os.sep),
                reverse=True,
            ):
                self._raise_if_cancelled()
                path, name, is_folder, size, modified_time, parent_path = row
                effective_size = (
                    folder_sizes.get(key, 0)
                    if is_folder
                    else (size or 0)
                )
                if is_folder:
                    all_rows[key] = (
                        path,
                        name,
                        is_folder,
                        effective_size,
                        modified_time,
                        parent_path,
                    )
                parent_key = _path_key(parent_path)
                if parent_key in folder_sizes:
                    folder_sizes[parent_key] += effective_size
            if cache is not None and hasattr(cache, 'store_filesystem_snapshot'):
                cache.store_filesystem_snapshot(
                    snapshot_key,
                    (all_rows, physical_child_keys, visible_child_keys),
                )

        matched_rows = [
            row for row in all_rows.values()
            if self._filesystem_row_matches(
                row,
                _path_key(row[0]) in physical_child_keys,
            )
        ]
        matched_rows.sort(
            key=lambda row: (
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
            ),
            reverse=self.options['sort_desc'],
        )
        total_matches = len(matched_rows)
        if self.options.get('paginated', True):
            offset = self.options['offset']
            matched_rows = matched_rows[offset:offset + self.options['limit']]

        original_order = {
            _path_key(row[0]): index
            for index, row in enumerate(matched_rows)
        }
        original_keys = set(original_order)
        rows_by_key = {_path_key(row[0]): row for row in matched_rows}
        if self.options['view_mode'] == 'Tree':
            scope_key = _path_key(folder_scope)
            for row in matched_rows:
                parent_path = row[5]
                while parent_path and _path_key(parent_path) != scope_key:
                    parent_key = _path_key(parent_path)
                    parent_row = all_rows.get(parent_key)
                    if parent_row is None:
                        break
                    rows_by_key.setdefault(parent_key, parent_row)
                    parent_path = parent_row[5]

        return (
            list(rows_by_key.values()),
            original_order,
            original_keys,
            visible_child_keys,
            total_matches,
        )

    def _prune_exclusion_only_folders(self, all_rows, physical_child_keys):
        children_by_parent = {}
        for key, row in all_rows.items():
            children_by_parent.setdefault(_path_key(row[5]), []).append((key, row))

        visible_folder_keys = set()
        hidden_folder_keys = set()
        folder_rows = [
            (key, row)
            for key, row in all_rows.items()
            if row[2]
        ]
        folder_rows.sort(
            key=lambda item: item[0].count(os.sep),
            reverse=True,
        )
        for key, _row in folder_rows:
            children = children_by_parent.get(key, [])
            is_physically_empty = key not in physical_child_keys
            has_visible_file = any(not child_row[2] for _child_key, child_row in children)
            has_visible_folder = any(
                child_key in visible_folder_keys
                for child_key, child_row in children
                if child_row[2]
            )
            if is_physically_empty or has_visible_file or has_visible_folder:
                visible_folder_keys.add(key)
            else:
                hidden_folder_keys.add(key)

        return {
            key: row
            for key, row in all_rows.items()
            if key not in hidden_folder_keys
        }

    def _filesystem_row_matches(self, row, has_children):
        _path, name, is_folder, _size, modified_time, _parent_path = row
        is_folder = bool(is_folder)
        view_mode = self.options['view_mode']
        status_filter = self.options['status_filter']
        age_cutoff = self.options['age_cutoff']
        videos_only = self.options['videos_only']
        name_filter = (self.options.get('name_filter') or '').strip().casefold()
        extension_filter = self.options.get('extension_filter')
        extension = os.path.splitext(name)[1].lower() if not is_folder else ""

        if view_mode == 'Files' and is_folder:
            return False
        if view_mode == 'Folders' and not is_folder:
            return False
        if name_filter and name_filter not in name.casefold():
            return False
        if extension_filter is not None and (is_folder or extension != extension_filter.lower()):
            return False
        if videos_only and (is_folder or extension not in VIDEO_EXTENSIONS):
            return False

        if status_filter == 'Empty':
            if (
                not is_folder
                or has_children
                or name.lower() in ('.git', '__pycache__', 'venv', '.venv', 'node_modules')
            ):
                return False
            return age_cutoff is None or modified_time <= age_cutoff
        if status_filter == 'Inactive':
            if view_mode == 'Tree' and is_folder:
                return False
            return age_cutoff is None or modified_time <= age_cutoff
        if status_filter == 'Active':
            return age_cutoff is not None and modified_time > age_cutoff
        if age_cutoff is not None:
            return modified_time <= age_cutoff
        return True

    def _filesystem_entry_is_excluded(self, name, is_folder, size):
        exclusions = self.options.get('scan_exclusions')
        if exclusions is None:
            return False
        if is_folder:
            return exclusions.matches_excluded_folder(name)
        extension = os.path.splitext(name)[1].lower()
        return (
            exclusions.matches_excluded_extension(extension)
            or (
                exclusions.min_file_size_bytes > 0
                and (size or 0) < exclusions.min_file_size_bytes
            )
        )

    def _filesystem_folder_size(self, folder_path):
        cache = self.options.get('folder_cache')
        if cache is not None:
            folder_sizes = getattr(cache, 'folder_sizes', {})
            folder_key = _path_key(folder_path)
            if folder_key in folder_sizes:
                return folder_sizes[folder_key] or 0
        total = 0
        for current, dir_names, file_names in os.walk(folder_path):
            self._raise_if_cancelled()
            dir_names[:] = [
                name for name in dir_names
                if not self._filesystem_entry_is_excluded(name, True, 0)
            ]
            for file_name in file_names:
                file_path = os.path.join(current, file_name)
                try:
                    size = os.path.getsize(file_path)
                except OSError:
                    continue
                if not self._filesystem_entry_is_excluded(file_name, False, size):
                    total += size
        return total

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

    def _hide_folder_scope_context(self, root_node, folder_scope):
        scope_key = _path_key(folder_scope)
        stack = list(root_node.get('children', []))
        while stack:
            node = stack.pop()
            node_path = node.get('path')
            if node_path and _path_key(node_path) == scope_key:
                root_node['children'] = list(node.get('children', []))
                return
            stack.extend(node.get('children', []))

    def _load_lazy_show_all_tree(self):
        from src.file_index_tool import FileIndexTool

        root_path = self.options.get('folder_scope') or self.options.get('scan_root') or ""
        root_path = os.path.normpath(root_path) if root_path else root_path
        is_scoped = bool(self.options.get('folder_scope'))
        cache = self.options.get('folder_cache')
        cached_children = None
        if cache and cache.child_count(root_path) > 0:
            cached_children = cache.children_for(
                root_path,
                self.options['sort_column'],
                self.options['sort_desc'],
            )
        if cached_children:
            total_matches = (
                cache.descendant_count(root_path)
                if is_scoped
                else max(cache.item_count() - 1, 0)
            )
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
        self._connection = tool.conn
        try:
            cursor = tool.conn.cursor()
            self._raise_if_cancelled()
            if is_scoped:
                stored_root_path = root_path
                total_matches = (
                    cache.summary_descendant_count(root_path)
                    if cache is not None
                    and hasattr(cache, 'summary_descendant_count')
                    else None
                )
                if total_matches is None:
                    path_variants = equivalent_path_variants(root_path)
                    placeholders = ",".join("?" * len(path_variants))
                    cursor.execute(
                        "SELECT file_count, folder_count FROM folder_summary "
                        f"WHERE path IN ({placeholders}) LIMIT 1",
                        path_variants,
                    )
                    summary_row = cursor.fetchone()
                    total_matches = (
                        max(0, int(summary_row[0] or 0) + int(summary_row[1] or 0) - 1)
                        if summary_row
                        else None
                    )
            else:
                cursor.execute("SELECT COUNT(*) FROM file_index")
                total_rows = cursor.fetchone()[0] or 0

            if not is_scoped:
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

            parent_variants = (
                equivalent_path_variants(stored_root_path)
                if is_scoped
                else [stored_root_path]
            )
            parent_placeholders = ",".join("?" * len(parent_variants))
            cursor.execute(
                "SELECT f.path, f.name, f.is_folder, f.size, f.modified_time, f.parent_path, "
                "(SELECT 1 FROM file_index child WHERE child.parent_path = f.path COLLATE NOCASE LIMIT 1) "
                "FROM file_index f "
                f"WHERE f.parent_path COLLATE NOCASE IN ({parent_placeholders}) "
                f"ORDER BY {build_sort_order_clause('Tree', self.options['sort_column'], self.options['sort_desc'])}",
                parent_variants,
            )
            rows = cursor.fetchall()
            load_source = "parent_path"
            if not rows and total_matches and not is_scoped:
                rows = self._load_direct_children_from_root_index(cursor, stored_root_path)
                load_source = "root_index_direct"
            if not rows and total_matches and not is_scoped:
                rows = self._load_direct_children_by_path(cursor, stored_root_path)
                load_source = "path_prefix_direct"
            if not rows and total_matches and not is_scoped:
                rows = self._load_indexed_rows_for_root(cursor, stored_root_path)
                load_source = "root_index_all"
            if not rows and is_scoped:
                rows = self._load_direct_children_from_filesystem(
                    stored_root_path,
                    cursor=cursor,
                )
                if rows:
                    load_source = "filesystem"
            if rows:
                folder_metadata = self._folder_metadata_for_filesystem_rows(
                    cursor,
                    [row[0] for row in rows if row[2]],
                )
                if folder_metadata:
                    enriched_rows = []
                    for row in rows:
                        path, name, is_folder, size, modified_time, parent_path, has_child = row
                        metadata = (
                            folder_metadata.get(_path_key(path))
                            if is_folder
                            else None
                        )
                        if metadata is not None:
                            cached_size, child_count = metadata
                            size = cached_size
                            has_child = int((child_count or 0) > 0)
                        enriched_rows.append((
                            path,
                            name,
                            is_folder,
                            size,
                            modified_time,
                            parent_path,
                            has_child,
                        ))
                    rows = enriched_rows
            if is_scoped and total_matches is None:
                total_matches = len(rows)
        finally:
            self._connection = None
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

        if not is_scoped:
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

    def _load_direct_children_from_filesystem(self, root_path, cursor=None):
        entries_data = []
        cache = self.options.get('folder_cache')
        try:
            with os.scandir(root_path) as entries:
                for entry in entries:
                    self._raise_if_cancelled()
                    try:
                        is_folder = entry.is_dir(follow_symlinks=True)
                        stat_result = entry.stat(follow_symlinks=True)
                    except OSError:
                        continue
                    if is_folder and self._filesystem_entry_is_excluded(
                        entry.name,
                        True,
                        0,
                    ):
                        continue
                    if is_folder:
                        if (
                            cache is not None
                            and hasattr(cache, 'is_exclusion_hidden')
                            and cache.is_exclusion_hidden(entry.path)
                        ):
                            continue
                    size = 0 if is_folder else stat_result.st_size
                    if not is_folder and self._filesystem_entry_is_excluded(
                        entry.name,
                        False,
                        size,
                    ):
                        continue
                    entries_data.append((
                        entry.path,
                        entry.name,
                        int(is_folder),
                        size,
                        stat_result.st_mtime,
                        root_path,
                    ))
        except OSError:
            return []

        folder_metadata = self._folder_metadata_for_filesystem_rows(
            cursor,
            [row[0] for row in entries_data if row[2]],
        )
        rows = []
        for path, name, is_folder, size, modified_time, parent_path in entries_data:
            has_child = 0
            if is_folder:
                metadata = folder_metadata.get(_path_key(path))
                if metadata is not None:
                    size, child_count = metadata
                    has_child = int((child_count or 0) > 0)
                else:
                    has_child = int(self._filesystem_path_has_children(path))
            rows.append((
                path,
                name,
                is_folder,
                size,
                modified_time,
                parent_path,
                has_child,
            ))
        return self._sort_show_all_rows(rows)

    def _folder_metadata_for_filesystem_rows(self, cursor, folder_paths):
        metadata = {}
        cache = self.options.get('folder_cache')
        unresolved = []
        indexed_root = None
        display_root = self.options.get('scan_root') or ""
        if cursor is not None and display_root:
            try:
                cursor.execute(
                    """
                    SELECT root
                    FROM file_index
                    GROUP BY root
                    ORDER BY COUNT(*) DESC, length(root) ASC
                    LIMIT 1
                    """
                )
                indexed_root_row = cursor.fetchone()
                indexed_root = indexed_root_row[0] if indexed_root_row else None
            except Exception:
                indexed_root = None

        def indexed_alias(path):
            if not indexed_root or not display_root:
                return path
            try:
                relative = os.path.relpath(
                    os.path.normpath(path),
                    os.path.normpath(display_root),
                )
            except ValueError:
                return path
            if relative == ".":
                return os.path.normpath(indexed_root)
            if relative.startswith(".."):
                return path
            return os.path.normpath(os.path.join(indexed_root, relative))

        for path in folder_paths:
            key = _path_key(path)
            alias = indexed_alias(path)
            cached_metadata = (
                cache.folder_metadata(path)
                if cache is not None and hasattr(cache, 'folder_metadata')
                else None
            )
            if (
                cached_metadata is None
                and cache is not None
                and hasattr(cache, 'folder_metadata')
                and _path_key(alias) != key
            ):
                cached_metadata = cache.folder_metadata(alias)
            if cached_metadata is None:
                unresolved.append((path, alias))
                continue
            metadata[key] = cached_metadata

        if cursor is None:
            return metadata
        for start in range(0, len(unresolved), 900):
            batch = unresolved[start:start + 900]
            if not batch:
                continue
            lookup_paths = list(dict.fromkeys(
                candidate
                for original, alias in batch
                for candidate in (original, alias)
            ))
            placeholders = ",".join("?" * len(lookup_paths))
            cursor.execute(
                "SELECT path, total_size, child_count FROM folder_summary "
                f"WHERE path COLLATE NOCASE IN ({placeholders})",
                lookup_paths,
            )
            rows_by_key = {
                _path_key(path): (total_size or 0, child_count or 0)
                for path, total_size, child_count in cursor.fetchall()
            }
            for original, alias in batch:
                resolved = (
                    rows_by_key.get(_path_key(original))
                    or rows_by_key.get(_path_key(alias))
                )
                if resolved is not None:
                    metadata[_path_key(original)] = resolved
        return metadata

    def _filesystem_path_has_children(self, folder_path, folders_only=False):
        try:
            with os.scandir(folder_path) as entries:
                for entry in entries:
                    try:
                        is_folder = entry.is_dir(follow_symlinks=True)
                        size = 0 if is_folder else entry.stat(follow_symlinks=True).st_size
                    except OSError:
                        continue
                    if (
                        is_folder
                        and self.options.get('folder_cache') is not None
                        and hasattr(
                            self.options.get('folder_cache'),
                            'is_exclusion_hidden',
                        )
                        and self.options.get(
                            'folder_cache',
                        ).is_exclusion_hidden(entry.path)
                    ):
                        continue
                    if self._filesystem_entry_is_excluded(entry.name, is_folder, size):
                        continue
                    if not folders_only or is_folder:
                        return True
        except OSError:
            return False
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

    def __init__(
        self,
        request_id,
        folder_path,
        sort_column=0,
        sort_desc=False,
        options=None,
        cache=None,
        folders_only=False,
        filesystem_fallback=False,
        scan_exclusions=None,
        force_filesystem=False,
        parent=None,
    ):
        super().__init__(parent)
        self.request_id = request_id
        self.folder_path = folder_path
        self.sort_column = sort_column
        self.sort_desc = sort_desc
        self.options = dict(options or {})
        self.apply_filter_options = options is not None
        self.cache = cache
        self.folders_only = folders_only
        self.filesystem_fallback = filesystem_fallback
        self.scan_exclusions = scan_exclusions
        self.force_filesystem = force_filesystem

    def _child_paths_with_children(self, cursor, child_paths):
        folders_with_children = set()
        for start in range(0, len(child_paths), 900):
            batch = child_paths[start:start + 900]
            if not batch:
                continue
            placeholders = ",".join("?" * len(batch))
            folders_only_sql = " AND is_folder = 1" if self.folders_only else ""
            cursor.execute(
                f"""
                SELECT DISTINCT parent_path
                FROM file_index
                WHERE parent_path COLLATE NOCASE IN ({placeholders})
                {folders_only_sql}
                """,
                batch,
            )
            folders_with_children.update(
                os.path.normcase(os.path.normpath(path))
                for (path,) in cursor.fetchall()
                if path
            )
        return folders_with_children

    def _physically_empty_folder_paths(self, cursor, folder_paths):
        empty_paths = set()
        for start in range(0, len(folder_paths), 900):
            batch = folder_paths[start:start + 900]
            if not batch:
                continue
            placeholders = ",".join("?" * len(batch))
            try:
                cursor.execute(
                    "SELECT path FROM folder_summary "
                    f"WHERE path COLLATE NOCASE IN ({placeholders}) "
                    "AND physical_child_count = 0",
                    batch,
                )
            except Exception:
                return set()
            empty_paths.update(
                _path_key(path)
                for (path,) in cursor.fetchall()
                if path
            )
        return empty_paths

    def _parent_path_variants(self):
        normalized = os.path.normpath(self.folder_path)
        variants = [self.folder_path, normalized]
        drive, tail = os.path.splitdrive(normalized)
        if drive.startswith("\\\\") and tail in ("", "\\", "/"):
            variants.extend((drive.rstrip("\\/"), drive.rstrip("\\/") + "\\"))
        return list(dict.fromkeys(variants))

    def _filesystem_folder_has_children(self, folder_path):
        try:
            with os.scandir(folder_path) as entries:
                for entry in entries:
                    try:
                        is_folder = entry.is_dir(follow_symlinks=True)
                        size = 0 if is_folder else entry.stat(follow_symlinks=True).st_size
                    except OSError:
                        continue
                    if (
                        is_folder
                        and self.cache is not None
                        and hasattr(self.cache, 'is_exclusion_hidden')
                        and self.cache.is_exclusion_hidden(entry.path)
                    ):
                        continue
                    if self._filesystem_entry_is_excluded(entry.name, is_folder, size):
                        continue
                    if not self.folders_only or is_folder:
                        return True
        except OSError:
            return False
        return False

    def _filesystem_entry_is_excluded(self, name, is_folder, size):
        exclusions = self.scan_exclusions
        if exclusions is None:
            return False
        if is_folder:
            return exclusions.matches_excluded_folder(name)
        extension = os.path.splitext(name)[1].lower()
        return (
            exclusions.matches_excluded_extension(extension)
            or (
                exclusions.min_file_size_bytes > 0
                and (size or 0) < exclusions.min_file_size_bytes
            )
        )

    def _filesystem_folder_size(self, folder_path):
        if self.cache is not None:
            folder_sizes = getattr(self.cache, 'folder_sizes', {})
            folder_key = _path_key(folder_path)
            if folder_key in folder_sizes:
                return folder_sizes[folder_key] or 0
        total = 0
        for current, dir_names, file_names in os.walk(folder_path):
            dir_names[:] = [
                name for name in dir_names
                if not self._filesystem_entry_is_excluded(name, True, 0)
            ]
            for file_name in file_names:
                file_path = os.path.join(current, file_name)
                try:
                    size = os.path.getsize(file_path)
                except OSError:
                    continue
                if not self._filesystem_entry_is_excluded(file_name, False, size):
                    total += size
        return total

    def _folder_metadata_for_paths(self, folder_paths, allow_database=True):
        metadata = {}
        unresolved = []
        for path in folder_paths:
            cached_metadata = (
                self.cache.folder_metadata(path)
                if self.cache is not None
                and hasattr(self.cache, 'folder_metadata')
                else None
            )
            if cached_metadata is None:
                unresolved.append(path)
            else:
                metadata[_path_key(path)] = cached_metadata

        if not unresolved or not allow_database:
            return metadata
        from src.file_index_tool import FileIndexTool

        tool = FileIndexTool()
        try:
            cursor = tool.conn.cursor()
            for start in range(0, len(unresolved), 900):
                batch = unresolved[start:start + 900]
                placeholders = ",".join("?" * len(batch))
                try:
                    cursor.execute(
                        "SELECT path, total_size, child_count FROM folder_summary "
                        f"WHERE path IN ({placeholders})",
                        batch,
                    )
                except Exception:
                    return metadata
                for path, total_size, child_count in cursor.fetchall():
                    metadata[_path_key(path)] = (
                        total_size or 0,
                        child_count or 0,
                    )
        finally:
            tool.close()
        return metadata

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
        if (
            not self.force_filesystem
            and self.cache
            and self.cache.has_children_for(self.folder_path)
        ):
            children = self.cache.children_for(self.folder_path, self.sort_column, self.sort_desc)
            if self.folders_only:
                children = [
                    child for child in children
                    if child.get('is_dir', False)
                ]
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
            if children:
                self.children_ready.emit(self.request_id, self.folder_path, children)
                return

        rows = []
        folders_with_children = set()
        physically_empty_folders = set()
        used_filesystem_rows = False
        if not self.force_filesystem:
            from src.file_index_tool import FileIndexTool

            tool = FileIndexTool()
            try:
                cursor = tool.conn.cursor()
                folders_only_sql = " AND is_folder = 1" if self.folders_only else ""
                parent_variants = self._parent_path_variants()
                placeholders = ",".join("?" * len(parent_variants))
                cursor.execute(
                    "SELECT path, name, is_folder, size, modified_time, parent_path "
                    "FROM file_index "
                    f"WHERE parent_path COLLATE NOCASE IN ({placeholders}) "
                    f"{folders_only_sql} "
                    f"ORDER BY {build_sort_order_clause('Tree', self.sort_column, self.sort_desc)}",
                    parent_variants,
                )
                rows = cursor.fetchall()
                child_folder_paths = [
                    path
                    for path, _name, is_folder, _size, _modified_time, _parent_path in rows
                    if is_folder
                ]
                folders_with_children = self._child_paths_with_children(
                    cursor,
                    child_folder_paths,
                )
                physically_empty_folders = self._physically_empty_folder_paths(
                    cursor,
                    child_folder_paths,
                )
            except Exception as exc:
                self.children_failed.emit(
                    self.request_id,
                    self.folder_path,
                    str(exc),
                )
                return
            finally:
                tool.close()

        if self.force_filesystem or (not rows and self.filesystem_fallback):
            used_filesystem_rows = True
            try:
                with os.scandir(self.folder_path) as entries:
                    rows = []
                    for entry in entries:
                        try:
                            is_folder = entry.is_dir(follow_symlinks=True)
                            if self.folders_only and not is_folder:
                                continue
                            try:
                                stat_result = entry.stat(follow_symlinks=True)
                                modified_time = stat_result.st_mtime
                            except OSError:
                                stat_result = None
                                modified_time = 0
                            if is_folder and self._filesystem_entry_is_excluded(
                                entry.name,
                                True,
                                0,
                            ):
                                continue
                            if (
                                is_folder
                                and self.cache is not None
                                and hasattr(self.cache, 'is_exclusion_hidden')
                                and self.cache.is_exclusion_hidden(entry.path)
                            ):
                                continue
                            cache_scope_complete = (
                                self.cache is not None
                                and hasattr(self.cache, 'summary_descendant_count')
                                and self.cache.summary_descendant_count(
                                    self.folder_path
                                ) is not None
                            )
                            if (
                                is_folder
                                and not self.force_filesystem
                                and not cache_scope_complete
                                and not filesystem_folder_has_visible_entries(
                                    entry.path,
                                    self.scan_exclusions,
                                )
                            ):
                                continue
                            size = (
                                0
                                if is_folder or stat_result is None
                                else stat_result.st_size
                            )
                            if not is_folder and self._filesystem_entry_is_excluded(
                                entry.name,
                                False,
                                size,
                            ):
                                continue
                            rows.append(
                                (
                                    entry.path,
                                    entry.name,
                                    int(is_folder),
                                    size,
                                    modified_time,
                                    self.folder_path,
                                )
                            )
                        except OSError:
                            continue
                rows.sort(key=lambda row: ((row[1] or "").lower(), (row[0] or "").lower()))
                folder_metadata = self._folder_metadata_for_paths(
                    [row[0] for row in rows if row[2]],
                    allow_database=not self.force_filesystem,
                )
                updated_rows = []
                for path, name, is_folder, size, modified_time, parent_path in rows:
                    if is_folder:
                        metadata = folder_metadata.get(_path_key(path))
                        if metadata is not None:
                            cached_size, child_count = metadata
                            size = 0 if self.folders_only else cached_size
                            if child_count:
                                folders_with_children.add(_path_key(path))
                        elif self._filesystem_folder_has_children(path):
                            folders_with_children.add(_path_key(path))
                    updated_rows.append(
                        (path, name, is_folder, size, modified_time, parent_path)
                    )
                rows = updated_rows
            except OSError as exc:
                self.children_failed.emit(self.request_id, self.folder_path, str(exc))
                return

        children = []
        for path, name, is_folder, size, modified_time, parent_path in rows:
            is_folder = bool(is_folder)
            has_child = os.path.normcase(os.path.normpath(path)) in folders_with_children
            status_has_child = has_child
            if is_folder and self.options.get('status_filter') == 'Empty':
                if used_filesystem_rows:
                    try:
                        with os.scandir(path) as entries:
                            status_has_child = next(entries, None) is not None
                    except OSError:
                        status_has_child = True
                else:
                    status_has_child = _path_key(path) not in physically_empty_folders
            status = self._status_for_child(
                is_folder,
                modified_time,
                status_has_child,
            )
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
                folder_clause = EMPTY_FOLDER_SQL
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
        return prune_contained_paths(
            paths,
            cancel_check=self._raise_if_cancelled,
        )

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

    def _filtered_results_total_size(self, cursor):
        where_sql = self.options.get('filtered_where_sql')
        if not where_sql:
            return None

        self._raise_if_cancelled()
        cursor.execute(
            f"SELECT path, is_folder, size FROM file_index{where_sql}",
            self.options.get('filtered_where_params', ()),
        )
        rows = cursor.fetchall()
        if not rows:
            return 0
        if all(not row[1] for row in rows):
            return sum((row[2] or 0) for row in rows)
        return self._sum_paths_total_size(cursor, [row[0] for row in rows])

    def _scoped_folder_total_size(self, cursor, folder_path):
        if not folder_path:
            return None

        normalized = os.path.normpath(folder_path).rstrip("\\/")
        self._raise_if_cancelled()
        cursor.execute(
            "SELECT total_size, file_count FROM folder_summary "
            "WHERE path = ? COLLATE NOCASE",
            (normalized,),
        )
        summary = cursor.fetchone()
        if summary and (summary[1] or summary[0]):
            return summary[0] or 0

        descendant_sql, descendant_params = descendant_scope_sql(normalized)
        cursor.execute(
            f"SELECT COUNT(*), COALESCE(SUM(size), 0) FROM file_index "
            f"WHERE is_folder = 0 AND ({descendant_sql})",
            descendant_params,
        )
        indexed_count, indexed_total = cursor.fetchone()
        if indexed_count:
            return indexed_total or 0

        total = 0
        try:
            for current_folder, _folders, files in os.walk(normalized):
                self._raise_if_cancelled()
                for name in files:
                    try:
                        total += os.path.getsize(os.path.join(current_folder, name))
                    except OSError:
                        continue
        except OSError:
            return 0
        return total

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

            filtered_total = self._filtered_results_total_size(cursor)
            cached_scoped_folder_total = self.options.get(
                'cached_scoped_folder_total'
            )
            scoped_folder_total = (
                cached_scoped_folder_total
                if cached_scoped_folder_total is not None
                else self._scoped_folder_total_size(
                    cursor,
                    self.options.get('folder_scope'),
                )
            )

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
            'filtered_total': filtered_total,
            'filtered_size_label': self.options.get('filtered_size_label'),
            'scoped_folder_total': scoped_folder_total,
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
        return prune_contained_paths(
            paths,
            cancel_check=self._raise_if_cancelled,
        )

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
            else QColor(current_palette()["text"])
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

    def set_text(self, text):
        self.title.setText(text)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class SegmentedRadioButton(QRadioButton):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setMouseTracking(True)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        palette = current_palette()
        # Draw one inset outline on half pixels so the one-pixel border stays
        # crisp while its rounded corners remain smoothly anti-aliased.
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        checked = self.isChecked()
        hovered = self.underMouse()

        background = QColor(0, 0, 0, 0)
        border = palette["border"]
        text = palette["text"]

        if checked:
            background = QColor(palette["accent_tint"])
            border = palette["accent"]
            text = palette["accent"]
        elif hovered:
            background = QColor(palette["accent_tint"])
            border = palette["accent"]
            text = palette["text"]

        pen = QPen(QColor(border), 1.0)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 8, 8)
        painter.setPen(QColor(text))
        painter.setFont(self.font())
        painter.drawText(rect.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignCenter, self.text())


class FilterPopover(QWidget):
    closed = pyqtSignal()

    def __init__(self, width, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint,
        )
        self.setObjectName("filterPopover")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(width)
        self._last_hidden_at = 0.0
        self._shown_once = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 12)
        outer.setSpacing(0)

        self.card = QFrame()
        self.card.setObjectName("filterPopoverCard")
        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)
        self.card_layout.setSpacing(0)
        outer.addWidget(self.card)

        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 90))
        self.card.setGraphicsEffect(shadow)

    def set_content(self, widget):
        self.card_layout.addWidget(widget)

    def show_below(self, anchor):
        self.layout().activate()
        self.adjustSize()
        position = anchor.mapToGlobal(anchor.rect().bottomLeft())
        screen = QApplication.screenAt(position)
        if screen is not None:
            available = screen.availableGeometry()
            x = min(
                max(position.x(), available.left()),
                available.right() - self.width() + 1,
            )
            y = position.y()
            if y + self.height() > available.bottom():
                y = anchor.mapToGlobal(anchor.rect().topLeft()).y() - self.height()
            position.setX(x)
            position.setY(max(available.top(), y))
        self.move(position)
        self._shown_once = True
        self.show()
        self.raise_()

    def recently_hidden(self, threshold=0.18):
        return time.monotonic() - self._last_hidden_at < threshold

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event):
        super().hideEvent(event)
        if not self._shown_once:
            return
        self._shown_once = False
        self._last_hidden_at = time.monotonic()
        self.closed.emit()


class FolderBrowserNode:
    def __init__(self, name="", path="", parent=None, has_children=False):
        self.name = name
        self.path = path
        self.parent = parent
        self.children = []
        self.has_children = bool(has_children)
        self.loaded = not self.has_children
        self.loading = False

    def row(self):
        if self.parent is None:
            return 0
        try:
            return self.parent.children.index(self)
        except ValueError:
            return 0


class FolderBrowserTreeModel(QAbstractItemModel):
    loadRequested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.root_node = FolderBrowserNode()
        self.nodes_by_path = {}
        self.folder_icon = QIcon(
            os.path.join(os.path.dirname(__file__), "assets", "folder_blue.svg")
        )

    def columnCount(self, parent=QModelIndex()):
        return 1

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid() and parent.column() != 0:
            return 0
        node = parent.internalPointer() if parent.isValid() else self.root_node
        return len(node.children)

    def index(self, row, column, parent=QModelIndex()):
        if column != 0 or row < 0:
            return QModelIndex()
        parent_node = parent.internalPointer() if parent.isValid() else self.root_node
        if row >= len(parent_node.children):
            return QModelIndex()
        return self.createIndex(row, column, parent_node.children[row])

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        node = index.internalPointer()
        parent_node = node.parent
        if parent_node is None or parent_node is self.root_node:
            return QModelIndex()
        return self.createIndex(parent_node.row(), 0, parent_node)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node = index.internalPointer()
        if role == Qt.ItemDataRole.DisplayRole:
            return node.name
        if role == Qt.ItemDataRole.DecorationRole:
            return self.folder_icon
        if role in (Qt.ItemDataRole.UserRole, Qt.ItemDataRole.ToolTipRole):
            return node.path
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def hasChildren(self, parent=QModelIndex()):
        if not parent.isValid():
            return bool(self.root_node.children)
        node = parent.internalPointer()
        return bool(node.children) or node.has_children

    def canFetchMore(self, parent):
        if not parent.isValid():
            return False
        node = parent.internalPointer()
        return node.has_children and not node.loaded and not node.loading

    def fetchMore(self, parent):
        if not self.canFetchMore(parent):
            return
        node = parent.internalPointer()
        node.loading = True
        self.loadRequested.emit(node.path)

    def reset_root(self, path=None):
        self.beginResetModel()
        self.root_node = FolderBrowserNode()
        self.nodes_by_path = {}
        if path:
            normalized = os.path.normpath(path)
            name = os.path.basename(normalized.rstrip("\\/")) or normalized
            root = FolderBrowserNode(
                name=name,
                path=normalized,
                parent=self.root_node,
                has_children=True,
            )
            self.root_node.children.append(root)
            self.nodes_by_path[_path_key(normalized)] = root
        self.endResetModel()

    def index_for_path(self, path):
        node = self.nodes_by_path.get(_path_key(path))
        if node is None:
            return QModelIndex()
        return self.createIndex(node.row(), 0, node)

    def apply_children(self, parent_path, children):
        parent_node = self.nodes_by_path.get(_path_key(parent_path))
        if parent_node is None:
            return
        folder_children = [child for child in children if child.get('is_dir', False)]
        parent_index = self.index_for_path(parent_path)
        if parent_node.children:
            self._remove_children(parent_node, parent_index)
        if folder_children:
            self.beginInsertRows(parent_index, 0, len(folder_children) - 1)
            for child in folder_children:
                node = FolderBrowserNode(
                    name=child.get('name') or os.path.basename(child.get('path', '')),
                    path=child.get('path', ''),
                    parent=parent_node,
                    has_children=not child.get('_children_loaded', True),
                )
                parent_node.children.append(node)
                self.nodes_by_path[_path_key(node.path)] = node
            self.endInsertRows()
        parent_node.loaded = True
        parent_node.loading = False
        parent_node.has_children = bool(folder_children)
        self.dataChanged.emit(parent_index, parent_index, [])

    def merge_children(self, parent_path, children):
        parent_node = self.nodes_by_path.get(_path_key(parent_path))
        if parent_node is None:
            return
        parent_index = self.index_for_path(parent_path)
        existing_keys = {
            _path_key(child.path)
            for child in parent_node.children
        }
        incoming_by_key = {
            _path_key(child.get('path', '')): child
            for child in children
            if child.get('is_dir', False)
        }
        for existing in parent_node.children:
            incoming = incoming_by_key.get(_path_key(existing.path))
            if incoming and not incoming.get('_children_loaded', True):
                existing.has_children = True
        folder_children = sorted(
            (
                child for child in children
                if child.get('is_dir', False)
                and _path_key(child.get('path', '')) not in existing_keys
            ),
            key=lambda child: (
                (child.get('name') or '').lower(),
                (child.get('path') or '').lower(),
            ),
        )
        for child in folder_children:
            name = child.get('name') or os.path.basename(child.get('path', ''))
            insert_at = len(parent_node.children)
            sort_key = (name.lower(), (child.get('path') or '').lower())
            for index, existing in enumerate(parent_node.children):
                existing_key = (existing.name.lower(), existing.path.lower())
                if sort_key < existing_key:
                    insert_at = index
                    break
            self.beginInsertRows(parent_index, insert_at, insert_at)
            node = FolderBrowserNode(
                name=name,
                path=child.get('path', ''),
                parent=parent_node,
                has_children=not child.get('_children_loaded', True),
            )
            parent_node.children.insert(insert_at, node)
            self.nodes_by_path[_path_key(node.path)] = node
            self.endInsertRows()
        parent_node.loaded = True
        parent_node.loading = False
        parent_node.has_children = bool(parent_node.children)
        self.dataChanged.emit(parent_index, parent_index, [])

    def defer_load(self, path):
        node = self.nodes_by_path.get(_path_key(path))
        if node is not None:
            node.loading = False
            node.loaded = False
            node.has_children = True

    def mark_load_failed(self, path):
        node = self.nodes_by_path.get(_path_key(path))
        if node is not None:
            node.loading = False
            node.loaded = True

    def refresh_path(self, path):
        node = self.nodes_by_path.get(_path_key(path))
        if node is None:
            return QModelIndex()
        index = self.index_for_path(path)
        self._remove_children(node, index)
        node.loaded = False
        node.loading = False
        node.has_children = True
        self.dataChanged.emit(index, index, [])
        return index

    def remove_path(self, path):
        node = self.nodes_by_path.get(_path_key(path))
        if node is None or node.parent is None:
            return False
        parent_node = node.parent
        parent_index = (
            QModelIndex()
            if parent_node is self.root_node
            else self.index_for_path(parent_node.path)
        )
        row = node.row()
        self.beginRemoveRows(parent_index, row, row)
        parent_node.children.pop(row)
        stack = [node]
        while stack:
            current = stack.pop()
            stack.extend(current.children)
            self.nodes_by_path.pop(_path_key(current.path), None)
        self.endRemoveRows()
        if parent_node is not self.root_node:
            parent_node.has_children = bool(parent_node.children)
            self.dataChanged.emit(parent_index, parent_index, [])
        return True

    def _remove_children(self, node, parent_index):
        if not node.children:
            return
        self.beginRemoveRows(parent_index, 0, len(node.children) - 1)
        stack = list(node.children)
        while stack:
            child = stack.pop()
            stack.extend(child.children)
            self.nodes_by_path.pop(_path_key(child.path), None)
        node.children = []
        self.endRemoveRows()


class FolderBrowserTreeView(QTreeView):
    def mouseMoveEvent(self, event):
        cursor = (
            Qt.CursorShape.PointingHandCursor
            if self.indexAt(event.position().toPoint()).isValid()
            else Qt.CursorShape.ArrowCursor
        )
        self.viewport().setCursor(cursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        if (
            event.button() == Qt.MouseButton.LeftButton
            and index.isValid()
            and event.position().x() < self.visualRect(index).left()
        ):
            self.setExpanded(index, not self.isExpanded(index))
            event.accept()
            return
        super().mousePressEvent(event)


class FolderBrowserPanel(QFrame):
    MIN_WIDTH = 160
    DEFAULT_WIDTH = MIN_WIDTH

    scopeChanged = pyqtSignal(str)
    closeRequested = pyqtSignal()
    loadRequested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Expanding,
        )
        self.resize(self.DEFAULT_WIDTH, self.height())
        self.root_path = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("searchSection")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_MD)
        title = QLabel("Folders Panel")
        title.setObjectName("filterDrawerTitle")
        header_layout.addWidget(title)
        layout.addWidget(header)

        self.tree = FolderBrowserTreeView()
        self.tree.setObjectName("folderBrowserTree")
        self.tree.setMouseTracking(True)
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(False)
        self.tree.setIndentation(16)
        self.tree.setIconSize(QSize(18, 18))
        self.tree.setViewportMargins(SPACE_SM, 0, 0, 0)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setExpandsOnDoubleClick(True)
        self.model = FolderBrowserTreeModel(self)
        self.tree.setModel(self.model)
        self.model.loadRequested.connect(self.loadRequested)
        self.tree.selectionModel().currentChanged.connect(self._on_current_changed)
        layout.addWidget(self.tree, 1)

        self.empty_label = QLabel("Folder navigation is available after a scan completes.")
        self.empty_label.setObjectName("modalSecondary")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
        layout.addWidget(self.empty_label, 1)

        action_box = QWidget()
        action_box.setObjectName("sidebarActionBox")
        self.action_box = action_box
        action_layout = QVBoxLayout(action_box)
        action_layout.setContentsMargins(SPACE_SM, SPACE_MD, SPACE_SM, SPACE_SM)
        self.btn_close = QPushButton("Close Folders Panel")
        self.btn_close.setObjectName("closeSidebar")
        self.btn_close.setToolTip("Hide the folder browser")
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setFixedHeight(32)
        self.btn_close.setMinimumWidth(0)
        self.btn_close.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.btn_close.clicked.connect(self.closeRequested)
        action_layout.addWidget(self.btn_close)
        layout.addWidget(action_box)

        self.set_root(None)
        self._update_close_button_width()

    def sizeHint(self):
        return QSize(self.DEFAULT_WIDTH, 0)

    def minimumSizeHint(self):
        return QSize(0, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_close_button_width()

    def _update_close_button_width(self):
        if not hasattr(self, "btn_close"):
            return
        available_width = max(120, self.width() - (2 * SPACE_SM))
        self.btn_close.setMaximumWidth(min(172, available_width))

    def set_root(self, path):
        self.root_path = os.path.normpath(path) if path else None
        blocker = QSignalBlocker(self.tree.selectionModel())
        self.model.reset_root(self.root_path)
        self.tree.setVisible(bool(self.root_path))
        self.empty_label.setVisible(not self.root_path)
        if self.root_path:
            root_index = self.model.index(0, 0)
            self.tree.setCurrentIndex(root_index)
            self.tree.expand(root_index)
        del blocker

    def select_root(self):
        if not self.root_path:
            return
        index = self.model.index_for_path(self.root_path)
        if index.isValid():
            blocker = QSignalBlocker(self.tree.selectionModel())
            self.tree.setCurrentIndex(index)
            del blocker

    def apply_children(self, parent_path, children):
        self.model.apply_children(parent_path, children)

    def merge_children(self, parent_path, children):
        self.model.merge_children(parent_path, children)

    def defer_load(self, path):
        self.model.defer_load(path)

    def mark_load_failed(self, path):
        self.model.mark_load_failed(path)

    def refresh_paths(self, parent_paths):
        for path in dict.fromkeys(parent_paths):
            index = self.model.refresh_path(path)
            if index.isValid() and self.tree.isExpanded(index):
                self.model.fetchMore(index)

    def remove_paths(self, paths):
        for path in sorted(
            dict.fromkeys(path for path in paths if path),
            key=lambda value: len(os.path.normpath(value)),
            reverse=True,
        ):
            self.model.remove_path(path)

    def _on_current_changed(self, current, previous):
        path = self.model.data(current, Qt.ItemDataRole.UserRole)
        if path:
            self.scopeChanged.emit(path)


class FilterPanel(QFrame):
    searchCleared = pyqtSignal()
    exclusionChanged = pyqtSignal()

    DEFAULT_STALE_MONTHS = 3
    AGE_FILTER_DISABLED = 0
    MAX_STALE_MONTHS = 24
    MAX_MANUAL_STALE_MONTHS = 240
    DEFAULT_STATUS_FILTER = "Inactive"
    MIN_WIDTH = 240
    DEFAULT_WIDTH = MIN_WIDTH
    MAX_WIDTH = 520
    FILTER_SEGMENT_MAX_WIDTH = 142
    FILTER_SEGMENT_MIN_WIDTH = 72

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("filterDrawer")
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setMaximumWidth(self.MAX_WIDTH)
        self.resize(self.DEFAULT_WIDTH, self.height())
        self._initial_drawer_width_applied = False
        self._exclusions_popup_layout_active = False
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.setVisible(False)
        self.settings = QSettings("IBMS", "Watchdog")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        drawer_header = QFrame()
        drawer_header.setObjectName("filterDrawerHeader")
        drawer_header_layout = QHBoxLayout(drawer_header)
        self.drawer_header_layout = drawer_header_layout
        drawer_header_layout.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_MD, SPACE_MD)
        drawer_header_layout.setSpacing(SPACE_SM)
        drawer_title_box = QWidget()
        drawer_title_layout = QVBoxLayout(drawer_title_box)
        drawer_title_layout.setContentsMargins(0, 0, 0, 0)
        drawer_title_layout.setSpacing(2)
        drawer_title = QLabel("Filters")
        drawer_title.setObjectName("filterDrawerTitle")
        drawer_title_layout.addWidget(drawer_title)
        drawer_header_layout.addWidget(drawer_title_box, 1)
        outer.addWidget(drawer_header)

        staging = QWidget()
        staging.setMinimumWidth(0)
        staging.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        staging_layout = QVBoxLayout(staging)
        staging_layout.setContentsMargins(0, 0, 0, 0)
        staging_layout.setSpacing(0)

        scroll_content = QWidget()
        scroll_content.setObjectName("filterStagingContent")
        scroll_content.setMinimumWidth(0)
        scroll_content.setMinimumHeight(0)
        scroll_content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        body = QVBoxLayout(scroll_content)
        body.setSizeConstraint(QLayout.SizeConstraint.SetDefaultConstraint)
        body.setContentsMargins(0, SPACE_SM, 0, SPACE_MD)
        body.setSpacing(0)
        staging_layout.addWidget(scroll_content)

        search_section = QWidget()
        search_section.setObjectName("searchSection")
        search_layout = QVBoxLayout(search_section)
        self.search_layout = search_layout
        search_layout.setContentsMargins(SPACE_LG, SPACE_SM, SPACE_LG, SPACE_SM)
        search_layout.setSpacing(SPACE_SM)

        lbl_search = QLabel("SEARCH")
        lbl_search.setObjectName("searchHeader")
        search_layout.addWidget(lbl_search)

        self.search_shell = QFrame()
        self.search_shell.setObjectName("searchFieldShell")
        self.search_shell.setFixedHeight(40)
        self.search_shell.setMinimumWidth(0)
        search_shell_layout = QHBoxLayout(self.search_shell)
        self.search_shell_layout = search_shell_layout
        search_shell_layout.setContentsMargins(SPACE_MD, 0, 4, 0)
        search_shell_layout.setSpacing(SPACE_SM)

        self.search_icon_label = QLabel()
        self.search_icon_label.setObjectName("searchFieldIcon")
        search_icon = QIcon(os.path.join(os.path.dirname(__file__), "assets", "search.svg"))
        self.search_icon_label.setPixmap(search_icon.pixmap(QSize(18, 18)))
        search_shell_layout.addWidget(self.search_icon_label)

        self.txt_search = QLineEdit()
        self.txt_search.setObjectName("searchFieldInput")
        self.txt_search.setMinimumWidth(0)
        self.txt_search.setPlaceholderText("Search files and folders")
        self.txt_search.setToolTip("Filter the current results by file or folder name")
        self.txt_search.setFixedHeight(36)
        clear_icon = QIcon(
            os.path.join(os.path.dirname(__file__), "assets", "x-circle-light.svg")
        )
        self.search_clear_action = self.txt_search.addAction(
            clear_icon,
            QLineEdit.ActionPosition.TrailingPosition,
        )
        self.search_clear_action.setToolTip("Clear search")
        self.search_clear_action.setVisible(False)
        self.search_clear_action.triggered.connect(self.clear_search_text)
        for button in self.txt_search.findChildren(QToolButton):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.txt_search.textChanged.connect(
            lambda text: self.search_clear_action.setVisible(bool(text))
        )
        self.txt_search.installEventFilter(self)
        search_shell_layout.addWidget(self.txt_search, 1)

        self.btn_search_submit = QPushButton()
        self.btn_search_submit.setObjectName("searchFieldAction")
        self.btn_search_submit.setToolTip("Apply search")
        self.btn_search_submit.setAccessibleName("Apply search")
        self.btn_search_submit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_search_submit.setFixedSize(32, 32)
        self.btn_search_submit.setIcon(
            QIcon(os.path.join(os.path.dirname(__file__), "assets", "arrow_right.svg"))
        )
        self.btn_search_submit.setIconSize(QSize(18, 18))
        self.btn_search_submit.clicked.connect(self.txt_search.returnPressed.emit)
        search_shell_layout.addWidget(self.btn_search_submit)

        search_layout.addWidget(self.search_shell)

        body.addWidget(search_section)
        body.addSpacing(SPACE_SM)

        self._age_section_expanded = True
        self._exclusions_section_expanded = False

        # Section 1: Display Mode
        display_section = QWidget()
        display_section.setObjectName("displayModeSection")
        display_layout = QVBoxLayout(display_section)
        self.display_layout = display_layout
        display_layout.setContentsMargins(SPACE_LG, SPACE_SM, SPACE_LG, SPACE_SM)
        display_layout.setSpacing(SPACE_SM)

        lbl_display = QLabel("DISPLAY MODE")
        lbl_display.setObjectName("displayModeHeader")

        display_layout.addWidget(lbl_display)

        self.rb_all      = SegmentedRadioButton("Show all")
        self.rb_inactive = SegmentedRadioButton("Inactive only")
        self.rb_empty    = SegmentedRadioButton("Empty only")
        self.rb_videos   = SegmentedRadioButton("Videos only")
        self.rb_all.setChecked(True)
        self.bg = QButtonGroup()
        display_grid = QGridLayout()
        self.display_grid = display_grid
        display_grid.setContentsMargins(0, 0, 0, 0)
        display_grid.setSpacing(SPACE_SM)
        self.display_buttons = [
            self.rb_all,
            self.rb_inactive,
            self.rb_empty,
            self.rb_videos,
        ]
        for rb in self.display_buttons:
            self.bg.addButton(rb)
            rb.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            rb.setProperty("segment", True)
            rb.setFixedHeight(32)
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rebuild_button_grid(self.display_grid, self.display_buttons, 2)
        display_layout.addLayout(display_grid)
        
        body.addWidget(display_section)
        body.addSpacing(SPACE_SM)

        # Section 1.5: View Mode
        view_section = QWidget()
        view_section.setObjectName("viewModeSection")
        view_layout = QVBoxLayout(view_section)
        self.view_layout = view_layout
        view_layout.setContentsMargins(SPACE_LG, SPACE_SM, SPACE_LG, SPACE_SM)
        view_layout.setSpacing(SPACE_SM)

        lbl_view = QLabel("VIEW MODE")
        lbl_view.setObjectName("viewModeHeader")
        view_layout.addWidget(lbl_view)

        self.rb_view_tree    = SegmentedRadioButton("Tree view")
        self.rb_view_files   = SegmentedRadioButton("Files only")
        self.rb_view_folders = SegmentedRadioButton("Folders only")
        self.rb_view_tree.setChecked(True)
        
        self.bg_view = QButtonGroup()
        view_grid = QGridLayout()
        self.view_grid = view_grid
        view_grid.setContentsMargins(0, 0, 0, 0)
        view_grid.setSpacing(SPACE_SM)
        self.view_buttons = [
            self.rb_view_tree,
            self.rb_view_files,
            self.rb_view_folders,
        ]
        for rb in self.view_buttons:
            self.bg_view.addButton(rb)
            rb.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            rb.setProperty("segment", True)
            rb.setFixedHeight(32)
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rebuild_button_grid(self.view_grid, self.view_buttons, 2)
        view_layout.addLayout(view_grid)
            
        body.addWidget(view_section)
        body.addSpacing(SPACE_SM)



        # Section 2: Date Range
        
        

        # Section 3: Stale Threshold
        age_section = QWidget()
        age_section.setObjectName("ageThresholdSection")
        age_outer_layout = QVBoxLayout(age_section)
        self.age_outer_layout = age_outer_layout
        age_outer_layout.setContentsMargins(SPACE_LG, SPACE_SM, SPACE_LG, SPACE_SM)
        age_outer_layout.setSpacing(SPACE_SM)

        self.age_heading = QLabel("Age: Off")
        self.age_heading.setObjectName("ageSectionHeading")
        self.age_heading.setCursor(Qt.CursorShape.ArrowCursor)
        age_outer_layout.addWidget(self.age_heading)

        self.age_box = QFrame()
        self.age_box.setObjectName("ageControl")
        age_layout = QVBoxLayout(self.age_box)
        self.age_layout = age_layout
        age_layout.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)
        age_layout.setSpacing(SPACE_SM)

        age_hdr = QHBoxLayout()
        self.age_summary_layout = age_hdr
        age_hdr.setSpacing(SPACE_SM)
        self.lbl_pill = QLabel()
        self.lbl_pill.setObjectName("agePill")
        self.lbl_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_pill.setFixedHeight(20)
        self.lbl_val = QLabel()
        self.lbl_val.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_val.setWordWrap(False)
        self.lbl_pill.setVisible(False)
        age_hdr.addWidget(self.lbl_val)
        age_hdr.addStretch()
        age_layout.addLayout(age_hdr)
        
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(self.AGE_FILTER_DISABLED, self.MAX_STALE_MONTHS)
        self.slider.setValue(self.AGE_FILTER_DISABLED)
        self.slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider.setTickInterval(1)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider.installEventFilter(self)
        self.slider.valueChanged.connect(self._update_age_label)
        self.slider.valueChanged.connect(self._sync_manual_age_from_slider)
        age_layout.addWidget(self.slider)

        self.age_input = QSpinBox()
        self.age_input.setRange(self.AGE_FILTER_DISABLED, self.MAX_MANUAL_STALE_MONTHS)
        self.age_input.setValue(self.AGE_FILTER_DISABLED)
        self.age_input.setSuffix(" months")
        self.age_input.setCursor(Qt.CursorShape.PointingHandCursor)
        self.age_input.valueChanged.connect(self._sync_slider_from_manual_age)

        self.btn_apply_age = QPushButton("Go")
        self.btn_apply_age.setObjectName("primaryBtn")
        self.btn_apply_age.setFixedWidth(52)
        self.btn_apply_age.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_apply_age.setToolTip("Apply age threshold")
        self.btn_apply_age.setEnabled(False)
        self.applied_age_value = self.AGE_FILTER_DISABLED

        bot_row = QHBoxLayout()
        self.age_manual_row = bot_row
        bot_row.setSpacing(SPACE_SM)
        lbl_manual = QLabel("More than 24 months? Enter here")
        lbl_manual.setObjectName("ageManualHint")
        lbl_manual.setWordWrap(True)
        self.age_manual_label = lbl_manual

        bot_row.addWidget(self.age_input)
        bot_row.addWidget(self.btn_apply_age)
        bot_row.addStretch()
        age_layout.addWidget(lbl_manual)
        age_layout.addLayout(bot_row)
        age_outer_layout.addWidget(self.age_box)

        self.slider.valueChanged.connect(self._update_age_apply_state)
        self.age_input.valueChanged.connect(self._update_age_apply_state)
        
        body.addWidget(age_section)
        body.addSpacing(SPACE_SM)

        exclusions_section = QWidget()
        exclusions_section.setObjectName("exclusionsSection")
        exclusions_outer_layout = QVBoxLayout(exclusions_section)
        self.exclusions_outer_layout = exclusions_outer_layout
        exclusions_outer_layout.setContentsMargins(SPACE_LG, SPACE_SM, SPACE_LG, SPACE_SM)
        exclusions_outer_layout.setSpacing(SPACE_SM)

        self.btn_exclusions_toggle = self._make_accordion_header("EXCLUSIONS")
        self.btn_exclusions_toggle.clicked.connect(lambda: self._toggle_collapsible_section("exclusions"))
        exclusions_outer_layout.addWidget(self.btn_exclusions_toggle)

        self.exclusions_box = QWidget()
        exclusions_layout = QVBoxLayout(self.exclusions_box)
        exclusions_layout.setContentsMargins(0, SPACE_SM, 0, SPACE_SM)
        exclusions_layout.setSpacing(SPACE_SM)

        lbl_folders = QLabel("Folder names")
        lbl_folders.setObjectName("manualLabel")
        exclusions_layout.addWidget(lbl_folders)

        self.txt_excluded_folders = QLineEdit()
        self.txt_excluded_folders.setObjectName("scanExclusionInput")
        self.txt_excluded_folders.setPlaceholderText(
            "Example: node_modules, *.git*, Temp"
        )
        self.txt_excluded_folders.setToolTip("Folder names or simple patterns to skip on the next scan")
        self.txt_excluded_folders.setFixedHeight(32)
        exclusions_layout.addWidget(self.txt_excluded_folders)

        lbl_extensions = QLabel("File extensions")
        lbl_extensions.setObjectName("manualLabel")
        exclusions_layout.addWidget(lbl_extensions)

        self.txt_excluded_extensions = QLineEdit()
        self.txt_excluded_extensions.setObjectName("scanExclusionInput")
        self.txt_excluded_extensions.setPlaceholderText(
            "Example: .tmp, .log, .iso"
        )
        self.txt_excluded_extensions.setToolTip("File extensions to skip on the next scan")
        self.txt_excluded_extensions.setFixedHeight(32)
        exclusions_layout.addWidget(self.txt_excluded_extensions)

        lbl_min_size = QLabel("Ignore files smaller than")
        lbl_min_size.setObjectName("manualLabel")
        exclusions_layout.addWidget(lbl_min_size)

        size_row = QHBoxLayout()
        self.exclusions_size_row = size_row
        size_row.setSpacing(SPACE_SM)
        self.min_size_input = QSpinBox()
        self.min_size_input.setObjectName("scanExclusionSize")
        self.min_size_input.setRange(0, 999999)
        self.min_size_input.setValue(0)
        self.min_size_input.setFixedWidth(86)
        self.min_size_input.setFixedHeight(36)
        self.min_size_input.setCursor(Qt.CursorShape.PointingHandCursor)
        self.min_size_unit = QComboBox()
        self.min_size_unit.setObjectName("scanExclusionUnit")
        self.min_size_unit.addItems(["KB", "MB", "GB"])
        self.min_size_unit.setFixedWidth(72)
        self.min_size_unit.setFixedHeight(36)
        self.min_size_unit.setCursor(Qt.CursorShape.PointingHandCursor)
        size_row.addWidget(self.min_size_input)
        size_row.addWidget(self.min_size_unit)
        size_row.addStretch()
        exclusions_layout.addLayout(size_row)

        self.lbl_exclusions_hint = QLabel("Exclusion changes apply on next scan - click Re-scan to apply.")
        self.lbl_exclusions_hint.setObjectName("manualLabel")
        self.lbl_exclusions_hint.setWordWrap(True)
        self.lbl_exclusions_hint.setVisible(False)
        exclusions_layout.addWidget(self.lbl_exclusions_hint)

        self.btn_reset_exclusions = QPushButton("Reset to defaults")
        self.btn_reset_exclusions.setObjectName("resetExclusions")
        self.btn_reset_exclusions.setToolTip("Restore the default scan exclusions")
        self.btn_reset_exclusions.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset_exclusions.setFixedHeight(36)

        self.btn_rescan_exclusions = QPushButton("Apply and Re-scan")
        self.btn_rescan_exclusions.setAccessibleName("Apply and re-scan")
        self.btn_rescan_exclusions.setObjectName("primaryBtn")
        self.btn_rescan_exclusions.setToolTip(
            "Apply these exclusions and scan the selected folder again"
        )
        self.btn_rescan_exclusions.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_rescan_exclusions.setFixedHeight(36)
        exclusions_actions = QHBoxLayout()
        self.exclusions_actions_layout = exclusions_actions
        exclusions_actions.setSpacing(SPACE_SM)
        exclusions_actions.addWidget(self.btn_reset_exclusions)
        exclusions_actions.addStretch()
        exclusions_actions.addWidget(self.btn_rescan_exclusions)
        exclusions_layout.addLayout(exclusions_actions)

        exclusions_outer_layout.addWidget(self.exclusions_box)
        body.addWidget(exclusions_section)

        self._update_age_label(self.slider.value())
        self._restore_scan_exclusions()
        self._age_section_expanded = True
        self._exclusions_section_expanded = self._scan_exclusions_from_controls().differs_from_default()
        self._sync_collapsible_sections()
        self.txt_excluded_folders.textChanged.connect(
            lambda _text: self.exclusionChanged.emit()
        )
        self.txt_excluded_extensions.textChanged.connect(
            lambda _text: self.exclusionChanged.emit()
        )
        self.txt_excluded_folders.editingFinished.connect(self._normalize_and_save_scan_exclusions)
        self.txt_excluded_extensions.editingFinished.connect(self._normalize_and_save_scan_exclusions)
        self.min_size_input.valueChanged.connect(self._save_scan_exclusions_from_controls)
        self.min_size_unit.currentTextChanged.connect(self._save_scan_exclusions_from_controls)
        self.btn_reset_exclusions.clicked.connect(self.reset_scan_exclusions_to_defaults)
        body.addStretch()
        self.filter_scroll = QScrollArea()
        self.filter_scroll.setObjectName("filterDrawerScroll")
        self.filter_scroll.setWidgetResizable(True)
        self.filter_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.filter_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.filter_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.filter_scroll.setWidget(staging)
        outer.addWidget(self.filter_scroll, 1)

        # Section 4: Actions
        action_box = QWidget()
        action_box.setObjectName("sidebarActionBox")
        action_layout = QVBoxLayout(action_box)
        self.action_layout = action_layout
        action_layout.setContentsMargins(SPACE_LG, SPACE_MD, SPACE_LG, SPACE_LG)
        action_layout.setSpacing(SPACE_SM)
        
        self.btn_reset = QPushButton("Reset all filters")
        self.btn_reset.setObjectName("resetFilters")

        self.btn_reset.setToolTip("Reset all filters to their default values")
        self.btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close = QPushButton("Close sidebar")
        self.btn_close.setObjectName("closeFilterDrawer")
        self.btn_close.setToolTip("Hide the filter sidebar")
        self.btn_close.setAccessibleName("Close filters")
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setText("×")
        self.btn_close.setFixedSize(32, 32)
        action_layout.addWidget(self.btn_reset)
        drawer_header_layout.addWidget(self.btn_close)
        outer.addWidget(action_box)

    def prepare_exclusions_popup_layout(self):
        """Give the exclusions popover aligned, evenly sized control rows."""
        self._exclusions_popup_layout_active = True
        size_row = self.exclusions_size_row
        while size_row.count():
            size_row.takeAt(0)
        size_row.setDirection(QBoxLayout.Direction.LeftToRight)
        size_row.setContentsMargins(0, 0, 0, 0)
        size_row.setSpacing(SPACE_SM)
        for control in (self.min_size_input, self.min_size_unit):
            control.setFixedHeight(36)
            control.setMinimumWidth(0)
            control.setMaximumWidth(16777215)
            control.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Fixed,
            )
            size_row.addWidget(control, 1)

        actions = self.exclusions_actions_layout
        while actions.count():
            actions.takeAt(0)
        actions.setDirection(QBoxLayout.Direction.LeftToRight)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(SPACE_SM)
        for button in (self.btn_reset_exclusions, self.btn_rescan_exclusions):
            button.setFixedHeight(36)
            button.setMinimumWidth(0)
            button.setMaximumWidth(16777215)
            button.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Fixed,
            )
            actions.addWidget(button, 1)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._initial_drawer_width_applied:
            self.resize(self.DEFAULT_WIDTH, self.height())
            self._initial_drawer_width_applied = True
        self._update_responsive_drawer_layout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_responsive_drawer_layout()

    def _convert_to_horizontal_layout(
        self,
        outer,
        old_scroll,
        old_action_box,
        search_section,
        display_section,
        view_section,
    ):
        """Move the existing filter controls into a compact two-row bar."""
        self.setObjectName("filterBar")
        self.setMinimumWidth(0)
        self.setMaximumWidth(16777215)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        outer.removeWidget(old_scroll)
        outer.removeWidget(old_action_box)

        main_row = QWidget()
        main_row.setObjectName("filterBarMain")
        main_row.setFixedHeight(56)
        main_layout = QHBoxLayout(main_row)
        main_layout.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)
        main_layout.setSpacing(SPACE_SM)

        search_layout = search_section.layout()
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(0)
        search_layout.itemAt(0).widget().setVisible(False)
        self.search_shell.setFixedWidth(240)
        self.txt_search.setFixedHeight(36)
        main_layout.addWidget(
            search_section,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        main_layout.addWidget(self._vline())

        self._make_segment_section_horizontal(
            display_section,
            (self.rb_all, self.rb_inactive, self.rb_empty, self.rb_videos),
        )
        main_layout.addWidget(
            display_section,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        main_layout.addWidget(self._vline())

        self._make_segment_section_horizontal(
            view_section,
            (self.rb_view_tree, self.rb_view_files, self.rb_view_folders),
        )
        main_layout.addWidget(
            view_section,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        main_layout.addWidget(self._vline())
        main_layout.addStretch(1)

        self.age_heading.setParent(main_row)
        self.age_heading.setFixedWidth(116)
        self.age_heading.setFixedHeight(36)
        main_layout.addWidget(
            self.age_heading,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )

        self.btn_exclusions_toggle.setParent(main_row)
        self.btn_exclusions_toggle.setFixedWidth(144)
        self.btn_exclusions_toggle.setFixedHeight(36)
        main_layout.addWidget(
            self.btn_exclusions_toggle,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )

        self.btn_reset.setParent(main_row)
        self.btn_reset.setFixedWidth(116)
        self.btn_reset.setFixedHeight(36)
        main_layout.addWidget(
            self.btn_reset,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )

        self._prepare_filter_popover_contents()

        self.age_popover = FilterPopover(340, self)
        self.age_popover.set_content(self.age_box)
        self.age_popover.closed.connect(
            lambda: self._on_filter_popover_closed("age")
        )

        self.exclusions_box.setObjectName("exclusionsControl")
        self.exclusions_popover = FilterPopover(360, self)
        self.exclusions_popover.set_content(self.exclusions_box)
        self.exclusions_popover.closed.connect(
            lambda: self._on_filter_popover_closed("exclusions")
        )

        self.main_row = main_row
        outer.addWidget(main_row)

        self.btn_close.setParent(None)
        self.btn_close.deleteLater()
        del self.btn_close
        old_scroll.setParent(None)
        old_scroll.deleteLater()
        old_action_box.setParent(None)
        old_action_box.deleteLater()
        self._age_section_expanded = True
        self._exclusions_section_expanded = False
        self._sync_collapsible_sections()

    def _make_segment_section_horizontal(self, section, buttons):
        layout = section.layout()
        label = layout.itemAt(0).widget()
        while layout.count():
            layout.takeAt(0)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        label.setVisible(False)

        segments = QHBoxLayout()
        segments.setContentsMargins(0, 0, 0, 0)
        segments.setSpacing(SPACE_XS)
        for button in buttons:
            button.setFixedWidth(76)
            button.setFixedHeight(36)
            segments.addWidget(button)
        layout.addLayout(segments)

    def _prepare_filter_popover_contents(self):
        age_layout = self.age_box.layout()
        while age_layout.count():
            age_layout.takeAt(0)
        age_layout.setContentsMargins(0, 0, 0, 0)
        age_layout.setSpacing(SPACE_MD)

        age_summary = QHBoxLayout()
        age_summary.setContentsMargins(0, 0, 0, 0)
        age_summary.setSpacing(SPACE_SM)
        self.lbl_pill.setVisible(False)
        self.lbl_val.setMinimumWidth(0)
        self.lbl_val.setWordWrap(False)
        age_summary.addWidget(self.lbl_val, 1)
        age_layout.addLayout(age_summary)

        self.slider.setMinimumWidth(0)
        age_layout.addWidget(self.slider)

        manual_label = getattr(self, "age_manual_label", None)
        manual_row = QHBoxLayout()
        manual_row.setContentsMargins(0, 0, 0, 0)
        manual_row.setSpacing(SPACE_SM)
        if manual_label is not None:
            age_layout.addWidget(manual_label)
        manual_row.addWidget(self.age_input, 1)
        manual_row.addWidget(self.btn_apply_age)
        age_layout.addLayout(manual_row)

        exclusions_layout = self.exclusions_box.layout()
        while exclusions_layout.count():
            exclusions_layout.takeAt(0)
        exclusions_layout.setContentsMargins(0, 0, 0, 0)
        exclusions_layout.setSpacing(SPACE_SM)

        detail_labels = {
            label.text(): label
            for label in self.exclusions_box.findChildren(QLabel)
            if label.objectName() == "manualLabel"
        }
        folders_label = detail_labels.get("Folder names")
        extensions_label = detail_labels.get("File extensions")
        size_label = detail_labels.get("Ignore files smaller than")
        if folders_label is not None:
            exclusions_layout.addWidget(folders_label)
        self.txt_excluded_folders.setMinimumWidth(0)
        exclusions_layout.addWidget(self.txt_excluded_folders)
        if extensions_label is not None:
            exclusions_layout.addWidget(extensions_label)
        self.txt_excluded_extensions.setMinimumWidth(0)
        exclusions_layout.addWidget(self.txt_excluded_extensions)

        if size_label is not None:
            exclusions_layout.addWidget(size_label)
        size_row = QHBoxLayout()
        self.exclusions_size_row = size_row
        size_row.setContentsMargins(0, 0, 0, 0)
        size_row.setSpacing(SPACE_SM)
        size_row.addWidget(self.min_size_input)
        size_row.addWidget(self.min_size_unit)
        size_row.addStretch()
        exclusions_layout.addLayout(size_row)

        self.lbl_exclusions_hint.setMaximumWidth(16777215)
        exclusions_layout.addWidget(self.lbl_exclusions_hint)
        exclusions_actions = QHBoxLayout()
        self.exclusions_actions_layout = exclusions_actions
        exclusions_actions.setContentsMargins(0, 0, 0, 0)
        exclusions_actions.setSpacing(SPACE_SM)
        exclusions_actions.addWidget(self.btn_reset_exclusions)
        exclusions_actions.addStretch()
        exclusions_actions.addWidget(self.btn_rescan_exclusions)
        exclusions_layout.addLayout(exclusions_actions)

    def _make_accordion_header(self, text):
        return AccordionHeader(text, self)

    def _rebuild_button_grid(self, grid, buttons, columns):
        while grid.count():
            grid.takeAt(0)
        for column in range(4):
            grid.setColumnStretch(column, 0)
        for index, button in enumerate(buttons):
            row = index // columns
            column = index % columns
            grid.addWidget(button, row, column, alignment=Qt.AlignmentFlag.AlignLeft)

    def _set_filter_button_widths(self, buttons, section_margin):
        scrollbar_clearance = 14
        grid_spacing = max(0, self.display_grid.spacing())
        usable_width = (
            self.width()
            - (2 * section_margin)
            - scrollbar_clearance
            - grid_spacing
        )
        button_width = max(
            self.FILTER_SEGMENT_MIN_WIDTH,
            min(self.FILTER_SEGMENT_MAX_WIDTH, usable_width // 2),
        )
        for button in buttons:
            button.setFixedWidth(button_width)

    def _update_responsive_drawer_layout(self):
        compact = self.width() <= 280
        section_margin = SPACE_MD if compact else SPACE_LG
        shell_left_margin = SPACE_SM if compact else SPACE_MD
        shell_right_margin = 2 if compact else 4
        shell_spacing = SPACE_XS if compact else SPACE_SM

        if hasattr(self, "drawer_header_layout"):
            self.drawer_header_layout.setContentsMargins(
                section_margin,
                SPACE_LG if not compact else SPACE_MD,
                SPACE_SM,
                SPACE_MD,
            )
        if hasattr(self, "search_layout"):
            self.search_layout.setContentsMargins(
                section_margin,
                SPACE_SM,
                section_margin,
                SPACE_SM,
            )
        if hasattr(self, "display_layout"):
            self.display_layout.setContentsMargins(
                section_margin,
                SPACE_SM,
                section_margin,
                SPACE_SM,
            )
        if hasattr(self, "view_layout"):
            self.view_layout.setContentsMargins(
                section_margin,
                SPACE_SM,
                section_margin,
                SPACE_SM,
            )
        if hasattr(self, "age_outer_layout"):
            self.age_outer_layout.setContentsMargins(
                section_margin,
                SPACE_SM,
                section_margin,
                SPACE_SM,
            )
        if hasattr(self, "exclusions_outer_layout"):
            self.exclusions_outer_layout.setContentsMargins(
                section_margin,
                SPACE_SM,
                section_margin,
                SPACE_SM,
            )
        if hasattr(self, "action_layout"):
            self.action_layout.setContentsMargins(
                section_margin,
                SPACE_MD,
                section_margin,
                SPACE_LG,
            )
        if hasattr(self, "search_shell_layout"):
            self.search_shell_layout.setContentsMargins(
                shell_left_margin,
                0,
                shell_right_margin,
                0,
            )
            self.search_shell_layout.setSpacing(shell_spacing)

        if hasattr(self, "display_grid") and hasattr(self, "display_buttons"):
            self._set_filter_button_widths(self.display_buttons, section_margin)
            self._rebuild_button_grid(
                self.display_grid,
                self.display_buttons,
                2,
            )
        if hasattr(self, "view_grid") and hasattr(self, "view_buttons"):
            self._set_filter_button_widths(self.view_buttons, section_margin)
            self._rebuild_button_grid(
                self.view_grid,
                self.view_buttons,
                2,
            )

        if hasattr(self, "age_summary_layout"):
            self.age_summary_layout.setDirection(
                QBoxLayout.Direction.TopToBottom if compact
                else QBoxLayout.Direction.LeftToRight
            )
        if hasattr(self, "age_manual_row"):
            self.age_manual_row.setDirection(QBoxLayout.Direction.LeftToRight)
            self.age_manual_row.setSpacing(shell_spacing)
        if hasattr(self, "age_manual_label"):
            self.age_manual_label.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
        if hasattr(self, "btn_apply_age"):
            self.btn_apply_age.setMinimumWidth(52)
            self.btn_apply_age.setMaximumWidth(52)
            self.btn_apply_age.setSizePolicy(
                QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Fixed,
            )

        if hasattr(self, "exclusions_size_row"):
            self.exclusions_size_row.setDirection(
                QBoxLayout.Direction.LeftToRight
                if self._exclusions_popup_layout_active
                else QBoxLayout.Direction.TopToBottom if compact
                else QBoxLayout.Direction.LeftToRight
            )
            self.exclusions_size_row.setSpacing(shell_spacing)
        if hasattr(self, "exclusions_actions_layout"):
            self.exclusions_actions_layout.setDirection(
                QBoxLayout.Direction.LeftToRight
                if self._exclusions_popup_layout_active or not compact
                else QBoxLayout.Direction.TopToBottom
            )
            self.exclusions_actions_layout.setSpacing(shell_spacing)
        if hasattr(self, "min_size_input"):
            expanding = self._exclusions_popup_layout_active or compact
            self.min_size_input.setMinimumWidth(0 if expanding else 86)
            self.min_size_input.setMaximumWidth(16777215 if expanding else 86)
            self.min_size_input.setSizePolicy(
                QSizePolicy.Policy.Ignored
                if self._exclusions_popup_layout_active
                else QSizePolicy.Policy.Expanding if expanding
                else QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Fixed,
            )
        if hasattr(self, "min_size_unit"):
            expanding = self._exclusions_popup_layout_active or compact
            self.min_size_unit.setMinimumWidth(0 if expanding else 72)
            self.min_size_unit.setMaximumWidth(16777215 if expanding else 72)
            self.min_size_unit.setSizePolicy(
                QSizePolicy.Policy.Ignored
                if self._exclusions_popup_layout_active
                else QSizePolicy.Policy.Expanding if expanding
                else QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Fixed,
            )

    def _toggle_collapsible_section(self, section):
        if section == "age":
            self._age_section_expanded = True
        elif section == "exclusions":
            if hasattr(self, "exclusions_popover"):
                should_close = (
                    self.exclusions_popover.isVisible()
                    or self.exclusions_popover.recently_hidden()
                )
            else:
                should_close = self._exclusions_section_expanded
            if should_close:
                self._exclusions_section_expanded = False
            else:
                self._exclusions_section_expanded = True
        self._sync_collapsible_sections()

    def _sync_collapsible_sections(self):
        self._update_popover_summaries()
        if hasattr(self, 'age_popover'):
            self.age_box.setVisible(True)
            self.exclusions_box.setVisible(True)
            if self._age_section_expanded and self.isVisible():
                self.exclusions_popover.hide()
                self.age_popover.show_below(self.age_heading)
            else:
                self.age_popover.hide()
            if self._exclusions_section_expanded and self.isVisible():
                self.age_popover.hide()
                self.exclusions_popover.show_below(self.btn_exclusions_toggle)
            else:
                self.exclusions_popover.hide()
        elif hasattr(self, 'age_box'):
            self.age_box.setVisible(True)
            self.age_box.updateGeometry()
            self.exclusions_box.setVisible(self._exclusions_section_expanded)
            self.exclusions_box.updateGeometry()
        if hasattr(self, 'age_heading'):
            self.age_heading.updateGeometry()
        if hasattr(self, 'btn_exclusions_toggle'):
            self.btn_exclusions_toggle.set_expanded(self._exclusions_section_expanded)
            self.btn_exclusions_toggle.updateGeometry()
        self.updateGeometry()

    def _on_filter_popover_closed(self, section):
        if section == "age":
            self._age_section_expanded = True
        elif section == "exclusions":
            self._exclusions_section_expanded = False
            self.btn_exclusions_toggle.set_expanded(False)

    def close_popovers(self):
        self._age_section_expanded = True
        self._exclusions_section_expanded = False
        if hasattr(self, "age_popover"):
            self.age_popover.hide()
        if hasattr(self, "exclusions_popover"):
            self.exclusions_popover.hide()
        if hasattr(self, "btn_exclusions_toggle"):
            self.btn_exclusions_toggle.set_expanded(False)

    def _update_popover_summaries(self):
        if hasattr(self, "age_heading") and hasattr(self, "applied_age_value"):
            age_text = (
                "Off"
                if self.applied_age_value == self.AGE_FILTER_DISABLED
                else f"{self.applied_age_value}mo"
            )
            self.age_heading.setText(f"Age: {age_text}")
        if hasattr(self, "btn_exclusions_toggle") and hasattr(
            self,
            "txt_excluded_folders",
        ):
            exclusions = self._scan_exclusions_from_controls()
            if exclusions.differs_from_default():
                current = exclusions.to_dict()
                default = ScanExclusions().to_dict()
                rule_count = (
                    len(
                        set(current["folder_names"])
                        ^ set(default["folder_names"])
                    )
                    + len(
                        set(current["extensions"])
                        ^ set(default["extensions"])
                    )
                    + int(
                        current["min_file_size_bytes"]
                        != default["min_file_size_bytes"]
                    )
                )
                summary = f"{rule_count} rule{'s' if rule_count != 1 else ''}"
            else:
                summary = "Default"
            self.btn_exclusions_toggle.set_text(f"Exclusions: {summary}")

    def _update_age_label(self, value):
        palette = current_palette()
        if value == self.AGE_FILTER_DISABLED:
            self.lbl_pill.setText("Off")
            self.lbl_pill.setStyleSheet(
                f"background-color: {palette['text_muted']}; color: {palette['bg']};"
            )
            self.lbl_val.setText("Age filtering is disabled")
        else:
            self.lbl_pill.setText(f"{value}m")
            self.lbl_pill.setStyleSheet(
                f"background-color: {palette['accent']}; color: {palette['on_accent']};"
            )
            
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

    def _update_age_apply_state(self, _value=None):
        controls_enabled = self.slider.isEnabled() and self.age_input.isEnabled()
        has_pending_value = self.age_input.value() != self.applied_age_value
        self.btn_apply_age.setEnabled(controls_enabled and has_pending_value)

    def get_older_than_secs(self):
        """Returns seconds threshold or None if age filtering is disabled."""
        v = self.applied_age_value
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
        # Exclusions are intentionally session-only; every launch starts clean.
        self.settings.remove("scan_exclusions")
        self._set_scan_exclusions_controls(ScanExclusions())

    def _save_scan_exclusions_from_controls(self):
        exclusions = self._scan_exclusions_from_controls()
        self.settings.remove("scan_exclusions")
        self._update_popover_summaries()
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
        
        self.applied_age_value = self.AGE_FILTER_DISABLED
        self._update_age_label(self.AGE_FILTER_DISABLED)
        self._update_age_apply_state()
        self._update_popover_summaries()

    def eventFilter(self, obj, event):
        if (
            hasattr(self, "slider")
            and obj == self.slider
            and event.type() == QEvent.Type.Wheel
        ):
            event.ignore()
            return True
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
        self.setWindowTitle("Watchdog")
        self.resize(1200, 720)

        self.scanner_thread = None
        self.delete_thread = None
        self.delete_progress = None
        self.page_load_thread = None
        self.page_load_threads = []
        self.page_load_request_id = 0
        self.lazy_child_threads = {}
        self.lazy_child_request_id = 0
        self.folder_browser_threads = {}
        self.folder_browser_request_id = 0
        self.folder_browser_scope = None
        self.folder_browser_live_refresh_at = 0.0
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
        self.has_completed_scan = False
        self.hide_partial_scan_results = False
        self.folder_cache = None
        self.loading_dialog = None
        self.selection_loading_dialog = None
        self.selection_loading_min_visible_until = 0.0
        self.delete_preview_thread = None
        self.pending_delete_preview_dialog = None
        self.file_type_thread = None
        self.file_type_request_id = 0
        self.file_types_dialog = None
        self.export_thread = None
        self.export_progress = None
        self.export_target_path = None
        self.active_extension_filter = None
        self.applied_scan_exclusions = None
        self.scan_exclusions_in_progress = None
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
        self._selection_button_targets_cache = None
        self._selection_sync_suppressed = False
        self.bulk_select_batch_size = 250
        self.bulk_select_batch_delay_ms = 0
        self.tree_model     = None
        self.proxy_model    = WatchdogFilterProxyModel()
        self.bulk_delete_scope = None
        self.selected_paths = IndexedPathDict()
        self.page_only_selected_paths = {}
        self.excluded_paths = IndexedPathDict()
        self.page_only_selection_page = None
        self.current_total_matches = 0
        self.current_lazy_show_all_tree = False
        self.current_filesystem_scope_fallback = False
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

    def _themed_toolbar_icon(self, asset_name, dark_color="#ffffff", light_color="#000000", size=18):
        icon_path = os.path.join(os.path.dirname(__file__), "assets", asset_name)
        pixmap = QIcon(icon_path).pixmap(QSize(size, size))
        painter = QPainter(pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(
            pixmap.rect(),
            QColor(dark_color if self.current_theme_name == "dark" else light_color),
        )
        painter.end()
        return QIcon(pixmap)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        app_layout = QVBoxLayout(root)
        app_layout.setContentsMargins(0, 0, 0, 0)
        app_layout.setSpacing(0)

        self.app_menu = QFrame()
        self.app_menu.setObjectName("appMenu")
        self.app_menu.setFixedHeight(58)
        menu_layout = QHBoxLayout(self.app_menu)
        menu_layout.setContentsMargins(SPACE_LG, SPACE_XS, SPACE_LG, SPACE_SM)
        menu_layout.setSpacing(SPACE_SM)

        workspace = QWidget()
        workspace.setObjectName("workspace")
        vbox = QVBoxLayout(workspace)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)
        app_layout.addWidget(workspace, 1)

        # Top bar
        self.topbar = QWidget()
        self.topbar.setObjectName("topbar")
        tb = QGridLayout(self.topbar)
        tb.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_LG, SPACE_SM)
        tb.setHorizontalSpacing(SPACE_MD)
        tb.setVerticalSpacing(0)
        tb.setColumnMinimumWidth(0, 170)
        tb.setColumnMinimumWidth(2, 170)
        tb.setColumnStretch(0, 1)
        tb.setColumnStretch(2, 1)

        self.brand_block = QFrame()
        self.brand_block.setObjectName("brandBlock")
        self.brand_block.setFixedWidth(170)
        brand_layout = QHBoxLayout(self.brand_block)
        brand_layout.setContentsMargins(0, SPACE_XS, SPACE_SM, SPACE_XS)
        brand_layout.setSpacing(0)
        self.lbl_app_title = QLabel("IBMS Watchdog")
        self.lbl_app_title.setObjectName("appTitle")
        brand_layout.addWidget(self.lbl_app_title)
        tb.addWidget(
            self.brand_block,
            0,
            0,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )

        self.path_action_layout = QHBoxLayout()
        path_action_layout = self.path_action_layout
        path_action_layout.setContentsMargins(0, 0, 0, 0)
        path_action_layout.setSpacing(SPACE_SM)

        self.path_input_shell = QFrame()
        self.path_input_shell.setObjectName("browsePathShell")
        self.path_input_shell.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.path_input_shell.setMinimumWidth(280)
        self.path_input_shell.setFixedHeight(44)
        path_input_layout = QHBoxLayout(self.path_input_shell)
        path_input_layout.setContentsMargins(SPACE_MD, 0, SPACE_SM, 0)
        path_input_layout.setSpacing(SPACE_SM)

        self.txt_path = QLineEdit()
        self.txt_path.setObjectName("browsePathInput")
        self.txt_path.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.txt_path.setMinimumWidth(0)
        self.txt_path.setFixedHeight(36)
        self.txt_path.setPlaceholderText("Choose a folder to scan")
        self.txt_path.setToolTip("Selected folder path")
        self.txt_path.setReadOnly(True)
        self.txt_path.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.txt_path.textChanged.connect(self._on_path_display_changed)
        path_input_layout.addWidget(self.txt_path)

        self.btn_browse = QPushButton("Browse")
        self.btn_browse.setObjectName("browsePathBtn")
        self.btn_browse.setToolTip("Browse folder")
        self.btn_browse.setAccessibleName("Browse folder")
        self.btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_browse.setFixedHeight(32)
        self.btn_browse.clicked.connect(self._browse)
        path_input_layout.addWidget(self.btn_browse)

        path_action_layout.addWidget(self.path_input_shell)

        self.btn_rescan = QPushButton("Rescan")
        self.btn_rescan.setObjectName("rescanBtn")
        self.btn_rescan.setToolTip("Scan the selected folder again")
        self.btn_rescan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_rescan.setFixedSize(80, 32)
        self.btn_rescan.setIcon(
            QIcon(os.path.join(os.path.dirname(__file__), "assets", "toolbar_refresh_green.svg"))
        )
        self.btn_rescan.setIconSize(QSize(18, 18))
        self.btn_rescan.clicked.connect(self.start_scan)
        path_action_layout.addWidget(self.btn_rescan)
        tb.addLayout(
            path_action_layout,
            0,
            1,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )

        self.btn_folder_browser = QPushButton("Folders Panel")
        self.btn_folder_browser.setObjectName("filterBtn")
        self.btn_folder_browser.setCheckable(True)
        self.btn_folder_browser.setChecked(True)
        self.btn_folder_browser.setToolTip("Show or hide folder navigation")
        self.btn_folder_browser.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_folder_browser.setIcon(
            QIcon(os.path.join(os.path.dirname(__file__), "assets", "folder_blue.svg"))
        )
        self.btn_folder_browser.setIconSize(QSize(18, 18))
        self.btn_folder_browser.setFixedHeight(38)
        self.btn_folder_browser.setMinimumWidth(138)
        self.btn_folder_browser.clicked.connect(self._toggle_folder_browser)
        self.btn_folder_browser.setProperty("menuItem", True)
        menu_layout.addWidget(self.btn_folder_browser)

        self.btn_filter = QPushButton("Filters")
        self.btn_filter.setObjectName("ghostBtn")
        self.btn_filter.setCheckable(True)
        self.btn_filter.setToolTip("Toggle filter bar (Alt+F)")
        self.btn_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_filter.setIcon(
            QIcon(os.path.join(os.path.dirname(__file__), "assets", "toolbar_filters_sliders.svg"))
        )
        self.btn_filter.setIconSize(QSize(18, 18))
        self.btn_filter.setFixedHeight(38)
        self.btn_filter.clicked.connect(self._toggle_filters)
        self.btn_filter.setObjectName("menuFilterBtn")
        self.btn_filter.setProperty("menuItem", True)
        menu_layout.addWidget(self.btn_filter)

        self.btn_file_types = QPushButton("File Extensions")
        self.btn_file_types.setObjectName("filterBtn")
        self.btn_file_types.setCheckable(True)
        self.btn_file_types.setToolTip("Show disk usage by file extension")
        self.btn_file_types.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_file_types.setIcon(
            QIcon(os.path.join(os.path.dirname(__file__), "assets", "file_blue.svg"))
        )
        self.btn_file_types.setIconSize(QSize(18, 18))
        self.btn_file_types.setFixedHeight(38)
        self.btn_file_types.setEnabled(False)
        self.btn_file_types.clicked.connect(self._show_file_types)
        self.btn_file_types.setProperty("menuItem", True)
        menu_layout.addWidget(self.btn_file_types)

        self.btn_exclusions = QPushButton("Exclusions")
        self.btn_exclusions.setObjectName("filterBtn")
        self.btn_exclusions.setCheckable(True)
        self.btn_exclusions.setToolTip("Choose files and folders to ignore during a scan")
        self.btn_exclusions.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_exclusions.setIcon(self._themed_toolbar_icon("toolbar_shield_lock.svg"))
        self.btn_exclusions.setIconSize(QSize(18, 18))
        self.btn_exclusions.setFixedHeight(38)
        self.btn_exclusions.setMinimumWidth(118)
        self.btn_exclusions.setSizePolicy(
            QSizePolicy.Policy.Minimum,
            QSizePolicy.Policy.Fixed,
        )
        self.btn_exclusions.clicked.connect(self._toggle_exclusions_popup)
        self.btn_exclusions.setProperty("menuItem", True)
        menu_layout.addWidget(self.btn_exclusions)

        self.btn_theme_toggle = QPushButton()
        self.btn_theme_toggle.setObjectName("themeToggleBtn")
        self.btn_theme_toggle.setToolTip("Toggle color scheme")
        self.btn_theme_toggle.setAccessibleName("Toggle color scheme")
        self.btn_theme_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_theme_toggle.setFixedSize(38, 38)
        self.btn_theme_toggle.clicked.connect(self._on_theme_changed)
        tb.addWidget(
            self.btn_theme_toggle,
            0,
            2,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

        self.btn_expand = QPushButton("Expand All")
        self.btn_expand.setObjectName("collapseAll")
        self.btn_expand.setCheckable(True)
        self.btn_expand.setToolTip("Expand or collapse all folders in the view")
        self.btn_expand.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_expand.clicked.connect(self._toggle_expand)

        menu_layout.addStretch()

        self.btn_export = QPushButton("Export CSV")
        self.btn_export.setObjectName("ghostBtn")
        self.btn_export.setToolTip("Export files, summaries, audit data, or scan history to CSV")
        self.btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_export.setIcon(
            QIcon(os.path.join(os.path.dirname(__file__), "assets", "toolbar_download.svg"))
        )
        self.btn_export.setIconSize(QSize(18, 18))
        self.btn_export.setFixedHeight(38)
        self.btn_export.setFixedWidth(138)
        self.btn_export.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.btn_export.clicked.connect(self._export_csv)
        self.btn_export.setProperty("menuItem", True)
        self.btn_export.setProperty("menuAction", True)
        menu_layout.addWidget(self.btn_export)

        self.btn_delete = QPushButton("Delete Selected")
        self.btn_delete.setObjectName("deleteBtn")
        self.btn_delete.setToolTip("Select items to move to the Recycle Bin")
        self.btn_delete.setAccessibleDescription(
            "Select items to move to the Recycle Bin"
        )
        self.btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete_icon_path = os.path.join(
            os.path.dirname(__file__),
            "assets",
            "toolbar_trash.svg",
        )
        self._delete_icon_active_path = os.path.join(
            os.path.dirname(__file__),
            "assets",
            "toolbar_trash_white.svg",
        )
        self.btn_delete.setIcon(QIcon(self._delete_icon_path))
        self.btn_delete.setIconSize(QSize(18, 18))
        self.btn_delete.setFixedHeight(38)
        self.btn_delete.setFixedWidth(176)
        self.btn_delete.clicked.connect(self._delete_selected)
        self._set_delete_armed(False)
        self.btn_delete.setEnabled(False)
        self.btn_delete.setProperty("menuItem", True)
        self.btn_delete.setProperty("menuAction", True)
        menu_layout.addWidget(self.btn_delete)
        self._update_theme_toggle_ui()
        vbox.addWidget(self.topbar)
        vbox.addWidget(self.app_menu)
        self._update_topbar_responsive_typography()

        # Main Content Area (resizable folder browser + content)
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setObjectName("mainSplitter")
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(3)

        # Left sidebar (folder browser only)
        self.folder_browser = FolderBrowserPanel()
        self.folder_browser.closeRequested.connect(self._toggle_folder_browser)
        self.folder_browser.scopeChanged.connect(self._on_folder_browser_scope_changed)
        self.folder_browser.loadRequested.connect(self._load_folder_browser_children)
        self.main_splitter.addWidget(self.folder_browser)

        self.fp = FilterPanel()
        self._filter_panel_width = self.fp.minimumWidth()
        self.fp.btn_close.clicked.connect(self._toggle_filters)
        self.fp.btn_reset.clicked.connect(self._reset_filters)
        # Dynamic filtering
        self.fp.bg.buttonClicked.connect(lambda _btn: self._on_filter_changed())
        self.fp.rb_videos.toggled.connect(self._on_videos_mode_toggled)
        self.fp.txt_search.returnPressed.connect(self._on_search_submitted)
        self.fp.searchCleared.connect(self._clear_search_filter)
        self.fp.btn_apply_age.clicked.connect(self._on_age_filter_apply_clicked)
        self.fp.exclusionChanged.connect(self._on_scan_exclusions_changed)
        self.fp.btn_rescan_exclusions.clicked.connect(self._rescan_from_exclusions)

        exclusions_section = self.fp.btn_exclusions_toggle.parentWidget()
        exclusions_section.setVisible(False)
        self.exclusions_popup_content = QWidget()
        exclusions_popup_layout = QVBoxLayout(self.exclusions_popup_content)
        exclusions_popup_layout.setContentsMargins(0, 0, 0, 0)
        exclusions_popup_layout.setSpacing(SPACE_XS)

        exclusions_title_row = QHBoxLayout()
        exclusions_title_row.setContentsMargins(0, 0, 0, 0)
        exclusions_title_row.setSpacing(SPACE_SM)
        exclusions_popup_title = QLabel("Exclusions")
        exclusions_popup_title.setObjectName("filterDrawerTitle")
        exclusions_title_row.addWidget(exclusions_popup_title)
        exclusions_title_row.addStretch(1)
        self.lbl_exclusions_rule_count = QLabel()
        self.lbl_exclusions_rule_count.setObjectName("exclusionsRuleCount")
        exclusions_title_row.addWidget(self.lbl_exclusions_rule_count)
        exclusions_popup_layout.addLayout(exclusions_title_row)

        exclusions_status_row = QHBoxLayout()
        exclusions_status_row.setContentsMargins(0, 0, 0, 0)
        exclusions_status_row.setSpacing(SPACE_SM)
        self.exclusions_popup_subtitle = QLabel(
            "Changes take effect after a re-scan."
        )
        self.exclusions_popup_subtitle.setObjectName("filterDrawerSubtitle")
        self.exclusions_popup_subtitle.setWordWrap(False)
        exclusions_status_row.addWidget(self.exclusions_popup_subtitle)
        exclusions_status_row.addStretch(1)
        self.lbl_exclusions_pending = QLabel("Re-scan required")
        self.lbl_exclusions_pending.setObjectName("exclusionsPendingBadge")
        self.lbl_exclusions_pending.setVisible(False)
        exclusions_status_row.addWidget(self.lbl_exclusions_pending)
        exclusions_popup_layout.addLayout(exclusions_status_row)
        exclusions_popup_layout.addSpacing(SPACE_SM)
        exclusions_popup_layout.addWidget(self.fp.exclusions_box)
        self.fp.prepare_exclusions_popup_layout()
        self.exclusions_popup = FilterPopover(380, self)
        self.exclusions_popup.set_content(self.exclusions_popup_content)
        self.exclusions_popup.closed.connect(
            lambda: self.btn_exclusions.setChecked(False)
        )
        self._update_exclusions_indicator()
        
        # View mode connections
        self.fp.rb_view_tree.toggled.connect(self._on_filter_changed)
        self.fp.rb_view_files.toggled.connect(self._on_filter_changed)
        self.fp.rb_view_folders.toggled.connect(self._on_filter_changed)
        
        self._apply_default_browse_preset(apply_now=False)
        self._update_age_controls_enabled()
        self._update_expand_control_visibility()
        # Right Content Area
        self.right_content = QWidget()
        self.right_content.setObjectName("contentArea")
        right_v = QVBoxLayout(self.right_content)
        right_v.setContentsMargins(SPACE_XL, SPACE_MD, SPACE_XL, SPACE_MD)
        right_v.setSpacing(SPACE_SM)

        # Controls row (above tree): expand / select all
        self.controls_bar = QWidget()
        controls_layout = QHBoxLayout(self.controls_bar)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(SPACE_XS)
        self.btn_expand.setObjectName("selectionControlBtn")
        self.btn_expand.setFixedSize(112, 34)
        controls_layout.addWidget(self.btn_expand)
        self.btn_select_all = QPushButton("Select All")
        self.btn_select_all.setObjectName("selectionControlBtn")
        self.btn_select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select_all.setFixedSize(112, 34)
        self.btn_select_all.clicked.connect(self._toggle_select_all)
        controls_layout.addWidget(self.btn_select_all)

        self.btn_current_page_selection = QPushButton("Select Current Page")
        self.btn_current_page_selection.setObjectName("selectionControlBtn")
        self.btn_current_page_selection.setToolTip("Select every item shown on this page")
        self.btn_current_page_selection.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_current_page_selection.setFixedSize(164, 34)
        self.btn_current_page_selection.setVisible(False)
        self.btn_current_page_selection.clicked.connect(self._toggle_current_page_selection)
        controls_layout.addWidget(self.btn_current_page_selection)

        self.btn_clear_selection = QPushButton("Unselect All")
        self.btn_clear_selection.setObjectName("selectionControlBtn")
        self.btn_clear_selection.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_selection.setFixedSize(124, 34)
        self.btn_clear_selection.clicked.connect(self._unselect_all)
        self.btn_clear_selection.setVisible(False)
        
        self.btn_select_inactive = QPushButton("Select All Inactive")
        self.btn_select_inactive.setObjectName("selectionControlBtn")
        self.btn_select_inactive.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select_inactive.setFixedSize(160, 34)
        self.btn_select_inactive.clicked.connect(self._select_inactive)
        self.btn_select_inactive.setEnabled(False)
        controls_layout.addWidget(self.btn_select_inactive)

        self.btn_select_empty = QPushButton("Select All Empty")
        self.btn_select_empty.setObjectName("selectionControlBtn")
        self.btn_select_empty.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select_empty.setFixedSize(144, 34)
        self.btn_select_empty.setToolTip("No empty folders are available in the current view.")
        self.btn_select_empty.clicked.connect(self._select_empty)
        self.btn_select_empty.setEnabled(False)
        controls_layout.addWidget(self.btn_select_empty)

        controls_layout.addStretch()

        self.btn_prev_page = QPushButton("<")
        self.btn_prev_page.setObjectName("pageNavBtn")
        self.btn_prev_page.setFixedSize(30, 30)
        self.btn_prev_page.setToolTip("Previous Page")
        self.btn_prev_page.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_prev_page.clicked.connect(self._prev_page)
        controls_layout.addWidget(self.btn_prev_page)

        self.lbl_page_info = QLabel("Page 1")
        self.lbl_page_info.setObjectName("pageInfo")
        self.lbl_page_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_page_info.setMinimumWidth(150)
        self.lbl_page_info.setFixedHeight(30)
        controls_layout.addWidget(self.lbl_page_info)

        self.btn_next_page = QPushButton(">")
        self.btn_next_page.setObjectName("pageNavBtn")
        self.btn_next_page.setFixedSize(30, 30)
        self.btn_next_page.setToolTip("Next Page")
        self.btn_next_page.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next_page.clicked.connect(self._next_page)
        controls_layout.addWidget(self.btn_next_page)

        self.type_filter_banner = QFrame()
        self.type_filter_banner.setObjectName("activeTypeFilter")
        type_filter_layout = QHBoxLayout(self.type_filter_banner)
        type_filter_layout.setContentsMargins(
            SPACE_MD,
            SPACE_XS,
            SPACE_MD,
            SPACE_XS,
        )
        type_filter_layout.setSpacing(SPACE_SM)

        type_filter_title = QLabel("File type filter")
        type_filter_title.setObjectName("activeTypeFilterLabel")
        type_filter_layout.addWidget(type_filter_title)

        self.lbl_active_type_filter = QLabel()
        self.lbl_active_type_filter.setObjectName("activeTypeFilterValue")
        self.lbl_active_type_filter.setWordWrap(False)
        type_filter_layout.addWidget(self.lbl_active_type_filter)

        self.lbl_active_type_filter_meta = QLabel()
        self.lbl_active_type_filter_meta.setObjectName("activeTypeFilterMeta")
        self.lbl_active_type_filter_meta.setWordWrap(False)
        type_filter_layout.addWidget(self.lbl_active_type_filter_meta)
        type_filter_layout.addStretch()

        self.btn_clear_type_filter = QPushButton("Clear type")
        self.btn_clear_type_filter.setObjectName("activeTypeFilterClear")
        self.btn_clear_type_filter.setToolTip("Clear the selected file type")
        self.btn_clear_type_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_type_filter.clicked.connect(self._clear_type_filter)
        type_filter_layout.addWidget(self.btn_clear_type_filter)

        self.type_filter_banner.setVisible(False)
        right_v.addWidget(self.type_filter_banner)

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
        btn_browse_cta.setObjectName("emptyBrowseBtn")
        btn_browse_cta.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_browse_cta.setFixedWidth(168)
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
        self.scan_stats.setWordWrap(False)
        self.scan_stats.setMaximumWidth(900)
        self.scan_stats.setMinimumHeight(24)

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
        self.main_splitter.addWidget(self.right_content)
        self.main_splitter.addWidget(self.fp)
        self.main_splitter.setCollapsible(0, False)
        self.main_splitter.setCollapsible(1, False)
        self.main_splitter.setCollapsible(2, False)
        self.main_splitter.splitterMoved.connect(self._remember_filter_panel_width)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        self.main_splitter.setSizes([self.folder_browser.minimumWidth(), 1000, 0])

        content_shell = QWidget()
        content_shell.setObjectName("contentShell")
        content_shell_layout = QHBoxLayout(content_shell)
        content_shell_layout.setContentsMargins(0, 0, 0, 0)
        content_shell_layout.setSpacing(0)
        content_shell_layout.addWidget(self.main_splitter, 1)
        vbox.addWidget(content_shell, 1)

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
        
        self.chip_empty = self._chip("Empty: 0 items", "chipEmpty")
        self.chip_inactive_folders = self._chip(
            "Inactive: 0 folders · 0 files",
            "chipInactive",
        )
        self.chip_inactive_files = self.chip_inactive_folders
        self.chip_browse_size = self._chip("Total Size: --", "chipSpace")
        self.chip_folder_size = self._chip("Folder Size --", "chipSpace")
        self.chip_folder_size.setVisible(False)
        self.chip_filtered_size = self._chip("Filtered Size --", "chipSpace")
        self.chip_filtered_size.setVisible(False)
        self.chip_page_size = self._chip("Current Page Size: --", "chipSpace")
        self.chip_selected_size = self._chip(
            "Selected: 0 files, 0 folders · 0 B",
            "chipSpace",
        )

        sbl.addWidget(self.chip_empty)
        sbl.addWidget(self.chip_inactive_folders)
        sbl.addWidget(self.chip_browse_size)
        sbl.addWidget(self.chip_folder_size)
        sbl.addWidget(self.chip_filtered_size)
        sbl.addWidget(self.chip_page_size)
        sbl.addWidget(self.chip_selected_size)
        self._update_status_metrics_visibility()
        vbox.addWidget(sb)

        # Do NOT auto-start scan - let the user enter a path first

    def _on_theme_changed(self):
        next_theme = "dark" if self.current_theme_name == "light" else "light"
        self.current_theme_name = resolve_theme_name(next_theme)
        self.settings.setValue("theme", self.current_theme_name)
        apply_theme(QApplication.instance(), self.current_theme_name)
        self._update_theme_toggle_ui()
        if hasattr(self, 'fp'):
            self.fp._update_age_label(self.fp.age_input.value())
        if self.scanner_thread and self.scanner_thread.isRunning():
            self._set_rescan_stop_ui()
        if hasattr(self, 'tree'):
            self._update_status_column_visibility()
            self.tree.viewport().update()
            self.tree.header().viewport().update()
        if self.file_types_dialog:
            self.file_types_dialog.update()

    def _update_theme_toggle_ui(self):
        if not hasattr(self, 'btn_theme_toggle'):
            return
        icon_name = "theme_moon.svg" if self.current_theme_name == "light" else "theme_sun.svg"
        icon_path = os.path.join(os.path.dirname(__file__), "assets", icon_name)
        self.btn_theme_toggle.setIcon(QIcon(icon_path))
        self.btn_theme_toggle.setIconSize(QSize(22, 22))
        if hasattr(self, "btn_exclusions"):
            self.btn_exclusions.setIcon(self._themed_toolbar_icon("toolbar_shield_lock.svg"))
            self.btn_exclusions.setIconSize(QSize(18, 18))
        button_label = "Switch to dark mode" if self.current_theme_name == "light" else "Switch to light mode"
        self.btn_theme_toggle.setToolTip(button_label)
        self.btn_theme_toggle.setAccessibleName(button_label)

    def _set_rescan_idle_ui(self):
        if not hasattr(self, 'btn_rescan'):
            return
        self.btn_rescan.setObjectName("rescanBtn")
        self.btn_rescan.setText("Rescan")
        self.btn_rescan.setToolTip("Scan the selected folder again")
        self.btn_rescan.setIcon(
            QIcon(os.path.join(os.path.dirname(__file__), "assets", "toolbar_refresh_green.svg"))
        )
        self.btn_rescan.setIconSize(QSize(18, 18))
        self.btn_rescan.style().unpolish(self.btn_rescan)
        self.btn_rescan.style().polish(self.btn_rescan)

    def _set_rescan_stop_ui(self):
        if not hasattr(self, 'btn_rescan'):
            return
        self.btn_rescan.setObjectName("stopScanBtn")
        self.btn_rescan.setText("Stop")
        self.btn_rescan.setToolTip("Stop the active scan")
        self.btn_rescan.setIcon(
            QIcon(os.path.join(os.path.dirname(__file__), "assets", "toolbar_stop_red.svg"))
        )
        self.btn_rescan.setIconSize(QSize(18, 18))
        self.btn_rescan.style().unpolish(self.btn_rescan)
        self.btn_rescan.style().polish(self.btn_rescan)

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
            display_text = "Total Size: --"
        elif isinstance(text, (int, float)):
            display_text = f"Total Size: {self._format_chip_size(int(text))}"
        else:
            display_text = text if str(text).startswith("Total Size: ") else f"Total Size: {text}"
        self._set_chip_text(self.chip_browse_size, display_text)

    def _known_browse_total(self):
        root_path = getattr(self, 'current_scan_root', None)
        root_key = self._path_key(root_path) if root_path else None
        if (
            root_key
            and root_key == self.cached_folder_total_root
            and self.cached_folder_total is not None
        ):
            return self.cached_folder_total

        cache = getattr(self, 'folder_cache', None)
        if cache is None:
            return None
        if root_path and hasattr(cache, 'folder_metadata'):
            metadata = cache.folder_metadata(root_path)
            if metadata is not None:
                return metadata[0]
        return getattr(cache, 'running_total_size', None)

    def _set_scanning_total_chip(self):
        running_total = 0
        if self.folder_cache is not None:
            running_total = getattr(self.folder_cache, "running_total_size", 0) or 0
        self._set_chip_text(self.chip_browse_size, f"Scanning… {self._format_chip_size(running_total)} so far")

        self.chip_folder_size.setVisible(False)
        self.chip_filtered_size.setVisible(False)

    def _set_selected_summary_chip(self, size_text=None, folder_count=None, file_count=None):
        if self.is_scanning:
            self.chip_selected_size.setVisible(False)
            return

        size_part = "--" if size_text is None else size_text
        folders_part = "--" if folder_count is None else f"{folder_count:,}"
        files_part = "--" if file_count is None else f"{file_count:,}"
        text = (
            f"Selected: {files_part} files, {folders_part} folders · {size_part}"
        )
        self._set_chip_text(self.chip_selected_size, text)
        self.chip_selected_size.setVisible(True)

    def _set_empty_result_metrics(self):
        self.totals_request_id += 1
        self._cancel_running_totals_thread()
        self.totals_refresh_pending = False
        self.totals_refresh_options = None
        self._set_chip_text(self.chip_empty, "Empty: 0 items")
        self._set_chip_text(
            self.chip_inactive_folders,
            "Inactive: 0 folders · 0 files",
        )
        self._set_chip_text(self.chip_page_size, "Current Page Size: 0 B")
        self.cached_selected_total = 0
        self._set_selected_summary_chip("0 B", 0, 0)
        if self.active_extension_filter is not None:
            extension = self.active_extension_filter or "(no extension)"
            self._set_chip_text(
                self.chip_filtered_size,
                f"Type {extension} Size 0 B",
            )
            self.chip_filtered_size.setVisible(True)
        elif self.applied_name_filter:
            self._set_chip_text(self.chip_filtered_size, "Search Size 0 B")
            self.chip_filtered_size.setVisible(True)
        else:
            self.chip_filtered_size.setVisible(False)

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
        seconds = max(0.0, float(seconds or 0.0))
        if seconds < 10:
            if seconds < 1:
                return f"{seconds:.2f}s"
            return f"{seconds:.1f}s"
        whole_seconds = int(seconds)
        if whole_seconds < 60:
            return f"{whole_seconds}s"
        hours = whole_seconds // 3600
        minutes = (whole_seconds % 3600) // 60
        secs = whole_seconds % 60
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    def _scan_stats_text(self, detail=None):
        if not detail:
            return "Preparing the index and waiting for the first batch of results."
        elapsed = 0.0 if not detail else detail.get("elapsed_secs", 0.0)
        rate = 0.0 if not detail else detail.get("rate", 0.0)
        scanned = 0 if not detail else detail.get("scanned", 0) or 0

        rate_text = "rate calculating..." if rate <= 0 else f"{rate:,.0f} items/sec"
        parts = [f"{scanned:,} items scanned", rate_text, f"{self._format_scan_duration(elapsed)} elapsed"]
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
                known_total = self._known_browse_total()
                self._set_total_summary_chip(
                    "--" if known_total is None else known_total
                )
                if self.folder_browser_scope:
                    cached_scoped_total = self._cached_scoped_folder_total()
                    self._set_chip_text(
                        self.chip_folder_size,
                        "Folder Size calculating..."
                        if cached_scoped_total is None
                        else (
                            "Folder Size "
                            f"{self._format_chip_size(cached_scoped_total)}"
                        ),
                    )
                    self.chip_folder_size.setVisible(True)
                else:
                    self.chip_folder_size.setVisible(False)
                if self.active_extension_filter is not None:
                    extension = self.active_extension_filter or "(no extension)"
                    self._set_chip_text(
                        self.chip_filtered_size,
                        f"Type {extension} Size calculating...",
                    )
                    self.chip_filtered_size.setVisible(True)
                elif self.applied_name_filter:
                    self._set_chip_text(self.chip_filtered_size, "Search Size calculating...")
                    self.chip_filtered_size.setVisible(True)
                else:
                    self.chip_filtered_size.setVisible(False)
        if page:
            self._set_chip_text(self.chip_page_size, "Current Page Size: calculating...")
        if selected:
            if self.is_scanning:
                self.chip_selected_size.setVisible(False)
            elif self._selected_roots_for_delete():
                self._set_chip_text(self.chip_selected_size, "Selected: calculating...")
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

    def resizeEvent(self, event):
        self._update_topbar_responsive_typography()
        self._update_path_field_width()
        super().resizeEvent(event)

    def _on_path_display_changed(self, path):
        self.txt_path.setToolTip(path or "Selected folder path")
        self._update_path_field_width()

    def _update_path_field_width(self):
        if not hasattr(self, 'path_input_shell'):
            return

        path = self.txt_path.text().strip()
        text_width = self.txt_path.fontMetrics().horizontalAdvance(
            path or self.txt_path.placeholderText()
        )
        browse_width = self.btn_browse.width() if hasattr(self, 'btn_browse') else 74
        desired_width = max(420, text_width + browse_width + 64)

        title_width = self.lbl_app_title.sizeHint().width() if hasattr(self, 'lbl_app_title') else 0
        theme_width = self.btn_theme_toggle.width() if hasattr(self, 'btn_theme_toggle') else 38
        rescan_width = self.btn_rescan.width() if hasattr(self, 'btn_rescan') else 80
        available_width = max(
            280,
            self.width() - title_width - theme_width - rescan_width - 128,
        )
        self.path_input_shell.setFixedWidth(
            min(desired_width, 720, available_width)
        )

    def _update_topbar_responsive_typography(self):
        if not hasattr(self, 'topbar'):
            return

        width = max(720, self.width())
        if width >= 1400:
            title_size = 16
            control_size = 12
            compact_size = 11
        elif width >= 1200:
            title_size = 15
            control_size = 11
            compact_size = 10
        elif width >= 980:
            title_size = 14
            control_size = 10
            compact_size = 9
        else:
            title_size = 13
            control_size = 9
            compact_size = 8
        self._topbar_control_font_px = control_size

        title_font = QFont(self.lbl_app_title.font())
        title_font.setPixelSize(title_size)
        self.lbl_app_title.setFont(title_font)

        control_targets = (
            self.txt_path,
            self.btn_browse,
            self.btn_rescan,
            self.btn_folder_browser,
            self.btn_filter,
            self.btn_file_types,
            self.btn_exclusions,
            self.btn_export,
            self.btn_delete,
        )
        for widget in control_targets:
            font = QFont(widget.font())
            font.setPixelSize(control_size)
            widget.setFont(font)
        toggle_font = QFont(self.btn_theme_toggle.font())
        toggle_font.setPixelSize(compact_size)
        self.btn_theme_toggle.setFont(toggle_font)

        browse_width = max(66, self.btn_browse.fontMetrics().horizontalAdvance("Browse") + 26)
        self.btn_browse.setFixedWidth(browse_width)
        self._update_delete_button_layout()
        self._update_path_field_width()

    def _update_delete_button_layout(self):
        if not hasattr(self, 'btn_delete'):
            return

        base_size = getattr(
            self,
            '_topbar_control_font_px',
            self.btn_delete.font().pixelSize(),
        )
        if base_size <= 0:
            base_size = 10
        fitted_size = base_size
        min_size = 7
        available_text_width = max(
            96,
            self.btn_delete.width() - self.btn_delete.iconSize().width() - 54,
        )
        button_text = self.btn_delete.text()

        while fitted_size > min_size:
            fitted_font = QFont(self.btn_delete.font())
            fitted_font.setPixelSize(fitted_size)
            if QFontMetrics(fitted_font).horizontalAdvance(button_text) <= available_text_width:
                break
            fitted_size -= 1

        final_font = QFont(self.btn_delete.font())
        final_font.setPixelSize(fitted_size)
        self.btn_delete.setFont(final_font)

    def _update_delete_button_copy(self, total=0, all_pages=False):
        self.btn_delete.setText("Delete Selected")
        total = max(0, int(total or 0))
        if total <= 0:
            tooltip = "Select items to move to the Recycle Bin"
        elif all_pages:
            tooltip = (
                f"Move {total:,} matching items across all pages "
                "to the Recycle Bin"
            )
        else:
            tooltip = (
                f"Move {total:,} selected item"
                f"{'s' if total != 1 else ''} to the Recycle Bin"
            )
        self.btn_delete.setToolTip(tooltip)
        self.btn_delete.setAccessibleDescription(tooltip)
        self._update_delete_button_layout()

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
    # Folder browser and filter panel
    # -------------------------------------------------------------------------

    def _toggle_folder_browser(self):
        is_visible = self.folder_browser.isVisible()
        self.folder_browser.setVisible(not is_visible)
        self.btn_folder_browser.setChecked(not is_visible)

    def _set_folder_browser_scope(self, path, reload=True):
        root = getattr(self, 'current_scan_root', None)
        normalized = os.path.normpath(path) if path else None
        if root and normalized and self._path_key(normalized) == self._path_key(root):
            normalized = None
        if normalized == self.folder_browser_scope:
            return
        self.folder_browser_scope = normalized
        update_cached_size = getattr(
            self,
            '_update_scoped_folder_size_from_cache',
            None,
        )
        if callable(update_cached_size):
            update_cached_size(normalized)
        if reload:
            self._on_filter_changed(clear_extension=False)

    def _cached_scoped_folder_total(self, path=None):
        scoped_path = path if path is not None else self.folder_browser_scope
        cache = getattr(self, 'folder_cache', None)
        if not scoped_path or cache is None or not hasattr(cache, 'folder_metadata'):
            return None
        metadata = cache.folder_metadata(scoped_path)
        return None if metadata is None else metadata[0]

    def _update_scoped_folder_size_from_cache(self, path=None):
        scoped_path = path if path is not None else self.folder_browser_scope
        if not scoped_path:
            self.chip_folder_size.setVisible(False)
            return
        cached_total = self._cached_scoped_folder_total(scoped_path)
        if cached_total is None:
            self._set_chip_text(
                self.chip_folder_size,
                "Folder Size calculating...",
            )
        else:
            self._set_chip_text(
                self.chip_folder_size,
                f"Folder Size {self._format_chip_size(cached_total)}",
            )
        self.chip_folder_size.setVisible(True)

    def _on_folder_browser_scope_changed(self, path):
        self._set_folder_browser_scope(path)

    def _load_folder_browser_children(self, folder_path):
        if not folder_path or not getattr(self, 'current_scan_root', None):
            return
        if (
            getattr(self, 'is_scanning', False)
            and getattr(self, 'hide_partial_scan_results', False)
        ):
            self.folder_browser.defer_load(folder_path)
            return
        cache = getattr(self, 'folder_cache', None)
        if cache:
            children = []
            if cache.has_children_for(folder_path):
                children = [
                    child
                    for child in cache.children_for(folder_path, 0, False)
                    if child.get('is_dir', False)
                ]
                for child in children:
                    child['_children_loaded'] = not cache.has_folder_children(
                        child.get('path')
                    )
            if children:
                self.folder_browser.apply_children(folder_path, children)
                if self.is_scanning:
                    return
            if self.is_scanning:
                # Keep the node retryable while the background loader falls back
                # to enumerating folders directly from the selected location.
                self.folder_browser.defer_load(folder_path)
        self.folder_browser_request_id += 1
        request_id = self.folder_browser_request_id
        thread = LazyChildrenLoadThread(
            request_id,
            folder_path,
            sort_column=0,
            sort_desc=False,
            options=None,
            cache=cache,
            folders_only=True,
            filesystem_fallback=True,
            force_filesystem=not self.is_scanning,
            scan_exclusions=getattr(
                getattr(self, 'fp', None),
                'get_scan_exclusions',
                lambda: ScanExclusions(),
            )(),
            parent=self,
        )
        self.folder_browser_threads[request_id] = thread
        thread.children_ready.connect(self._on_folder_browser_children_ready)
        thread.children_failed.connect(self._on_folder_browser_children_failed)
        thread.finished.connect(
            lambda request_id=request_id: self.folder_browser_threads.pop(request_id, None)
        )
        thread.start()

    def _on_folder_browser_children_ready(self, request_id, folder_path, children):
        if request_id not in self.folder_browser_threads:
            return
        if (
            getattr(self, 'is_scanning', False)
            and getattr(self, 'hide_partial_scan_results', False)
        ):
            return
        self.folder_browser.apply_children(folder_path, children)

    def _on_folder_browser_children_failed(self, request_id, folder_path, error):
        if request_id not in self.folder_browser_threads:
            return
        self.folder_browser.mark_load_failed(folder_path)

    def _reset_folder_browser(self, root_path=None):
        self.folder_browser_request_id += 1
        self.folder_browser_threads.clear()
        self.folder_browser_scope = None
        self.folder_browser.set_root(root_path)

    def _refresh_folder_browser_from_cache(self, force=False):
        if (
            getattr(self, 'is_scanning', False)
            and getattr(self, 'hide_partial_scan_results', False)
        ):
            return
        cache = getattr(self, 'folder_cache', None)
        root_path = getattr(self, 'current_scan_root', None)
        if not cache or not root_path or not hasattr(self, 'folder_browser'):
            return

        now = time.monotonic()
        if not force and now - self.folder_browser_live_refresh_at < 0.75:
            return
        self.folder_browser_live_refresh_at = now

        browser_root = self.folder_browser.root_path
        if not browser_root or _path_key(browser_root) != _path_key(root_path):
            self._reset_folder_browser(root_path)

        model = self.folder_browser.model
        nodes = list(model.nodes_by_path.values())
        for node in nodes:
            index = model.index_for_path(node.path)
            should_refresh = (
                _path_key(node.path) == _path_key(root_path)
                or node.loaded
                or (index.isValid() and self.folder_browser.tree.isExpanded(index))
            )
            if not should_refresh or not cache.has_children_for(node.path):
                continue
            children = [
                child
                for child in cache.children_for(node.path, 0, False)
                if child.get('is_dir', False)
            ]
            for child in children:
                child['_children_loaded'] = not cache.has_folder_children(
                    child.get('path')
                )
            if children:
                self.folder_browser.merge_children(node.path, children)
            elif self.is_scanning and not node.loaded:
                self.folder_browser.defer_load(node.path)
            elif (
                not self.is_scanning
                and index.isValid()
                and self.folder_browser.tree.isExpanded(index)
            ):
                model.fetchMore(index)

    def _toggle_filters(self):
        is_visible = self.fp.isVisible()
        if is_visible:
            self.fp.close_popovers()
            self._filter_panel_width = max(
                self.fp.minimumWidth(),
                min(self.fp.width(), self.fp.maximumWidth()),
            )
            sizes = self.main_splitter.sizes()
            if len(sizes) >= 3:
                self.fp.setVisible(False)
                self.main_splitter.setSizes([sizes[0], sizes[1] + sizes[2], 0])
        else:
            self.fp.setVisible(True)
            self._show_filter_panel()
        self.btn_filter.setChecked(not is_visible)

    def _toggle_exclusions_popup(self, _checked=False):
        should_close = (
            self.exclusions_popup.isVisible()
            or self.exclusions_popup.recently_hidden()
        )
        if should_close:
            self.exclusions_popup.hide()
            self.btn_exclusions.setChecked(False)
            return
        self.fp.exclusions_box.setVisible(True)
        self.exclusions_popup.show_below(self.btn_exclusions)
        self.btn_exclusions.setChecked(True)

    def _show_filter_panel(self):
        filter_width = max(
            self.fp.minimumWidth(),
            min(self._filter_panel_width, self.fp.maximumWidth()),
        )
        sizes = self.main_splitter.sizes()
        if len(sizes) < 3:
            return
        available_right = max(420, sizes[1] + sizes[2])
        filter_width = min(filter_width, max(self.fp.minimumWidth(), available_right - 420))
        content_width = max(420, available_right - filter_width)
        self.main_splitter.setSizes([sizes[0], content_width, filter_width])

    def _remember_filter_panel_width(self, *_args):
        if self.fp.isVisible() and self.fp.width() > 0:
            self._filter_panel_width = max(
                self.fp.minimumWidth(),
                min(self.fp.width(), self.fp.maximumWidth()),
            )

    def _apply_filters(self):
        # 1. Update proxy model so it can format the Status column correctly
        self._cancel_running_bulk_select_thread()
        self._update_age_controls_enabled()
        age_secs = self.fp.get_older_than_secs()
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
        view_mode = self.fp.get_view_mode()
        if view_mode == "Files":
            self.btn_expand.setVisible(False)
            return
        if self.fp.rb_all.isChecked() and view_mode in ("Tree", "Folders"):
            expanded = self._has_expanded_tree_nodes()
            blocker = QSignalBlocker(self.btn_expand)
            self.btn_expand.setChecked(expanded)
            self.btn_expand.setText("Collapse All")
            self.btn_expand.setVisible(expanded)
            self.btn_expand.setEnabled(expanded)
            del blocker
            return
        self.btn_expand.setVisible(True)

    def _has_expanded_tree_nodes(self):
        if not hasattr(self, 'tree') or not self.proxy_model:
            return False
        stack = [QModelIndex()]
        while stack:
            parent = stack.pop()
            for row in range(self.proxy_model.rowCount(parent)):
                index = self.proxy_model.index(row, 0, parent)
                if self.tree.isExpanded(index):
                    return True
                stack.append(index)
        return False

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
            paginated_files_view = bool(
                self.fp.get_view_mode() == "Files"
                and self.current_total_matches > 2000
                and not self.current_lazy_show_all_tree
            )
            self.chip_page_size.setVisible(
                not self.fp.rb_all.isChecked() or paginated_files_view
            )
        if hasattr(self, 'chip_selected_size'):
            self.chip_selected_size.setVisible((not self.is_scanning) and show_status)

    def _set_page_controls_visible(self, visible):
        for widget in (
            getattr(self, 'btn_current_page_selection', None),
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

        self.fp.slider.setEnabled(True)
        self.fp.age_input.setEnabled(True)
        self.fp.lbl_pill.setEnabled(True)
        self.fp.lbl_val.setEnabled(True)
        self.fp.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fp.age_input.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fp.btn_apply_age.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fp._update_age_apply_state()

    def _reset_filters(self):
        self.search_debounce_timer.stop()
        self.applied_name_filter = ""
        self.active_extension_filter = None
        self._update_type_filter_ui()
        blocker = QSignalBlocker(self.fp.txt_search)
        self.fp.txt_search.clear()
        del blocker
        self.fp.search_clear_action.setVisible(False)
        self._apply_default_browse_preset()

    def _on_scan_exclusions_changed(self):
        self._update_exclusions_indicator()

    def _rescan_from_exclusions(self):
        self.fp._normalize_and_save_scan_exclusions()
        self.fp.close_popovers()
        self.start_scan()

    def _on_scan_exclusions_summary(self, excluded_count):
        self.last_scan_excluded_count = excluded_count or 0

    def _update_exclusions_indicator(self):
        if not hasattr(self, 'btn_exclusions') or not hasattr(self, 'fp'):
            return

        current = self.fp.get_scan_exclusions().to_dict()
        default = ScanExclusions().to_dict()
        applied = (
            self.scan_exclusions_in_progress
            if self.is_scanning and self.scan_exclusions_in_progress is not None
            else self.applied_scan_exclusions
        )
        rule_count = (
            len(current["folder_names"])
            + len(current["extensions"])
            + int(current["min_file_size_bytes"] > 0)
        )

        if current == default and applied in (None, default):
            state = "default"
            text = "Exclusions: Default"
            tooltip = "Default exclusion rules are active"
        elif applied is not None and current == applied:
            state = "active"
            text = f"Exclusions: {rule_count} active"
            tooltip = (
                f"{rule_count} exclusion rule"
                f"{'s are' if rule_count != 1 else ' is'} active"
            )
        else:
            state = "pending"
            text = "Exclusions: Pending"
            tooltip = "Exclusion changes are waiting for a re-scan"

        if hasattr(self, "lbl_exclusions_rule_count"):
            self.lbl_exclusions_rule_count.setText(
                f"{rule_count} active rule"
                f"{'s' if rule_count != 1 else ''}"
            )
        if hasattr(self, "lbl_exclusions_pending"):
            self.lbl_exclusions_pending.setVisible(state == "pending")

        self.btn_exclusions.setText(text)
        self.btn_exclusions.setToolTip(tooltip)
        self.btn_exclusions.setProperty("exclusionState", state)
        self.btn_exclusions.setMinimumWidth(
            max(118, self.btn_exclusions.sizeHint().width() + SPACE_XS)
        )
        self.btn_exclusions.style().unpolish(self.btn_exclusions)
        self.btn_exclusions.style().polish(self.btn_exclusions)
        self.btn_exclusions.update()

    def _has_completed_scan_context(self):
        return bool(getattr(self, 'current_scan_root', None)) and not self.is_scanning

    def _update_file_types_enabled(self):
        if hasattr(self, 'btn_file_types'):
            self.btn_file_types.setEnabled(self._has_completed_scan_context())

    def _show_file_types(self):
        if not self._has_completed_scan_context():
            return

        self._update_type_filter_ui()
        self.file_type_request_id += 1
        request_id = self.file_type_request_id

        if self.file_type_thread and self.file_type_thread.isRunning():
            self.file_type_thread.cancel()

        self.file_types_dialog = FileTypesDialog(self)
        self.file_types_dialog.extension_selected.connect(self._drill_down_file_type)
        self.file_types_dialog.set_loading()
        self.file_types_dialog.show()

        self.file_type_thread = FileTypeBreakdownThread(request_id, parent=self)
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
        self._update_type_filter_ui()
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

    def _update_type_filter_ui(self):
        if not hasattr(self, 'btn_file_types'):
            return

        is_active = self.active_extension_filter is not None
        extension = self.active_extension_filter or "(no extension)"
        has_extension = extension.startswith(".")
        display_extension = extension.upper() if has_extension else extension
        value_label = (
            f"{display_extension} files"
            if has_extension
            else "Files with no extension"
        )
        meta_label = (
            f"Showing {display_extension} files in Files view."
            if has_extension
            else "Showing files without an extension in Files view."
        )
        self.btn_file_types.setChecked(is_active)
        self.btn_file_types.setText(
            f"Extension: {display_extension}" if is_active else "File Extensions"
        )
        self.btn_file_types.setToolTip(
            f"File type filter active: {extension}. Click to choose another type."
            if is_active
            else "Show disk usage by file extension"
        )

        if hasattr(self, 'type_filter_banner'):
            self.lbl_active_type_filter.setText(value_label)
            self.lbl_active_type_filter_meta.setText(meta_label)
            self.type_filter_banner.setVisible(is_active)

    def _clear_type_filter(self):
        if self.active_extension_filter is None:
            return
        self._on_filter_changed(clear_extension=True)

    def _toggle_expand(self, checked):
        if (
            self.fp.rb_all.isChecked()
            and self.fp.get_view_mode() in ("Tree", "Folders")
        ):
            self._set_expand_state(False)
            self._update_expand_control_visibility()
            return
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
            options.get('folder_scope'),
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

    def _toggle_select_all(self):
        has_selection = bool(
            self.bulk_delete_scope
            or self.selected_paths
            or self.page_only_selected_paths
        )
        if has_selection:
            self._unselect_all()
        else:
            self._select_all()

    def _toggle_current_page_selection(self):
        if not self.tree_model:
            return

        mode = self._display_mode()
        status = mode if mode in ("Inactive", "Empty") else None
        videos_only = mode == "Videos"
        indices = self._collect_bulk_target_indices(
            status=status,
            videos_only=videos_only,
        )
        if not indices:
            self._refresh_selection_buttons()
            return

        if self._are_all_indices_checked(indices):
            self._clear_current_page_checks()
            return

        requested_scope = "current"
        if getattr(self, "bulk_delete_scope", None):
            self._set_bulk_scope_page_excluded(indices, False)
            requested_scope = "bulk_current"
        self._preserve_results_focus = True
        self._begin_chunked_bulk_selection(
            indices,
            Qt.CheckState.Checked,
            current_page_count=len(indices),
            label="current page",
            offer_all_pages=False,
            status=status,
            videos_only=videos_only,
            requested_scope=requested_scope,
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
            self._set_indices_check_state_direct_suppressed(
                indices,
                Qt.CheckState.Unchecked,
                explicit=False,
            )
        self._do_recount()

    def _clear_current_page_checks(self):
        self.cached_selected_total = None
        if not self.tree_model:
            self._do_recount()
            return

        if self.bulk_delete_scope:
            mode = self._display_mode()
            all_targets, inactive_targets, empty_targets = self._collect_selection_button_targets(mode)
            indices = (
                inactive_targets
                if mode == "Inactive"
                else empty_targets
                if mode == "Empty"
                else all_targets
            )
            self._set_bulk_scope_page_excluded(indices, True)
        else:
            indices = self._collect_checked_source_indices()
        if indices:
            self._set_indices_check_state_direct_suppressed(
                indices,
                Qt.CheckState.Unchecked,
                explicit=False,
            )
        visible_keys = {
            self._path_key(self.tree_model.data(idx, Qt.ItemDataRole.UserRole).get('path', ''))
            for idx in indices
            if self.tree_model.data(idx, Qt.ItemDataRole.UserRole)
        }
        self.selected_paths = IndexedPathDict({
            key: path for key, path in self.selected_paths.items()
            if self._path_key(path) not in visible_keys
        })
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
        self._update_delete_button_copy()
        self._set_delete_armed(False)
        self.btn_delete.setEnabled(False)

    def _on_filter_changed(self, *_args, clear_extension=True):
        # User manually changed a filter control - clear selections and apply
        if clear_extension:
            self.active_extension_filter = None
            self._update_type_filter_ui()
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

    def _on_age_filter_apply_clicked(self):
        self.fp.age_input.interpretText()
        self.fp.applied_age_value = self.fp.age_input.value()
        self.fp._update_age_apply_state()
        self.fp._update_popover_summaries()
        self._on_filter_changed()

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
            if (
                not index.isValid()
                or self.tree_model.data(index, Qt.ItemDataRole.CheckStateRole)
                != Qt.CheckState.Checked
            ):
                return False
        return True

    def _path_key(self, path):
        return _path_key(path)

    def _has_selected_ancestor(self, path, include_self=True):
        return self.selected_paths.path_index.has_ancestor(
            self._path_key(path),
            include_self=include_self,
        )

    def _has_any_selected_ancestor(self, path, include_self=True):
        return self._has_selected_ancestor(path, include_self=include_self)

    def _is_descendant_of_selected_path(self, path):
        return self._has_any_selected_ancestor(path, include_self=False)

    def _has_excluded_ancestor(self, path, include_self=True):
        return self.excluded_paths.path_index.has_ancestor(
            self._path_key(path),
            include_self=include_self,
        )

    def _has_excluded_descendant(self, path):
        return self.excluded_paths.path_index.has_descendant(self._path_key(path))

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

        started_at = time.perf_counter() if PERF_DEBUG else None
        visited = 0
        bulk_scope = self.bulk_delete_scope
        bulk_status = bulk_scope.get('status') if bulk_scope else None
        bulk_videos_only = bool(bulk_scope and bulk_scope.get('videos_only', False))
        bulk_exact_only = bool(
            bulk_scope
            and (
                self.proxy_model.has_active_filters()
                or bulk_status is not None
                or bulk_videos_only
            )
        )
        bulk_checked_indices = []
        bulk_unchecked_indices = []
        stack = [QModelIndex()]
        while stack:
            parent = stack.pop()
            for row in range(self.tree_model.rowCount(parent)):
                index = self.tree_model.index(row, 0, parent)
                visited += 1
                item_data = self.tree_model.data(index, Qt.ItemDataRole.UserRole)
                if item_data and item_data.get('path') and self._is_persistable_selection_index(index):
                    key = self._path_key(item_data['path'])
                    state = self.tree_model.data(index, Qt.ItemDataRole.CheckStateRole)
                    is_page_result = item_data.get(
                        '_is_page_result',
                        not item_data.get('_is_context_fetched', False),
                    )
                    is_bulk_target = bool(
                        bulk_scope
                        and is_page_result
                        and (bulk_status is None or item_data.get('status') == bulk_status)
                        and (not bulk_videos_only or self._is_video_item(item_data))
                        and (
                            not bulk_exact_only
                            or self.proxy_model.matches_source_index(index)
                        )
                    )
                    if state == Qt.CheckState.Checked:
                        self.excluded_paths.remove_descendants(key, include_self=True)
                        if not self._is_descendant_of_selected_path(item_data['path']):
                            self.selected_paths[key] = item_data['path']
                        if is_bulk_target:
                            bulk_checked_indices.append(index)
                    elif state == Qt.CheckState.Unchecked:
                        if self._has_any_selected_ancestor(item_data['path'], include_self=False):
                            self.excluded_paths[key] = item_data['path']
                        self.selected_paths.pop(key, None)
                        if is_bulk_target:
                            bulk_unchecked_indices.append(index)
                if self.tree_model.hasChildren(index):
                    stack.append(index)
        if bulk_checked_indices:
            self._set_bulk_scope_page_excluded(bulk_checked_indices, False)
        if bulk_unchecked_indices:
            self._set_bulk_scope_page_excluded(bulk_unchecked_indices, True)
        _perf_log(
            "selection model sync",
            started_at,
            nodes=visited,
            selected=len(self.selected_paths),
            excluded=len(self.excluded_paths),
        )

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
            previous_suppressed = self._selection_sync_suppressed
            self._selection_sync_suppressed = True
            try:
                if recursive:
                    self.tree_model.set_indices_check_state(to_change, state, explicit=True)
                else:
                    self.tree_model.set_indices_check_state_direct(to_change, state, explicit=True)
            finally:
                self._selection_sync_suppressed = previous_suppressed
            self._sync_persistent_selection_from_model()

    def _set_indices_check_state_direct_suppressed(self, indices, state, explicit=True):
        previous_suppressed = self._selection_sync_suppressed
        self._selection_sync_suppressed = True
        try:
            self.tree_model.set_indices_check_state_direct(indices, state, explicit=explicit)
        finally:
            self._selection_sync_suppressed = previous_suppressed

    def _build_bulk_where(self, status=None, videos_only=False):
        where_clauses = []
        params = []
        age_secs = self.fp.get_older_than_secs()
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

        if self.folder_browser_scope:
            scope_sql, scope_params = descendant_scope_sql(self.folder_browser_scope)
            where_clauses.append(f"({scope_sql})")
            params.extend(scope_params)

        scan_root = getattr(self, 'current_scan_root', None) or os.path.normpath(self.txt_path.text().strip() or "")
        if scan_root:
            root_variants = equivalent_path_variants(scan_root)
            placeholders = ",".join("?" * len(root_variants))
            where_clauses.append(
                f"path COLLATE NOCASE NOT IN ({placeholders})"
            )
            params.extend(root_variants)

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

        folder_clause = EMPTY_FOLDER_SQL
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
            'excluded_paths': [],
            '_base_total': effective_total,
            '_base_folders': effective_folders,
            '_base_files': effective_files,
            '_base_size': effective_size,
            '_base_matched_total': total,
            '_excluded_items': {},
        }

    def _set_bulk_scope_page_excluded(self, indices, excluded):
        scope = self.bulk_delete_scope
        if not scope:
            return

        excluded_items = scope.setdefault('_excluded_items', {})
        folder_delete_mode = scope.get('folder_delete_mode', 'empty_only')
        for index in indices:
            item_data = self.tree_model.data(index, Qt.ItemDataRole.UserRole) or {}
            path = item_data.get('path')
            if not path:
                continue
            key = self._path_key(path)
            if excluded:
                is_folder = bool(item_data.get('is_dir', False))
                deletable = (
                    not is_folder
                    or folder_delete_mode == 'all'
                    or item_data.get('status') == 'Empty'
                )
                excluded_items[key] = {
                    'path': path,
                    'is_folder': is_folder,
                    'size': int(item_data.get('size', 0) or 0) if not is_folder else 0,
                    'deletable': deletable,
                }
            else:
                excluded_items.pop(key, None)

        scope['excluded_paths'] = [item['path'] for item in excluded_items.values()]
        excluded_deletable = [
            item for item in excluded_items.values() if item.get('deletable', True)
        ]
        scope['total'] = max(
            0,
            scope.get('_base_total', scope.get('total', 0)) - len(excluded_deletable),
        )
        scope['folders'] = max(
            0,
            scope.get('_base_folders', scope.get('folders', 0))
            - sum(1 for item in excluded_deletable if item.get('is_folder')),
        )
        scope['files'] = max(
            0,
            scope.get('_base_files', scope.get('files', 0))
            - sum(1 for item in excluded_deletable if not item.get('is_folder')),
        )
        scope['size'] = max(
            0,
            scope.get('_base_size', scope.get('size', 0))
            - sum(item.get('size', 0) for item in excluded_deletable),
        )
        scope['matched_total'] = max(
            0,
            scope.get('_base_matched_total', scope.get('matched_total', 0))
            - len(excluded_items),
        )

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
        bulk_has_selection = bool(
            self.bulk_delete_scope
            and self.bulk_delete_scope.get('total', 1) > 0
        )
        has_selection = bool(
            bulk_has_selection
            or self.selected_paths
            or self.page_only_selected_paths
        )
        all_targets, inactive_targets, empty_targets = self._collect_selection_button_targets(mode)

        focused_selection_mode = mode in ('Inactive', 'Empty')
        self.btn_select_all.setVisible(not focused_selection_mode)
        self.btn_clear_selection.setVisible(False)
        self.btn_clear_selection.setEnabled(False)

        if has_selection and mode == 'Videos':
            self.btn_select_all.setText("Unselect All Videos")
        elif has_selection:
            self.btn_select_all.setText("Unselect All")
        elif mode == 'Videos':
            self.btn_select_all.setText("Select All Videos")
        else:
            self.btn_select_all.setText("Select All")

        inactive_checked = self._are_all_indices_checked(inactive_targets)
        empty_checked = self._are_all_indices_checked(empty_targets)
        inactive_has_selection = has_selection if mode == 'Inactive' else inactive_checked
        empty_has_selection = has_selection if mode == 'Empty' else empty_checked
        self.btn_select_inactive.setText(
            "Unselect All Inactive"
            if inactive_has_selection
            else "Select All Inactive"
        )
        self.btn_select_empty.setText(
            "Unselect All Empty" if empty_has_selection else "Select All Empty"
        )

        self.btn_select_all.setEnabled(
            mode in ('All', 'Videos')
            and (has_selection or bool(all_targets))
        )
        self.btn_select_inactive.setEnabled(
            mode == 'Inactive' and (inactive_has_selection or bool(inactive_targets))
        )
        empty_enabled = mode in ('All', 'Empty') and (
            empty_has_selection or bool(empty_targets)
        )
        self.btn_select_empty.setEnabled(empty_enabled)
        self.btn_select_empty.setToolTip(
            "Select every empty folder in the current view."
            if empty_enabled
            else "No empty folders are available in the current view."
        )
        self.btn_select_inactive.setVisible(mode == 'Inactive')
        self.btn_select_empty.setVisible(mode == 'Empty')
        page_checked = False
        if hasattr(self, "btn_current_page_selection"):
            page_targets = (
                inactive_targets
                if mode == "Inactive"
                else empty_targets
                if mode == "Empty"
                else all_targets
            )
            page_checked = self._are_all_indices_checked(page_targets)
            self.btn_current_page_selection.setText(
                "Unselect Current Page" if page_checked else "Select Current Page"
            )
            self.btn_current_page_selection.setToolTip(
                "Unselect every item shown on this page"
                if page_checked
                else "Select every item shown on this page"
            )
            self.btn_current_page_selection.setEnabled(bool(page_targets))

        self._set_selection_control_active(self.btn_select_all, has_selection)
        self._set_selection_control_active(
            self.btn_select_inactive,
            inactive_has_selection,
        )
        self._set_selection_control_active(self.btn_select_empty, empty_has_selection)
        if hasattr(self, "btn_current_page_selection"):
            self._set_selection_control_active(
                self.btn_current_page_selection,
                page_checked,
            )

    def _set_selection_control_active(self, button, active):
        active = bool(active)
        if button.property("selectionActive") == active:
            return
        button.setProperty("selectionActive", active)
        button.style().unpolish(button)
        button.style().polish(button)
        button.update()

    def _collect_selection_button_targets(self, mode):
        if not self.tree_model or self.proxy_model.sourceModel() is None:
            return [], [], []

        cached = self._selection_button_targets_cache
        if (
            cached
            and cached.get('model') is self.tree_model
            and cached.get('mode') == mode
        ):
            return cached['targets']

        all_targets = []
        inactive_targets = []
        empty_targets = []
        started_at = time.perf_counter() if PERF_DEBUG else None
        visited = 0
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
            nonlocal visited
            for row in range(self.proxy_model.rowCount(parent)):
                proxy_index = self.proxy_model.index(row, 0, parent)
                source_index = self.proxy_model.mapToSource(proxy_index)
                visited += 1
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
        targets = (all_targets, inactive_targets, empty_targets)
        self._selection_button_targets_cache = {
            'model': self.tree_model,
            'mode': mode,
            'targets': targets,
        }
        _perf_log(
            "selection button target walk",
            started_at,
            nodes=visited,
            targets=sum(len(group) for group in targets),
        )
        return targets

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
        return prune_contained_paths(paths, sort_alpha=True)

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
                if (
                    root_key not in self.excluded_paths
                    and not self.excluded_paths.path_index.has_descendant(root_key)
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
        deleted_path_index = PathKeyIndex(deleted_keys)

        def is_deleted(path):
            return deleted_path_index.has_ancestor(self._path_key(path))

        self.selected_paths = IndexedPathDict({
            key: path for key, path in self.selected_paths.items()
            if not is_deleted(path)
        })
        self.page_only_selected_paths = {
            key: path for key, path in self.page_only_selected_paths.items()
            if not is_deleted(path)
        }
        self.excluded_paths = IndexedPathDict({
            key: path for key, path in self.excluded_paths.items()
            if not is_deleted(path)
        })

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
        total_size = 0
        if pruned_paths:
            from src.file_index_tool import FileIndexTool

            tool = FileIndexTool()
            try:
                cursor = tool.conn.cursor()
                _, folder_count, file_count, total_size = summarize_paths_batch(cursor, pruned_paths)
            finally:
                tool.close()
        return {
            'paths': pruned_paths,
            'total': len(pruned_paths),
            'folders': folder_count,
            'files': file_count,
            'size': total_size,
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
        selecting = not button.text().startswith(("Unselect", "Deselect"))
        if not selecting:
            self._clear_all_checks()
            self._focus_results_view()
            return
        scope_status = self.bulk_delete_scope.get('status') if self.bulk_delete_scope else None
        if scope_status == status:
            indices = self._collect_bulk_target_indices(status=status)
            self._set_bulk_scope_page_excluded(indices, False)
            self._begin_chunked_bulk_selection(
                indices,
                Qt.CheckState.Checked,
                current_page_count=len(indices),
                label=f"{status.lower()} items",
                offer_all_pages=False,
                status=status,
                videos_only=False,
                requested_scope="bulk_current",
            )
            return
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
        self.current_filesystem_scope_fallback = False
        self.refresh_tree_state_key = None
        self.refresh_collapsed_tree_paths = set()
        self.folder_cache = None
        self.current_scan_root = None
        self.active_extension_filter = None
        self.applied_scan_exclusions = None
        self.scan_exclusions_in_progress = None
        self._update_type_filter_ui()
        self._reset_folder_browser(None)

        # Reset pagination state
        self.current_page = 0
        self.current_total_matches = 0
        self.is_scanning  = False
        self.has_completed_scan = False
        self.hide_partial_scan_results = False
        self.cached_folder_total = None
        self.cached_folder_total_root = None
        self.totals_request_id += 1

        # Reset status chips
        self.chip_empty.setText("Empty: 0 items")
        self.chip_inactive_folders.setText("Inactive: 0 folders · 0 files")
        self._set_total_summary_chip("--")
        self._set_chip_text(self.chip_page_size, "Current Page Size: --")
        self._set_selected_summary_chip("0 B", 0, 0)
        self._update_status_metrics_visibility()

        # Hide controls, show empty page
        self.controls_bar.setVisible(False)
        self.content_stack.setCurrentIndex(0)
        self.lbl_status.setText("Ready - select a folder and click Re-scan")
        self._update_file_types_enabled()
        self._update_exclusions_indicator()

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
        
        # Never publish partial rows during either an initial scan or a re-scan.
        # The completed index is exposed once from _on_scan_done().
        self.hide_partial_scan_results = True

        self.current_page = 0
        self.total_scanned = 0
        self.last_scan_excluded_count = 0
        self.last_scan_elapsed_secs = 0.0
        self.last_scan_item_count = 0
        self.scan_progress_was_determinate = False
        self.is_scanning = True
        self.active_extension_filter = None
        self._update_type_filter_ui()
        self.current_scan_root = os.path.normcase(os.path.normpath(path))
        self.cached_folder_total = None
        self.cached_folder_total_root = self.current_scan_root
        self.totals_request_id += 1
        self._cancel_running_totals_thread()

        self.lbl_status.setText("Scanning...")
        self._set_rescan_stop_ui()
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
        scan_exclusions = self.fp.get_scan_exclusions()
        self.scan_exclusions_in_progress = scan_exclusions.to_dict()
        self._update_exclusions_indicator()
        history = get_scan_history(self.current_scan_root)
        estimated_total_items = None
        if history:
            estimated_total_items = history.get("item_count") or None
        self.scanner_thread = ScannerThread(
            path,
            stale_months=self.fp.get_stale_months_for_scan(),
            exclusions=scan_exclusions,
            estimated_total_items=estimated_total_items,
        )
        self.folder_cache = self.scanner_thread.cache
        self.folder_browser_live_refresh_at = 0.0
        self.folder_browser.setEnabled(not self.hide_partial_scan_results)
        self._reset_folder_browser(
            None if self.hide_partial_scan_results else self.current_scan_root
        )
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
        if not getattr(self, 'hide_partial_scan_results', False):
            self._refresh_folder_browser_from_cache()

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
        if getattr(self, 'hide_partial_scan_results', False):
            return
        self._refresh_folder_browser_from_cache(force=True)
        options = self._page_load_options()
        if options.get('defer_tree_load_until_scan_done') and not self._can_live_load_from_cache(options):
            return
        if self.current_page == 0 and self.content_stack.currentIndex() in (0, 3):
            self._load_page()

    def _on_batch_ready(self):
        """Called during scanning when a new batch of items is indexed."""
        if getattr(self, 'hide_partial_scan_results', False):
            return
        self._refresh_folder_browser_from_cache(force=True)
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
        root_path = options.get('folder_scope') or options.get('scan_root')
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
            age_secs = self.fp.get_older_than_secs()
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

            if self.folder_browser_scope:
                scope_sql, scope_params = descendant_like_sql(self.folder_browser_scope)
                where_clauses.append(f"({scope_sql})")
                params.extend(scope_params)

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
        was_hiding_partial_results = getattr(
            self,
            'hide_partial_scan_results',
            False,
        )
        if finished_thread:
            self.last_scan_elapsed_secs = getattr(finished_thread, "scan_elapsed_secs", 0.0) or 0.0
            self.last_scan_item_count = (
                getattr(finished_thread, "scan_indexed_count", 0)
                or getattr(finished_thread, "scan_scanned_count", 0)
                or 0
        )
        self.btn_rescan.setEnabled(True)
        MainWindow._set_rescan_idle_ui(self)
        self.is_scanning = False
        scan_cancelled = bool(finished_thread and finished_thread.is_cancelled)
        if scan_cancelled:
            self.applied_scan_exclusions = None
        else:
            self.applied_scan_exclusions = getattr(
                self,
                'scan_exclusions_in_progress',
                None,
            )
        self.scan_exclusions_in_progress = None
        update_exclusions_indicator = getattr(
            self,
            '_update_exclusions_indicator',
            None,
        )
        if callable(update_exclusions_indicator):
            update_exclusions_indicator()
        self.hide_partial_scan_results = False
        if not scan_cancelled:
            self.has_completed_scan = True
        if hasattr(self, 'folder_browser'):
            self.folder_browser.setEnabled(True)
            if was_hiding_partial_results:
                self._reset_folder_browser(
                    None if scan_cancelled else self.current_scan_root
                )
            if not scan_cancelled:
                self._refresh_folder_browser_from_cache(force=True)
                if self.current_scan_root:
                    self._load_folder_browser_children(self.current_scan_root)
        self._update_file_types_enabled()
        
        if scan_cancelled:
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
        if scan_cancelled and was_hiding_partial_results:
            self.tree_model = None
            self.proxy_model.setSourceModel(None)
            self.controls_bar.setVisible(False)
            self.content_stack.setCurrentIndex(0)
            self._set_total_summary_chip("--")
            self._set_selected_summary_chip("0 B", 0, 0)
            return
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
            cached_scope_lookup = getattr(
                self,
                '_cached_scoped_folder_total',
                None,
            )
            completed_total = (
                cached_scope_lookup(self.current_scan_root)
                if callable(cached_scope_lookup)
                else None
            )
            if completed_total is None:
                completed_total = (
                    getattr(self.folder_cache, "running_total_size", 0) or 0
                )
            self.cached_folder_total = completed_total
            self.cached_folder_total_root = _path_key(self.current_scan_root)
            self._set_total_summary_chip(completed_total)
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
        folder_scope = self.folder_browser_scope

        if hasattr(self.fp, 'rb_all') and self.fp.rb_all.isChecked():
            status_filter = None
        elif self.fp.rb_empty.isChecked():
            status_filter = 'Empty'
        elif self.fp.rb_videos.isChecked():
            status_filter = None
        else:
            status_filter = 'Inactive'

        age_secs = self.fp.get_older_than_secs()
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
            'folder_scope': folder_scope,
            'sort_column': self.sort_column,
            'sort_desc': self.sort_order == Qt.SortOrder.DescendingOrder,
            'scan_root': getattr(self, 'current_scan_root', os.path.normpath(self.txt_path.text().strip() or "")),
            'folder_cache': self.folder_cache,
            'scan_exclusions': getattr(
                self.fp,
                'get_scan_exclusions',
                lambda: ScanExclusions(),
            )(),
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

    def _rebuild_and_restore_page_selection(self):
        self.source_index_by_path = {}
        self._selection_button_targets_cache = None
        if not self.tree_model:
            return

        started_at = time.perf_counter() if PERF_DEBUG else None
        restore_by_key = {}
        all_targets = []
        inactive_targets = []
        empty_targets = []
        seen_all = set()
        seen_inactive = set()
        seen_empty = set()
        mode = self._display_mode()
        exact_all = self.proxy_model.has_active_filters() or mode == 'Videos'
        bulk_scope = self.bulk_delete_scope
        has_persistent_selection = bool(self.selected_paths)
        bulk_status = bulk_scope.get('status') if bulk_scope else None
        bulk_videos_only = bool(bulk_scope and bulk_scope.get('videos_only', False))
        bulk_excluded_keys = bulk_scope_excluded_keys(bulk_scope)
        bulk_exact_only = bool(
            bulk_scope
            and (
                self.proxy_model.has_active_filters()
                or bulk_status is not None
                or bulk_videos_only
            )
        )
        visited = 0

        def add_target(targets, seen, index, path):
            if path in seen:
                return
            seen.add(path)
            targets.append(index)

        stack = [QModelIndex()]
        while stack:
            parent = stack.pop()
            for row in range(self.tree_model.rowCount(parent)):
                index = self.tree_model.index(row, 0, parent)
                visited += 1
                item_data = self.tree_model.data(index, Qt.ItemDataRole.UserRole)
                if item_data and item_data.get('path'):
                    path = item_data['path']
                    path_key = self._path_key(path)
                    self.source_index_by_path[path_key] = index
                    persistable = (
                        self._is_persistable_selection_index(index)
                        if has_persistent_selection or bulk_scope else False
                    )
                    if (
                        has_persistent_selection
                        and persistable
                        and self._is_effectively_selected(path)
                    ):
                        restore_by_key[path_key] = index

                    proxy_index = self.proxy_model.mapFromSource(index)
                    if proxy_index.isValid():
                        is_exact_match = self.proxy_model.matches_source_index(index)
                        is_page_result = item_data.get(
                            '_is_page_result',
                            not item_data.get('_is_context_fetched', False),
                        )
                        is_video = self._is_video_item(item_data)
                        if is_page_result:
                            if (
                                (not exact_all or is_exact_match)
                                and (mode != 'Videos' or is_video)
                            ):
                                add_target(all_targets, seen_all, index, path)
                            if item_data.get('status') == 'Inactive' and is_exact_match:
                                add_target(inactive_targets, seen_inactive, index, path)
                            if item_data.get('status') == 'Empty' and is_exact_match:
                                add_target(empty_targets, seen_empty, index, path)

                            if (
                                bulk_scope
                                and persistable
                                and path_key not in bulk_excluded_keys
                                and (bulk_status is None or item_data.get('status') == bulk_status)
                                and (not bulk_videos_only or is_video)
                                and (not bulk_exact_only or is_exact_match)
                            ):
                                restore_by_key[path_key] = index
                if self.tree_model.hasChildren(index):
                    stack.append(index)

        targets = (all_targets, inactive_targets, empty_targets)
        self._selection_button_targets_cache = {
            'model': self.tree_model,
            'mode': mode,
            'targets': targets,
        }
        restore_indices = list(restore_by_key.values())
        if restore_indices:
            self._set_indices_check_state_direct_suppressed(
                restore_indices,
                Qt.CheckState.Checked,
                explicit=True,
            )
        _perf_log(
            "page selection preparation",
            started_at,
            nodes=visited,
            restored=len(restore_indices),
            button_targets=sum(len(group) for group in targets),
        )

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

        self.recount_timer.stop()
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
            elif self.bulk_select_requested_scope == 'bulk_current':
                pass
            else:
                self.bulk_delete_scope = None
        else:
            self.bulk_delete_scope = None
        self._set_bulk_selection_busy(False)
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
        self._do_recount()
        QMessageBox.critical(self, "Selection Error", error)

    def _build_totals_refresh_options(self):
        age_secs = self.fp.get_older_than_secs()
        age_cutoff = (datetime.now().timestamp() - age_secs) if age_secs is not None else None
        root_path = getattr(self, 'current_scan_root', None) or os.path.normpath(self.txt_path.text().strip() or "")
        root_key = os.path.normcase(os.path.normpath(root_path)) if root_path else None
        cached_folder_total = self.cached_folder_total if root_key and root_key == self.cached_folder_total_root else None
        cached_scoped_folder_total = self._cached_scoped_folder_total()

        filtered_where_sql = None
        filtered_where_params = ()
        filtered_size_label = None
        if self.applied_name_filter or self.active_extension_filter is not None:
            filtered_where_sql, filtered_where_params = self._build_bulk_where()
            if self.active_extension_filter is not None:
                extension = self.active_extension_filter or "(no extension)"
                filtered_size_label = f"Type {extension} Size"
            else:
                filtered_size_label = "Search Size"

        return {
            'age_cutoff': age_cutoff,
            'is_scanning': self.is_scanning,
            'root_path': root_path,
            'folder_scope': self.folder_browser_scope,
            'cached_folder_total': cached_folder_total,
            'cached_scoped_folder_total': cached_scoped_folder_total,
            'current_page_file_paths': self._current_page_file_paths(),
            'selected_paths': self._selected_roots_for_delete(),
            'filtered_where_sql': filtered_where_sql,
            'filtered_where_params': filtered_where_params,
            'filtered_size_label': filtered_size_label,
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
                self._set_chip_text(self.chip_page_size, "Current Page Size: 0 B")
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
            self.btn_current_page_selection,
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
            self.page_load_thread.cancel()

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
        self.current_filesystem_scope_fallback = bool(
            result.get('filesystem_scope_fallback', False)
        )

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
                self._set_empty_result_metrics()
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
        self._rebuild_and_restore_page_selection()

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
        self._do_recount()
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

        self._set_chip_text(self.chip_empty, f"Empty: {result['empty_n']:,} items")
        self._set_chip_text(
            self.chip_inactive_folders,
            f"Inactive: {result['inactive_folders']:,} folders · "
            f"{result['inactive_files']:,} files",
        )
        if self.is_scanning:
            self._set_scanning_total_chip()
            return
        scoped_folder_total = result.get('scoped_folder_total')
        if self.folder_browser_scope and scoped_folder_total is not None:
            self._set_chip_text(
                self.chip_folder_size,
                f"Folder Size {self._format_chip_size(scoped_folder_total)}",
            )
            self.chip_folder_size.setVisible(True)
        else:
            self.chip_folder_size.setVisible(False)
        filtered_total = result.get('filtered_total')
        filtered_size_label = result.get('filtered_size_label')
        if filtered_size_label and filtered_total is not None:
            self._set_chip_text(
                self.chip_filtered_size,
                f"{filtered_size_label} {self._format_chip_size(filtered_total)}",
            )
            self.chip_filtered_size.setVisible(True)
        else:
            self.chip_filtered_size.setVisible(False)
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
            self._set_chip_text(self.chip_page_size, f"Current Page Size: {format_size(size)}")
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
            self._set_chip_text(self.chip_page_size, "Current Page Size: --")
            return

        self.cached_selected_total = None
        self._set_selected_summary_chip("0 B", 0, 0)

    def _on_totals_failed(self, request_id, error):
        if request_id != self.totals_request_id:
            return

        if self.is_scanning:
            self._set_scanning_total_chip()
        else:
            known_total = self._known_browse_total()
            self._set_total_summary_chip(
                "--" if known_total is None else known_total
            )
            if self.folder_browser_scope:
                cached_scoped_total = self._cached_scoped_folder_total()
                if cached_scoped_total is not None:
                    self._set_chip_text(
                        self.chip_folder_size,
                        "Folder Size "
                        f"{self._format_chip_size(cached_scoped_total)}",
                    )
                else:
                    self._set_chip_text(
                        self.chip_folder_size,
                        "Folder Size unavailable",
                    )
                self.chip_folder_size.setVisible(True)

    # -------------------------------------------------------------------------
    # Selection count (debounced)
    # -------------------------------------------------------------------------

    def _on_checked(self, tl=None, br=None, roles=None):
        if roles is None or Qt.ItemDataRole.CheckStateRole in roles:
            if self.bulk_select_active or self._selection_sync_suppressed:
                return
            self._sync_persistent_selection_from_model()
            self._set_size_totals_pending(selected=True)
            self.recount_timer.start(80)

    def _do_recount(self):
        started_at = time.perf_counter() if PERF_DEBUG else None
        if not self.tree_model:
            self._update_delete_button_copy()
            self._set_delete_armed(False)
            self.btn_delete.setEnabled(False)
            self._refresh_selection_buttons()
            _perf_log("selection recount", started_at, roots=0)
            return
        if self.bulk_delete_scope:
            scope = self.bulk_delete_scope
            armed = scope['total'] > 0
            self._update_delete_button_copy(scope['total'], all_pages=True)
            self._set_delete_armed(armed)
            self.btn_delete.setEnabled(armed)
            self._refresh_selection_buttons()
            self._update_chips_sql()
            _perf_log("selection recount", started_at, roots=scope['total'])
            return
        selected_paths = self._selected_roots_for_delete()
        total = len(selected_paths)
        self._update_delete_button_copy(total)
        armed = total > 0
        self._set_delete_armed(armed)
        self.btn_delete.setEnabled(armed)
        self._set_size_totals_pending(selected=True)
        self._refresh_selection_buttons()
        self._update_chips_sql()
        _perf_log("selection recount", started_at, roots=total)

    def _set_delete_armed(self, armed):
        self.btn_delete.setProperty("armed", bool(armed))
        icon_path = self._delete_icon_active_path if armed else self._delete_icon_path
        self.btn_delete.setIcon(QIcon(icon_path))
        self._update_delete_button_layout()
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
        QTimer.singleShot(0, self._update_expand_control_visibility)

    def _on_tree_collapsed(self, proxy_index):
        QTimer.singleShot(0, self._fit_tree_columns_to_viewport)
        QTimer.singleShot(0, self._update_expand_control_visibility)

    def _start_lazy_child_load(self, source_index):
        folder_path = self.tree_model.begin_async_child_load(source_index)
        if not folder_path:
            return

        self.lazy_child_request_id += 1
        request_id = self.lazy_child_request_id
        if (
            self.current_lazy_show_all_tree
            and self.folder_cache is not None
            and self.folder_cache.has_children_for(folder_path)
        ):
            self.lazy_child_threads[request_id] = {
                'thread': None,
                'index': QPersistentModelIndex(source_index),
                'path': folder_path,
                'model': self.tree_model,
            }
            children = self.folder_cache.children_for(
                folder_path,
                self.sort_column,
                self.sort_order == Qt.SortOrder.DescendingOrder,
            )
            self._on_lazy_children_ready(request_id, folder_path, children)
            self.lazy_child_threads.pop(request_id, None)
            return

        thread = LazyChildrenLoadThread(
            request_id,
            folder_path,
            sort_column=self.sort_column,
            sort_desc=self.sort_order == Qt.SortOrder.DescendingOrder,
            options=None if self.current_lazy_show_all_tree else getattr(self.tree_model, 'options', {}),
            cache=self.folder_cache,
            filesystem_fallback=(
                self.current_lazy_show_all_tree
                or self.current_filesystem_scope_fallback
            ),
            scan_exclusions=getattr(
                self.fp,
                'get_scan_exclusions',
                lambda: ScanExclusions(),
            )(),
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
        self._rebuild_and_restore_page_selection()
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
            excluded_keys = bulk_scope_excluded_keys(self.bulk_delete_scope)
            for path, is_folder in rows:
                if self._path_key(path) in excluded_keys:
                    continue
                if not is_folder:
                    paths.append(path)
                elif folder_delete_mode == 'all':
                    paths.append(path)
                elif folder_delete_mode == 'empty_only':
                    if folder_is_physically_empty(cursor, path):
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
        self.btn_folder_browser.setEnabled(enabled)
        self.btn_filter.setEnabled(enabled)
        if hasattr(self, 'btn_file_types'):
            self.btn_file_types.setEnabled(enabled and self._has_completed_scan_context())
        self.controls_bar.setEnabled(enabled)
        self.tree.setEnabled(enabled)
        self.folder_browser.setEnabled(enabled)

    def _refresh_folder_browser_after_delete(self, deleted_paths):
        if not deleted_paths or not self.folder_browser.root_path:
            return
        self.folder_browser_request_id += 1
        self.folder_browser_threads.clear()
        deleted_keys = [_path_key(path) for path in deleted_paths if path]
        deleted_index = PathKeyIndex(deleted_keys)
        if (
            self.folder_browser_scope
            and deleted_index.has_ancestor(_path_key(self.folder_browser_scope))
        ):
            self.folder_browser.select_root()
            self._set_folder_browser_scope(None, reload=False)
        self.folder_browser.remove_paths(deleted_paths)
        parent_paths = [
            os.path.dirname(os.path.normpath(path))
            for path in deleted_paths
            if path
        ]
        self.folder_browser.refresh_paths(parent_paths)

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
            self.delete_progress.update_progress(done, total, path)
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
                updated_total = (
                    getattr(self.folder_cache, 'running_total_size', 0) or 0
                )
                root_path = getattr(self, 'current_scan_root', None)
                self.cached_folder_total = updated_total
                self.cached_folder_total_root = (
                    _path_key(root_path) if root_path else None
                )
                set_total_chip = getattr(
                    self,
                    '_set_total_summary_chip',
                    None,
                )
                if callable(set_total_chip):
                    set_total_chip(updated_total)
                update_scoped_size = getattr(
                    self,
                    '_update_scoped_folder_size_from_cache',
                    None,
                )
                if callable(update_scoped_size):
                    update_scoped_size()
            self._remove_deleted_paths_from_selection(deleted_paths)
            if hasattr(self, '_refresh_folder_browser_after_delete'):
                self._refresh_folder_browser_after_delete(deleted_paths)
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

    def _current_page_export_rows(self):
        rows = []
        seen = set()

        def collect(parent):
            for row in range(self.proxy_model.rowCount(parent)):
                index = self.proxy_model.index(row, 0, parent)
                data = self.proxy_model.data(index, Qt.ItemDataRole.UserRole)
                if isinstance(data, dict) and data.get("path"):
                    path = data["path"]
                    key = _path_key(path)
                    if key not in seen:
                        seen.add(key)
                        is_folder = bool(data.get("is_dir", False))
                        status = data.get("status", "")
                        rows.append((
                            path,
                            data.get("name") or os.path.basename(path),
                            int(is_folder),
                            int(data.get("size", 0) or 0),
                            float(data.get("last_modified", 0) or 0),
                            data.get("location") or os.path.dirname(path),
                            "" if is_folder else os.path.splitext(path)[1].lower(),
                            int(status == "Empty"),
                        ))
                if self.proxy_model.hasChildren(index):
                    collect(index)

        collect(QModelIndex())
        return rows

    def _export_metadata(self, export_type, scope):
        exclusions = self.fp.get_scan_exclusions().to_dict()
        age_months = getattr(self.fp, "applied_age_value", 0)
        display_mode = "Show all"
        if self.fp.rb_inactive.isChecked():
            display_mode = "Inactive"
        elif self.fp.rb_empty.isChecked():
            display_mode = "Empty"
        elif self.fp.rb_videos.isChecked():
            display_mode = "Videos"
        return {
            "Exported at": datetime.now().isoformat(timespec="seconds"),
            "Scan root": getattr(self, "current_scan_root", "") or "",
            "Export type": export_type,
            "Scope": scope,
            "Display mode": display_mode,
            "View mode": self.fp.get_view_mode(),
            "Age threshold": "Off" if not age_months else f"{age_months} months",
            "Search": self.applied_name_filter or "(none)",
            "Extension filter": self.active_extension_filter or "(none)",
            "Folder scope": self.folder_browser_scope or "(root)",
            "Excluded folders": ", ".join(exclusions["folder_names"]) or "(none)",
            "Excluded extensions": ", ".join(exclusions["extensions"]) or "(none)",
            "Minimum file size": exclusions["min_file_size_bytes"],
        }

    def _default_export_filename(self, export_type, scope):
        labels = {
            "listing": scope,
            "file_types": "file_types",
            "folder_summary": "folder_summary",
            "delete_audit": "delete_audit",
            "scan_history": "scan_history",
        }
        suffix = labels.get(export_type, "report")
        return f"watchdog_export_{suffix}_{datetime.now():%Y-%m-%d}.csv"

    def _close_export_progress(self):
        if self.export_progress:
            self.export_progress._allow_close = True
            self.export_progress.hide()
            self.export_progress.close()
            self.export_progress = None
        self.btn_export.setEnabled(True)

    def _on_export_finished(self, row_count, file_size):
        target_path = self.export_target_path
        self._close_export_progress()
        self.export_target_path = None
        self.lbl_status.setText(
            f"Export complete. {row_count:,} rows written ({format_size(file_size)})."
        )
        message = QMessageBox(self)
        message.setIcon(QMessageBox.Icon.Information)
        message.setWindowTitle("Export complete")
        message.setText(f"Exported {row_count:,} rows ({format_size(file_size)}).")
        message.setInformativeText(target_path or "")
        reveal_button = message.addButton("Show in Explorer", QMessageBox.ButtonRole.ActionRole)
        message.addButton(QMessageBox.StandardButton.Ok)
        message.exec()
        if message.clickedButton() is reveal_button and target_path:
            self._open(target_path, is_dir=False)

    def _on_export_failed(self, error):
        self._close_export_progress()
        self.export_target_path = None
        self.lbl_status.setText("Export failed.")
        QMessageBox.critical(self, "Export failed", error)

    def _on_export_cancelled(self):
        self._close_export_progress()
        self.export_target_path = None
        self.lbl_status.setText("Export cancelled.")

    def _on_export_thread_stopped(self):
        thread = self.sender()
        if self.export_thread is thread:
            self.export_thread = None
        if thread is not None:
            thread.deleteLater()

    def _export_csv(self):
        if self.export_thread and self.export_thread.isRunning():
            return
        has_results = bool(self.proxy_model and self.proxy_model.sourceModel())
        has_scan = bool(getattr(self, "current_scan_root", None))
        if not has_results and not has_scan:
            QMessageBox.information(self, "Export", "Nothing to export - run a scan first.")
            return

        selected_paths = self._selected_roots_for_delete() if has_results else []
        dialog = ExportDialog(
            has_selection=bool(selected_paths),
            has_bulk_scope=bool(self.bulk_delete_scope),
            settings=self.settings,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        export_type = dialog.export_type()
        scope = dialog.export_scope()
        last_directory = self.settings.value(
            "last_export_directory",
            os.path.expanduser("~"),
        )
        default_name = self._default_export_filename(export_type, scope)
        initial_path = os.path.join(str(last_directory), default_name)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save CSV Report",
            initial_path,
            "CSV Files (*.csv)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        self.settings.setValue("last_export_directory", os.path.dirname(path))

        where_sql, params = self._build_bulk_where()
        options = self._page_load_options()
        config = {
            "export_type": export_type,
            "scope": scope,
            "columns": dialog.selected_columns(),
            "include_summary": dialog.include_summary.isChecked(),
            "metadata": self._export_metadata(export_type, scope),
            "scan_root": getattr(self, "current_scan_root", None),
            "age_cutoff": options.get("age_cutoff"),
            "where_sql": where_sql,
            "params": params,
            "current_page_rows": (
                self._current_page_export_rows()
                if export_type == "listing" and scope == "current_page"
                else []
            ),
            "selected_paths": selected_paths if scope == "selected" else [],
        }
        if scope == "bulk_scope" and self.bulk_delete_scope:
            config.update({
                "bulk_where_sql": self.bulk_delete_scope.get("where_sql", ""),
                "bulk_params": list(self.bulk_delete_scope.get("params", [])),
                "folder_delete_mode": self.bulk_delete_scope.get(
                    "folder_delete_mode",
                    "empty_only",
                ),
                "excluded_paths": list(
                    self.bulk_delete_scope.get("excluded_paths", [])
                ),
            })

        self.export_target_path = path
        self.export_progress = ExportProgressDialog(self)
        self.export_thread = ExportThread(path, config, parent=self)
        self.export_thread.progress.connect(self.export_progress.update_progress)
        self.export_thread.export_finished.connect(self._on_export_finished)
        self.export_thread.export_failed.connect(self._on_export_failed)
        self.export_thread.export_cancelled.connect(self._on_export_cancelled)
        self.export_thread.finished.connect(self._on_export_thread_stopped)
        self.export_progress.cancel_requested.connect(self.export_thread.cancel)
        self.btn_export.setEnabled(False)
        self.lbl_status.setText("Exporting CSV report...")
        self.export_thread.start()
        self.export_progress.show()
