from PyQt6.QtCore import (
    QThread,
    pyqtSignal,
)

from ..constants import EMPTY_FOLDER_SQL
from ..sql_utils import build_sort_order_clause, prune_contained_paths, summarize_paths_batch


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
