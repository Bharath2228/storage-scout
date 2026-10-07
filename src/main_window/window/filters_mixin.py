import os

from PyQt6.QtCore import (
    QModelIndex,
    QSignalBlocker,
    QTimer,
    Qt,
)

from ...scan_exclusions import ScanExclusions
from ...theme import SPACE_XS
from ..constants import VIDEO_EXTENSIONS
from ..dialogs.file_types import FileTypesDialog
from ..workers.file_types import FileTypeBreakdownThread


class _FiltersMixin:
    def _toggle_filters(self):
        is_visible = self.fp.isVisible()
        if is_visible:
            self.fp.close_popovers()
            self._filter_panel_width = max(
                self.fp.minimumWidth(),
                min(self.fp.width(), self.fp.maximumWidth()),
            )
            sizes = self.main_splitter.sizes()
            if len(sizes) >= 3:
                self.fp.setVisible(False)
                self.main_splitter.setSizes([sizes[0], sizes[1] + sizes[2], 0])
        else:
            self.fp.setVisible(True)
            self._show_filter_panel()
        self.btn_filter.setChecked(not is_visible)

    def _toggle_exclusions_popup(self, _checked=False):
        should_close = (
            self.exclusions_popup.isVisible()
            or self.exclusions_popup.recently_hidden()
        )
        if should_close:
            self.exclusions_popup.hide()
            self.btn_exclusions.setChecked(False)
            return
        self.fp.exclusions_box.setVisible(True)
        self.exclusions_popup.show_below(self.btn_exclusions)
        self.btn_exclusions.setChecked(True)

    def _show_filter_panel(self):
        filter_width = max(
            self.fp.minimumWidth(),
            min(self._filter_panel_width, self.fp.maximumWidth()),
        )
        sizes = self.main_splitter.sizes()
        if len(sizes) < 3:
            return
        available_right = max(420, sizes[1] + sizes[2])
        filter_width = min(filter_width, max(self.fp.minimumWidth(), available_right - 420))
        content_width = max(420, available_right - filter_width)
        self.main_splitter.setSizes([sizes[0], content_width, filter_width])

    def _remember_filter_panel_width(self, *_args):
        if self.fp.isVisible() and self.fp.width() > 0:
            self._filter_panel_width = max(
                self.fp.minimumWidth(),
                min(self.fp.width(), self.fp.maximumWidth()),
            )

    def _apply_filters(self):
        # 1. Update proxy model so it can format the Status column correctly
        self._cancel_running_bulk_select_thread()
        self._update_age_controls_enabled()
        age_secs = self.fp.get_older_than_secs()
        if hasattr(self.fp, 'rb_all') and self.fp.rb_all.isChecked():
            status_filter = None
        elif self.fp.rb_empty.isChecked():
            status_filter = 'Empty'
        elif self.fp.rb_videos.isChecked():
            status_filter = None
        else:
            status_filter = 'Inactive'

        if self.content_stack.currentIndex() != 0:
            self.proxy_model.setSourceModel(None)
            self.tree_model = None

        self.proxy_model.set_filters(
            empty_only=False,
            older_than_secs=age_secs,
            status_filter=status_filter,
            view_mode=self.fp.get_view_mode(),
            name_filter=self.applied_name_filter,
        )
        self._update_expand_control_visibility()
        self._update_status_column_visibility()
        self._update_status_metrics_visibility()

        # 2. Fetch the paginated data from SQL
        self.current_page = 0
        if self.content_stack.currentIndex() != 0:
            self._load_page()

    def _apply_default_browse_preset(self, apply_now=True):
        blockers = [
            QSignalBlocker(self.fp.bg),
            QSignalBlocker(self.fp.slider),
            QSignalBlocker(self.fp.age_input),
        ]
        try:
            self.fp.apply_default_browse_preset()
        finally:
            del blockers

        if apply_now:
            self._cancel_running_bulk_select_thread()
            self._discard_current_page_selection()
            self._apply_filters()

    def _update_expand_control_visibility(self):
        if not hasattr(self, 'btn_expand') or not hasattr(self, 'fp'):
            return
        view_mode = self.fp.get_view_mode()
        if view_mode == "Files":
            self.btn_expand.setVisible(False)
            return
        if self.fp.rb_all.isChecked() and view_mode in ("Tree", "Folders"):
            expanded = self._has_expanded_tree_nodes()
            blocker = QSignalBlocker(self.btn_expand)
            self.btn_expand.setChecked(expanded)
            self.btn_expand.setText("Collapse All")
            self.btn_expand.setVisible(expanded)
            self.btn_expand.setEnabled(expanded)
            del blocker
            return
        self.btn_expand.setVisible(True)

    def _has_expanded_tree_nodes(self):
        if not hasattr(self, 'tree') or not self.proxy_model:
            return False
        stack = [QModelIndex()]
        while stack:
            parent = stack.pop()
            for row in range(self.proxy_model.rowCount(parent)):
                index = self.proxy_model.index(row, 0, parent)
                if self.tree.isExpanded(index):
                    return True
                stack.append(index)
        return False

    def _update_status_column_visibility(self):
        if not hasattr(self, 'tree') or not hasattr(self, 'fp'):
            return

        show_status = not (self.fp.rb_all.isChecked() or self.fp.rb_videos.isChecked())
        self.tree.setColumnHidden(5, not show_status)
        QTimer.singleShot(0, self._fit_tree_columns_to_viewport)

    def _update_status_metrics_visibility(self):
        if not hasattr(self, 'chip_empty'):
            return

        show_status = not (self.fp.rb_all.isChecked() or self.fp.rb_videos.isChecked())
        for widget in (
            self.chip_empty,
            self.chip_inactive_folders,
            self.chip_inactive_files,
        ):
            widget.setVisible(show_status)
        if hasattr(self, 'chip_page_size'):
            paginated_files_view = bool(
                self.fp.get_view_mode() == "Files"
                and self.current_total_matches > 2000
                and not self.current_lazy_show_all_tree
            )
            self.chip_page_size.setVisible(
                not self.fp.rb_all.isChecked() or paginated_files_view
            )
        if hasattr(self, 'chip_selected_size'):
            self.chip_selected_size.setVisible((not self.is_scanning) and show_status)

    def _update_age_controls_enabled(self):
        if not hasattr(self, 'fp'):
            return

        self.fp.slider.setEnabled(True)
        self.fp.age_input.setEnabled(True)
        self.fp.lbl_pill.setEnabled(True)
        self.fp.lbl_val.setEnabled(True)
        self.fp.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fp.age_input.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fp.btn_apply_age.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fp._update_age_apply_state()

    def _reset_filters(self):
        self.search_debounce_timer.stop()
        self.applied_name_filter = ""
        self.active_extension_filter = None
        self._update_type_filter_ui()
        blocker = QSignalBlocker(self.fp.txt_search)
        self.fp.txt_search.clear()
        del blocker
        self.fp.search_clear_action.setVisible(False)
        self._apply_default_browse_preset()

    def _on_scan_exclusions_changed(self):
        self._update_exclusions_indicator()

    def _rescan_from_exclusions(self):
        self.fp._normalize_and_save_scan_exclusions()
        self.fp.close_popovers()
        self.start_scan()

    def _on_scan_exclusions_summary(self, excluded_count):
        self.last_scan_excluded_count = excluded_count or 0

    def _update_exclusions_indicator(self):
        if not hasattr(self, 'btn_exclusions') or not hasattr(self, 'fp'):
            return

        current = self.fp.get_scan_exclusions().to_dict()
        default = ScanExclusions().to_dict()
        applied = (
            self.scan_exclusions_in_progress
            if self.is_scanning and self.scan_exclusions_in_progress is not None
            else self.applied_scan_exclusions
        )
        rule_count = (
            len(current["folder_names"])
            + len(current["extensions"])
            + int(current["min_file_size_bytes"] > 0)
        )

        if current == default and applied in (None, default):
            state = "default"
            text = "Exclusions: Default"
            tooltip = "Default exclusion rules are active"
        elif applied is not None and current == applied:
            state = "active"
            text = f"Exclusions: {rule_count} active"
            tooltip = (
                f"{rule_count} exclusion rule"
                f"{'s are' if rule_count != 1 else ' is'} active"
            )
        else:
            state = "pending"
            text = "Exclusions: Pending"
            tooltip = "Exclusion changes are waiting for a re-scan"

        if hasattr(self, "lbl_exclusions_rule_count"):
            self.lbl_exclusions_rule_count.setText(
                f"{rule_count} active rule"
                f"{'s' if rule_count != 1 else ''}"
            )
        if hasattr(self, "lbl_exclusions_pending"):
            self.lbl_exclusions_pending.setVisible(state == "pending")

        self.btn_exclusions.setText(text)
        self.btn_exclusions.setToolTip(tooltip)
        self.btn_exclusions.setProperty("exclusionState", state)
        self.btn_exclusions.setMinimumWidth(
            max(118, self.btn_exclusions.sizeHint().width() + SPACE_XS)
        )
        self.btn_exclusions.style().unpolish(self.btn_exclusions)
        self.btn_exclusions.style().polish(self.btn_exclusions)
        self.btn_exclusions.update()

    def _has_completed_scan_context(self):
        return bool(getattr(self, 'current_scan_root', None)) and not self.is_scanning

    def _update_file_types_enabled(self):
        if hasattr(self, 'btn_file_types'):
            self.btn_file_types.setEnabled(self._has_completed_scan_context())

    def _show_file_types(self):
        if not self._has_completed_scan_context():
            return

        self._update_type_filter_ui()
        self.file_type_request_id += 1
        request_id = self.file_type_request_id

        if self.file_type_thread and self.file_type_thread.isRunning():
            self.file_type_thread.cancel()

        self.file_types_dialog = FileTypesDialog(self)
        self.file_types_dialog.extension_selected.connect(self._drill_down_file_type)
        self.file_types_dialog.set_loading()
        self.file_types_dialog.show()

        self.file_type_thread = FileTypeBreakdownThread(request_id, parent=self)
        self.file_type_thread.breakdown_ready.connect(self._on_file_types_ready)
        self.file_type_thread.breakdown_failed.connect(self._on_file_types_failed)
        self.file_type_thread.finished.connect(self._cleanup_file_type_thread)
        self.file_type_thread.start()

    def _cleanup_file_type_thread(self):
        if self.sender() is self.file_type_thread:
            self.file_type_thread = None

    def _on_file_types_ready(self, request_id, rows):
        if request_id != self.file_type_request_id or not self.file_types_dialog:
            return
        self.file_types_dialog.set_rows(rows)

    def _on_file_types_failed(self, request_id, error):
        if request_id != self.file_type_request_id or not self.file_types_dialog:
            return
        self.file_types_dialog.set_error(error)

    def _drill_down_file_type(self, extension):
        self.active_extension_filter = extension
        self._update_type_filter_ui()
        self.search_debounce_timer.stop()
        self.applied_name_filter = ""
        self._cancel_running_bulk_select_thread()
        self._discard_current_page_selection()
        self.bulk_delete_scope = None
        self.current_page = 0

        blockers = [
            QSignalBlocker(self.fp.bg),
            QSignalBlocker(self.fp.bg_view),
            QSignalBlocker(self.fp.rb_all),
            QSignalBlocker(self.fp.rb_inactive),
            QSignalBlocker(self.fp.rb_empty),
            QSignalBlocker(self.fp.rb_videos),
            QSignalBlocker(self.fp.rb_view_tree),
            QSignalBlocker(self.fp.rb_view_files),
            QSignalBlocker(self.fp.rb_view_folders),
            QSignalBlocker(self.fp.txt_search),
        ]
        try:
            self.fp.rb_all.setChecked(True)
            self.fp.rb_view_files.setChecked(True)
            self.fp.txt_search.clear()
        finally:
            del blockers

        self.fp.search_clear_action.setVisible(False)
        self.sort_column = 4
        self.sort_order = Qt.SortOrder.DescendingOrder
        self._apply_sort_indicator()
        self._apply_filters()

    def _update_type_filter_ui(self):
        if not hasattr(self, 'btn_file_types'):
            return

        is_active = self.active_extension_filter is not None
        extension = self.active_extension_filter or "(no extension)"
        has_extension = extension.startswith(".")
        display_extension = extension.upper() if has_extension else extension
        value_label = (
            f"{display_extension} files"
            if has_extension
            else "Files with no extension"
        )
        meta_label = (
            f"Showing {display_extension} files in Files view."
            if has_extension
            else "Showing files without an extension in Files view."
        )
        self.btn_file_types.setChecked(is_active)
        self.btn_file_types.setText(
            f"Extension: {display_extension}" if is_active else "File Extensions"
        )
        self.btn_file_types.setToolTip(
            f"File type filter active: {extension}. Click to choose another type."
            if is_active
            else "Show disk usage by file extension"
        )

        if hasattr(self, 'type_filter_banner'):
            self.lbl_active_type_filter.setText(value_label)
            self.lbl_active_type_filter_meta.setText(meta_label)
            self.type_filter_banner.setVisible(is_active)

    def _clear_type_filter(self):
        if self.active_extension_filter is None:
            return
        self._on_filter_changed(clear_extension=True)

    def _toggle_expand(self, checked):
        if (
            self.fp.rb_all.isChecked()
            and self.fp.get_view_mode() in ("Tree", "Folders")
        ):
            self._set_expand_state(False)
            self._update_expand_control_visibility()
            return
        self._set_expand_state(checked)

    def _update_content_page(self):
        """Switch between tree (page 1) and no-results (page 2) based on current proxy row count.
        Only acts when data is loaded (page 0 = no scan yet is handled separately)."""
        if self.content_stack.currentIndex() == 0:
            return  # still on the welcome page, no scan done yet
        if self._has_visible_rows():
            self.content_stack.setCurrentIndex(1)
        else:
            self.content_stack.setCurrentIndex(2)

    def _has_visible_rows(self):
        if not self.proxy_model:
            return False
        return self.proxy_model.rowCount(QModelIndex()) > 0

    def _set_expand_state(self, expanded):
        if not hasattr(self, 'tree'):
            return

        if expanded:
            if self.proxy_model and self.proxy_model.rowCount() > 0:
                # Filtered pages are capped by the selected page size, so expandAll stays bounded.
                self.is_programmatic_expand = True
                try:
                    self.tree.expandAll()
                finally:
                    self.is_programmatic_expand = False
        else:
            self.is_programmatic_expand = True
            try:
                self.tree.collapseAll()
            finally:
                self.is_programmatic_expand = False

        if hasattr(self, 'btn_expand'):
            blocker = QSignalBlocker(self.btn_expand)
            self.btn_expand.setChecked(expanded)
            self.btn_expand.setText("Collapse All" if expanded else "Expand All")
            self.btn_expand.setEnabled(self._has_visible_rows())
            del blocker

    def _expand_loaded_search_branches(self):
        if not self.tree_model or self.proxy_model.sourceModel() is None:
            return

        self.is_programmatic_expand = True
        try:
            stack = [
                self.tree_model.index(row, 0, QModelIndex())
                for row in range(self.tree_model.rowCount(QModelIndex()))
            ]
            while stack:
                source_index = stack.pop()
                item = source_index.internalPointer()
                real_children = [
                    child
                    for child in item.childItems
                    if not child.itemData.get('_is_dummy')
                ]
                if not real_children:
                    continue

                proxy_index = self.proxy_model.mapFromSource(source_index)
                if proxy_index.isValid():
                    self.tree.setExpanded(proxy_index, True)
                for row in range(self.tree_model.rowCount(source_index)):
                    stack.append(self.tree_model.index(row, 0, source_index))
        finally:
            self.is_programmatic_expand = False

    def _tree_refresh_state_signature(self, options=None):
        options = options or self._page_load_options()
        filter_mode = 'all'
        age_value = None
        if hasattr(self, 'fp'):
            if self.fp.rb_empty.isChecked():
                filter_mode = 'empty'
            elif self.fp.rb_videos.isChecked():
                filter_mode = 'videos'
            elif self.fp.rb_all.isChecked():
                filter_mode = 'all'
            else:
                filter_mode = 'inactive'
            if hasattr(self.fp, 'slider'):
                age_value = self.fp.slider.value()
        return (
            options.get('scan_root'),
            options.get('view_mode'),
            filter_mode,
            age_value,
            options.get('name_filter', ''),
            options.get('folder_scope'),
            options.get('page'),
            bool(options.get('lazy_show_all_tree')),
            bool(options.get('filtered_expanded_tree')),
        )

    def _should_preserve_tree_refresh_state(self, options=None):
        options = options or self._page_load_options()
        return (
            options.get('view_mode') == 'Tree'
            and not options.get('lazy_show_all_tree')
        )

    def _capture_tree_refresh_state(self, options=None):
        options = options or self._page_load_options()
        if not self._should_preserve_tree_refresh_state(options):
            self.refresh_tree_state_key = None
            self.refresh_collapsed_tree_paths = set()
            return
        if not self.tree_model or self.proxy_model.sourceModel() is None:
            self.refresh_tree_state_key = self._tree_refresh_state_signature(options)
            self.refresh_collapsed_tree_paths = set()
            return

        collapsed_paths = set()
        stack = [QModelIndex()]
        while stack:
            parent = stack.pop()
            for row in range(self.proxy_model.rowCount(parent)):
                proxy_index = self.proxy_model.index(row, 0, parent)
                source_index = self.proxy_model.mapToSource(proxy_index)
                if not source_index.isValid():
                    continue
                item_data = self.tree_model.data(source_index, Qt.ItemDataRole.UserRole) or {}
                if not item_data.get('is_dir') or not item_data.get('path'):
                    continue
                if self.tree.isExpanded(proxy_index):
                    stack.append(proxy_index)
                else:
                    collapsed_paths.add(self._path_key(item_data['path']))

        self.refresh_tree_state_key = self._tree_refresh_state_signature(options)
        self.refresh_collapsed_tree_paths = collapsed_paths

    def _restore_tree_refresh_state(self, options=None):
        options = options or self._page_load_options()
        if not self._should_preserve_tree_refresh_state(options):
            self.refresh_tree_state_key = None
            self.refresh_collapsed_tree_paths = set()
            return
        if self.refresh_tree_state_key != self._tree_refresh_state_signature(options):
            return
        if not self.tree_model or self.proxy_model.sourceModel() is None:
            return

        self.is_programmatic_expand = True
        try:
            for path_key in sorted(
                self.refresh_collapsed_tree_paths,
                key=lambda key: key.count(os.sep),
            ):
                source_index = self.source_index_by_path.get(path_key)
                if source_index is None or not source_index.isValid():
                    continue
                proxy_index = self.proxy_model.mapFromSource(source_index)
                if proxy_index.isValid():
                    self.tree.setExpanded(proxy_index, False)
        finally:
            self.is_programmatic_expand = False

    def _on_filter_changed(self, *_args, clear_extension=True):
        # User manually changed a filter control - clear selections and apply
        if clear_extension:
            self.active_extension_filter = None
            self._update_type_filter_ui()
        self._discard_current_page_selection()
        self._apply_filters()

    def _on_search_submitted(self):
        search_text = self.fp.txt_search.text().strip()
        if search_text == self.applied_name_filter:
            return
        self.applied_name_filter = search_text
        self._on_filter_changed()

    def _clear_search_filter(self):
        self.search_debounce_timer.stop()
        if not self.applied_name_filter:
            return
        self.applied_name_filter = ""
        self._on_filter_changed()

    def _on_age_filter_apply_clicked(self):
        self.fp.age_input.interpretText()
        self.fp.applied_age_value = self.fp.age_input.value()
        self.fp._update_age_apply_state()
        self.fp._update_popover_summaries()
        self._on_filter_changed()

    def _on_videos_mode_toggled(self, checked):
        self._update_age_controls_enabled()

    def _is_video_item(self, item_data):
        if not item_data or item_data.get('is_dir', False):
            return False
        return os.path.splitext(item_data.get('name', ''))[1].lower() in VIDEO_EXTENSIONS

    def _set_status_filter(self, status):
        if status == 'Inactive' and not self.fp.rb_inactive.isChecked():
            self.fp.rb_inactive.setChecked(True)
            return True
        if status == 'Empty' and not self.fp.rb_empty.isChecked():
            self.fp.rb_empty.setChecked(True)
            return True
        if status is None and hasattr(self.fp, 'rb_all') and not self.fp.rb_all.isChecked():
            self.fp.rb_all.setChecked(True)
            return True
        return False

    def _display_mode(self):
        if self.fp.rb_inactive.isChecked():
            return 'Inactive'
        if self.fp.rb_empty.isChecked():
            return 'Empty'
        if self.fp.rb_videos.isChecked():
            return 'Videos'
        return 'All'
