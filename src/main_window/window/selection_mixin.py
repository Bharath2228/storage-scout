import os
import time
from datetime import datetime

from PyQt6.QtCore import (
    QModelIndex,
    QTimer,
    Qt,
)
from PyQt6.QtGui import (
    QIcon,
)
from PyQt6.QtWidgets import (
    QMessageBox,
)

from ..constants import EMPTY_FOLDER_SQL, PERF_DEBUG, VIDEO_EXTENSIONS
from ..export_utils import _perf_log
from ..path_utils import IndexedPathDict, _path_key, bulk_scope_excluded_keys, equivalent_path_variants
from ..sql_utils import descendant_scope_sql, escape_sql_like, prune_contained_paths
from ..workers.selection import BulkPageSelectThread


class _SelectionMixin:
    def _select_all(self):
        if not self.tree_model:
            return

        self._preserve_results_focus = True
        videos_only = self._display_mode() == 'Videos'
        selecting = not self.btn_select_all.text().startswith("Deselect")
        self._begin_page_select_flow(
            label="videos" if videos_only else "items",
            videos_only=videos_only,
            selecting=selecting,
        )

    def _toggle_select_all(self):
        has_selection = bool(
            self.bulk_delete_scope
            or self.selected_paths
            or self.page_only_selected_paths
        )
        if has_selection:
            self._unselect_all()
        else:
            self._select_all()

    def _toggle_current_page_selection(self):
        if not self.tree_model:
            return

        mode = self._display_mode()
        status = mode if mode in ("Inactive", "Empty") else None
        videos_only = mode == "Videos"
        indices = self._collect_bulk_target_indices(
            status=status,
            videos_only=videos_only,
        )
        if not indices:
            self._refresh_selection_buttons()
            return

        if self._are_all_indices_checked(indices):
            self._clear_current_page_checks()
            return

        requested_scope = "current"
        if getattr(self, "bulk_delete_scope", None):
            self._set_bulk_scope_page_excluded(indices, False)
            requested_scope = "bulk_current"
        self._preserve_results_focus = True
        self._begin_chunked_bulk_selection(
            indices,
            Qt.CheckState.Checked,
            current_page_count=len(indices),
            label="current page",
            offer_all_pages=False,
            status=status,
            videos_only=videos_only,
            requested_scope=requested_scope,
        )

    def _clear_all_checks(self):
        self.bulk_delete_scope = None
        self.selected_paths.clear()
        self.page_only_selected_paths.clear()
        self.excluded_paths.clear()
        self.page_only_selection_page = None
        self.cached_selected_total = None
        if not self.tree_model:
            self._do_recount()
            return
        indices = []

        def collect(parent=QModelIndex()):
            for r in range(self.tree_model.rowCount(parent)):
                idx = self.tree_model.index(r, 0, parent)
                indices.append(idx)
                if self.tree_model.hasChildren(idx):
                    collect(idx)

        collect()
        if indices:
            self._set_indices_check_state_direct_suppressed(
                indices,
                Qt.CheckState.Unchecked,
                explicit=False,
            )
        self._do_recount()

    def _clear_current_page_checks(self):
        self.cached_selected_total = None
        if not self.tree_model:
            self._do_recount()
            return

        if self.bulk_delete_scope:
            mode = self._display_mode()
            all_targets, inactive_targets, empty_targets = self._collect_selection_button_targets(mode)
            indices = (
                inactive_targets
                if mode == "Inactive"
                else empty_targets
                if mode == "Empty"
                else all_targets
            )
            self._set_bulk_scope_page_excluded(indices, True)
        else:
            indices = self._collect_checked_source_indices()
        if indices:
            self._set_indices_check_state_direct_suppressed(
                indices,
                Qt.CheckState.Unchecked,
                explicit=False,
            )
        visible_keys = {
            self._path_key(self.tree_model.data(idx, Qt.ItemDataRole.UserRole).get('path', ''))
            for idx in indices
            if self.tree_model.data(idx, Qt.ItemDataRole.UserRole)
        }
        self.selected_paths = IndexedPathDict({
            key: path for key, path in self.selected_paths.items()
            if self._path_key(path) not in visible_keys
        })
        for key in visible_keys:
            path = self.source_index_by_path.get(key)
            item_data = self.tree_model.data(path, Qt.ItemDataRole.UserRole) if path and path.isValid() else None
            if item_data and self._has_any_selected_ancestor(item_data.get('path', ''), include_self=False):
                self.excluded_paths[key] = item_data['path']
        self.page_only_selected_paths.clear()
        self.page_only_selection_page = None
        self._do_recount()

    def _unselect_all(self):
        if not self.tree_model or not (
            self.bulk_delete_scope
            or self.selected_paths
            or self.page_only_selected_paths
        ):
            return

        if not (hasattr(self, 'lbl_page_info') and self.lbl_page_info.isVisible()):
            self._clear_all_checks()
            return

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle("Clear selection")
        msg.setText("Clear the current page or clear everything selected?")
        msg.setInformativeText(
            "Current page only clears the checked rows that are visible right now.\n"
            "Clear everything removes the full selection across all pages."
        )
        btn_page = msg.addButton("Current page only", QMessageBox.ButtonRole.RejectRole)
        btn_all = msg.addButton("Clear everything", QMessageBox.ButtonRole.AcceptRole)
        btn_cancel = msg.addButton("Cancel", QMessageBox.ButtonRole.DestructiveRole)
        btn_cancel.hide()
        msg.setEscapeButton(btn_cancel)
        msg.setDefaultButton(btn_page)
        msg.exec()

        if msg.clickedButton() == btn_all:
            self._clear_all_checks()
        elif msg.clickedButton() == btn_page:
            self._clear_current_page_checks()

    def _discard_current_page_selection(self):
        self.bulk_delete_scope = None
        self.selected_paths.clear()
        self.page_only_selected_paths.clear()
        self.excluded_paths.clear()
        self.page_only_selection_page = None
        self.cached_selected_total = None
        self._update_delete_button_copy()
        self._set_delete_armed(False)
        self.btn_delete.setEnabled(False)

    def _collect_bulk_target_indices(self, status=None, videos_only=False, include_context=False):
        if not self.tree_model or self.proxy_model.sourceModel() is None:
            return []

        targets = []
        seen_paths = set()
        exact_only = self.proxy_model.has_active_filters() or status is not None or videos_only

        def walk(parent=QModelIndex()):
            for row in range(self.proxy_model.rowCount(parent)):
                proxy_index = self.proxy_model.index(row, 0, parent)
                source_index = self.proxy_model.mapToSource(proxy_index)
                item_data = self.tree_model.data(source_index, Qt.ItemDataRole.UserRole)
                if item_data:
                    path = item_data.get('path')
                    is_exact_match = self.proxy_model.matches_source_index(source_index)
                    is_page_result = item_data.get(
                        '_is_page_result',
                        not item_data.get('_is_context_fetched', False),
                    )
                    if (
                        path and path not in seen_paths
                        and (include_context or is_page_result)
                        and (status is None or item_data.get('status') == status)
                        and (not videos_only or self._is_video_item(item_data))
                        and (not exact_only or is_exact_match)
                    ):
                        seen_paths.add(path)
                        targets.append(source_index)
                if self.proxy_model.hasChildren(proxy_index):
                    walk(proxy_index)

        walk()
        return targets

    def _are_all_indices_checked(self, indices):
        if not indices:
            return False
        for index in indices:
            if (
                not index.isValid()
                or self.tree_model.data(index, Qt.ItemDataRole.CheckStateRole)
                != Qt.CheckState.Checked
            ):
                return False
        return True

    def _path_key(self, path):
        return _path_key(path)

    def _has_selected_ancestor(self, path, include_self=True):
        return self.selected_paths.path_index.has_ancestor(
            self._path_key(path),
            include_self=include_self,
        )

    def _has_any_selected_ancestor(self, path, include_self=True):
        return self._has_selected_ancestor(path, include_self=include_self)

    def _is_descendant_of_selected_path(self, path):
        return self._has_any_selected_ancestor(path, include_self=False)

    def _has_excluded_ancestor(self, path, include_self=True):
        return self.excluded_paths.path_index.has_ancestor(
            self._path_key(path),
            include_self=include_self,
        )

    def _has_excluded_descendant(self, path):
        return self.excluded_paths.path_index.has_descendant(self._path_key(path))

    def _is_effectively_selected(self, path):
        return (
            self._has_selected_ancestor(path)
            and not self._has_excluded_ancestor(path)
        )

    def _is_persistable_selection_index(self, index):
        if not index.isValid():
            return False
        item_data = self.tree_model.data(index, Qt.ItemDataRole.UserRole)
        if not item_data or not item_data.get('path'):
            return False
        is_context_fetched = item_data.get('_is_context_fetched', False)
        if self.proxy_model.sourceModel() is self.tree_model:
            if self.proxy_model.is_context_only(index) or is_context_fetched:
                return False
        return True

    def _sync_persistent_selection_from_model(self):
        if not self.tree_model:
            return

        started_at = time.perf_counter() if PERF_DEBUG else None
        visited = 0
        bulk_scope = self.bulk_delete_scope
        bulk_status = bulk_scope.get('status') if bulk_scope else None
        bulk_videos_only = bool(bulk_scope and bulk_scope.get('videos_only', False))
        bulk_exact_only = bool(
            bulk_scope
            and (
                self.proxy_model.has_active_filters()
                or bulk_status is not None
                or bulk_videos_only
            )
        )
        bulk_checked_indices = []
        bulk_unchecked_indices = []
        stack = [QModelIndex()]
        while stack:
            parent = stack.pop()
            for row in range(self.tree_model.rowCount(parent)):
                index = self.tree_model.index(row, 0, parent)
                visited += 1
                item_data = self.tree_model.data(index, Qt.ItemDataRole.UserRole)
                if item_data and item_data.get('path') and self._is_persistable_selection_index(index):
                    key = self._path_key(item_data['path'])
                    state = self.tree_model.data(index, Qt.ItemDataRole.CheckStateRole)
                    is_page_result = item_data.get(
                        '_is_page_result',
                        not item_data.get('_is_context_fetched', False),
                    )
                    is_bulk_target = bool(
                        bulk_scope
                        and is_page_result
                        and (bulk_status is None or item_data.get('status') == bulk_status)
                        and (not bulk_videos_only or self._is_video_item(item_data))
                        and (
                            not bulk_exact_only
                            or self.proxy_model.matches_source_index(index)
                        )
                    )
                    if state == Qt.CheckState.Checked:
                        self.excluded_paths.remove_descendants(key, include_self=True)
                        if not self._is_descendant_of_selected_path(item_data['path']):
                            self.selected_paths[key] = item_data['path']
                        if is_bulk_target:
                            bulk_checked_indices.append(index)
                    elif state == Qt.CheckState.Unchecked:
                        if self._has_any_selected_ancestor(item_data['path'], include_self=False):
                            self.excluded_paths[key] = item_data['path']
                        self.selected_paths.pop(key, None)
                        if is_bulk_target:
                            bulk_unchecked_indices.append(index)
                if self.tree_model.hasChildren(index):
                    stack.append(index)
        if bulk_checked_indices:
            self._set_bulk_scope_page_excluded(bulk_checked_indices, False)
        if bulk_unchecked_indices:
            self._set_bulk_scope_page_excluded(bulk_unchecked_indices, True)
        _perf_log(
            "selection model sync",
            started_at,
            nodes=visited,
            selected=len(self.selected_paths),
            excluded=len(self.excluded_paths),
        )

    def _promote_current_page_selection_to_page_only(self):
        if not self.tree_model:
            return

        checked_indices = self._collect_checked_source_indices()
        if not checked_indices:
            return

        for index in checked_indices:
            item_data = self.tree_model.data(index, Qt.ItemDataRole.UserRole)
            if not item_data or not item_data.get('path'):
                continue
            key = self._path_key(item_data['path'])
            if key not in self.selected_paths:
                self.selected_paths[key] = item_data['path']

    def _set_indices_checked(self, indices, state, recursive=True):
        if not indices:
            return
        to_change = [idx for idx in indices if self.tree_model.data(idx, Qt.ItemDataRole.CheckStateRole) != state]
        if to_change:
            self.cached_selected_total = None
            previous_suppressed = self._selection_sync_suppressed
            self._selection_sync_suppressed = True
            try:
                if recursive:
                    self.tree_model.set_indices_check_state(to_change, state, explicit=True)
                else:
                    self.tree_model.set_indices_check_state_direct(to_change, state, explicit=True)
            finally:
                self._selection_sync_suppressed = previous_suppressed
            self._sync_persistent_selection_from_model()

    def _set_indices_check_state_direct_suppressed(self, indices, state, explicit=True):
        previous_suppressed = self._selection_sync_suppressed
        self._selection_sync_suppressed = True
        try:
            self.tree_model.set_indices_check_state_direct(indices, state, explicit=explicit)
        finally:
            self._selection_sync_suppressed = previous_suppressed

    def _build_bulk_where(self, status=None, videos_only=False):
        where_clauses = []
        params = []
        age_secs = self.fp.get_older_than_secs()
        age_cutoff = (datetime.now().timestamp() - age_secs) if age_secs is not None else None
        view_mode = self.fp.get_view_mode()

        if status == 'Inactive':
            if view_mode == 'Tree':
                where_clauses.append("is_folder = 0")
            if age_cutoff is not None:
                where_clauses.append("modified_time <= ?")
                params.append(age_cutoff)
            else:
                where_clauses.append("1=1")
        elif status == 'Empty':
            if age_cutoff is not None:
                where_clauses.append("modified_time <= ?")
                params.append(age_cutoff)
            where_clauses.append(EMPTY_FOLDER_SQL)
        elif videos_only:
            placeholders = ','.join('?' * len(VIDEO_EXTENSIONS))
            where_clauses.append("is_folder = 0")
            where_clauses.append(f"extension IN ({placeholders})")
            params.extend(VIDEO_EXTENSIONS)
            if age_cutoff is not None:
                where_clauses.append("modified_time <= ?")
                params.append(age_cutoff)
        else:
            if self.fp.rb_empty.isChecked():
                if age_cutoff is not None:
                    where_clauses.append("modified_time <= ?")
                    params.append(age_cutoff)
                where_clauses.append(EMPTY_FOLDER_SQL)
            elif self.fp.rb_inactive.isChecked():
                if view_mode == 'Tree':
                    where_clauses.append("is_folder = 0")
                if age_cutoff is not None:
                    where_clauses.append("modified_time <= ?")
                    params.append(age_cutoff)
                else:
                    where_clauses.append("1=1")
            elif self.fp.rb_videos.isChecked():
                placeholders = ','.join('?' * len(VIDEO_EXTENSIONS))
                where_clauses.append("is_folder = 0")
                where_clauses.append(f"extension IN ({placeholders})")
                params.extend(VIDEO_EXTENSIONS)
                if age_cutoff is not None:
                    where_clauses.append("modified_time <= ?")
                    params.append(age_cutoff)
            elif age_cutoff is not None:
                where_clauses.append("modified_time <= ?")
                params.append(age_cutoff)

        if view_mode == 'Files':
            where_clauses.append("is_folder = 0")
        elif view_mode == 'Folders':
            where_clauses.append("is_folder = 1")

        name_filter = self.applied_name_filter
        if name_filter:
            where_clauses.append("name LIKE ? ESCAPE '\\' COLLATE NOCASE")
            params.append(f"%{escape_sql_like(name_filter)}%")

        if self.active_extension_filter is not None:
            where_clauses.append("is_folder = 0")
            where_clauses.append("extension = ? COLLATE NOCASE")
            params.append(self.active_extension_filter)

        if self.folder_browser_scope:
            scope_sql, scope_params = descendant_scope_sql(self.folder_browser_scope)
            where_clauses.append(f"({scope_sql})")
            params.extend(scope_params)

        scan_root = getattr(self, 'current_scan_root', None) or os.path.normpath(self.txt_path.text().strip() or "")
        if scan_root:
            root_variants = equivalent_path_variants(scan_root)
            placeholders = ",".join("?" * len(root_variants))
            where_clauses.append(
                f"path COLLATE NOCASE NOT IN ({placeholders})"
            )
            params.extend(root_variants)

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        return where_sql, params

    def _bulk_count(self, status=None, videos_only=False):
        from src.file_index_tool import FileIndexTool
        where_sql, params = self._build_bulk_where(status=status, videos_only=videos_only)
        tool = FileIndexTool()
        try:
            cursor = tool.conn.cursor()
            cursor.execute(
                (
                    "SELECT COUNT(*), "
                    "SUM(CASE WHEN is_folder = 1 THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN is_folder = 0 THEN 1 ELSE 0 END), "
                    "COALESCE(SUM(size), 0) "
                    "FROM file_index"
                ) + where_sql,
                params,
            )
            total, folders, files, total_size = cursor.fetchone()
        finally:
            tool.close()
        return total or 0, folders or 0, files or 0, total_size or 0, where_sql, params

    def _effective_bulk_counts(self, where_sql, params, folder_delete_mode, total, folders, files, total_size):
        if folder_delete_mode != 'empty_only' or not folders:
            return total, folders, files, total_size

        from src.file_index_tool import FileIndexTool

        folder_clause = EMPTY_FOLDER_SQL
        effective_where = (where_sql + " AND " + folder_clause) if where_sql else (" WHERE " + folder_clause)
        tool = FileIndexTool()
        try:
            cursor = tool.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM file_index" + effective_where, params)
            deletable_folders = cursor.fetchone()[0] or 0
        finally:
            tool.close()

        return files + deletable_folders, deletable_folders, files, total_size

    def _bulk_folder_delete_mode(self, status=None, videos_only=False):
        if videos_only:
            return 'none'
        if status == 'Empty' or self.fp.rb_empty.isChecked():
            return 'all'
        return 'empty_only'

    def _offer_all_pages_selection(self, label, current_page_count, status=None, videos_only=False, precomputed=None):
        if not (hasattr(self, 'lbl_page_info') and self.lbl_page_info.isVisible()):
            self.bulk_delete_scope = None
            return

        if precomputed:
            total = precomputed.get('total_matches', 0)
            folders = precomputed.get('folders', 0)
            files = precomputed.get('files', 0)
            total_size = precomputed.get('total_size', 0)
            where_sql = precomputed.get('where_sql', "")
            params = precomputed.get('params', [])
        else:
            total, folders, files, total_size, where_sql, params = self._bulk_count(status=status, videos_only=videos_only)
        if total <= current_page_count:
            self.bulk_delete_scope = None
            return

        folder_delete_mode = self._bulk_folder_delete_mode(status=status, videos_only=videos_only)
        effective_total, effective_folders, effective_files, effective_size = self._effective_bulk_counts(
            where_sql,
            params,
            folder_delete_mode,
            total,
            folders,
            files,
            total_size,
        )
        safety_note = ""
        if folder_delete_mode == 'empty_only' and folders:
            safety_note = (
                "\n\nSafety: non-empty matching folders will not be deleted in all-pages mode "
                "because they may contain files that do not match the current filter."
            )

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle("Select across all pages?")
        msg.setText(f"You selected {current_page_count} {label} on this page.")
        msg.setInformativeText(
            "There are more matching items across all pages.\n\n"
            f"All pages: {total} items\n"
            f"Delete targets: {effective_total}\n"
            f"Folders: {effective_folders}\n"
            f"Files: {effective_files}\n\n"
            "Choose whether Delete Selected should apply only to this page or to every matching item."
            f"{safety_note}"
        )
        btn_current = msg.addButton("Current page only", QMessageBox.ButtonRole.RejectRole)
        btn_all = msg.addButton(f"All {total} matching", QMessageBox.ButtonRole.AcceptRole)
        msg.setDefaultButton(btn_current)
        msg.exec()

        if msg.clickedButton() == btn_all:
            self._set_all_pages_selection_scope(
                label,
                status=status,
                videos_only=videos_only,
                precomputed=precomputed,
            )
        else:
            self._promote_current_page_selection_to_page_only()
            self.bulk_delete_scope = None

    def _set_all_pages_selection_scope(self, label, status=None, videos_only=False, precomputed=None):
        if precomputed:
            total = precomputed.get('total_matches', 0)
            folders = precomputed.get('folders', 0)
            files = precomputed.get('files', 0)
            total_size = precomputed.get('total_size', 0)
            where_sql = precomputed.get('where_sql', "")
            params = precomputed.get('params', [])
        else:
            total, folders, files, total_size, where_sql, params = self._bulk_count(status=status, videos_only=videos_only)

        folder_delete_mode = self._bulk_folder_delete_mode(status=status, videos_only=videos_only)
        if precomputed and 'effective_total' in precomputed:
            effective_total = precomputed.get('effective_total', total)
            effective_folders = precomputed.get('effective_folders', folders)
            effective_files = precomputed.get('effective_files', files)
            effective_size = precomputed.get('effective_size', total_size)
        else:
            effective_total, effective_folders, effective_files, effective_size = self._effective_bulk_counts(
                where_sql,
                params,
                folder_delete_mode,
                total,
                folders,
                files,
                total_size,
            )
        self.bulk_delete_scope = {
            'label': label,
            'total': effective_total,
            'folders': effective_folders,
            'files': effective_files,
            'size': effective_size,
            'matched_total': total,
            'where_sql': where_sql,
            'params': params,
            'folder_delete_mode': folder_delete_mode,
            'status': status,
            'videos_only': videos_only,
            'excluded_paths': [],
            '_base_total': effective_total,
            '_base_folders': effective_folders,
            '_base_files': effective_files,
            '_base_size': effective_size,
            '_base_matched_total': total,
            '_excluded_items': {},
        }

    def _set_bulk_scope_page_excluded(self, indices, excluded):
        scope = self.bulk_delete_scope
        if not scope:
            return

        excluded_items = scope.setdefault('_excluded_items', {})
        folder_delete_mode = scope.get('folder_delete_mode', 'empty_only')
        for index in indices:
            item_data = self.tree_model.data(index, Qt.ItemDataRole.UserRole) or {}
            path = item_data.get('path')
            if not path:
                continue
            key = self._path_key(path)
            if excluded:
                is_folder = bool(item_data.get('is_dir', False))
                deletable = (
                    not is_folder
                    or folder_delete_mode == 'all'
                    or item_data.get('status') == 'Empty'
                )
                excluded_items[key] = {
                    'path': path,
                    'is_folder': is_folder,
                    'size': int(item_data.get('size', 0) or 0) if not is_folder else 0,
                    'deletable': deletable,
                }
            else:
                excluded_items.pop(key, None)

        scope['excluded_paths'] = [item['path'] for item in excluded_items.values()]
        excluded_deletable = [
            item for item in excluded_items.values() if item.get('deletable', True)
        ]
        scope['total'] = max(
            0,
            scope.get('_base_total', scope.get('total', 0)) - len(excluded_deletable),
        )
        scope['folders'] = max(
            0,
            scope.get('_base_folders', scope.get('folders', 0))
            - sum(1 for item in excluded_deletable if item.get('is_folder')),
        )
        scope['files'] = max(
            0,
            scope.get('_base_files', scope.get('files', 0))
            - sum(1 for item in excluded_deletable if not item.get('is_folder')),
        )
        scope['size'] = max(
            0,
            scope.get('_base_size', scope.get('size', 0))
            - sum(item.get('size', 0) for item in excluded_deletable),
        )
        scope['matched_total'] = max(
            0,
            scope.get('_base_matched_total', scope.get('matched_total', 0))
            - len(excluded_items),
        )

    def _choose_select_scope(self, label, current_page_count=0):
        if not (hasattr(self, 'lbl_page_info') and self.lbl_page_info.isVisible()):
            return 'current'

        total = self.current_total_matches or current_page_count
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle("Select across all pages?")
        msg.setText(f"You selected {current_page_count} {label} on this page.")
        msg.setInformativeText(
            "There are more matching items across all pages.\n\n"
            f"All pages: {total} items\n\n"
            "Choose whether Delete Selected should apply only to this page or to every matching item.\n"
            "If selection takes a moment, a selecting dialog will stay visible until it finishes."
        )
        btn_current = msg.addButton("Current page only", QMessageBox.ButtonRole.RejectRole)
        btn_all = msg.addButton(f"All {total} matching", QMessageBox.ButtonRole.AcceptRole)
        btn_cancel = msg.addButton("Cancel", QMessageBox.ButtonRole.DestructiveRole)
        btn_cancel.hide()
        msg.setEscapeButton(btn_cancel)
        msg.setDefaultButton(btn_current)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked == btn_all:
            return 'all'
        if clicked == btn_current:
            return 'current'
        return None

    def _begin_page_select_flow(self, label, status=None, videos_only=False, selecting=True):
        target_state = Qt.CheckState.Checked if selecting else Qt.CheckState.Unchecked
        indices = self._collect_bulk_target_indices(status=status, videos_only=videos_only)
        if not indices:
            self._refresh_selection_buttons()
            return
        scope = self._choose_select_scope(label, current_page_count=len(indices)) if selecting else 'current'
        if scope is None:
            return

        self._begin_chunked_bulk_selection(
            indices,
            target_state,
            current_page_count=len(indices),
            label=label,
            offer_all_pages=False,
            status=status,
            videos_only=videos_only,
            requested_scope=scope,
        )

    def _finish_immediate_page_selection(self, label):
        self._set_bulk_selection_busy(False)
        self._do_recount()

    def _refresh_selection_buttons(self):
        if not hasattr(self, 'btn_select_all'):
            return

        mode = self._display_mode()
        bulk_has_selection = bool(
            self.bulk_delete_scope
            and self.bulk_delete_scope.get('total', 1) > 0
        )
        has_selection = bool(
            bulk_has_selection
            or self.selected_paths
            or self.page_only_selected_paths
        )
        all_targets, inactive_targets, empty_targets = self._collect_selection_button_targets(mode)

        focused_selection_mode = mode in ('Inactive', 'Empty')
        self.btn_select_all.setVisible(not focused_selection_mode)
        self.btn_clear_selection.setVisible(False)
        self.btn_clear_selection.setEnabled(False)

        if has_selection and mode == 'Videos':
            self.btn_select_all.setText("Unselect All Videos")
        elif has_selection:
            self.btn_select_all.setText("Unselect All")
        elif mode == 'Videos':
            self.btn_select_all.setText("Select All Videos")
        else:
            self.btn_select_all.setText("Select All")

        inactive_checked = self._are_all_indices_checked(inactive_targets)
        empty_checked = self._are_all_indices_checked(empty_targets)
        inactive_has_selection = has_selection if mode == 'Inactive' else inactive_checked
        empty_has_selection = has_selection if mode == 'Empty' else empty_checked
        self.btn_select_inactive.setText(
            "Unselect All Inactive"
            if inactive_has_selection
            else "Select All Inactive"
        )
        self.btn_select_empty.setText(
            "Unselect All Empty" if empty_has_selection else "Select All Empty"
        )

        self.btn_select_all.setEnabled(
            mode in ('All', 'Videos')
            and (has_selection or bool(all_targets))
        )
        self.btn_select_inactive.setEnabled(
            mode == 'Inactive' and (inactive_has_selection or bool(inactive_targets))
        )
        empty_enabled = mode in ('All', 'Empty') and (
            empty_has_selection or bool(empty_targets)
        )
        self.btn_select_empty.setEnabled(empty_enabled)
        self.btn_select_empty.setToolTip(
            "Select every empty folder in the current view."
            if empty_enabled
            else "No empty folders are available in the current view."
        )
        self.btn_select_inactive.setVisible(mode == 'Inactive')
        self.btn_select_empty.setVisible(mode == 'Empty')
        page_checked = False
        if hasattr(self, "btn_current_page_selection"):
            page_targets = (
                inactive_targets
                if mode == "Inactive"
                else empty_targets
                if mode == "Empty"
                else all_targets
            )
            page_checked = self._are_all_indices_checked(page_targets)
            self.btn_current_page_selection.setText(
                "Unselect Current Page" if page_checked else "Select Current Page"
            )
            self.btn_current_page_selection.setToolTip(
                "Unselect every item shown on this page"
                if page_checked
                else "Select every item shown on this page"
            )
            self.btn_current_page_selection.setEnabled(bool(page_targets))

        self._set_selection_control_active(self.btn_select_all, has_selection)
        self._set_selection_control_active(
            self.btn_select_inactive,
            inactive_has_selection,
        )
        self._set_selection_control_active(self.btn_select_empty, empty_has_selection)
        if hasattr(self, "btn_current_page_selection"):
            self._set_selection_control_active(
                self.btn_current_page_selection,
                page_checked,
            )

    def _set_selection_control_active(self, button, active):
        active = bool(active)
        if button.property("selectionActive") == active:
            return
        button.setProperty("selectionActive", active)
        button.style().unpolish(button)
        button.style().polish(button)
        button.update()

    def _collect_selection_button_targets(self, mode):
        if not self.tree_model or self.proxy_model.sourceModel() is None:
            return [], [], []

        cached = self._selection_button_targets_cache
        if (
            cached
            and cached.get('model') is self.tree_model
            and cached.get('mode') == mode
        ):
            return cached['targets']

        all_targets = []
        inactive_targets = []
        empty_targets = []
        started_at = time.perf_counter() if PERF_DEBUG else None
        visited = 0
        exact_all = self.proxy_model.has_active_filters() or mode == 'Videos'
        seen_all = set()
        seen_inactive = set()
        seen_empty = set()

        def add_target(targets, seen, source_index, path):
            if path in seen:
                return
            seen.add(path)
            targets.append(source_index)

        def walk(parent=QModelIndex()):
            nonlocal visited
            for row in range(self.proxy_model.rowCount(parent)):
                proxy_index = self.proxy_model.index(row, 0, parent)
                source_index = self.proxy_model.mapToSource(proxy_index)
                visited += 1
                item_data = self.tree_model.data(source_index, Qt.ItemDataRole.UserRole)
                if item_data:
                    path = item_data.get('path')
                    if path:
                        is_exact_match = self.proxy_model.matches_source_index(source_index)
                        is_page_result = item_data.get(
                            '_is_page_result',
                            not item_data.get('_is_context_fetched', False),
                        )
                        if is_page_result:
                            if (
                                (not exact_all or is_exact_match)
                                and (mode != 'Videos' or self._is_video_item(item_data))
                            ):
                                add_target(all_targets, seen_all, source_index, path)
                            if item_data.get('status') == 'Inactive' and is_exact_match:
                                add_target(inactive_targets, seen_inactive, source_index, path)
                            if item_data.get('status') == 'Empty' and is_exact_match:
                                add_target(empty_targets, seen_empty, source_index, path)
                if self.proxy_model.hasChildren(proxy_index):
                    walk(proxy_index)

        walk()
        targets = (all_targets, inactive_targets, empty_targets)
        self._selection_button_targets_cache = {
            'model': self.tree_model,
            'mode': mode,
            'targets': targets,
        }
        _perf_log(
            "selection button target walk",
            started_at,
            nodes=visited,
            targets=sum(len(group) for group in targets),
        )
        return targets

    def _collect_checked_source_indices(self):
        if not self.tree_model:
            return []

        checked_indices = []
        stack = [QModelIndex()]
        while stack:
            parent = stack.pop()
            for row in range(self.tree_model.rowCount(parent)):
                index = self.tree_model.index(row, 0, parent)
                if self.tree_model.data(index, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked:
                    checked_indices.append(index)
                if self.tree_model.hasChildren(index):
                    stack.append(index)
        return checked_indices

    def _prune_paths(self, paths):
        return prune_contained_paths(paths, sort_alpha=True)

    def _checked_delete_paths(self):
        pruned = self._selected_roots_for_delete()
        if not pruned or not self.excluded_paths:
            return pruned
        return self._expand_selected_paths_around_exclusions(pruned)

    def _selected_roots_for_delete(self):
        combined = list(self.selected_paths.values()) + list(self.page_only_selected_paths.values())
        return self._prune_paths(combined)

    def _select_by_status(self, status):
        if not self.tree_model:
            return

        button = self.btn_select_inactive if status == 'Inactive' else self.btn_select_empty
        selecting = not button.text().startswith(("Unselect", "Deselect"))
        if not selecting:
            self._clear_all_checks()
            self._focus_results_view()
            return
        scope_status = self.bulk_delete_scope.get('status') if self.bulk_delete_scope else None
        if scope_status == status:
            indices = self._collect_bulk_target_indices(status=status)
            self._set_bulk_scope_page_excluded(indices, False)
            self._begin_chunked_bulk_selection(
                indices,
                Qt.CheckState.Checked,
                current_page_count=len(indices),
                label=f"{status.lower()} items",
                offer_all_pages=False,
                status=status,
                videos_only=False,
                requested_scope="bulk_current",
            )
            return
        self._begin_page_select_flow(
            label=f"{status.lower()} items",
            status=status,
            selecting=selecting,
        )

    def _select_inactive(self):
        self._select_by_status('Inactive')

    def _select_empty(self):
        self._select_by_status('Empty')

    def _cleanup_bulk_select_thread(self, thread):
        if thread is self.bulk_select_thread:
            self.bulk_select_thread = None

    def _cancel_running_bulk_select_thread(self):
        self.bulk_select_request_id += 1
        if self.bulk_select_thread and self.bulk_select_thread.isRunning():
            self.bulk_select_thread.cancel()
        if self.bulk_select_active:
            self.bulk_select_active = False
            self.bulk_select_queue = []
            self.bulk_select_offer_all_pages = False
            self.bulk_select_current_page_count = 0
            self.bulk_select_label = ""
            self.bulk_select_status = None
            self.bulk_select_videos_only = False
            self.bulk_select_precomputed_scope = None
            self.bulk_select_requested_scope = 'current'
            self.bulk_select_forced_state = None
            self._set_bulk_selection_busy(False)

    def _set_bulk_selection_busy(self, busy, label=""):
        self.bulk_select_active = busy
        if hasattr(self, 'fp'):
            self.fp.setEnabled(not busy)
        self._set_delete_controls_enabled(not busy)
        if busy:
            self.bulk_select_label = label
            self._show_selection_loading_dialog(label)
        else:
            self._hide_selection_loading_dialog()

    def _rebuild_and_restore_page_selection(self):
        self.source_index_by_path = {}
        self._selection_button_targets_cache = None
        if not self.tree_model:
            return

        started_at = time.perf_counter() if PERF_DEBUG else None
        restore_by_key = {}
        all_targets = []
        inactive_targets = []
        empty_targets = []
        seen_all = set()
        seen_inactive = set()
        seen_empty = set()
        mode = self._display_mode()
        exact_all = self.proxy_model.has_active_filters() or mode == 'Videos'
        bulk_scope = self.bulk_delete_scope
        has_persistent_selection = bool(self.selected_paths)
        bulk_status = bulk_scope.get('status') if bulk_scope else None
        bulk_videos_only = bool(bulk_scope and bulk_scope.get('videos_only', False))
        bulk_excluded_keys = bulk_scope_excluded_keys(bulk_scope)
        bulk_exact_only = bool(
            bulk_scope
            and (
                self.proxy_model.has_active_filters()
                or bulk_status is not None
                or bulk_videos_only
            )
        )
        visited = 0

        def add_target(targets, seen, index, path):
            if path in seen:
                return
            seen.add(path)
            targets.append(index)

        stack = [QModelIndex()]
        while stack:
            parent = stack.pop()
            for row in range(self.tree_model.rowCount(parent)):
                index = self.tree_model.index(row, 0, parent)
                visited += 1
                item_data = self.tree_model.data(index, Qt.ItemDataRole.UserRole)
                if item_data and item_data.get('path'):
                    path = item_data['path']
                    path_key = self._path_key(path)
                    self.source_index_by_path[path_key] = index
                    persistable = (
                        self._is_persistable_selection_index(index)
                        if has_persistent_selection or bulk_scope else False
                    )
                    if (
                        has_persistent_selection
                        and persistable
                        and self._is_effectively_selected(path)
                    ):
                        restore_by_key[path_key] = index

                    proxy_index = self.proxy_model.mapFromSource(index)
                    if proxy_index.isValid():
                        is_exact_match = self.proxy_model.matches_source_index(index)
                        is_page_result = item_data.get(
                            '_is_page_result',
                            not item_data.get('_is_context_fetched', False),
                        )
                        is_video = self._is_video_item(item_data)
                        if is_page_result:
                            if (
                                (not exact_all or is_exact_match)
                                and (mode != 'Videos' or is_video)
                            ):
                                add_target(all_targets, seen_all, index, path)
                            if item_data.get('status') == 'Inactive' and is_exact_match:
                                add_target(inactive_targets, seen_inactive, index, path)
                            if item_data.get('status') == 'Empty' and is_exact_match:
                                add_target(empty_targets, seen_empty, index, path)

                            if (
                                bulk_scope
                                and persistable
                                and path_key not in bulk_excluded_keys
                                and (bulk_status is None or item_data.get('status') == bulk_status)
                                and (not bulk_videos_only or is_video)
                                and (not bulk_exact_only or is_exact_match)
                            ):
                                restore_by_key[path_key] = index
                if self.tree_model.hasChildren(index):
                    stack.append(index)

        targets = (all_targets, inactive_targets, empty_targets)
        self._selection_button_targets_cache = {
            'model': self.tree_model,
            'mode': mode,
            'targets': targets,
        }
        restore_indices = list(restore_by_key.values())
        if restore_indices:
            self._set_indices_check_state_direct_suppressed(
                restore_indices,
                Qt.CheckState.Checked,
                explicit=True,
            )
        _perf_log(
            "page selection preparation",
            started_at,
            nodes=visited,
            restored=len(restore_indices),
            button_targets=sum(len(group) for group in targets),
        )

    def _snapshot_bulk_candidates(self):
        candidates = []
        if not self.tree_model or self.proxy_model.sourceModel() is None:
            return candidates

        def walk(parent=QModelIndex()):
            for row in range(self.proxy_model.rowCount(parent)):
                proxy_index = self.proxy_model.index(row, 0, parent)
                source_index = self.proxy_model.mapToSource(proxy_index)
                item_data = self.tree_model.data(source_index, Qt.ItemDataRole.UserRole) or {}
                path = item_data.get('path')
                if not path:
                    continue
                candidates.append({
                    'path': path,
                    'status': item_data.get('status'),
                    'checked': self.tree_model.data(source_index, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked,
                    'is_video': self._is_video_item(item_data),
                    'is_page_result': item_data.get('_is_page_result', not item_data.get('_is_context_fetched', False)),
                    'is_exact_match': self.proxy_model.matches_source_index(source_index),
                })
                if self.proxy_model.hasChildren(proxy_index):
                    walk(proxy_index)

        walk()
        return candidates

    def _paths_to_indices(self, paths):
        indices = []
        seen = set()
        for path in paths:
            key = self._path_key(path)
            if key in seen:
                continue
            seen.add(key)
            index = self.source_index_by_path.get(key)
            if index and index.isValid():
                indices.append(index)
        return indices

    def _begin_chunked_bulk_selection(self, indices, state, current_page_count=0, label="items", offer_all_pages=False, status=None, videos_only=False, requested_scope='ask_after'):
        if not indices:
            self.bulk_delete_scope = None
            self._set_bulk_selection_busy(False)
            return

        self.recount_timer.stop()
        self.bulk_select_active = True
        self.bulk_select_queue = list(indices)
        self.bulk_select_state = Qt.CheckState(state)
        self.bulk_select_current_page_count = current_page_count
        self.bulk_select_offer_all_pages = offer_all_pages
        self.bulk_select_label = label
        self.bulk_select_status = status
        self.bulk_select_videos_only = videos_only
        self.bulk_select_requested_scope = requested_scope
        self._set_bulk_selection_busy(True, label)
        QTimer.singleShot(0, self._apply_bulk_selection_batch)

    def _apply_bulk_selection_batch(self):
        if not self.bulk_select_active or not self.tree_model:
            return

        batch = self.bulk_select_queue[:self.bulk_select_batch_size]
        self.bulk_select_queue = self.bulk_select_queue[self.bulk_select_batch_size:]
        if batch:
            self.tree_model.set_indices_check_state_direct(batch, self.bulk_select_state, explicit=True)

        if self.bulk_select_queue:
            QTimer.singleShot(self.bulk_select_batch_delay_ms, self._apply_bulk_selection_batch)
        else:
            self._finish_bulk_selection()

    def _finish_bulk_selection(self):
        if not self.bulk_select_active:
            return

        self.bulk_select_active = False
        self.bulk_select_queue = []
        self._sync_persistent_selection_from_model()
        if self.bulk_select_state == Qt.CheckState.Checked:
            if self.bulk_select_requested_scope == 'all':
                self._start_page_bulk_select_async(
                    status=self.bulk_select_status,
                    videos_only=self.bulk_select_videos_only,
                    label=self.bulk_select_label,
                    requested_scope='scope_only',
                )
                return
            elif self.bulk_select_requested_scope == 'ask_after' and self.bulk_select_offer_all_pages:
                self._offer_all_pages_selection(
                    label=self.bulk_select_label,
                    current_page_count=self.bulk_select_current_page_count,
                    status=self.bulk_select_status,
                    videos_only=self.bulk_select_videos_only,
                    precomputed=self.bulk_select_precomputed_scope,
                )
            elif self.bulk_select_requested_scope == 'ask_after':
                self._promote_current_page_selection_to_page_only()
            elif self.bulk_select_requested_scope == 'bulk_current':
                pass
            else:
                self.bulk_delete_scope = None
        else:
            self.bulk_delete_scope = None
        self._set_bulk_selection_busy(False)
        self._do_recount()
        self._focus_results_view()
        self.bulk_select_precomputed_scope = None
        self.bulk_select_forced_state = None
        self.bulk_select_requested_scope = 'current'

    def _start_page_bulk_select_async(self, status=None, videos_only=False, label="items", requested_scope='current', forced_state=None):
        if self.bulk_select_thread and self.bulk_select_thread.isRunning():
            return

        where_sql, params = self._build_bulk_where(status=status, videos_only=videos_only)

        options = self._page_load_options()
        options['limit'] = 2000
        options['offset'] = self.current_page * options['limit']
        options['folder_delete_mode'] = self._bulk_folder_delete_mode(status=status, videos_only=videos_only)

        self.bulk_select_request_id += 1
        request_id = self.bulk_select_request_id
        self.bulk_select_label = label
        self.bulk_select_status = status
        self.bulk_select_videos_only = videos_only
        self.bulk_select_requested_scope = requested_scope
        self.bulk_select_forced_state = forced_state
        self._set_bulk_selection_busy(True, label)

        thread = BulkPageSelectThread(request_id, options, where_sql, params, parent=self)
        self.bulk_select_thread = thread
        thread.bulk_ready.connect(self._on_bulk_page_select_ready)
        thread.bulk_failed.connect(self._on_bulk_page_select_failed)
        thread.finished.connect(lambda thread=thread: self._cleanup_bulk_select_thread(thread))
        thread.start()

    def _on_bulk_page_select_ready(self, request_id, result):
        if request_id != self.bulk_select_request_id:
            return

        if self.bulk_select_requested_scope == 'scope_only':
            self.bulk_select_precomputed_scope = result
            self._set_all_pages_selection_scope(
                self.bulk_select_label,
                status=self.bulk_select_status,
                videos_only=self.bulk_select_videos_only,
                precomputed=result,
            )
            self._set_bulk_selection_busy(False)
            self._do_recount()
            self._focus_results_view()
            self.bulk_select_precomputed_scope = None
            self.bulk_select_forced_state = None
            self.bulk_select_requested_scope = 'current'
            return

        paths = result.get('paths', [])
        if not paths:
            self._set_bulk_selection_busy(False)
            self.bulk_select_precomputed_scope = None
            self.bulk_select_forced_state = None
            self.bulk_select_requested_scope = 'current'
            self._do_recount()
            self._focus_results_view()
            return

        if self.bulk_select_forced_state is not None:
            target_state = Qt.CheckState(self.bulk_select_forced_state)
        else:
            all_checked = all(self._is_effectively_selected(path) for path in paths)
            target_state = Qt.CheckState.Unchecked if all_checked else Qt.CheckState.Checked
        indices = self._paths_to_indices(result.get('paths', []))
        self.bulk_select_precomputed_scope = result
        self._begin_chunked_bulk_selection(
            indices,
            target_state,
            current_page_count=result.get('current_page_count', len(indices)),
            label=self.bulk_select_label,
            offer_all_pages=(target_state == Qt.CheckState.Checked),
            status=self.bulk_select_status,
            videos_only=self.bulk_select_videos_only,
            requested_scope=self.bulk_select_requested_scope,
        )

    def _on_bulk_page_select_failed(self, request_id, error):
        if request_id != self.bulk_select_request_id:
            return

        self._set_bulk_selection_busy(False)
        self.bulk_select_precomputed_scope = None
        self.bulk_select_forced_state = None
        self.bulk_select_requested_scope = 'current'
        self.lbl_status.setText("Selection failed.")
        self._do_recount()
        QMessageBox.critical(self, "Selection Error", error)

    def _on_checked(self, tl=None, br=None, roles=None):
        if roles is None or Qt.ItemDataRole.CheckStateRole in roles:
            if self.bulk_select_active or self._selection_sync_suppressed:
                return
            self._sync_persistent_selection_from_model()
            self._set_size_totals_pending(selected=True)
            self.recount_timer.start(80)

    def _do_recount(self):
        started_at = time.perf_counter() if PERF_DEBUG else None
        if not self.tree_model:
            self._update_delete_button_copy()
            self._set_delete_armed(False)
            self.btn_delete.setEnabled(False)
            self._refresh_selection_buttons()
            _perf_log("selection recount", started_at, roots=0)
            return
        if self.bulk_delete_scope:
            scope = self.bulk_delete_scope
            armed = scope['total'] > 0
            self._update_delete_button_copy(scope['total'], all_pages=True)
            self._set_delete_armed(armed)
            self.btn_delete.setEnabled(armed)
            self._refresh_selection_buttons()
            self._update_chips_sql()
            _perf_log("selection recount", started_at, roots=scope['total'])
            return
        selected_paths = self._selected_roots_for_delete()
        total = len(selected_paths)
        self._update_delete_button_copy(total)
        armed = total > 0
        self._set_delete_armed(armed)
        self.btn_delete.setEnabled(armed)
        self._set_size_totals_pending(selected=True)
        self._refresh_selection_buttons()
        self._update_chips_sql()
        _perf_log("selection recount", started_at, roots=total)

    def _set_delete_armed(self, armed):
        self.btn_delete.setProperty("armed", bool(armed))
        icon_path = self._delete_icon_active_path if armed else self._delete_icon_path
        self.btn_delete.setIcon(QIcon(icon_path))
        self._update_delete_button_layout()
        self.btn_delete.style().unpolish(self.btn_delete)
        self.btn_delete.style().polish(self.btn_delete)
        self.btn_delete.update()
