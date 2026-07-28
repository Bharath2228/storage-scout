import os
import sqlite3
import sys
import tempfile
import unittest
from types import MethodType, SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from src import file_index_tool
from src.folder_cache import FolderCache
from src.main_window import (
    FolderBrowserTreeModel,
    LazyChildrenLoadThread,
    MainWindow,
    PageLoadThread,
)


class FolderBrowserModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_model_requests_and_applies_folder_children_lazily(self):
        model = FolderBrowserTreeModel()
        requests = []
        model.loadRequested.connect(requests.append)
        root = os.path.normpath(r"C:\scan")
        model.reset_root(root)
        root_index = model.index(0, 0)

        self.assertTrue(model.canFetchMore(root_index))
        model.fetchMore(root_index)
        self.assertEqual(requests, [root])

        model.apply_children(
            root,
            [
                {
                    "name": "Folder A",
                    "path": os.path.normpath(r"C:\scan\Folder A"),
                    "is_dir": True,
                    "_children_loaded": False,
                },
                {
                    "name": "ignored.txt",
                    "path": os.path.normpath(r"C:\scan\ignored.txt"),
                    "is_dir": False,
                    "_children_loaded": True,
                },
            ],
        )

        self.assertEqual(model.rowCount(root_index), 1)
        folder_index = model.index(0, 0, root_index)
        self.assertEqual(model.data(folder_index), "Folder A")
        self.assertTrue(model.canFetchMore(folder_index))

    def test_live_scan_cache_populates_folder_browser_without_files(self):
        root = os.path.normpath(r"C:\scan")
        folder = os.path.join(root, "Folder A")
        cache = FolderCache()
        cache.add_item(root, "scan", True, 0, 1, None)
        cache.add_item(folder, "Folder A", True, 0, 1, root)
        cache.add_item(os.path.join(root, "ignored.txt"), "ignored.txt", False, 10, 1, root)
        browser = SimpleNamespace(
            apply_children=mock.Mock(),
            defer_load=mock.Mock(),
        )
        window = SimpleNamespace(
            current_scan_root=root,
            folder_cache=cache,
            is_scanning=True,
            folder_browser=browser,
        )

        MainWindow._load_folder_browser_children(window, root)

        browser.apply_children.assert_called_once()
        loaded_children = browser.apply_children.call_args.args[1]
        self.assertEqual([child["path"] for child in loaded_children], [folder])
        browser.defer_load.assert_not_called()

    def test_live_scan_cache_matches_unc_root_with_or_without_trailing_separator(self):
        browser_root = r"\\server\share"
        scanned_root = browser_root + "\\"
        folder = scanned_root + "Folder A"
        cache = FolderCache()
        cache.add_item(scanned_root, "share", True, 0, 1, None)
        cache.add_item(folder, "Folder A", True, 0, 1, scanned_root)
        browser = SimpleNamespace(
            apply_children=mock.Mock(),
            defer_load=mock.Mock(),
        )
        window = SimpleNamespace(
            current_scan_root=browser_root,
            folder_cache=cache,
            is_scanning=True,
            folder_browser=browser,
        )

        MainWindow._load_folder_browser_children(window, browser_root)

        browser.apply_children.assert_called_once()
        loaded_children = browser.apply_children.call_args.args[1]
        self.assertEqual([child["path"] for child in loaded_children], [folder])
        browser.defer_load.assert_not_called()

    def test_empty_live_scan_cache_starts_folder_browser_fallback(self):
        root = os.path.normpath(r"C:\scan")
        browser = SimpleNamespace(
            apply_children=mock.Mock(),
            defer_load=mock.Mock(),
        )
        window = SimpleNamespace(
            current_scan_root=root,
            folder_cache=FolderCache(),
            is_scanning=True,
            folder_browser=browser,
            folder_browser_request_id=0,
            folder_browser_threads={},
            _on_folder_browser_children_ready=mock.Mock(),
            _on_folder_browser_children_failed=mock.Mock(),
        )

        fake_thread = SimpleNamespace(
            children_ready=SimpleNamespace(connect=mock.Mock()),
            children_failed=SimpleNamespace(connect=mock.Mock()),
            finished=SimpleNamespace(connect=mock.Mock()),
            start=mock.Mock(),
        )
        with mock.patch(
            "src.main_window.LazyChildrenLoadThread",
            return_value=fake_thread,
        ) as thread_class:
            MainWindow._load_folder_browser_children(window, root)

        browser.apply_children.assert_not_called()
        browser.defer_load.assert_called_once_with(root)
        self.assertTrue(thread_class.call_args.kwargs["filesystem_fallback"])
        fake_thread.start.assert_called_once_with()


