import os

from PyQt6.QtCore import (
    QModelIndex,
    Qt,
)
from PyQt6.QtWidgets import (
    QDialog,
    QMessageBox,
)

from ...auth import append_delete_audit
from ..dialogs.delete import DeleteAuthDialog, DeletePreviewDialog, DeleteProgressDialog
from ..path_utils import IndexedPathDict, PathKeyIndex, _path_key, bulk_scope_excluded_keys
from ..sql_utils import case_insensitive_path_sql, descendant_like_sql, folder_is_physically_empty, summarize_paths_batch
from ..workers.delete import DeleteThread
from ..workers.delete_preview import DeletePreviewThread


class _DeleteMixin:
    def _expand_selected_paths_around_exclusions(self, selected_roots):
        from src.file_index_tool import FileIndexTool

        expanded = []
        tool = FileIndexTool()
        try:
            cursor = tool.conn.cursor()
            for root_path in selected_roots:
                root_key = self._path_key(root_path)
                if (
                    root_key not in self.excluded_paths
                    and not self.excluded_paths.path_index.has_descendant(root_key)
                ):
                    expanded.append(root_path)
                    continue

                descendant_sql, descendant_params = descendant_like_sql(root_path)
                cursor.execute(
                    f"""
                    SELECT path, is_folder
                    FROM file_index
                    WHERE {case_insensitive_path_sql()} OR ({descendant_sql})
                    ORDER BY length(path) ASC, lower(path) ASC
                    """,
                    (root_path, *descendant_params),
                )
                for path, is_folder in cursor.fetchall():
                    key = self._path_key(path)
                    if self._has_excluded_ancestor(path):
                        continue
                    if is_folder and self._has_excluded_descendant(path):
                        continue
                    expanded.append(path)
        finally:
            tool.close()

        return self._prune_paths(expanded)

    def _summarize_delete_paths(self, paths):
        from src.file_index_tool import FileIndexTool

        pruned_paths = self._prune_paths(paths)
        if not pruned_paths:
            return [], 0, 0, 0

        tool = FileIndexTool()
        try:
            cursor = tool.conn.cursor()
            pruned_paths, total_folders, total_files, total_size = summarize_paths_batch(cursor, pruned_paths)
        finally:
            tool.close()

        return pruned_paths, total_folders, total_files, total_size

    def _count_selected_path_contents(self, cursor, path):
        cursor.execute(
            f"SELECT is_folder FROM file_index WHERE {case_insensitive_path_sql()}",
            (path,),
        )
        row = cursor.fetchone()
        if not row:
            return (1, 0) if os.path.isdir(path) else (0, 1)

        is_folder = row[0]
        if not is_folder:
            return 0, 1

        descendant_sql, descendant_params = descendant_like_sql(path)
        cursor.execute(
            f"""
            SELECT
                COALESCE(SUM(CASE WHEN is_folder = 1 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN is_folder = 0 THEN 1 ELSE 0 END), 0)
            FROM file_index
            WHERE {case_insensitive_path_sql()} OR ({descendant_sql})
            """,
            (path, *descendant_params),
        )
        folders, files = cursor.fetchone()
        return folders or 0, files or 0

    def _checked_delete_summary(self):
        """Return delete targets and effective folder/file totals."""
        target_paths = self._checked_delete_paths()
        return self._summarize_delete_paths(target_paths)

    def _remove_deleted_paths_from_selection(self, deleted_paths):
        if not deleted_paths:
            return

        deleted_keys = [self._path_key(path) for path in deleted_paths if path]
        if not deleted_keys:
            return
        deleted_path_index = PathKeyIndex(deleted_keys)

        def is_deleted(path):
            return deleted_path_index.has_ancestor(self._path_key(path))

        self.selected_paths = IndexedPathDict({
            key: path for key, path in self.selected_paths.items()
            if not is_deleted(path)
        })
        self.page_only_selected_paths = {
            key: path for key, path in self.page_only_selected_paths.items()
            if not is_deleted(path)
        }
        self.excluded_paths = IndexedPathDict({
            key: path for key, path in self.excluded_paths.items()
            if not is_deleted(path)
        })

    def _show_delete_preview(self, paths=None, bulk_scope=None):
        dialog = DeletePreviewDialog(self)
        dialog.preview_mode = 'bulk' if bulk_scope else 'direct'
        if bulk_scope:
            thread = DeletePreviewThread(
                bulk_scope=bulk_scope,
                parent=self,
            )
        else:
            thread = DeletePreviewThread(
                paths or [],
                excluded_paths=self.excluded_paths,
                parent=self,
            )

        self.delete_preview_thread = thread
        thread.preview_ready.connect(dialog.apply_preview)
        thread.preview_failed.connect(dialog.show_error)
        dialog.finished.connect(thread.cancel)
        thread.start()

        self.pending_delete_preview_dialog = dialog
        dialog.finished.connect(lambda *_: setattr(self, 'pending_delete_preview_dialog', None))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        result = dialog.exec()
        thread.cancel()
        if thread.isRunning():
            thread.wait()
        if self.delete_preview_thread is thread:
            self.delete_preview_thread = None
        if result == QDialog.DialogCode.Accepted:
            payload = dict(dialog.preview_payload or {})
            payload['paths'] = list(dialog.preview_paths or [])
            return payload
        return None

    def _build_direct_delete_preview(self, paths):
        pruned_paths = self._prune_paths(paths)
        folder_count = 0
        file_count = 0
        total_size = 0
        if pruned_paths:
            from src.file_index_tool import FileIndexTool

            tool = FileIndexTool()
            try:
                cursor = tool.conn.cursor()
                _, folder_count, file_count, total_size = summarize_paths_batch(cursor, pruned_paths)
            finally:
                tool.close()
        return {
            'paths': pruned_paths,
            'total': len(pruned_paths),
            'folders': folder_count,
            'files': file_count,
            'size': total_size,
        }

    def _current_page_file_paths(self):
        if not self.tree_model or self.proxy_model.sourceModel() is None:
            return []

        paths = []
        exact_only = self.proxy_model.has_active_filters()
        view_mode = self.fp.get_view_mode()

        def walk(parent=QModelIndex()):
            for row in range(self.proxy_model.rowCount(parent)):
                proxy_index = self.proxy_model.index(row, 0, parent)
                source_index = self.proxy_model.mapToSource(proxy_index)
                item_data = self.tree_model.data(source_index, Qt.ItemDataRole.UserRole)
                if item_data and item_data.get('path'):
                    is_page_result = item_data.get(
                        '_is_page_result',
                        not item_data.get('_is_context_fetched', False),
                    )
                    is_exact_match = self.proxy_model.matches_source_index(source_index)
                    is_chargeable = is_page_result and (not exact_only or is_exact_match)
                    if is_chargeable:
                        paths.append(item_data['path'])
                        if view_mode == 'Tree' and item_data.get('is_dir', False):
                            continue

                if view_mode == 'Tree' and self.proxy_model.hasChildren(proxy_index):
                    walk(proxy_index)

        walk()
        return list(dict.fromkeys(paths))

    def _path_subtree_size(self, cursor, path):
        cursor.execute(
            f"SELECT is_folder, size FROM file_index WHERE {case_insensitive_path_sql()}",
            (path,),
        )
        row = cursor.fetchone()
        if not row:
            return 0

        is_folder, size = row
        if not is_folder:
            return size or 0

        descendant_sql, descendant_params = descendant_like_sql(path)
        cursor.execute(
            f"SELECT COALESCE(SUM(size), 0) FROM file_index WHERE is_folder = 0 AND ({descendant_sql})",
            descendant_params,
        )
        return cursor.fetchone()[0] or 0

    def _sum_paths_total_size(self, paths):
        from src.file_index_tool import FileIndexTool

        pruned_paths = self._prune_paths(paths)
        if not pruned_paths:
            return 0

        tool = FileIndexTool()
        cursor = tool.conn.cursor()
        try:
            _, _, _, total_size = summarize_paths_batch(cursor, pruned_paths)
        finally:
            tool.close()
        return total_size

    def _browse_folder_total_size(self, cursor):
        root_path = getattr(self, 'current_scan_root', None) or os.path.normpath(self.txt_path.text().strip() or "")
        if not root_path:
            return 0

        normalized = os.path.normpath(root_path).rstrip("\\/")
        cursor.execute(
            "SELECT total_size FROM folder_summary WHERE path = ? COLLATE NOCASE",
            (normalized,),
        )
        summary = cursor.fetchone()
        if summary:
            return summary[0] or 0

        descendant_sql, descendant_params = descendant_like_sql(normalized)
        cursor.execute(
            f"SELECT COALESCE(SUM(size), 0) FROM file_index WHERE is_folder = 0 AND ({case_insensitive_path_sql()} OR {descendant_sql})",
            (normalized, *descendant_params),
        )
        return cursor.fetchone()[0] or 0

    def _remove_path_from_db(self, path):
        from src.file_index_tool import FileIndexTool
        tool = FileIndexTool()
        try:
            tool.conn.execute(
                f"DELETE FROM file_index WHERE {case_insensitive_path_sql()}",
                (path,),
            )
            descendant_sql, descendant_params = descendant_like_sql(path)
            tool.conn.execute(
                f"DELETE FROM file_index WHERE {descendant_sql}",
                descendant_params,
            )
            tool.conn.commit()
        finally:
            tool.close()

    def _delete_one(self, path):
        r = QMessageBox.question(self, "Confirm Delete",
            f"Send to Recycle Bin?\n\n{path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if r == QMessageBox.StandardButton.Yes:
            try:
                _paths, _folders, _files, total_size = self._summarize_delete_paths([path])
            except Exception:
                total_size = 0
            authorized_by = self._authorize_delete(1, total_size)
            if authorized_by:
                self._start_delete([path], authorized_by=authorized_by, audit_size=total_size)

    def _bulk_delete_paths(self):
        if not self.bulk_delete_scope:
            return []

        from src.file_index_tool import FileIndexTool
        tool = FileIndexTool()
        cursor = tool.conn.cursor()
        try:
            cursor.execute(
                "SELECT path, is_folder FROM file_index" + self.bulk_delete_scope['where_sql'],
                self.bulk_delete_scope['params'],
            )
            rows = cursor.fetchall()

            paths = []
            folder_delete_mode = self.bulk_delete_scope.get('folder_delete_mode', 'empty_only')
            excluded_keys = bulk_scope_excluded_keys(self.bulk_delete_scope)
            for path, is_folder in rows:
                if self._path_key(path) in excluded_keys:
                    continue
                if not is_folder:
                    paths.append(path)
                elif folder_delete_mode == 'all':
                    paths.append(path)
                elif folder_delete_mode == 'empty_only':
                    if folder_is_physically_empty(cursor, path):
                        paths.append(path)
        finally:
            tool.close()
        return self._prune_paths(paths)

    def _short_path_for_progress(self, path):
        return ("..." + path[-96:]) if len(path) > 100 else path

    def _set_delete_controls_enabled(self, enabled):
        if not enabled:
            self.btn_delete.setEnabled(False)
        self.btn_rescan.setEnabled(enabled)
        self.btn_folder_browser.setEnabled(enabled)
        self.btn_filter.setEnabled(enabled)
        if hasattr(self, 'btn_file_types'):
            self.btn_file_types.setEnabled(enabled and self._has_completed_scan_context())
        self.controls_bar.setEnabled(enabled)
        self.tree.setEnabled(enabled)
        self.folder_browser.setEnabled(enabled)

    def _refresh_folder_browser_after_delete(self, deleted_paths):
        if not deleted_paths or not self.folder_browser.root_path:
            return
        self.folder_browser_request_id += 1
        self.folder_browser_threads.clear()
        deleted_keys = [_path_key(path) for path in deleted_paths if path]
        deleted_index = PathKeyIndex(deleted_keys)
        if (
            self.folder_browser_scope
            and deleted_index.has_ancestor(_path_key(self.folder_browser_scope))
        ):
            self.folder_browser.select_root()
            self._set_folder_browser_scope(None, reload=False)
        self.folder_browser.remove_paths(deleted_paths)
        parent_paths = [
            os.path.dirname(os.path.normpath(path))
            for path in deleted_paths
            if path
        ]
        self.folder_browser.refresh_paths(parent_paths)

    def _authorize_delete(self, item_count, total_size=0):
        dialog = DeleteAuthDialog(item_count, total_size, self)
        if dialog.store.is_empty():
            QMessageBox.warning(
                self,
                "Delete authorization",
                "No authorized users are configured for this installation. Contact your administrator.",
            )
            return None

        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.authorized_username
        return None

    def _start_delete(self, paths, authorized_by=None, audit_size=0):
        if not paths:
            return
        if not authorized_by:
            QMessageBox.warning(
                self,
                "Delete authorization",
                "Delete authorization is required before items can be deleted.",
            )
            return

        self.delete_authorized_by = authorized_by
        self.delete_audit_items = len(paths)
        self.delete_audit_size = int(audit_size or 0)
        self._set_delete_controls_enabled(False)
        self.lbl_status.setText(f"Deleting 0 of {len(paths)}...")

        self.delete_progress = DeleteProgressDialog(len(paths), self)

        self.delete_thread = DeleteThread(paths)
        self.delete_thread.delete_progress.connect(self._on_delete_progress)
        self.delete_thread.delete_finished.connect(self._on_delete_finished)
        self.delete_progress.cancel_requested.connect(self.delete_thread.cancel)
        self.delete_thread.start()
        self.delete_progress.show()

    def _on_delete_progress(self, done, total, path):
        if self.delete_progress:
            self.delete_progress.update_progress(done, total, path)
        self.lbl_status.setText(f"Deleting {done} of {total}...")

    def _on_delete_finished(self, deleted_count, errors, cancelled, deleted_paths):

        if self.delete_progress:
            self.delete_progress._allow_close = True
            self.delete_progress.hide()
            self.delete_progress.close()
            self.delete_progress = None

        self.delete_thread = None
        self.bulk_delete_scope = None
        append_delete_audit(
            "delete_completed",
            username=getattr(self, 'delete_authorized_by', "UNKNOWN"),
            items=getattr(self, 'delete_audit_items', deleted_count),
            size=getattr(self, 'delete_audit_size', None),
            errors=len(errors or []),
        )
        self.delete_authorized_by = None
        self.delete_audit_items = 0
        self.delete_audit_size = 0
        if deleted_paths:
            if self.folder_cache is not None:
                for path in deleted_paths:
                    self.folder_cache.remove_path(path)
                updated_total = (
                    getattr(self.folder_cache, 'running_total_size', 0) or 0
                )
                root_path = getattr(self, 'current_scan_root', None)
                self.cached_folder_total = updated_total
                self.cached_folder_total_root = (
                    _path_key(root_path) if root_path else None
                )
                set_total_chip = getattr(
                    self,
                    '_set_total_summary_chip',
                    None,
                )
                if callable(set_total_chip):
                    set_total_chip(updated_total)
                update_scoped_size = getattr(
                    self,
                    '_update_scoped_folder_size_from_cache',
                    None,
                )
                if callable(update_scoped_size):
                    update_scoped_size()
            self._remove_deleted_paths_from_selection(deleted_paths)
            if hasattr(self, '_refresh_folder_browser_after_delete'):
                self._refresh_folder_browser_after_delete(deleted_paths)
        self._set_delete_controls_enabled(True)
        self._load_page()
        if deleted_paths:
            self._start_totals_refresh()

        if cancelled:
            title = "Deletion stopped"
            message = f"Deleted {deleted_count} item(s) before stopping."
        else:
            title = "Deletion complete"
            message = f"Deleted {deleted_count} item(s)."

        if errors:
            message += f"\n\nFailed: {len(errors)}"

        if self._should_show_completion_notification(
            elapsed_secs=None,
            cancelled=cancelled,
        ):
            self._show_system_notification(title, message)

        if errors:
            QMessageBox.warning(self, title, message + "\n\n" + "\n".join(errors[:10]))
        else:
            QMessageBox.information(self, title, message)

    def _delete_selected(self):
        if not self.tree_model:
            return
        if self.bulk_delete_scope:
            scope = self.bulk_delete_scope
            preview = self._show_delete_preview(bulk_scope=scope)
            if preview:
                paths = preview.get('paths', [])
                total_size = preview.get('size', 0) or 0
                item_count = preview.get('delete_operations', len(paths))
                authorized_by = self._authorize_delete(item_count, total_size)
                if authorized_by:
                    self._start_delete(paths, authorized_by=authorized_by, audit_size=total_size)
            return

        paths = self._selected_roots_for_delete()
        if not paths:
            QMessageBox.information(self, "Delete", "No items selected.")
            return
        preview = self._show_delete_preview(paths=paths)
        if preview:
            preview_paths = preview.get('paths', [])
            total_size = preview.get('size', 0) or 0
            item_count = preview.get('delete_operations', len(preview_paths))
            authorized_by = self._authorize_delete(item_count, total_size)
            if authorized_by:
                self._start_delete(preview_paths, authorized_by=authorized_by, audit_size=total_size)
