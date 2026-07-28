import unittest
from types import SimpleNamespace
from unittest import mock

from src.folder_cache import FolderCache
from src.main_window import MainWindow


class FolderCacheRemovalTests(unittest.TestCase):
    def setUp(self):
        self.cache = FolderCache()
        self.root = r"C:\scan"
        self.folder = r"C:\scan\folder"
        self.subfolder = r"C:\scan\folder\subfolder"
        self.nested_file = r"C:\scan\folder\nested.txt"
        self.deep_file = r"C:\scan\folder\subfolder\deep.txt"
        self.root_file = r"C:\scan\root.txt"

        self.cache.add_item(self.root, "scan", True, 0, 1, None)
        self.cache.add_item(self.folder, "folder", True, 0, 1, self.root)
        self.cache.add_item(
            self.subfolder,
            "subfolder",
            True,
            0,
            1,
            self.folder,
        )
        self.cache.add_item(
            self.nested_file,
            "nested.txt",
            False,
            10,
            1,
            self.folder,
        )
        self.cache.add_item(
            self.deep_file,
            "deep.txt",
            False,
            20,
            1,
            self.subfolder,
        )
        self.cache.add_item(
            self.root_file,
            "root.txt",
            False,
            5,
            1,
            self.root,
        )

        self.cache.set_folder_summary(self.root, 35, 3, 3, 2)
        self.cache.set_folder_summary(self.folder, 30, 2, 2, 2)
        self.cache.set_folder_summary(self.subfolder, 20, 1, 1, 1)

    def test_remove_folder_removes_descendants_and_updates_ancestors(self):
        self.assertTrue(self.cache.remove_path(self.folder))

        folder_key = self.cache._key(self.folder)
        subfolder_key = self.cache._key(self.subfolder)
        self.assertNotIn(folder_key, self.cache.items)
        self.assertNotIn(subfolder_key, self.cache.items)
        self.assertNotIn(self.cache._key(self.nested_file), self.cache.items)
        self.assertNotIn(self.cache._key(self.deep_file), self.cache.items)
        self.assertNotIn(folder_key, self.cache.children)
        self.assertNotIn(folder_key, self.cache.folder_sizes)
        self.assertNotIn(folder_key, self.cache.folder_counts)

        root_key = self.cache._key(self.root)
        self.assertEqual(self.cache.children[root_key], [self.cache._key(self.root_file)])
        self.assertEqual(
            [item["path"] for item in self.cache.children_for(self.root)],
            [self.root_file],
        )
        self.assertFalse(self.cache.has_children_for(self.folder))
        self.assertEqual(self.cache.folder_sizes[root_key], 5)
        self.assertEqual(
            self.cache.folder_counts[root_key],
            {"files": 1, "folders": 1, "children": 1},
        )
        self.assertEqual(self.cache.items[root_key]["size"], 5)
        self.assertEqual(self.cache.running_total_size, 5)

    def test_remove_file_updates_parent_and_is_idempotent(self):
        self.assertTrue(self.cache.remove_path(self.root_file))

        root_key = self.cache._key(self.root)
        self.assertEqual(self.cache.folder_sizes[root_key], 30)
        self.assertEqual(
            self.cache.folder_counts[root_key],
            {"files": 2, "folders": 3, "children": 1},
        )
        self.assertEqual(self.cache.running_total_size, 30)

        self.assertFalse(self.cache.remove_path(self.root_file))
        self.assertEqual(self.cache.folder_sizes[root_key], 30)
        self.assertEqual(self.cache.running_total_size, 30)

    def test_descendant_count_is_scoped_to_requested_folder(self):
        self.assertEqual(self.cache.descendant_count(self.root), 5)
        self.assertEqual(self.cache.descendant_count(self.folder), 3)
        self.assertEqual(self.cache.descendant_count(self.subfolder), 1)

    def test_folder_child_detection_ignores_files(self):
        self.assertTrue(self.cache.has_folder_children(self.folder))
        self.assertFalse(self.cache.has_folder_children(self.subfolder))


class DeleteCompletionCacheTests(unittest.TestCase):
    def test_shared_delete_completion_invalidates_cache_and_refreshes_totals(self):
        folder_cache = mock.Mock()
        window = SimpleNamespace(
            delete_progress=None,
            delete_thread=object(),
            bulk_delete_scope={"scope": "all"},
            delete_authorized_by="Alice",
            delete_audit_items=2,
            delete_audit_size=30,
            folder_cache=folder_cache,
            cached_folder_total=30,
            cached_folder_total_root=r"c:\scan",
            _remove_deleted_paths_from_selection=mock.Mock(),
            _set_delete_controls_enabled=mock.Mock(),
            _load_page=mock.Mock(),
            _start_totals_refresh=mock.Mock(),
            _should_show_completion_notification=mock.Mock(return_value=True),
            _show_system_notification=mock.Mock(),
        )
        deleted_paths = [r"C:\scan\one.txt", r"C:\scan\folder"]

        with (
            mock.patch("src.main_window.append_delete_audit"),
            mock.patch("src.main_window.QMessageBox.information"),
        ):
            MainWindow._on_delete_finished(
                window,
                deleted_count=2,
                errors=[],
                cancelled=False,
                deleted_paths=deleted_paths,
            )

        self.assertEqual(
            folder_cache.remove_path.call_args_list,
            [mock.call(path) for path in deleted_paths],
        )
        self.assertIsNone(window.cached_folder_total)
        self.assertIsNone(window.cached_folder_total_root)
        window._remove_deleted_paths_from_selection.assert_called_once_with(deleted_paths)
        window._load_page.assert_called_once_with()
        window._start_totals_refresh.assert_called_once_with()
        window._should_show_completion_notification.assert_called_once_with(
            elapsed_secs=None,
            cancelled=False,
        )
        window._show_system_notification.assert_called_once_with(
            "Deletion complete",
            "Deleted 2 item(s).",
        )


if __name__ == "__main__":
    unittest.main()
