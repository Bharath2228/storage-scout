import os
import tempfile
import unittest
from unittest import mock

from src.file_index_tool import FileIndexTool
from src.main_window import DeleteThread, EMPTY_FOLDER_SQL


class DeleteEmptyFolderRefreshTests(unittest.TestCase):
    def _prepare_index(self, db_path, root, folder, visible_file, physical_count):
        tool = FileIndexTool(db_path)
        try:
            tool.conn.execute(
                """
                INSERT INTO file_index (
                    root, path, name, parent_path, is_folder, size,
                    modified_time, extension, inactive
                ) VALUES (?, ?, ?, ?, 1, 0, 0, '', 0)
                """,
                (root, folder, os.path.basename(folder), root),
            )
            tool.conn.execute(
                """
                INSERT INTO file_index (
                    root, path, name, parent_path, is_folder, size,
                    modified_time, extension, inactive
                ) VALUES (?, ?, ?, ?, 0, 1, 0, '.txt', 0)
                """,
                (root, visible_file, os.path.basename(visible_file), folder),
            )
            tool.conn.execute(
                """
                INSERT INTO folder_summary (
                    path, total_size, file_count, folder_count,
                    child_count, physical_child_count
                ) VALUES (?, 1, 1, 1, 1, ?)
                """,
                (folder, physical_count),
            )
            tool.conn.commit()
        finally:
            tool.close()

    def _run_delete(self, db_path, path):
        real_tool = FileIndexTool

        def remove_from_disk(target):
            os.remove(target)

        with (
            mock.patch(
                "src.file_index_tool.FileIndexTool",
                side_effect=lambda: real_tool(db_path),
            ),
            mock.patch(
                "src.main_window.send2trash.send2trash",
                side_effect=remove_from_disk,
            ),
        ):
            DeleteThread([path]).run()

    def test_parent_appears_in_empty_filter_after_last_child_is_deleted(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, "folder")
            os.mkdir(folder)
            visible_file = os.path.join(folder, "visible.txt")
            with open(visible_file, "w", encoding="ascii") as stream:
                stream.write("x")
            db_path = os.path.join(root, "index.db")
            self._prepare_index(db_path, root, folder, visible_file, 1)

            self._run_delete(db_path, visible_file)

            tool = FileIndexTool(db_path)
            try:
                summary = tool.conn.execute(
                    "SELECT child_count, physical_child_count "
                    "FROM folder_summary WHERE path = ?",
                    (folder,),
                ).fetchone()
                empty_paths = {
                    row[0]
                    for row in tool.conn.execute(
                        f"SELECT path FROM file_index WHERE {EMPTY_FOLDER_SQL}"
                    )
                }
            finally:
                tool.close()

            self.assertEqual(summary, (0, 0))
            self.assertIn(folder, empty_paths)

    def test_parent_with_excluded_physical_child_does_not_appear_empty(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, "folder")
            os.mkdir(folder)
            visible_file = os.path.join(folder, "visible.txt")
            excluded_file = os.path.join(folder, "excluded.iso")
            for path in (visible_file, excluded_file):
                with open(path, "w", encoding="ascii") as stream:
                    stream.write("x")
            db_path = os.path.join(root, "index.db")
            self._prepare_index(db_path, root, folder, visible_file, 2)

            self._run_delete(db_path, visible_file)

            tool = FileIndexTool(db_path)
            try:
                physical_count = tool.conn.execute(
                    "SELECT physical_child_count FROM folder_summary "
                    "WHERE path = ?",
                    (folder,),
                ).fetchone()[0]
                empty_paths = {
                    row[0]
                    for row in tool.conn.execute(
                        f"SELECT path FROM file_index WHERE {EMPTY_FOLDER_SQL}"
                    )
                }
            finally:
                tool.close()

            self.assertEqual(physical_count, 1)
            self.assertNotIn(folder, empty_paths)


if __name__ == "__main__":
    unittest.main()
