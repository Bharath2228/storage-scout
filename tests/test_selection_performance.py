import os
import sys
import unittest
from types import MethodType, SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QPushButton

from src.main_window import (
    IndexedPathDict,
    MainWindow,
    PathKeyIndex,
    prune_contained_paths,
)
from src.models import WatchdogFilterProxyModel, WatchdogTreeModel


class PathKeyIndexTests(unittest.TestCase):
    def test_ancestor_and_descendant_queries_preserve_path_boundaries(self):
        root = os.path.normcase(os.path.normpath(r"C:\data"))
        nested = os.path.normcase(os.path.normpath(r"C:\data\one\two"))
        sibling = os.path.normcase(os.path.normpath(r"C:\database"))
        index = PathKeyIndex((root, nested, sibling))

        self.assertTrue(index.has_ancestor(os.path.normcase(os.path.normpath(r"C:\data\file.txt"))))
        self.assertFalse(index.has_ancestor(root, include_self=False))
        self.assertFalse(index.has_ancestor(os.path.normcase(os.path.normpath(r"C:\datacenter\x"))))
        self.assertTrue(index.has_descendant(root))
        self.assertFalse(index.has_descendant(sibling))

        drive_root = os.path.normcase(os.path.normpath("C:\\"))
        root_only_index = PathKeyIndex((drive_root,))
        self.assertFalse(root_only_index.has_descendant(drive_root))

    def test_indexed_dictionary_stays_synced_and_removes_subtrees(self):
        parent = os.path.normcase(os.path.normpath(r"C:\data\folder"))
        child = os.path.normcase(os.path.normpath(r"C:\data\folder\child.txt"))
        other = os.path.normcase(os.path.normpath(r"C:\data\other.txt"))
        paths = IndexedPathDict({
            parent: r"C:\data\folder",
            child: r"C:\data\folder\child.txt",
            other: r"C:\data\other.txt",
        })

        paths.remove_descendants(parent, include_self=True)

        self.assertNotIn(parent, paths)
        self.assertNotIn(child, paths)
        self.assertIn(other, paths)
        self.assertFalse(paths.path_index.has_descendant(parent))

    def test_pruning_uses_ancestor_index_without_changing_results(self):
        paths = [
            r"C:\data\folder\child.txt",
            r"C:\data\other.txt",
            r"C:\data\folder",
            r"C:\data\folder\deeper\item.txt",
        ]

        self.assertEqual(
            prune_contained_paths(paths, sort_alpha=True),
            [r"C:\data\folder", r"C:\data\other.txt"],
        )


class CombinedPageSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def _window_stub(self):
        root = {
            "name": "root",
            "is_dir": True,
            "path": r"C:\data",
            "children": [
                {
                    "name": "selected",
                    "path": r"C:\data\selected",
                    "is_dir": True,
                    "status": "Inactive",
                    "_is_page_result": True,
                    "_children_loaded": True,
                    "children": [
                        {
                            "name": "keep.txt",
                            "path": r"C:\data\selected\keep.txt",
                            "is_dir": False,
                            "status": "Inactive",
                            "_is_page_result": True,
                            "children": [],
                        },
                        {
                            "name": "excluded.txt",
                            "path": r"C:\data\selected\excluded.txt",
                            "is_dir": False,
                            "status": "Active",
                            "_is_page_result": True,
                            "children": [],
                        },
                    ],
                },
                {
                    "name": "bulk.txt",
                    "path": r"C:\data\bulk.txt",
                    "is_dir": False,
                    "status": "Inactive",
                    "_is_page_result": True,
                    "children": [],
                },
            ],
        }
        tree_model = WatchdogTreeModel(root)
        proxy_model = WatchdogFilterProxyModel()
        proxy_model.setSourceModel(tree_model)
        selected_key = os.path.normcase(os.path.normpath(r"C:\data\selected"))
        excluded_key = os.path.normcase(
            os.path.normpath(r"C:\data\selected\excluded.txt")
        )
        window = SimpleNamespace(
            tree_model=tree_model,
            proxy_model=proxy_model,
            selected_paths=IndexedPathDict({
                selected_key: r"C:\data\selected",
            }),
            excluded_paths=IndexedPathDict({
                excluded_key: r"C:\data\selected\excluded.txt",
            }),
            bulk_delete_scope={"status": "Inactive", "videos_only": False},
            source_index_by_path={},
            _selection_button_targets_cache=None,
            _selection_sync_suppressed=False,
            fp=SimpleNamespace(),
        )
        for method_name in (
            "_path_key",
            "_has_selected_ancestor",
            "_has_any_selected_ancestor",
            "_is_descendant_of_selected_path",
            "_has_excluded_ancestor",
            "_is_effectively_selected",
            "_is_persistable_selection_index",
            "_is_video_item",
            "_are_all_indices_checked",
            "_set_bulk_scope_page_excluded",
            "_set_indices_check_state_direct_suppressed",
            "_rebuild_and_restore_page_selection",
            "_collect_selection_button_targets",
        ):
            setattr(window, method_name, MethodType(getattr(MainWindow, method_name), window))
        window._display_mode = lambda: "Inactive"
        return window

    def test_combined_walk_restores_selection_in_one_model_update(self):
        window = self._window_stub()
        original = window.tree_model.set_indices_check_state_direct
        window.tree_model.set_indices_check_state_direct = mock.Mock(wraps=original)

        window._rebuild_and_restore_page_selection()

        self.assertEqual(window.tree_model.set_indices_check_state_direct.call_count, 1)
        self.assertEqual(len(window.source_index_by_path), 4)
        excluded_index = window.source_index_by_path[
            os.path.normcase(os.path.normpath(r"C:\data\selected\excluded.txt"))
        ]
        self.assertEqual(
            window.tree_model.data(excluded_index, Qt.ItemDataRole.CheckStateRole),
            Qt.CheckState.Unchecked,
        )
        cached_targets = window._selection_button_targets_cache["targets"]
        self.assertIs(
            window._collect_selection_button_targets("Inactive"),
            cached_targets,
        )

    def test_bulk_scope_restored_rows_are_recognized_as_checked(self):
        window = self._window_stub()
        window.selected_paths.clear()
        window.excluded_paths.clear()

        window._rebuild_and_restore_page_selection()

        inactive_targets = window._collect_selection_button_targets("Inactive")[1]
        self.assertTrue(inactive_targets)
        self.assertTrue(MainWindow._are_all_indices_checked(window, inactive_targets))

    def test_bulk_scope_page_refresh_updates_both_selection_labels(self):
        window = self._window_stub()
        window.selected_paths.clear()
        window.excluded_paths.clear()
        window.page_only_selected_paths = {}
        window.btn_select_all = QPushButton("Select All")
        window.btn_clear_selection = QPushButton("Unselect All")
        window.btn_select_inactive = QPushButton("Select All Inactive")
        window.btn_select_empty = QPushButton("Select All Empty")
        window.btn_current_page_selection = QPushButton("Select Current Page")
        window._set_selection_control_active = MethodType(
            MainWindow._set_selection_control_active,
            window,
        )
        window._refresh_selection_buttons = MethodType(
            MainWindow._refresh_selection_buttons,
            window,
        )

        window._rebuild_and_restore_page_selection()
        window._refresh_selection_buttons()

        self.assertEqual(window.btn_select_inactive.text(), "Unselect All Inactive")
        self.assertEqual(window.btn_current_page_selection.text(), "Unselect Current Page")
        self.assertTrue(window.btn_select_inactive.property("selectionActive"))
        self.assertTrue(window.btn_current_page_selection.property("selectionActive"))

    def test_inactive_button_reflects_selection_from_another_page(self):
        window = self._window_stub()
        selected_path = r"C:\data\previous-page.txt"
        window.bulk_delete_scope = None
        window.selected_paths.clear()
        window.selected_paths[window._path_key(selected_path)] = selected_path
        window.page_only_selected_paths = {}
        window.btn_select_all = QPushButton("Select All")
        window.btn_clear_selection = QPushButton("Unselect All")
        window.btn_select_inactive = QPushButton("Select All Inactive")
        window.btn_select_empty = QPushButton("Select All Empty")
        window.btn_current_page_selection = QPushButton("Select Current Page")
        window._set_selection_control_active = MethodType(
            MainWindow._set_selection_control_active,
            window,
        )
        window._refresh_selection_buttons = MethodType(
            MainWindow._refresh_selection_buttons,
            window,
        )

        window._refresh_selection_buttons()

        self.assertEqual(window.btn_select_inactive.text(), "Unselect All Inactive")
        self.assertEqual(window.btn_current_page_selection.text(), "Select Current Page")
        self.assertTrue(window.btn_select_inactive.property("selectionActive"))
        self.assertFalse(window.btn_current_page_selection.property("selectionActive"))

    def test_unselect_current_page_keeps_bulk_scope_for_later_pages(self):
        window = self._window_stub()
        window.selected_paths.clear()
        window.excluded_paths.clear()
        window.page_only_selected_paths = {}
        window.page_only_selection_page = None
        window.cached_selected_total = None
        window._do_recount = mock.Mock()
        window._collect_checked_source_indices = MethodType(
            MainWindow._collect_checked_source_indices,
            window,
        )
        window._clear_current_page_checks = MethodType(
            MainWindow._clear_current_page_checks,
            window,
        )

        window._rebuild_and_restore_page_selection()
        current_targets = window._collect_selection_button_targets("Inactive")[1]
        current_paths = {
            window.tree_model.data(index, Qt.ItemDataRole.UserRole)["path"]
            for index in current_targets
        }
        window._clear_current_page_checks()

        self.assertIsNotNone(window.bulk_delete_scope)
        self.assertEqual(
            set(window.bulk_delete_scope["excluded_paths"]),
            current_paths,
        )
        self.assertFalse(MainWindow._are_all_indices_checked(window, current_targets))

        later_root = {
            "name": "root",
            "is_dir": True,
            "path": r"C:\data",
            "children": [{
                "name": "later.txt",
                "path": r"C:\data\later.txt",
                "is_dir": False,
                "status": "Inactive",
                "_is_page_result": True,
                "children": [],
            }],
        }
        window.tree_model = WatchdogTreeModel(later_root)
        window.proxy_model.setSourceModel(window.tree_model)
        window._selection_button_targets_cache = None
        window._rebuild_and_restore_page_selection()
        later_targets = window._collect_selection_button_targets("Inactive")[1]

        self.assertTrue(MainWindow._are_all_indices_checked(window, later_targets))

    def test_manual_bulk_uncheck_and_recheck_persist_across_page_reloads(self):
        window = self._window_stub()
        window.selected_paths.clear()
        window.excluded_paths.clear()
        window._sync_persistent_selection_from_model = MethodType(
            MainWindow._sync_persistent_selection_from_model,
            window,
        )
        window._rebuild_and_restore_page_selection()
        bulk_key = os.path.normcase(os.path.normpath(r"C:\data\bulk.txt"))
        bulk_index = window.source_index_by_path[bulk_key]

        window.tree_model.setData(
            bulk_index,
            Qt.CheckState.Unchecked,
            Qt.ItemDataRole.CheckStateRole,
        )
        window._sync_persistent_selection_from_model()

        self.assertIn(
            r"C:\data\bulk.txt",
            window.bulk_delete_scope["excluded_paths"],
        )

        reloaded = self._window_stub()
        reloaded.selected_paths.clear()
        reloaded.excluded_paths.clear()
        reloaded.bulk_delete_scope = window.bulk_delete_scope
        reloaded._sync_persistent_selection_from_model = MethodType(
            MainWindow._sync_persistent_selection_from_model,
            reloaded,
        )
        reloaded._rebuild_and_restore_page_selection()
        reloaded_index = reloaded.source_index_by_path[bulk_key]
        self.assertEqual(
            reloaded.tree_model.data(reloaded_index, Qt.ItemDataRole.CheckStateRole),
            Qt.CheckState.Unchecked,
        )

        reloaded.tree_model.setData(
            reloaded_index,
            Qt.CheckState.Checked,
            Qt.ItemDataRole.CheckStateRole,
        )
        reloaded._sync_persistent_selection_from_model()
        self.assertNotIn(
            r"C:\data\bulk.txt",
            reloaded.bulk_delete_scope["excluded_paths"],
        )

    def test_checked_signal_is_suppressed_during_programmatic_or_bulk_updates(self):
        window = SimpleNamespace(
            bulk_select_active=False,
            _selection_sync_suppressed=True,
            _sync_persistent_selection_from_model=mock.Mock(),
            _set_size_totals_pending=mock.Mock(),
            recount_timer=mock.Mock(),
        )

        MainWindow._on_checked(
            window,
            roles=[Qt.ItemDataRole.CheckStateRole],
        )

        window._sync_persistent_selection_from_model.assert_not_called()
        window.recount_timer.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
