import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QModelIndex, Qt

from src.models import StorageScoutFilterProxyModel, StorageScoutTreeModel


class SearchDescendantCheckboxTests(unittest.TestCase):
    def _proxy_for(self, root_data, search_text):
        source = StorageScoutTreeModel(root_data)
        proxy = StorageScoutFilterProxyModel()
        proxy.setSourceModel(source)
        proxy.set_filters(
            empty_only=False,
            status_filter=None,
            view_mode="Tree",
            name_filter=search_text,
        )
        return source, proxy

    def test_files_below_matching_folder_keep_their_checkboxes(self):
        root_data = {
            "children": [
                {
                    "name": "Zoom",
                    "path": r"C:\data\Zoom",
                    "is_dir": True,
                    "status": "Inactive",
                    "_children_loaded": True,
                    "children": [
                        {
                            "name": "Workshop",
                            "path": r"C:\data\Zoom\Workshop",
                            "is_dir": True,
                            "status": "Inactive",
                            "_children_loaded": True,
                            "children": [
                                {
                                    "name": "recording.mp4",
                                    "path": r"C:\data\Zoom\Workshop\recording.mp4",
                                    "is_dir": False,
                                    "status": "Inactive",
                                    "children": [],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        _, proxy = self._proxy_for(root_data, "zoom")

        zoom = proxy.index(0, 0, QModelIndex())
        workshop = proxy.index(0, 0, zoom)
        recording = proxy.index(0, 0, workshop)

        for index in (zoom, workshop, recording):
            self.assertTrue(index.isValid())
            self.assertTrue(proxy.flags(index) & Qt.ItemFlag.ItemIsUserCheckable)
            self.assertEqual(
                proxy.data(index, Qt.ItemDataRole.CheckStateRole),
                Qt.CheckState.Unchecked,
            )

    def test_ancestor_shown_only_as_context_still_has_no_checkbox(self):
        root_data = {
            "children": [
                {
                    "name": "Archive",
                    "path": r"C:\data\Archive",
                    "is_dir": True,
                    "status": "Inactive",
                    "_children_loaded": True,
                    "children": [
                        {
                            "name": "zoom-notes.txt",
                            "path": r"C:\data\Archive\zoom-notes.txt",
                            "is_dir": False,
                            "status": "Inactive",
                            "children": [],
                        }
                    ],
                }
            ]
        }
        _, proxy = self._proxy_for(root_data, "zoom")

        archive = proxy.index(0, 0, QModelIndex())
        result_file = proxy.index(0, 0, archive)

        self.assertFalse(proxy.flags(archive) & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertIsNone(proxy.data(archive, Qt.ItemDataRole.CheckStateRole))
        self.assertTrue(proxy.flags(result_file) & Qt.ItemFlag.ItemIsUserCheckable)


if __name__ == "__main__":
    unittest.main()
