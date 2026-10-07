import os

from PyQt6.QtCore import (
    QThread,
    pyqtSignal,
)

from ..constants import EMPTY_FOLDER_SQL
from ..sql_utils import case_insensitive_path_sql, descendant_like_sql, descendant_scope_sql, prune_contained_paths


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
