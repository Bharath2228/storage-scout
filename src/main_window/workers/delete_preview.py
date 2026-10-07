import os

from PyQt6.QtCore import (
    QThread,
    pyqtSignal,
)

from ..path_utils import PathKeyIndex, _path_key, bulk_scope_excluded_keys
from ..sql_utils import case_insensitive_path_sql, descendant_like_sql, folder_is_physically_empty, prune_contained_paths, summarize_paths_batch


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
