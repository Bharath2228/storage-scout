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
    EMPTY_FOLDER_SQL,
    FolderBrowserTreeModel,
    LazyChildrenLoadThread,
    MainWindow,
    PageLoadThread,
    WatchdogTreeModel,
)
from src.scan_exclusions import ScanExclusions


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

    def test_model_removes_deleted_folder_immediately(self):
        model = FolderBrowserTreeModel()
        root = os.path.normpath(r"C:\scan")
        child = os.path.join(root, "Deleted")
        model.reset_root(root)
        model.apply_children(
            root,
            [{
                "name": "Deleted",
                "path": child,
                "is_dir": True,
                "_children_loaded": True,
            }],
        )

        self.assertTrue(model.remove_path(child))
        self.assertEqual(model.rowCount(model.index(0, 0)), 0)
        self.assertFalse(model.index_for_path(child).isValid())

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
        connection.execute(
            """
            CREATE TABLE folder_summary (
                path TEXT PRIMARY KEY,
                total_size INTEGER DEFAULT 0,
                file_count INTEGER DEFAULT 0,
                folder_count INTEGER DEFAULT 0,
                child_count INTEGER DEFAULT 0,
                physical_child_count INTEGER DEFAULT -1
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

    def test_empty_filter_uses_physical_contents_and_show_all_hides_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = os.path.join(temp_dir, "scan")
            truly_empty = os.path.join(root, "Truly Empty")
            excluded_only = os.path.join(root, "Excluded Only")
            os.makedirs(truly_empty)
            os.makedirs(excluded_only)
            with open(os.path.join(excluded_only, "ignored.tmp"), "wb") as handle:
                handle.write(b"hidden")

            cache = FolderCache()
            tool = file_index_tool.FileIndexTool(
                os.path.join(temp_dir, "index.db")
            )
            tool.scan(
                root,
                cache=cache,
                exclusions=ScanExclusions(
                    folder_names=[],
                    extensions=[".tmp"],
                ),
            )

            class FakeTool:
                def __init__(self):
                    self.conn = tool.conn

                def close(self):
                    pass

            base_options = self._options(root, None, "Folders")
            base_options.update({
                "age_cutoff": None,
                "scan_exclusions": ScanExclusions(
                    folder_names=[],
                    extensions=[".tmp"],
                ),
            })
            empty_options = dict(base_options, status_filter="Empty")
            with mock.patch.object(file_index_tool, "FileIndexTool", FakeTool):
                empty_result = PageLoadThread(1, empty_options)._load()
                all_result = PageLoadThread(
                    2,
                    dict(base_options, status_filter=None),
                )._load()

            cursor = tool.conn.cursor()
            cursor.execute(
                "SELECT name FROM file_index WHERE " + EMPTY_FOLDER_SQL
            )
            self.assertEqual(
                {name for (name,) in cursor.fetchall()},
                {"Truly Empty"},
            )
            self.assertEqual(
                {child["name"] for child in empty_result["root_node"]["children"]},
                {"Truly Empty"},
            )
            self.assertNotIn(
                os.path.basename(root),
                {child["name"] for child in all_result["root_node"]["children"]},
            )
            self.assertEqual(
                {child["name"] for child in all_result["root_node"]["children"]},
                {"Truly Empty"},
            )
            self.assertNotIn(
                cache._key(excluded_only),
                cache.items,
            )
            tool.close()

    def test_nested_extension_only_folder_chain_is_pruned(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = os.path.join(temp_dir, "scan")
            parent = os.path.join(root, "Parent")
            child = os.path.join(parent, "Child")
            os.makedirs(child)
            with open(os.path.join(child, "archive.iso"), "wb") as handle:
                handle.write(b"excluded")

            cache = FolderCache()
            tool = file_index_tool.FileIndexTool(
                os.path.join(temp_dir, "index.db")
            )
            tool.scan(
                root,
                cache=cache,
                exclusions=ScanExclusions(
                    folder_names=[],
                    extensions=[".iso"],
                ),
            )

            cursor = tool.conn.cursor()
            cursor.execute("SELECT path FROM file_index")
            indexed_paths = {_path for (_path,) in cursor.fetchall()}
            self.assertIn(os.path.normpath(root), indexed_paths)
            self.assertNotIn(os.path.normpath(parent), indexed_paths)
            self.assertNotIn(os.path.normpath(child), indexed_paths)
            self.assertNotIn(cache._key(parent), cache.items)
            self.assertNotIn(cache._key(child), cache.items)
            tool.close()

    def test_nas_scope_follows_parent_links_when_child_path_uses_an_alias(self):
        connection, root, scope = self._database()
        aliased_child = os.path.normpath(r"Z:\dfs-alias\report.txt")
        connection.execute(
            "INSERT INTO file_index VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (aliased_child, "report.txt", 0, 25, 10, scope, ".txt", root),
        )

        class FakeTool:
            def __init__(self):
                self.conn = connection

            def close(self):
                pass

        with mock.patch.object(file_index_tool, "FileIndexTool", FakeTool):
            result = PageLoadThread(1, self._options(root, scope, "Files"))._load()

        self.assertEqual(result["total_matches"], 2)
        self.assertIn(
            aliased_child,
            [child["path"] for child in result["root_node"]["children"]],
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

    def test_empty_folder_result_has_no_expand_arrow(self):
        connection, root, scope = self._database()
        empty_folder = os.path.join(scope, "Empty")
        connection.execute(
            "INSERT INTO file_index VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (empty_folder, "Empty", 1, 0, 1, scope, "", root),
        )
        options = self._options(root, scope, "Tree")
        options.update({"status_filter": None, "age_cutoff": None})

        class FakeTool:
            def __init__(self):
                self.conn = connection

            def close(self):
                pass

        with mock.patch.object(file_index_tool, "FileIndexTool", FakeTool):
            result = PageLoadThread(1, options)._load()

        model = WatchdogTreeModel(result["root_node"])
        empty_index = next(
            model.index(row, 0)
            for row in range(model.rowCount())
            if model.data(model.index(row, 0)) == "Empty"
        )
        self.assertFalse(model.hasChildren(empty_index))
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
        self.assertTrue(emitted[0]["_children_loaded"])
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

    def test_scoped_lazy_tree_falls_back_to_accessible_filesystem_folder(self):
        connection, _root, _scope = self._database()
        connection.execute("DELETE FROM file_index")
        with tempfile.TemporaryDirectory() as temp_root:
            scope = os.path.join(temp_root, "NAS Folder")
            nested = os.path.join(scope, "Nested")
            os.makedirs(nested)
            file_path = os.path.join(scope, "report.csv")
            with open(file_path, "w", encoding="utf-8") as handle:
                handle.write("data")

            class FakeTool:
                def __init__(self):
                    self.conn = connection

                def close(self):
                    pass

            options = self._options(temp_root, scope, "Tree")
            options.update({
                "status_filter": None,
                "age_cutoff": None,
                "paginated": False,
                "folder_cache": FolderCache(),
                "lazy_show_all_tree": True,
            })
            with mock.patch.object(file_index_tool, "FileIndexTool", FakeTool):
                result = PageLoadThread(1, options)._load()

        self.assertEqual(result["debug_info"].split()[0], "show_all_source=filesystem")
        self.assertEqual(
            {child["name"] for child in result["root_node"]["children"]},
            {"Nested", "report.csv"},
        )
        self.assertEqual(result["total_matches"], 2)
        connection.close()

    def test_scoped_filesystem_fallback_supports_other_filters_and_views(self):
        connection, _root, _scope = self._database()
        connection.execute("DELETE FROM file_index")
        with tempfile.TemporaryDirectory() as temp_root:
            scope = os.path.join(temp_root, "NAS Folder")
            os.makedirs(scope)
            empty_folder = os.path.join(scope, "Empty")
            os.mkdir(empty_folder)
            old_file = os.path.join(scope, "old-report.txt")
            new_file = os.path.join(scope, "new-report.txt")
            video_file = os.path.join(scope, "clip.mp4")
            for path in (old_file, new_file, video_file):
                with open(path, "wb") as handle:
                    handle.write(b"x")
            os.utime(old_file, (10, 10))
            os.utime(video_file, (10, 10))
            os.utime(new_file, (100, 100))
            os.utime(empty_folder, (10, 10))

            class FakeTool:
                def __init__(self):
                    self.conn = connection

                def close(self):
                    pass

            cases = [
                (
                    {"view_mode": "Tree", "status_filter": "Inactive", "age_cutoff": 50},
                    {"old-report.txt", "clip.mp4"},
                ),
                (
                    {"view_mode": "Folders", "status_filter": "Empty", "age_cutoff": 50},
                    {"Empty"},
                ),
                (
                    {
                        "view_mode": "Files",
                        "status_filter": None,
                        "age_cutoff": None,
                        "videos_only": True,
                    },
                    {"clip.mp4"},
                ),
                (
                    {
                        "view_mode": "Files",
                        "status_filter": None,
                        "age_cutoff": None,
                        "name_filter": "report",
                    },
                    {"old-report.txt", "new-report.txt"},
                ),
            ]

            for overrides, expected_names in cases:
                with self.subTest(overrides=overrides):
                    options = self._options(temp_root, scope, overrides["view_mode"])
                    options.update(overrides)
                    with mock.patch.object(file_index_tool, "FileIndexTool", FakeTool):
                        result = PageLoadThread(1, options)._load()
                    self.assertEqual(
                        {child["name"] for child in result["root_node"]["children"]},
                        expected_names,
                    )
                    self.assertEqual(result["debug_info"], "filtered_source=filesystem")

        connection.close()

    def test_filtered_scoped_tree_keeps_nested_filesystem_branches_expandable(self):
        connection, _root, _scope = self._database()
        connection.execute("DELETE FROM file_index")
        with tempfile.TemporaryDirectory() as temp_root:
            scope = os.path.join(temp_root, "NAS Folder")
            parent_folder = os.path.join(scope, "Parent")
            child_folder = os.path.join(parent_folder, "Child")
            os.makedirs(child_folder)
            nested_file = os.path.join(child_folder, "old.txt")
            with open(nested_file, "wb") as handle:
                handle.write(b"x")
            os.utime(nested_file, (10, 10))

            class FakeTool:
                def __init__(self):
                    self.conn = connection

                def close(self):
                    pass

            options = self._options(temp_root, scope, "Tree")
            with mock.patch.object(file_index_tool, "FileIndexTool", FakeTool):
                result = PageLoadThread(1, options)._load()

        model = WatchdogTreeModel(result["root_node"])
        parent_index = model.index(0, 0)
        child_index = model.index(0, 0, parent_index)
        file_index = model.index(0, 0, child_index)
        self.assertEqual(model.data(parent_index), "Parent")
        self.assertTrue(model.hasChildren(parent_index))
        self.assertEqual(model.data(child_index), "Child")
        self.assertTrue(model.hasChildren(child_index))
        self.assertEqual(model.data(file_index), "old.txt")
        self.assertTrue(result["filesystem_scope_fallback"])
        connection.close()

    def test_filesystem_scope_calculates_nested_folder_sizes(self):
        connection, _root, _scope = self._database()
        connection.execute("DELETE FROM file_index")
        with tempfile.TemporaryDirectory() as temp_root:
            scope = os.path.join(temp_root, "NAS Folder")
            parent_folder = os.path.join(scope, "Parent")
            child_folder = os.path.join(parent_folder, "Child")
            os.makedirs(child_folder)
            with open(os.path.join(parent_folder, "one.bin"), "wb") as handle:
                handle.write(b"123")
            with open(os.path.join(child_folder, "two.bin"), "wb") as handle:
                handle.write(b"12345")

            class FakeTool:
                def __init__(self):
                    self.conn = connection

                def close(self):
                    pass

            options = self._options(temp_root, scope, "Folders")
            options["age_cutoff"] = None
            with mock.patch.object(file_index_tool, "FileIndexTool", FakeTool):
                result = PageLoadThread(1, options)._load()

        sizes = {
            child["name"]: child["size"]
            for child in result["root_node"]["children"]
        }
        self.assertEqual(sizes["Parent"], 8)
        connection.close()

    def test_scoped_filter_uses_scan_cache_without_rewalking_filesystem(self):
        connection, _root, _scope = self._database()
        connection.execute("DELETE FROM file_index")
        root = os.path.normpath(r"Z:\scan")
        scope = os.path.join(root, "Scope")
        file_path = os.path.join(scope, "old.txt")
        cache = FolderCache()
        cache.add_item(root, "scan", True, 0, 1, None)
        cache.add_item(scope, "Scope", True, 0, 1, root)
        cache.add_item(file_path, "old.txt", False, 12, 10, scope)
        cache.set_folder_summary(root, 12, 1, 2, 1, 1)
        cache.set_folder_summary(scope, 12, 1, 1, 1, 1)

        class FakeTool:
            def __init__(self):
                self.conn = connection

            def close(self):
                pass

        options = self._options(root, scope, "Files")
        options["folder_cache"] = cache
        with (
            mock.patch.object(file_index_tool, "FileIndexTool", FakeTool),
            mock.patch(
                "src.main_window.os.scandir",
                side_effect=AssertionError("filesystem should not be walked"),
            ),
        ):
            result = PageLoadThread(1, options)._load()

        self.assertEqual(
            [child["name"] for child in result["root_node"]["children"]],
            ["old.txt"],
        )
        connection.close()

    def test_filesystem_scope_and_lazy_children_honor_exclusions(self):
        connection, _root, _scope = self._database()
        connection.execute("DELETE FROM file_index")
        with tempfile.TemporaryDirectory() as temp_root:
            scope = os.path.join(temp_root, "NAS Folder")
            skipped_folder = os.path.join(scope, "skip")
            os.makedirs(skipped_folder)
            with open(os.path.join(skipped_folder, "hidden.txt"), "wb") as handle:
                handle.write(b"hidden")
            with open(os.path.join(scope, "ignored.tmp"), "wb") as handle:
                handle.write(b"ignored")
            with open(os.path.join(scope, "keep.txt"), "wb") as handle:
                handle.write(b"kept")

            exclusions = ScanExclusions(
                folder_names=["skip"],
                extensions=[".tmp"],
            )

            class FakeTool:
                def __init__(self):
                    self.conn = connection

                def close(self):
                    pass

            options = self._options(temp_root, scope, "Files")
            options["age_cutoff"] = None
            options["scan_exclusions"] = exclusions
            with mock.patch.object(file_index_tool, "FileIndexTool", FakeTool):
                result = PageLoadThread(1, options)._load()

            emitted = []
            thread = LazyChildrenLoadThread(
                1,
                scope,
                filesystem_fallback=True,
                scan_exclusions=exclusions,
            )
            thread.children_ready.connect(
                lambda request_id, path, children: emitted.extend(children)
            )
            with mock.patch.object(file_index_tool, "FileIndexTool", FakeTool):
                thread.run()

        self.assertEqual(
            [child["name"] for child in result["root_node"]["children"]],
            ["keep.txt"],
        )
        self.assertEqual([child["name"] for child in emitted], ["keep.txt"])
        connection.close()

    def test_filesystem_fallback_hides_extension_only_folder_but_keeps_true_empty(self):
        connection, _root, _scope = self._database()
        connection.execute("DELETE FROM file_index")
        with tempfile.TemporaryDirectory() as temp_root:
            scope = os.path.join(temp_root, "NAS Folder")
            excluded_only = os.path.join(scope, "Excluded Only")
            truly_empty = os.path.join(scope, "Truly Empty")
            os.makedirs(excluded_only)
            os.makedirs(truly_empty)
            with open(os.path.join(excluded_only, "archive.iso"), "wb") as handle:
                handle.write(b"excluded")

            exclusions = ScanExclusions(
                folder_names=[],
                extensions=[".iso"],
            )

            class FakeTool:
                def __init__(self):
                    self.conn = connection

                def close(self):
                    pass

            options = self._options(temp_root, scope, "Folders")
            options.update({
                "status_filter": None,
                "age_cutoff": None,
                "scan_exclusions": exclusions,
            })
            with mock.patch.object(file_index_tool, "FileIndexTool", FakeTool):
                result = PageLoadThread(1, options)._load()

            emitted = []
            thread = LazyChildrenLoadThread(
                1,
                scope,
                folders_only=True,
                filesystem_fallback=True,
                scan_exclusions=exclusions,
            )
            thread.children_ready.connect(
                lambda request_id, path, children: emitted.extend(children)
            )
            with mock.patch.object(file_index_tool, "FileIndexTool", FakeTool):
                thread.run()

        self.assertEqual(
            [child["name"] for child in result["root_node"]["children"]],
            ["Truly Empty"],
        )
        self.assertEqual([child["name"] for child in emitted], ["Truly Empty"])
        connection.close()

    def test_lazy_filesystem_child_loader_expands_nested_nas_folder(self):
        connection, _root, _scope = self._database()
        connection.execute("DELETE FROM file_index")
        with tempfile.TemporaryDirectory() as temp_root:
            parent_folder = os.path.join(temp_root, "Parent")
            child_folder = os.path.join(parent_folder, "Child")
            os.makedirs(child_folder)

            class FakeTool:
                def __init__(self):
                    self.conn = connection

                def close(self):
                    pass

            emitted = []
            thread = LazyChildrenLoadThread(
                1,
                parent_folder,
                filesystem_fallback=True,
            )
            thread.children_ready.connect(
                lambda request_id, path, children: emitted.extend(children)
            )
            with mock.patch.object(file_index_tool, "FileIndexTool", FakeTool):
                thread.run()

        self.assertEqual([child["name"] for child in emitted], ["Child"])
        connection.close()

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
            remove_paths=mock.Mock(),
            refresh_paths=mock.Mock(),
        )
        window = SimpleNamespace(
            folder_browser=folder_browser,
            folder_browser_scope=scope,
            folder_browser_request_id=3,
            folder_browser_threads={1: object()},
            _set_folder_browser_scope=mock.Mock(),
        )

        MainWindow._refresh_folder_browser_after_delete(window, [scope])

        folder_browser.select_root.assert_called_once_with()
        window._set_folder_browser_scope.assert_called_once_with(None, reload=False)
        folder_browser.remove_paths.assert_called_once_with([scope])
        folder_browser.refresh_paths.assert_called_once_with([root])
        self.assertEqual(window.folder_browser_request_id, 4)
        self.assertEqual(window.folder_browser_threads, {})


if __name__ == "__main__":
    unittest.main()
