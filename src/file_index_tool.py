import argparse
import os
import queue
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS folder_summary (
                path TEXT PRIMARY KEY,
                total_size INTEGER DEFAULT 0,
                file_count INTEGER DEFAULT 0,
                folder_count INTEGER DEFAULT 0,
                child_count INTEGER DEFAULT 0
            );
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_name ON file_index(name);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_parent ON file_index(parent_path);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_parent_nocase ON file_index(parent_path COLLATE NOCASE);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_modified ON file_index(modified_time);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_inactive ON file_index(inactive);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_folder ON file_index(is_folder);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_extension ON file_index(extension);")
        self.conn.commit()

    def clear_index(self) -> None:
        self.conn.execute("DELETE FROM file_index;")
        self.conn.execute("DELETE FROM folder_summary;")
        self.conn.commit()

    def _default_worker_count(self, root_folder: str) -> int:
        cpu_count = os.cpu_count() or 4
        if root_folder.startswith("\\\\") or root_folder.startswith("//"):
            return max(2, min(6, cpu_count))
        return max(4, min(16, cpu_count * 2))

    def scan(
        self,
        root_folder: str,
        inactive_months: int = 24,
        inactive_years: int | None = None,
        batch_size: int = 1000,
        max_workers: int | None = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        cancel_callback: Optional[Callable[[], bool]] = None,
    ) -> None:
        root_folder = str(Path(root_folder).resolve())
        if inactive_years is not None:
            inactive_months = inactive_years * 12
        cutoff_timestamp = (datetime.now() - timedelta(days=30 * inactive_months)).timestamp()

        max_workers = max_workers or self._default_worker_count(root_folder)
        scanned_count = 0
        inserted_count = 0
        latest_folder = root_folder

        rows_queue: queue.Queue = queue.Queue(maxsize=max_workers * 4)
        work_queue: queue.Queue = queue.Queue()
        stop_writer = object()
        pending_lock = threading.Lock()
        progress_lock = threading.Lock()
        summary_lock = threading.Lock()
        pending_folders = 0
        folder_info: dict[str, dict] = {}

        def path_key(value: str) -> str:
            return os.path.normcase(os.path.normpath(value))

        def ensure_folder(path: str, parent_path: str | None = None, modified_time: float = 0) -> dict:
            key = path_key(path)
            info = folder_info.get(key)
            if info is None:
                info = {
                    "path": path,
                    "parent": parent_path,
                    "total_size": 0,
                    "file_count": 0,
                    "folder_count": 1,
                    "child_count": 0,
                    "modified_time": modified_time,
                }
                folder_info[key] = info
            else:
                if parent_path is not None:
                    info["parent"] = parent_path
                if modified_time:
                    info["modified_time"] = modified_time
            return info

        def queue_folder(folder_path: str) -> None:
            nonlocal pending_folders
            with pending_lock:
                pending_folders += 1
            work_queue.put(folder_path)

        def mark_folder_done() -> None:
            nonlocal pending_folders
            with pending_lock:
                pending_folders -= 1

        def is_done() -> bool:
            with pending_lock:
                return pending_folders <= 0

        def report_progress(current_folder: str) -> None:
            if not progress_callback:
                return
            progress_callback(scanned_count, inserted_count, current_folder)

        def writer_loop() -> None:
            nonlocal inserted_count
            batch = []
            while True:
                item = rows_queue.get()
                if item is stop_writer:
                    break
                batch.extend(item)
                if len(batch) >= batch_size:
                    inserted = self._insert_batch(batch)
                    with progress_lock:
                        inserted_count += inserted
                        report_progress(latest_folder)
                    batch.clear()

            if batch:
                inserted = self._insert_batch(batch)
                with progress_lock:
                    inserted_count += inserted
                    report_progress(latest_folder)

        try:
            root_stat = os.stat(root_folder, follow_symlinks=False)
            root_name = Path(root_folder).name or root_folder
            with summary_lock:
                ensure_folder(root_folder, None, root_stat.st_mtime)
            rows_queue.put([
                (
                    root_folder,
                    root_folder,
                    root_name,
                    None,
                    1,
                    0,
                    root_stat.st_mtime,
                    "",
                    1 if root_stat.st_mtime < cutoff_timestamp else 0,
                )
            ])
            with progress_lock:
                scanned_count += 1
        except (PermissionError, FileNotFoundError, OSError):
            return

        writer = threading.Thread(target=writer_loop, name="file-index-writer", daemon=True)
        writer.start()
        queue_folder(root_folder)

        def scan_folder(current_folder: str) -> None:
            nonlocal scanned_count, latest_folder
            if cancel_callback and cancel_callback():
                mark_folder_done()
                return
            local_batch = []
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

                            local_batch.append(
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

                            with progress_lock:
                                scanned_count += 1
                                latest_folder = current_folder

                            with summary_lock:
                                current_info = ensure_folder(current_folder)
                                current_info["child_count"] += 1
                                if is_folder:
                                    ensure_folder(path, current_folder, modified_time)
                                    if name not in {"venv", "__pycache__", "node_modules", ".git"}:
                                        queue_folder(path)
                                else:
                                    current_info["total_size"] += size or 0
                                    current_info["file_count"] += 1

                            if len(local_batch) >= batch_size:
                                rows_queue.put(local_batch)
                                local_batch = []

                        except (PermissionError, FileNotFoundError, OSError):
                            continue

            except (PermissionError, FileNotFoundError, OSError):
                pass
            finally:
                if local_batch:
                    rows_queue.put(local_batch)
                mark_folder_done()

        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="scan-worker") as executor:
            futures = []
            while True:
                if cancel_callback and cancel_callback():
                    break
                try:
                    folder = work_queue.get(timeout=0.05)
                except queue.Empty:
                    if is_done():
                        break
                    continue
                futures.append(executor.submit(scan_folder, folder))

            for future in futures:
                future.result()

        rows_queue.put(stop_writer)
        writer.join()

        self._write_folder_summaries(folder_info)

        if progress_callback:
            progress_callback(scanned_count, inserted_count, root_folder)

    def _write_folder_summaries(self, folder_info: dict[str, dict]) -> None:
        if not folder_info:
            return

        summaries = list(folder_info.values())
        summaries.sort(key=lambda item: len(os.path.normpath(item["path"])), reverse=True)
        by_key = {
            os.path.normcase(os.path.normpath(item["path"])): item
            for item in summaries
        }

        for item in summaries:
            parent = item.get("parent")
            if not parent:
                continue
            parent_item = by_key.get(os.path.normcase(os.path.normpath(parent)))
            if parent_item:
                parent_item["total_size"] += item["total_size"]
                parent_item["file_count"] += item["file_count"]
                parent_item["folder_count"] += item["folder_count"]

        rows = [
            (
                item["path"],
                item["total_size"],
                item["file_count"],
                item["folder_count"],
                item["child_count"],
            )
            for item in summaries
        ]
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO folder_summary
            (path, total_size, file_count, folder_count, child_count)
            VALUES (?, ?, ?, ?, ?);
            """,
            rows,
        )
        self.conn.executemany(
            "UPDATE file_index SET size = ? WHERE path = ? AND is_folder = 1;",
            [(item["total_size"], item["path"]) for item in summaries],
        )
        self.conn.commit()

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
            WHERE parent_path = ? COLLATE NOCASE
            ORDER BY is_folder DESC, name ASC
            LIMIT ? OFFSET ?;
            """,
            (folder_path, limit, offset),
        )
        return cursor.fetchall()
        
    def close(self) -> None:
        self.conn.close()
