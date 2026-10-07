import os
import sqlite3
import send2trash

from PyQt6.QtCore import (
    QThread,
    pyqtSignal,
)

from ..sql_utils import case_insensitive_path_sql, descendant_like_sql


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
