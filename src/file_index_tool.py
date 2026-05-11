import argparse
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

DEFAULT_DB = "file_index.db"

class FileIndexTool:
    def __init__(self, db_path: str = DEFAULT_DB):
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.execute("PRAGMA temp_store=MEMORY;")
        self.create_tables()

    def create_tables(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS file_index (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                root TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                parent_path TEXT,
                is_folder INTEGER NOT NULL,
                size INTEGER,
                modified_time REAL,
                extension TEXT,
                inactive INTEGER DEFAULT 0
            );
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_name ON file_index(name);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_parent ON file_index(parent_path);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_modified ON file_index(modified_time);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_inactive ON file_index(inactive);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_folder ON file_index(is_folder);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_extension ON file_index(extension);")
        self.conn.commit()

    def clear_index(self) -> None:
        self.conn.execute("DELETE FROM file_index;")
        self.conn.commit()

    def scan(
        self,
        root_folder: str,
        inactive_years: int = 2,
        batch_size: int = 1000,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        cancel_callback: Optional[Callable[[], bool]] = None,
    ) -> None:
        root_folder = str(Path(root_folder).resolve())
        cutoff_timestamp = (datetime.now() - timedelta(days=365 * inactive_years)).timestamp()

        stack = [root_folder]
        batch = []
        scanned_count = 0
        inserted_count = 0

        while stack:
            if cancel_callback and cancel_callback():
                break

            current_folder = stack.pop()

            try:
                with os.scandir(current_folder) as entries:
                    for entry in entries:
                        if cancel_callback and cancel_callback():
                            break

                        try:
                            is_folder = entry.is_dir(follow_symlinks=False)
                            stat = entry.stat(follow_symlinks=False)

                            path = entry.path
                            name = entry.name
                            parent_path = current_folder
                            size = 0 if is_folder else stat.st_size
                            modified_time = stat.st_mtime
                            extension = "" if is_folder else Path(name).suffix.lower()
                            inactive = 1 if modified_time < cutoff_timestamp else 0

                            batch.append(
                                (
                                    root_folder,
                                    path,
                                    name,
                                    parent_path,
                                    int(is_folder),
                                    size,
                                    modified_time,
                                    extension,
                                    inactive,
                                )
                            )

                            scanned_count += 1

                            if is_folder:
                                if name not in {"venv", "__pycache__", "node_modules", ".git"}:
                                    stack.append(path)

                            if len(batch) >= batch_size:
                                inserted_count += self._insert_batch(batch)
                                batch.clear()

                                if progress_callback:
                                    progress_callback(scanned_count, inserted_count, current_folder)

                        except (PermissionError, FileNotFoundError, OSError):
                            continue

            except (PermissionError, FileNotFoundError, OSError):
                continue

        if batch:
            inserted_count += self._insert_batch(batch)
            batch.clear()

        if progress_callback:
            progress_callback(scanned_count, inserted_count, root_folder)

    def _insert_batch(self, batch: list[tuple]) -> int:
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO file_index
            (
                root,
                path,
                name,
                parent_path,
                is_folder,
                size,
                modified_time,
                extension,
                inactive
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            batch,
        )
        self.conn.commit()
        return len(batch)

    def children_of_folder(self, folder_path: str, limit: int = 500, offset: int = 0) -> list[tuple]:
        folder_path = str(Path(folder_path).resolve())
        cursor = self.conn.execute(
            """
            SELECT path, is_folder, size, modified_time, name
            FROM file_index
            WHERE parent_path = ?
            ORDER BY is_folder DESC, name ASC
            LIMIT ? OFFSET ?;
            """,
            (folder_path, limit, offset),
        )
        return cursor.fetchall()
        
    def close(self) -> None:
        self.conn.close()
