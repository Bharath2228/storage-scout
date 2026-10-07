import os
import subprocess

from PyQt6.QtCore import (
    QModelIndex,
    QPersistentModelIndex,
    QTimer,
    Qt,
)
from PyQt6.QtWidgets import (
    QApplication,
    QMenu,
)

from ...scan_exclusions import ScanExclusions
from ...theme import SPACE_LG, SPACE_SM, SPACE_XS, current_palette
from ..workers.page_load import LazyChildrenLoadThread


class _TreeInteractionsMixin:
    def _item_data(self, proxy_index):
        if not self.tree_model:
            return None
        src = self.proxy_model.mapToSource(proxy_index)
        return self.tree_model.data(src, Qt.ItemDataRole.UserRole)

    def _relative_display_location(self, folder_path):
        if not folder_path:
            return ""

        root = getattr(self, 'current_scan_root', None)
        normalized_folder = os.path.normpath(folder_path)
        if not root:
            root = os.path.normpath(self.txt_path.text().strip() or "")

        if root:
            try:
                relative = os.path.relpath(normalized_folder, root)
                if relative == ".":
                    return "Root directory"
                if not relative.startswith(".."):
                    return relative
            except ValueError:
                pass

        parts = normalized_folder.replace("/", "\\").split("\\")
        return "\\".join(parts[-3:]) if len(parts) > 3 else normalized_folder

    def _is_tree_index_clickable(self, index):
        if not index.isValid():
            return False
        return index.column() == 0

    def _update_tree_cursor(self, index):
        cursor = (
            Qt.CursorShape.PointingHandCursor
            if self._is_tree_index_clickable(index)
            else Qt.CursorShape.ArrowCursor
        )
        self.tree.viewport().setCursor(cursor)

    def _on_click(self, index):
        return

    def _on_double_click(self, index):
        if not self._is_tree_index_clickable(index):
            return
        d = self._item_data(index)
        if d:
            self._open(d['path'], d.get('is_dir', True))

    def _on_tree_expanded(self, proxy_index):
        if getattr(self, 'is_programmatic_expand', False):
            return
        if not self.tree_model or self.proxy_model.sourceModel() is None:
            return
        source_index = self.proxy_model.mapToSource(proxy_index)
        if source_index.isValid():
            self._start_lazy_child_load(source_index)
        QTimer.singleShot(0, self._fit_tree_columns_to_viewport)
        QTimer.singleShot(0, self._update_expand_control_visibility)

    def _on_tree_collapsed(self, proxy_index):
        QTimer.singleShot(0, self._fit_tree_columns_to_viewport)
        QTimer.singleShot(0, self._update_expand_control_visibility)

    def _start_lazy_child_load(self, source_index):
        folder_path = self.tree_model.begin_async_child_load(source_index)
        if not folder_path:
            return

        self.lazy_child_request_id += 1
        request_id = self.lazy_child_request_id
        if (
            self.current_lazy_show_all_tree
            and self.folder_cache is not None
            and self.folder_cache.has_children_for(folder_path)
        ):
            self.lazy_child_threads[request_id] = {
                'thread': None,
                'index': QPersistentModelIndex(source_index),
                'path': folder_path,
                'model': self.tree_model,
            }
            children = self.folder_cache.children_for(
                folder_path,
                self.sort_column,
                self.sort_order == Qt.SortOrder.DescendingOrder,
            )
            self._on_lazy_children_ready(request_id, folder_path, children)
            self.lazy_child_threads.pop(request_id, None)
            return

        thread = LazyChildrenLoadThread(
            request_id,
            folder_path,
            sort_column=self.sort_column,
            sort_desc=self.sort_order == Qt.SortOrder.DescendingOrder,
            options=None if self.current_lazy_show_all_tree else getattr(self.tree_model, 'options', {}),
            cache=self.folder_cache,
            filesystem_fallback=(
                self.current_lazy_show_all_tree
                or self.current_filesystem_scope_fallback
            ),
            scan_exclusions=getattr(
                self.fp,
                'get_scan_exclusions',
                lambda: ScanExclusions(),
            )(),
            parent=self,
        )
        self.lazy_child_threads[request_id] = {
            'thread': thread,
            'index': QPersistentModelIndex(source_index),
            'path': folder_path,
            'model': self.tree_model,
        }
        thread.children_ready.connect(self._on_lazy_children_ready)
        thread.children_failed.connect(self._on_lazy_children_failed)
        thread.finished.connect(lambda request_id=request_id: self._cleanup_lazy_child_thread(request_id))
        thread.start()

    def _on_lazy_children_ready(self, request_id, folder_path, children):
        state = self.lazy_child_threads.get(request_id)
        if not state or state.get('path') != folder_path:
            return
        persistent_index = state.get('index')
        if (
            not persistent_index
            or not persistent_index.isValid()
            or not self.tree_model
            or state.get('model') is not self.tree_model
        ):
            return

        expanded_path_keys = set()
        stack = [QModelIndex()]
        while stack:
            parent = stack.pop()
            for row in range(self.proxy_model.rowCount(parent)):
                proxy_index = self.proxy_model.index(row, 0, parent)
                if not self.tree.isExpanded(proxy_index):
                    continue
                source_for_expanded = self.proxy_model.mapToSource(proxy_index)
                item_data = self.tree_model.data(source_for_expanded, Qt.ItemDataRole.UserRole) or {}
                if item_data.get('path'):
                    expanded_path_keys.add(self._path_key(item_data['path']))
                stack.append(proxy_index)
        expanded_path_keys.add(self._path_key(folder_path))

        source_index = QModelIndex(persistent_index)
        vbar = self.tree.verticalScrollBar()
        hbar = self.tree.horizontalScrollBar()
        previous_vertical = vbar.value()
        previous_horizontal = hbar.value()
        self.tree.setUpdatesEnabled(False)
        try:
            self.proxy_model.setSourceModel(None)
            try:
                self.tree_model.finish_async_child_load(source_index, children)
            finally:
                self.proxy_model.setSourceModel(self.tree_model)
                self.tree.setModel(self.proxy_model)
                self._update_status_column_visibility()
        finally:
            self.tree.setUpdatesEnabled(True)
        self._rebuild_and_restore_page_selection()
        self._refresh_selection_buttons()
        self.is_programmatic_expand = True
        try:
            for path_key in sorted(expanded_path_keys, key=lambda key: key.count(os.sep)):
                loaded_source_index = self.source_index_by_path.get(path_key, QModelIndex())
                proxy_index = self.proxy_model.mapFromSource(loaded_source_index)
                if not proxy_index.isValid():
                    continue
                self.tree.setExpanded(proxy_index, True)
        finally:
            self.is_programmatic_expand = False
        self._restore_tree_scroll_position(previous_vertical, previous_horizontal)
        QTimer.singleShot(
            0,
            lambda v=previous_vertical, h=previous_horizontal: self._restore_tree_scroll_position(v, h),
        )
        QTimer.singleShot(0, self._update_status_column_visibility)
        QTimer.singleShot(0, self._fit_tree_columns_to_viewport)

    def _on_lazy_children_failed(self, request_id, folder_path, error):
        state = self.lazy_child_threads.get(request_id)
        if not state or state.get('path') != folder_path:
            return
        persistent_index = state.get('index')
        if (
            persistent_index
            and persistent_index.isValid()
            and self.tree_model
            and state.get('model') is self.tree_model
        ):
            self.tree_model.fail_async_child_load(QModelIndex(persistent_index))
        self.lbl_status.setText("Failed to load folder contents.")

    def _context_menu(self, pos):
        idx = self.tree.indexAt(pos)
        if not idx.isValid():
            return
        d = self._item_data(idx)
        if not d:
            return
        menu = QMenu()
        palette = current_palette()
        menu.setStyleSheet(f"""
            QMenu {{ background:{palette['surface']}; color:{palette['text']}; border:1px solid {palette['border']};
                    border-radius:6px; padding:{SPACE_XS}px; }}
            QMenu::item {{ padding:{SPACE_SM}px {SPACE_LG}px; border-radius:4px; }}
            QMenu::item:selected {{ background:{palette['accent']}; color:white; }}
            QMenu::separator {{ background:{palette['border']}; height:1px; margin:{SPACE_XS}px 0; }}
        """)
        a_open   = menu.addAction("Open Location")
        a_copy   = menu.addAction("Copy Path")
        menu.addSeparator()
        a_delete = menu.addAction("Delete (Recycle Bin)")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen == a_open:
            self._open(d['path'], d.get('is_dir', True))
        elif chosen == a_copy:
            QApplication.clipboard().setText(d['path'])
        elif chosen == a_delete:
            self._delete_one(d['path'])

    def _open(self, path, is_dir=True):
        if not os.path.exists(path):
            return
        path = os.path.normpath(path)
        try:
            if is_dir:
                os.startfile(path)
            else:
                subprocess.Popen(f'explorer /select,"{path}"')
        except Exception:
            pass
