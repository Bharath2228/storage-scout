import os
import sys
import unittest
from types import MethodType, SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

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
            "_has_excluded_ancestor",
            "_is_effectively_selected",
            "_is_persistable_selection_index",
            "_is_video_item",
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