class FolderScopeQueryTests(unittest.TestCase):
    def _database(self):
        connection = sqlite3.connect(":memory:")
        connection.execute(
            """
            CREATE TABLE file_index (
                path TEXT,
                name TEXT,
                is_folder INTEGER,
                size INTEGER,
                modified_time REAL,
                parent_path TEXT,
                extension TEXT,
                root TEXT
            )
            """
        )
        root = os.path.normpath(r"C:\scan")
        folder_a = os.path.normpath(r"C:\scan\A")
        folder_b = os.path.normpath(r"C:\scan\B")
        rows = [
            (root, "scan", 1, 0, 1, None, "", root),
            (folder_a, "A", 1, 0, 1, root, "", root),
            (folder_b, "B", 1, 0, 1, root, "", root),
            (os.path.join(folder_a, "old.txt"), "old.txt", 0, 10, 10, folder_a, ".txt", root),
            (os.path.join(folder_a, "new.txt"), "new.txt", 0, 10, 100, folder_a, ".txt", root),
            (os.path.join(folder_b, "other.txt"), "other.txt", 0, 10, 10, folder_b, ".txt", root),
        ]
        connection.executemany(
            "INSERT INTO file_index VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        return connection, root, folder_a

    def _options(self, root, scope, view_mode):
        return {
            "limit": 2000,
            "offset": 0,
            "page": 0,
            "paginated": True,
            "view_mode": view_mode,
            "status_filter": "Inactive",
            "age_cutoff": 50,
            "videos_only": False,
            "name_filter": "",
            "extension_filter": None,
            "folder_scope": scope,
            "sort_column": 0,
            "sort_desc": False,
            "scan_root": root,
            "folder_cache": None,
            "lazy_show_all_tree": False,
            "filtered_expanded_tree": False,
            "defer_tree_load_until_scan_done": False,
        }

    def test_page_query_combines_folder_scope_with_age_filter(self):
        connection, root, scope = self._database()

        class FakeTool:
            def __init__(self):
                self.conn = connection

            def close(self):
                pass

        with mock.patch.object(file_index_tool, "FileIndexTool", FakeTool):
            result = PageLoadThread(1, self._options(root, scope, "Files"))._load()

        self.assertEqual(result["total_matches"], 1)
        self.assertEqual(
            [child["name"] for child in result["root_node"]["children"]],
            ["old.txt"],
        )
        connection.close()

    def test_tree_scope_hides_scope_and_ancestor_context_rows(self):
        connection, root, scope = self._database()

        class FakeTool:
            def __init__(self):
                self.conn = connection

            def close(self):
                pass

        with mock.patch.object(file_index_tool, "FileIndexTool", FakeTool):
            result = PageLoadThread(1, self._options(root, scope, "Tree"))._load()

        self.assertEqual(
            [child["name"] for child in result["root_node"]["children"]],
            ["old.txt"],
        )
        connection.close()

    def test_shared_lazy_loader_can_return_folders_only(self):
        connection, root, scope = self._database()
        subfolder = os.path.join(scope, "Nested")
        connection.execute(
            "INSERT INTO file_index VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (subfolder, "Nested", 1, 0, 1, scope, "", root),
        )

        class FakeTool:
            def __init__(self):
                self.conn = connection

            def close(self):
                pass

        emitted = []
        thread = LazyChildrenLoadThread(
            1,
            root,
            folders_only=True,
        )
        thread.children_ready.connect(
            lambda request_id, path, children: emitted.extend(children)
        )
        with mock.patch.object(file_index_tool, "FileIndexTool", FakeTool):
            thread.run()

        self.assertEqual({child["name"] for child in emitted}, {"A", "B"})
        folder_a = next(child for child in emitted if child["name"] == "A")
        self.assertFalse(folder_a["_children_loaded"])
        connection.close()

    def test_folder_loader_falls_back_to_sql_for_incomplete_unc_cache(self):
        connection = sqlite3.connect(":memory:")
        connection.execute(
            """
            CREATE TABLE file_index (
                path TEXT,
                name TEXT,
                is_folder INTEGER,
                size INTEGER,
                modified_time REAL,
                parent_path TEXT,
                extension TEXT,
                root TEXT
            )
            """
        )
        requested_root = r"\\server\share"
        stored_root = requested_root + "\\"
        child_path = stored_root + "Folder A"
        connection.execute(
            "INSERT INTO file_index VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (child_path, "Folder A", 1, 0, 1, stored_root, "", stored_root),
        )
        cache = FolderCache()
        cache.add_item(stored_root, "share", True, 0, 1, None)

        class FakeTool:
            def __init__(self):
                self.conn = connection

            def close(self):
                pass

        emitted = []
        thread = LazyChildrenLoadThread(
            1,
            requested_root,
            cache=cache,
            folders_only=True,
        )
        thread.children_ready.connect(
            lambda request_id, path, children: emitted.extend(children)
        )

        with mock.patch.object(file_index_tool, "FileIndexTool", FakeTool):
            thread.run()

        self.assertEqual([child["path"] for child in emitted], [child_path])
        connection.close()

    def test_folder_loader_can_enumerate_directories_outside_the_index(self):
        connection = sqlite3.connect(":memory:")
        connection.execute(
            """
            CREATE TABLE file_index (
                path TEXT,
                name TEXT,
                is_folder INTEGER,
                size INTEGER,
                modified_time REAL,
                parent_path TEXT,
                extension TEXT,
                root TEXT
            )
            """
        )

        class FakeTool:
            def __init__(self):
                self.conn = connection

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, "Folder A")
            os.mkdir(folder)
            with open(os.path.join(root, "ignored.txt"), "w", encoding="utf-8"):
                pass

            emitted = []
            thread = LazyChildrenLoadThread(
                1,
                root,
                folders_only=True,
                filesystem_fallback=True,
            )
            thread.children_ready.connect(
                lambda request_id, path, children: emitted.extend(children)
            )
            with mock.patch.object(file_index_tool, "FileIndexTool", FakeTool):
                thread.run()

        self.assertEqual([child["path"] for child in emitted], [folder])
        self.assertFalse(emitted[0]["_children_loaded"])
        connection.close()

    def test_page_options_keep_lazy_tree_mode_for_folder_scope(self):
        scope = os.path.normpath(r"C:\scan\A")
        checked = SimpleNamespace(isChecked=lambda: True)
        unchecked = SimpleNamespace(isChecked=lambda: False)
        window = SimpleNamespace(
            fp=SimpleNamespace(
                get_view_mode=lambda: "Tree",
                rb_all=checked,
                rb_empty=unchecked,
                rb_videos=unchecked,
                get_older_than_secs=lambda: None,
            ),
            applied_name_filter="",
            active_extension_filter=None,
            folder_browser_scope=scope,
            current_page=0,
            sort_column=0,
            sort_order=Qt.SortOrder.AscendingOrder,
            current_scan_root=os.path.normpath(r"C:\scan"),
            folder_cache=None,
            txt_path=SimpleNamespace(text=lambda: r"C:\scan"),
        )

        options = MainWindow._page_load_options(window)

        self.assertEqual(options["folder_scope"], scope)
        self.assertTrue(options["lazy_show_all_tree"])

    def test_scoped_lazy_tree_uses_selected_folder_cache_children(self):
        root = os.path.normpath(r"\\server\share\scan")
        scope = os.path.join(root, "Projects")
        nested = os.path.join(scope, "Nested")
        file_path = os.path.join(scope, "report.csv")
        cache = FolderCache()
        cache.add_item(root, "scan", True, 0, 1, None)
        cache.add_item(scope, "Projects", True, 0, 1, root)
        cache.add_item(nested, "Nested", True, 0, 1, scope)
        cache.add_item(file_path, "report.csv", False, 42, 1, scope)

        options = self._options(root, scope, "Tree")
        options.update({
            "status_filter": None,
            "age_cutoff": None,
            "paginated": False,
            "folder_cache": cache,
            "lazy_show_all_tree": True,
        })
        result = PageLoadThread(1, options)._load()

        self.assertEqual(
            {child["path"] for child in result["root_node"]["children"]},
            {nested, file_path},
        )
        self.assertEqual(result["total_matches"], 2)

    def test_bulk_selection_where_clause_keeps_folder_scope(self):
        scope = os.path.normpath(r"C:\scan\A")
        checked = SimpleNamespace(isChecked=lambda: True)
        unchecked = SimpleNamespace(isChecked=lambda: False)
        window = SimpleNamespace(
            fp=SimpleNamespace(
                rb_all=checked,
                rb_empty=unchecked,
                rb_inactive=unchecked,
                rb_videos=unchecked,
                get_older_than_secs=lambda: None,
                get_view_mode=lambda: "Files",
            ),
            applied_name_filter="",
            active_extension_filter=None,
            folder_browser_scope=scope,
            current_scan_root=os.path.normpath(r"C:\scan"),
            txt_path=SimpleNamespace(text=lambda: r"C:\scan"),
        )

        where_sql, params = MainWindow._build_bulk_where(window)

        self.assertIn("path LIKE", where_sql)
        self.assertTrue(any("A" in str(param) for param in params))


