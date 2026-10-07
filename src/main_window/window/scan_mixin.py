import os
import time
from datetime import datetime

from PyQt6.QtCore import (
    QSignalBlocker,
    QTimer,
    Qt,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHeaderView,
    QMessageBox,
)

from ...models import StorageScoutTreeModel
from ...scan_exclusions import ScanExclusions
from ...scan_history import get_scan_history, record_scan_history
from ...scanner import ScannerThread
from ..constants import EMPTY_FOLDER_SQL, VIDEO_EXTENSIONS
from ..dialogs.loading import LoadingDialog
from ..path_utils import _path_key
from ..sql_utils import descendant_like_sql
from ..workers.page_load import PageLoadThread
from ..workers.selection import PathSizeThread
from ..workers.totals import TotalsThread
from .base import _BaseMixin


class _ScanMixin:
    def _set_page_controls_visible(self, visible):
        for widget in (
            getattr(self, 'btn_current_page_selection', None),
            getattr(self, 'btn_prev_page', None),
            getattr(self, 'lbl_page_info', None),
            getattr(self, 'btn_next_page', None),
        ):
            if widget is not None:
                widget.setVisible(visible)

    def _update_page_controls(self, total_matches):
        if not hasattr(self, 'lbl_page_info'):
            return

        limit = 2000
        has_multiple_pages = total_matches > limit and not self.current_lazy_show_all_tree
        self._set_page_controls_visible(has_multiple_pages)
        if not has_multiple_pages:
            return

        offset = self.current_page * limit
        end = min(offset + limit, total_matches)
        self.lbl_page_info.setText(f"{offset + 1}-{end} of {total_matches}")
        self.btn_prev_page.setEnabled(self.current_page > 0)
        self.btn_next_page.setEnabled(offset + limit < total_matches)

    def _reset_view(self):
        """Clear all displayed data and return to the empty/welcome page."""
        # Stop any running scan first
        if self.scanner_thread and self.scanner_thread.isRunning():
            self.scanner_thread.cancel()
        self._cancel_running_bulk_select_thread()

        # Clear the tree model
        self.tree_model = None
        self.proxy_model.setSourceModel(None)
        self.bulk_delete_scope = None
        self.page_load_request_id += 1
        self.scan_refresh_timer.stop()
        self._hide_loading_dialog()
        self._cancel_running_totals_thread()
        self.totals_refresh_pending = False
        self.totals_refresh_options = None
        self.selected_paths.clear()
        self.page_only_selected_paths.clear()
        self.excluded_paths.clear()
        self.page_only_selection_page = None
        self.source_index_by_path = {}
        self.lazy_child_request_id += 1
        self.current_lazy_show_all_tree = False
        self.current_filesystem_scope_fallback = False
        self.refresh_tree_state_key = None
        self.refresh_collapsed_tree_paths = set()
        self.folder_cache = None
        self.current_scan_root = None
        self.active_extension_filter = None
        self.applied_scan_exclusions = None
        self.scan_exclusions_in_progress = None
        self._update_type_filter_ui()
        self._reset_folder_browser(None)

        # Reset pagination state
        self.current_page = 0
        self.current_total_matches = 0
        self.is_scanning  = False
        self.has_completed_scan = False
        self.hide_partial_scan_results = False
        self.cached_folder_total = None
        self.cached_folder_total_root = None
        self.totals_request_id += 1

        # Reset status chips
        self.chip_empty.setText("Empty: 0 items")
        self.chip_inactive_folders.setText("Inactive: 0 folders · 0 files")
        self._set_total_summary_chip("--")
        self._set_chip_text(self.chip_page_size, "Current Page Size: --")
        self._set_selected_summary_chip("0 B", 0, 0)
        self._update_status_metrics_visibility()

        # Hide controls, show empty page
        self.controls_bar.setVisible(False)
        self.content_stack.setCurrentIndex(0)
        self.lbl_status.setText("Ready - select a folder and click Re-scan")
        self._update_file_types_enabled()
        self._update_exclusions_indicator()

    def _browse(self):
        # Use native Windows Explorer dialog
        start_dir = self.txt_path.text().strip() or ""
        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder", start_dir
        )
        if folder:
            # Clear any previous scan results immediately
            self._reset_view()

            # Preserve UNC paths; normpath mangles \\server to \server
            if folder.startswith("//") or folder.startswith("\\\\"):
                self.txt_path.setText(folder.replace("/", "\\"))
            else:
                self.txt_path.setText(os.path.normpath(folder))
            self._apply_default_browse_preset()
            self.fp.setVisible(True)
            self.btn_filter.setChecked(True)
            # Automatically start a scan once the user has selected a folder
            self.start_scan()

    def start_scan(self):
        # If currently scanning, this button acts as a Stop button
        if self.scanner_thread and self.scanner_thread.isRunning():
            self.scanner_thread.cancel()
            self.lbl_status.setText("Stopping scan...")
            self.btn_rescan.setEnabled(False)
            return

        path = self.txt_path.text().strip()
        if not path:
            self.lbl_status.setText("Please enter a folder path.")
            return
        
        # Normalize slashes but preserve UNC prefix (\\server\share)
        if path.startswith("\\\\") or path.startswith("//"):
            # UNC path - keep as-is but normalize forward slashes to back
            path = path.replace("/", "\\")
        else:
            path = os.path.normpath(path)

        # Probe the path - use scandir which works reliably for both local and UNC
        try:
            with os.scandir(path):
                pass   # path is accessible
        except PermissionError:
            self.lbl_status.setText(f"Access denied: {path}")
            return
        except Exception:
            self.lbl_status.setText(f"Path not found or not accessible: {path}")
            return
        
        # Never publish partial rows during either an initial scan or a re-scan.
        # The completed index is exposed once from _on_scan_done().
        self.hide_partial_scan_results = True

        self.current_page = 0
        self.total_scanned = 0
        self.last_scan_excluded_count = 0
        self.last_scan_elapsed_secs = 0.0
        self.last_scan_item_count = 0
        self.scan_progress_was_determinate = False
        self.is_scanning = True
        self.active_extension_filter = None
        self._update_type_filter_ui()
        self.current_scan_root = os.path.normcase(os.path.normpath(path))
        self.cached_folder_total = None
        self.cached_folder_total_root = self.current_scan_root
        self.totals_request_id += 1
        self._cancel_running_totals_thread()

        self.lbl_status.setText("Scanning...")
        self._set_rescan_stop_ui()
        self._set_size_totals_pending(browse=True, page=True, selected=True)
        self.content_stack.setCurrentIndex(3)
        self._update_file_types_enabled()
        self._set_scanning_panel(
            path=path,
            detail="Preparing the index and waiting for the first batch of results.",
        )
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setValue(0)
        self._set_scan_stats(None)

        self.fp.show_exclusions_rescan_hint(False)
        scan_exclusions = self.fp.get_scan_exclusions()
        self.scan_exclusions_in_progress = scan_exclusions.to_dict()
        self._update_exclusions_indicator()
        history = get_scan_history(self.current_scan_root)
        estimated_total_items = None
        if history:
            estimated_total_items = history.get("item_count") or None
        self.scanner_thread = ScannerThread(
            path,
            stale_months=self.fp.get_stale_months_for_scan(),
            exclusions=scan_exclusions,
            estimated_total_items=estimated_total_items,
        )
        self.folder_cache = self.scanner_thread.cache
        self.folder_browser_live_refresh_at = 0.0
        self.folder_browser.setEnabled(not self.hide_partial_scan_results)
        self._reset_folder_browser(
            None if self.hide_partial_scan_results else self.current_scan_root
        )
        self.scanner_thread.scan_started.connect(self._on_scan_started)
        self.scanner_thread.scan_finished.connect(self._on_scan_done)
        self.scanner_thread.scan_exclusions_summary.connect(self._on_scan_exclusions_summary)
        self.scanner_thread.scan_progress.connect(self._on_progress)
        self.scanner_thread.scan_progress_detail.connect(self._on_progress_detail)
        self.scanner_thread.first_batch_ready.connect(self._on_first_batch_ready)
        self.scanner_thread.batch_ready.connect(self._on_batch_ready)
        self.scanner_thread.start()

    def _on_progress(self, path):
        s = ("..." + path[-72:]) if len(path) > 75 else path
        if self.is_scanning:
            self._set_scanning_total_chip()
        if self.content_stack.currentIndex() == 3:
            self._set_scanning_panel(path=s)

    def _on_progress_detail(self, detail):
        self.last_scan_elapsed_secs = detail.get("elapsed_secs", 0.0)
        self.last_scan_item_count = detail.get("scanned", 0) or 0
        self._set_scan_stats(detail)
        self.lbl_status.setText(f"Scanning - {self._scan_stats_text(detail)}")
        if not getattr(self, 'hide_partial_scan_results', False):
            self._refresh_folder_browser_from_cache()

    def _on_scan_started(self):
        if self.is_scanning:
            self._set_scanning_total_chip()
            self.chip_selected_size.setVisible(False)

    def _set_match_status(self, total_matches):
        if self.is_scanning:
            return
        else:
            self.lbl_status.setText(self._scan_complete_status_text())

    def _scan_complete_status_text(self):
        parts = [
            f"Scan complete in {self._format_scan_duration(getattr(self, 'last_scan_elapsed_secs', 0.0))}.",
            f"{getattr(self, 'last_scan_item_count', 0):,} items scanned.",
        ]
        if getattr(self, 'last_scan_excluded_count', 0):
            parts.append(f"{self.last_scan_excluded_count:,} items skipped by exclusion rules.")
        return " ".join(parts)

    def _on_first_batch_ready(self):
        if getattr(self, 'hide_partial_scan_results', False):
            return
        self._refresh_folder_browser_from_cache(force=True)
        options = self._page_load_options()
        if options.get('defer_tree_load_until_scan_done') and not self._can_live_load_from_cache(options):
            return
        if self.current_page == 0 and self.content_stack.currentIndex() in (0, 3):
            self._load_page()

    def _on_batch_ready(self):
        """Called during scanning when a new batch of items is indexed."""
        if getattr(self, 'hide_partial_scan_results', False):
            return
        self._refresh_folder_browser_from_cache(force=True)
        # Counting matches can be expensive on large scans, so throttle it.
        if self.content_stack.currentIndex() == 3:
            options = self._page_load_options()
            if self._can_live_load_from_cache(options):
                self._load_page()
            return
        if self.content_stack.currentIndex() == 1 and not self.scan_refresh_timer.isActive():
            self.scan_refresh_timer.start()

    def _can_live_load_from_cache(self, options):
        cache = options.get('folder_cache')
        root_path = options.get('folder_scope') or options.get('scan_root')
        return bool(
            options.get('lazy_show_all_tree')
            and cache
            and root_path
            and cache.has_children_for(root_path)
            and cache.child_count(root_path) > 0
        )

    def _refresh_pagination_only(self):
        """Update pagination buttons/info without reloading the whole tree."""
        if self.page_load_thread and self.page_load_thread.isRunning():
            return

        from src.file_index_tool import FileIndexTool
        tool = FileIndexTool()
        try:
            cursor = tool.conn.cursor()

            # We need the same WHERE clause as _load_page
            where_clauses = []
            params = []
            # Determine status filter (simplified mirror of _load_page logic)
            if hasattr(self.fp, 'rb_all') and self.fp.rb_all.isChecked(): status_filter = None
            elif self.fp.rb_empty.isChecked(): status_filter = 'Empty'
            elif self.fp.rb_videos.isChecked(): status_filter = None
            else: status_filter = 'Inactive'

            view_mode = self.fp.get_view_mode()
            age_secs = self.fp.get_older_than_secs()
            age_cutoff = (datetime.now().timestamp() - age_secs) if age_secs is not None else None

            if status_filter == 'Inactive':
                if view_mode == 'Tree':
                    where_clauses.append("is_folder = 0")
                if age_cutoff is not None:
                    where_clauses.append("modified_time <= ?")
                    params.append(age_cutoff)
                else: where_clauses.append("1=1")
            elif status_filter == 'Empty':
                if age_cutoff is not None:
                    where_clauses.append("modified_time <= ?")
                    params.append(age_cutoff)
            elif status_filter == 'Active':
                if age_cutoff is not None:
                    where_clauses.append("modified_time > ?")
                    params.append(age_cutoff)
                else: where_clauses.append("1=0")

            if status_filter == 'Empty': where_clauses.append(EMPTY_FOLDER_SQL)

            if self.fp.rb_videos.isChecked():
                placeholders = ','.join('?' * len(VIDEO_EXTENSIONS))
                where_clauses.append("is_folder = 0")
                where_clauses.append(f"extension IN ({placeholders})")
                params.extend(VIDEO_EXTENSIONS)
                if age_cutoff is not None:
                    where_clauses.append("modified_time <= ?")
                    params.append(age_cutoff)

            if view_mode == 'Files':
                where_clauses.append("is_folder = 0")
            elif view_mode == 'Folders':
                where_clauses.append("is_folder = 1")

            if self.folder_browser_scope:
                scope_sql, scope_params = descendant_like_sql(self.folder_browser_scope)
                where_clauses.append(f"({scope_sql})")
                params.extend(scope_params)

            scan_root = getattr(self, 'current_scan_root', None) or os.path.normpath(self.txt_path.text().strip() or "")
            if scan_root:
                where_clauses.append("path != ? COLLATE NOCASE")
                params.append(scan_root)

            where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
            cursor.execute("SELECT COUNT(*) FROM file_index" + where_sql, params)
            total_matches = cursor.fetchone()[0]
        finally:
            tool.close()

        # Update UI controls
        self.current_total_matches = total_matches or 0
        self._update_page_controls(total_matches or 0)
        
        self._set_match_status(total_matches)

    def _on_scan_done(self):
        finished_thread = self.scanner_thread
        was_hiding_partial_results = getattr(
            self,
            'hide_partial_scan_results',
            False,
        )
        if finished_thread:
            self.last_scan_elapsed_secs = getattr(finished_thread, "scan_elapsed_secs", 0.0) or 0.0
            self.last_scan_item_count = (
                getattr(finished_thread, "scan_indexed_count", 0)
                or getattr(finished_thread, "scan_scanned_count", 0)
                or 0
        )
        self.btn_rescan.setEnabled(True)
        _BaseMixin._set_rescan_idle_ui(self)
        self.is_scanning = False
        scan_cancelled = bool(finished_thread and finished_thread.is_cancelled)
        if scan_cancelled:
            self.applied_scan_exclusions = None
        else:
            self.applied_scan_exclusions = getattr(
                self,
                'scan_exclusions_in_progress',
                None,
            )
        self.scan_exclusions_in_progress = None
        update_exclusions_indicator = getattr(
            self,
            '_update_exclusions_indicator',
            None,
        )
        if callable(update_exclusions_indicator):
            update_exclusions_indicator()
        self.hide_partial_scan_results = False
        if not scan_cancelled:
            self.has_completed_scan = True
        if hasattr(self, 'folder_browser'):
            self.folder_browser.setEnabled(True)
            if was_hiding_partial_results:
                self._reset_folder_browser(
                    None if scan_cancelled else self.current_scan_root
                )
            if not scan_cancelled:
                self._refresh_folder_browser_from_cache(force=True)
                if self.current_scan_root:
                    self._load_folder_browser_children(self.current_scan_root)
        self._update_file_types_enabled()
        
        if scan_cancelled:
            self.lbl_status.setText("Scan stopped by user.")
        else:
            if self.scan_progress_was_determinate:
                self.scan_progress.setRange(0, 100)
                self.scan_progress.setValue(100)
            completion_message = self._scan_complete_status_text()
            self.lbl_status.setText(completion_message)
            record_scan_history(
                self.current_scan_root,
                getattr(finished_thread, "scan_scanned_count", 0) or self.last_scan_item_count,
                getattr(self.folder_cache, "running_total_size", 0) if self.folder_cache else 0,
                self.last_scan_elapsed_secs,
            )
            if self._should_show_completion_notification(
                elapsed_secs=self.last_scan_elapsed_secs,
                cancelled=False,
            ):
                self._show_system_notification("Scan complete", completion_message)
        if scan_cancelled and was_hiding_partial_results:
            self.tree_model = None
            self.proxy_model.setSourceModel(None)
            self.controls_bar.setVisible(False)
            self.content_stack.setCurrentIndex(0)
            self._set_total_summary_chip("--")
            self._set_selected_summary_chip("0 B", 0, 0)
            return
        if self.content_stack.currentIndex() in (0, 2, 3):
            self._load_page()
        elif self.content_stack.currentIndex() == 1:
            if self.current_lazy_show_all_tree:
                if self.tree_model:
                    self.tree_model.update_sizes_from_cache(self.folder_cache)
            else:
                self.scan_refresh_timer.start()
            self._update_chips_sql()
        if self.folder_cache is not None:
            cached_scope_lookup = getattr(
                self,
                '_cached_scoped_folder_total',
                None,
            )
            completed_total = (
                cached_scope_lookup(self.current_scan_root)
                if callable(cached_scope_lookup)
                else None
            )
            if completed_total is None:
                completed_total = (
                    getattr(self.folder_cache, "running_total_size", 0) or 0
                )
            self.cached_folder_total = completed_total
            self.cached_folder_total_root = _path_key(self.current_scan_root)
            self._set_total_summary_chip(completed_total)
        self._set_selected_summary_chip("0 B", 0, 0)

    def _prev_page(self):
        if self.current_page > 0:
            self._preserve_results_focus = True
            self.current_page -= 1
            self._update_pending_pagination_state()
            self._load_page()
            self._clear_path_input_focus()

    def _next_page(self):
        self._preserve_results_focus = True
        self.current_page += 1
        self._update_pending_pagination_state()
        self._load_page()
        self._clear_path_input_focus()

    def _update_pending_pagination_state(self):
        if not hasattr(self, 'lbl_page_info'):
            return

        limit = 2000
        total_matches = self.current_total_matches or 0
        self._update_page_controls(total_matches)
        if self.current_lazy_show_all_tree or total_matches <= 0:
            return

        offset = self.current_page * limit
        end = min(offset + limit, total_matches) if total_matches else offset + limit
        self.lbl_page_info.setText(f"{offset + 1}-{end} of {total_matches}")
        self.btn_prev_page.setEnabled(self.current_page > 0)
        self.btn_next_page.setEnabled(offset + limit < total_matches)

    def _default_sort_order_for_column(self, col):
        if col in (2, 3, 4, 5):
            return Qt.SortOrder.DescendingOrder
        return Qt.SortOrder.AscendingOrder

    def _apply_sort_indicator(self):
        hdr = self.tree.header()
        blocker = QSignalBlocker(hdr)
        hdr.setSortIndicatorShown(False)
        del blocker
        model = getattr(self, 'tree_model', None)
        if model is not None and hasattr(model, 'set_sort_header_state'):
            model.set_sort_header_state(self.sort_column, self.sort_order)

    def _focus_results_view(self):
        self._clear_path_input_focus()
        self.tree.setFocus(Qt.FocusReason.OtherFocusReason)

    def _on_header_sort_clicked(self, col):
        if col >= 6:
            return
        if self.fp.get_view_mode() != 'Tree' and col == 1:
            return

        if col == self.sort_column:
            order = (
                Qt.SortOrder.AscendingOrder
                if self.sort_order == Qt.SortOrder.DescendingOrder
                else Qt.SortOrder.DescendingOrder
            )
        else:
            order = self._default_sort_order_for_column(col)

        self._on_sort_changed(col, order)

    def _on_sort_changed(self, col, order):
        if col >= 6:
            return

        self.bulk_delete_scope = None
        self._preserve_results_focus = True
        self.sort_column = col
        self.sort_order = order
        self._apply_sort_indicator()
        self.current_page = 0 # Reset to first page when sorting changes
        self._load_page()

    def _page_load_options(self):
        view_mode = self.fp.get_view_mode()
        paginated = True
        name_filter = self.applied_name_filter
        extension_filter = self.active_extension_filter
        folder_scope = self.folder_browser_scope

        if hasattr(self.fp, 'rb_all') and self.fp.rb_all.isChecked():
            status_filter = None
        elif self.fp.rb_empty.isChecked():
            status_filter = 'Empty'
        elif self.fp.rb_videos.isChecked():
            status_filter = None
        else:
            status_filter = 'Inactive'

        age_secs = self.fp.get_older_than_secs()
        age_cutoff = (datetime.now().timestamp() - age_secs) if age_secs is not None else None

        limit = 2000
        lazy_show_all_tree = (
            view_mode == 'Tree'
            and status_filter is None
            and not self.fp.rb_videos.isChecked()
            and age_cutoff is None
            and not name_filter
            and extension_filter is None
        )
        filtered_expanded_tree = (
            view_mode == 'Tree'
            and not name_filter
            and extension_filter is None
            and (
                status_filter in ('Inactive', 'Empty')
                or self.fp.rb_videos.isChecked()
            )
        )
        defer_tree_load_until_scan_done = (
            lazy_show_all_tree
            or filtered_expanded_tree
            or bool(name_filter)
            or extension_filter is not None
        )
        return {
            'limit': limit,
            'offset': self.current_page * limit if paginated else 0,
            'page': self.current_page if paginated else 0,
            'paginated': paginated,
            'view_mode': view_mode,
            'status_filter': status_filter,
            'age_cutoff': age_cutoff,
            'videos_only': self.fp.rb_videos.isChecked(),
            'name_filter': name_filter,
            'extension_filter': extension_filter,
            'folder_scope': folder_scope,
            'sort_column': self.sort_column,
            'sort_desc': self.sort_order == Qt.SortOrder.DescendingOrder,
            'scan_root': getattr(self, 'current_scan_root', os.path.normpath(self.txt_path.text().strip() or "")),
            'folder_cache': self.folder_cache,
            'scan_exclusions': getattr(
                self.fp,
                'get_scan_exclusions',
                lambda: ScanExclusions(),
            )(),
            'lazy_show_all_tree': lazy_show_all_tree,
            'filtered_expanded_tree': filtered_expanded_tree,
            'defer_tree_load_until_scan_done': defer_tree_load_until_scan_done,
        }

    def _show_loading_dialog(self):
        if self.page_load_thread and self.page_load_thread.isRunning():
            if self.loading_dialog is None:
                self.loading_dialog = LoadingDialog(
                    "Loading results",
                    "Applying filters and preparing the table...",
                    self,
                )
            self.loading_dialog.show()
            QApplication.processEvents()

    def _hide_loading_dialog(self):
        self.loading_timer.stop()
        if self.loading_dialog:
            self.loading_dialog.hide()
            self.loading_dialog.close()
            self.loading_dialog = None

    def _show_selection_loading_dialog(self, label):
        if self.selection_loading_dialog is None:
            detail = (
                "Matching videos and updating the current page..."
                if label == "videos"
                else "Matching items and updating the current page..."
            )
            self.selection_loading_dialog = LoadingDialog(
                "Working...",
                detail,
                self,
            )
        self.selection_loading_min_visible_until = time.monotonic() + 0.25
        self.selection_loading_dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.selection_loading_dialog.show()
        self.selection_loading_dialog.raise_()
        self.selection_loading_dialog.activateWindow()
        QApplication.processEvents()

    def _hide_selection_loading_dialog(self):
        if self.selection_loading_dialog:
            remaining = self.selection_loading_min_visible_until - time.monotonic()
            if remaining > 0:
                QTimer.singleShot(int(remaining * 1000), self._hide_selection_loading_dialog)
                return
            self.selection_loading_dialog.hide()
            self.selection_loading_dialog.close()
            self.selection_loading_dialog = None

    def _cleanup_page_thread(self, thread):
        if thread in self.page_load_threads:
            self.page_load_threads.remove(thread)

    def _cleanup_lazy_child_thread(self, request_id):
        self.lazy_child_threads.pop(request_id, None)

    def _cleanup_totals_thread(self, thread):
        if thread in self.totals_threads:
            self.totals_threads.remove(thread)
        if thread is self.totals_thread:
            self.totals_thread = None

    def _cleanup_path_total_thread(self, thread):
        if thread in self.path_total_threads:
            self.path_total_threads.remove(thread)
        if thread is self.current_page_total_thread:
            self.current_page_total_thread = None
        if thread is self.selected_total_thread:
            self.selected_total_thread = None

    def _cancel_running_totals_thread(self):
        if self.totals_thread and self.totals_thread.isRunning():
            self.totals_thread.cancel()
        if self.current_page_total_thread and self.current_page_total_thread.isRunning():
            self.current_page_total_thread.cancel()
        if self.selected_total_thread and self.selected_total_thread.isRunning():
            self.selected_total_thread.cancel()

    def _build_totals_refresh_options(self):
        age_secs = self.fp.get_older_than_secs()
        age_cutoff = (datetime.now().timestamp() - age_secs) if age_secs is not None else None
        root_path = getattr(self, 'current_scan_root', None) or os.path.normpath(self.txt_path.text().strip() or "")
        root_key = os.path.normcase(os.path.normpath(root_path)) if root_path else None
        cached_folder_total = self.cached_folder_total if root_key and root_key == self.cached_folder_total_root else None
        cached_scoped_folder_total = self._cached_scoped_folder_total()

        filtered_where_sql = None
        filtered_where_params = ()
        filtered_size_label = None
        if self.applied_name_filter or self.active_extension_filter is not None:
            filtered_where_sql, filtered_where_params = self._build_bulk_where()
            if self.active_extension_filter is not None:
                extension = self.active_extension_filter or "(no extension)"
                filtered_size_label = f"Type {extension} Size"
            else:
                filtered_size_label = "Search Size"

        return {
            'age_cutoff': age_cutoff,
            'is_scanning': self.is_scanning,
            'root_path': root_path,
            'folder_scope': self.folder_browser_scope,
            'cached_folder_total': cached_folder_total,
            'cached_scoped_folder_total': cached_scoped_folder_total,
            'current_page_file_paths': self._current_page_file_paths(),
            'selected_paths': self._selected_roots_for_delete(),
            'filtered_where_sql': filtered_where_sql,
            'filtered_where_params': filtered_where_params,
            'filtered_size_label': filtered_size_label,
        }

    def _start_totals_thread(self, request_id, options):
        thread = TotalsThread(request_id, options)
        self.totals_thread = thread
        self.totals_threads.append(thread)
        thread.totals_ready.connect(self._on_totals_ready)
        thread.totals_failed.connect(self._on_totals_failed)
        thread.finished.connect(lambda thread=thread: self._on_totals_thread_finished(thread))
        thread.start()

    def _start_path_total_thread(self, request_id, kind, paths):
        if not paths:
            if kind == 'page':
                self._set_chip_text(self.chip_page_size, "Current Page Size: 0 B")
            else:
                self.cached_selected_total = 0
                self._set_selected_summary_chip("0 B", 0, 0)
            return

        thread = PathSizeThread(request_id, kind, paths, parent=self)
        if kind == 'page':
            self.current_page_total_thread = thread
        else:
            self.selected_total_thread = thread
        self.path_total_threads.append(thread)
        thread.total_ready.connect(self._on_path_total_ready)
        thread.total_failed.connect(self._on_path_total_failed)
        thread.finished.connect(lambda thread=thread: self._cleanup_path_total_thread(thread))
        thread.start()

    def _on_totals_thread_finished(self, thread):
        self._cleanup_totals_thread(thread)
        self.totals_refresh_pending = False
        self.totals_refresh_options = None

    def _start_totals_refresh(self):
        self.totals_request_id += 1
        request_id = self.totals_request_id
        options = self._build_totals_refresh_options()
        self._cancel_running_totals_thread()
        self.totals_refresh_pending = False
        self.totals_refresh_options = None
        self._start_totals_thread(request_id, options)
        self._start_path_total_thread(request_id, 'page', options['current_page_file_paths'])
        self._start_path_total_thread(request_id, 'selected', options['selected_paths'])

    def _set_loading_controls_enabled(self, enabled):
        self.tree.setEnabled(enabled)
        for button in (
            self.btn_expand,
            self.btn_select_all,
            self.btn_current_page_selection,
            self.btn_clear_selection,
            self.btn_select_inactive,
            self.btn_select_empty,
            self.btn_prev_page,
            self.btn_next_page,
        ):
            button.setEnabled(enabled and button.isVisible())
        self.btn_delete.setEnabled(enabled and self.btn_delete.isEnabled())

    def _load_page(self):
        self._cancel_running_bulk_select_thread()
        self.page_load_request_id += 1
        request_id = self.page_load_request_id
        options = self._page_load_options()
        self._capture_tree_refresh_state(options)

        if self.page_load_thread and self.page_load_thread.isRunning():
            try:
                self.page_load_thread.page_ready.disconnect()
                self.page_load_thread.page_failed.disconnect()
            except TypeError:
                pass
            self.page_load_thread.cancel()

        self.lbl_status.setText("Loading results...")
        root_path = getattr(self, 'current_scan_root', None) or os.path.normpath(self.txt_path.text().strip() or "")
        root_key = os.path.normcase(os.path.normpath(root_path)) if root_path else None
        has_cached_folder_total = bool(root_key and root_key == self.cached_folder_total_root and self.cached_folder_total is not None)
        self._set_size_totals_pending(
            browse=self.is_scanning or not has_cached_folder_total,
            page=True,
            selected=True,
        )
        self._set_loading_controls_enabled(False)
        self.loading_timer.start()

        self.page_load_thread = PageLoadThread(request_id, options)
        self.page_load_threads.append(self.page_load_thread)
        self.page_load_thread.page_ready.connect(self._on_page_load_ready)
        self.page_load_thread.page_failed.connect(self._on_page_load_failed)
        self.page_load_thread.finished.connect(lambda: self._cleanup_page_thread(self.sender()))
        self.page_load_thread.start()
        return

    def _on_page_load_ready(self, request_id, result):
        if request_id != self.page_load_request_id:
            return

        self._hide_loading_dialog()
        self._set_loading_controls_enabled(True)
        self.page_load_thread = None
        self._update_file_types_enabled()

        total_matches = result['total_matches']
        self.current_total_matches = total_matches or 0
        rows_count = result['rows_count']
        limit = result['limit']
        offset = result['offset']
        view_mode = result['view_mode']
        lazy_show_all_tree = result.get('lazy_show_all_tree', False)
        self.current_lazy_show_all_tree = lazy_show_all_tree
        self.current_filesystem_scope_fallback = bool(
            result.get('filesystem_scope_fallback', False)
        )

        if rows_count == 0 and self.current_page > 0:
            self.current_page -= 1
            self._load_page()
            return

        if rows_count == 0 and self.current_page == 0:
            if self.is_scanning:
                self.content_stack.setCurrentIndex(3)
                self._set_scanning_panel(
                    detail="Indexing is still running. The first batch will replace this panel as soon as it is ready.",
                )
            else:
                self.content_stack.setCurrentIndex(2)
                self.controls_bar.setVisible(False)
                self._set_page_controls_visible(False)
                self._set_empty_result_metrics()
                debug_info = result.get('debug_info')
                self.lbl_status.setText(
                    f"No matching items found ({debug_info})"
                    if debug_info else "No matching items found"
                )
            return

        self.tree_model = StorageScoutTreeModel(result['root_node'])
        self.tree_model.view_mode = view_mode
        self.tree_model.options = self._page_load_options()
        self.proxy_model.setSourceModel(self.tree_model)
        self.tree.setModel(self.proxy_model)
        self._rebuild_and_restore_page_selection()

        self._update_page_controls(total_matches or 0)

        self.content_stack.setCurrentIndex(1)
        self.controls_bar.setVisible(True)

        hdr = self.tree.header()
        for index in range(0, 6):
            hdr.setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(False)
        self._apply_sort_indicator()
        self._fit_tree_columns_to_viewport()

        if result.get('name_filter'):
            self._set_expand_state(False)
            self._expand_loaded_search_branches()
        else:
            should_expand_loaded_tree = view_mode == 'Tree' and not lazy_show_all_tree
            self._set_expand_state(should_expand_loaded_tree)
        self._restore_tree_refresh_state(self.tree_model.options)
        self._update_expand_control_visibility()
        self._update_status_column_visibility()
        self._update_status_metrics_visibility()
        self._set_match_status(total_matches)
        self._do_recount()
        self.tree_model.dataChanged.connect(self._on_checked)
        self.tree_model.layoutChanged.connect(self._on_checked)
        if self._preserve_results_focus:
            self._focus_results_view()
            self._preserve_results_focus = False

    def _on_page_load_failed(self, request_id, error):
        if request_id != self.page_load_request_id:
            return

        self._hide_loading_dialog()
        self._set_loading_controls_enabled(True)
        self.page_load_thread = None
        self.lbl_status.setText("Failed to load results")
        QMessageBox.critical(self, "Load Error", error)

    def _update_chips_sql(self):
        self._start_totals_refresh()

    def _on_totals_ready(self, request_id, result):
        from ...models import format_size

        if request_id != self.totals_request_id:
            return

        root_path = getattr(self, 'current_scan_root', None) or os.path.normpath(self.txt_path.text().strip() or "")
        root_key = os.path.normcase(os.path.normpath(root_path)) if root_path else None
        folder_file_count = result.get('folder_file_count')
        folder_total_is_real_zero = result['folder_total'] == 0 and folder_file_count == 0
        folder_total_is_unknown_zero = result['folder_total'] == 0 and folder_file_count not in (0, None)
        if result['folder_total'] is not None and root_key and not folder_total_is_unknown_zero:
            self.cached_folder_total = result['folder_total']
            self.cached_folder_total_root = root_key

        self._set_chip_text(self.chip_empty, f"Empty: {result['empty_n']:,} items")
        self._set_chip_text(
            self.chip_inactive_folders,
            f"Inactive: {result['inactive_folders']:,} folders · "
            f"{result['inactive_files']:,} files",
        )
        if self.is_scanning:
            self._set_scanning_total_chip()
            return
        scoped_folder_total = result.get('scoped_folder_total')
        if self.folder_browser_scope and scoped_folder_total is not None:
            self._set_chip_text(
                self.chip_folder_size,
                f"Folder Size {self._format_chip_size(scoped_folder_total)}",
            )
            self.chip_folder_size.setVisible(True)
        else:
            self.chip_folder_size.setVisible(False)
        filtered_total = result.get('filtered_total')
        filtered_size_label = result.get('filtered_size_label')
        if filtered_size_label and filtered_total is not None:
            self._set_chip_text(
                self.chip_filtered_size,
                f"{filtered_size_label} {self._format_chip_size(filtered_total)}",
            )
            self.chip_filtered_size.setVisible(True)
        else:
            self.chip_filtered_size.setVisible(False)
        if result['folder_total'] is None:
            self._set_total_summary_chip("--")
        elif folder_total_is_unknown_zero:
            self.cached_folder_total = None
            self._set_total_summary_chip("--")
        else:
            folder_total = result['folder_total']
            folder_total_text = "0 B" if folder_total_is_real_zero else format_size(folder_total)
            self._set_total_summary_chip(folder_total_text)

    def _on_path_total_ready(self, request_id, kind, total):
        from ...models import format_size

        if request_id != self.totals_request_id:
            return

        if isinstance(total, dict):
            size = total.get('size', 0)
            folders = total.get('folders', 0)
            files = total.get('files', 0)
        else:
            size = total
            folders = 0
            files = 0

        if kind == 'page':
            self._set_chip_text(self.chip_page_size, f"Current Page Size: {format_size(size)}")
            return

        self.cached_selected_total = size
        self._set_selected_summary_chip(format_size(size), folders, files)
        if (
            self.pending_delete_preview_dialog
            and getattr(self.pending_delete_preview_dialog, 'preview_mode', '') == 'direct'
        ):
            self.pending_delete_preview_dialog.apply_preview(self._build_direct_delete_preview(self._selected_roots_for_delete()))

    def _on_path_total_failed(self, request_id, kind, error):
        if request_id != self.totals_request_id:
            return

        if kind == 'page':
            self._set_chip_text(self.chip_page_size, "Current Page Size: --")
            return

        self.cached_selected_total = None
        self._set_selected_summary_chip("0 B", 0, 0)

    def _on_totals_failed(self, request_id, error):
        if request_id != self.totals_request_id:
            return

        if self.is_scanning:
            self._set_scanning_total_chip()
        else:
            known_total = self._known_browse_total()
            self._set_total_summary_chip(
                "--" if known_total is None else known_total
            )
            if self.folder_browser_scope:
                cached_scoped_total = self._cached_scoped_folder_total()
                if cached_scoped_total is not None:
                    self._set_chip_text(
                        self.chip_folder_size,
                        "Folder Size "
                        f"{self._format_chip_size(cached_scoped_total)}",
                    )
                else:
                    self._set_chip_text(
                        self.chip_folder_size,
                        "Folder Size unavailable",
                    )
                self.chip_folder_size.setVisible(True)
