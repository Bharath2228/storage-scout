import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from src.file_index_tool import FileIndexTool, stable_absolute_path


class FileIndexSchemaTests(unittest.TestCase):
    def test_stable_absolute_path_preserves_mapped_drive_identity(self):
        mapped_path = r"Z:\shared\scan"

        self.assertEqual(
            stable_absolute_path(mapped_path),
            str(Path(mapped_path)),
        )

    def test_concurrent_startup_migrates_physical_child_count_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "index.db")
            connection = sqlite3.connect(db_path)
            connection.execute(
                """
                CREATE TABLE folder_summary (
                    path TEXT PRIMARY KEY,
                    total_size INTEGER DEFAULT 0,
                    file_count INTEGER DEFAULT 0,
                    folder_count INTEGER DEFAULT 0,
                    child_count INTEGER DEFAULT 0
                )
                """
            )
            connection.commit()
            connection.close()

            barrier = threading.Barrier(3)
            errors = []

            def open_tool():
                barrier.wait()
                try:
                    tool = FileIndexTool(db_path)
                    tool.close()
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=open_tool) for _ in range(2)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join()

            self.assertEqual(errors, [])
            connection = sqlite3.connect(db_path)
            columns = [
                row[1]
                for row in connection.execute("PRAGMA table_info(folder_summary)")
            ]
            connection.close()
            self.assertEqual(columns.count("physical_child_count"), 1)


if __name__ == "__main__":
    unittest.main()