class FolderScopeInteractionTests(unittest.TestCase):
    def test_live_cache_readiness_uses_selected_folder_scope(self):
        root = os.path.normpath(r"\\server\share\scan")
        scope = os.path.join(root, "Projects")
        cache = FolderCache()
        cache.add_item(scope, "Projects", True, 0, 1, None)
        cache.add_item(
            os.path.join(scope, "report.csv"),
            "report.csv",
            False,
            42,
            1,
            scope,
        )
        options = {
            "lazy_show_all_tree": True,
            "folder_cache": cache,
            "scan_root": root,
            "folder_scope": scope,
        }

        self.assertTrue(MainWindow._can_live_load_from_cache(SimpleNamespace(), options))

    def test_selecting_scan_root_clears_scope_and_reloads_filters(self):
        root = os.path.normpath(r"C:\scan")
        window = SimpleNamespace(
            current_scan_root=root,
            folder_browser_scope=os.path.join(root, "A"),
            _on_filter_changed=mock.Mock(),
            _path_key=MethodType(MainWindow._path_key, SimpleNamespace()),
        )

        MainWindow._set_folder_browser_scope(window, root)

        self.assertIsNone(window.folder_browser_scope)
        window._on_filter_changed.assert_called_once_with(clear_extension=False)

    def test_delete_of_scoped_folder_returns_to_root_and_refreshes_parent(self):
        root = os.path.normpath(r"C:\scan")
        scope = os.path.join(root, "A")
        folder_browser = SimpleNamespace(
            root_path=root,
            select_root=mock.Mock(),
            refresh_paths=mock.Mock(),
        )
        window = SimpleNamespace(
            folder_browser=folder_browser,
            folder_browser_scope=scope,
            _set_folder_browser_scope=mock.Mock(),
        )

        MainWindow._refresh_folder_browser_after_delete(window, [scope])

        folder_browser.select_root.assert_called_once_with()
        window._set_folder_browser_scope.assert_called_once_with(None, reload=False)
        folder_browser.refresh_paths.assert_called_once_with([root])


if __name__ == "__main__":
    unittest.main()
