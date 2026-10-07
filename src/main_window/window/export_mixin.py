import os
from datetime import datetime

from PyQt6.QtCore import (
    QModelIndex,
    Qt,
)
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QMessageBox,
)

from ...models import format_size
from ..dialogs.export import ExportDialog, ExportProgressDialog
from ..path_utils import _path_key
from ..workers.export import ExportThread


class _ExportMixin:
    def _current_page_export_rows(self):
        rows = []
        seen = set()

        def collect(parent):
            for row in range(self.proxy_model.rowCount(parent)):
                index = self.proxy_model.index(row, 0, parent)
                data = self.proxy_model.data(index, Qt.ItemDataRole.UserRole)
                if isinstance(data, dict) and data.get("path"):
                    path = data["path"]
                    key = _path_key(path)
                    if key not in seen:
                        seen.add(key)
                        is_folder = bool(data.get("is_dir", False))
                        status = data.get("status", "")
                        rows.append((
                            path,
                            data.get("name") or os.path.basename(path),
                            int(is_folder),
                            int(data.get("size", 0) or 0),
                            float(data.get("last_modified", 0) or 0),
                            data.get("location") or os.path.dirname(path),
                            "" if is_folder else os.path.splitext(path)[1].lower(),
                            int(status == "Empty"),
                        ))
                if self.proxy_model.hasChildren(index):
                    collect(index)

        collect(QModelIndex())
        return rows

    def _export_metadata(self, export_type, scope):
        exclusions = self.fp.get_scan_exclusions().to_dict()
        age_months = getattr(self.fp, "applied_age_value", 0)
        display_mode = "Show all"
        if self.fp.rb_inactive.isChecked():
            display_mode = "Inactive"
        elif self.fp.rb_empty.isChecked():
            display_mode = "Empty"
        elif self.fp.rb_videos.isChecked():
            display_mode = "Videos"
        return {
            "Exported at": datetime.now().isoformat(timespec="seconds"),
            "Scan root": getattr(self, "current_scan_root", "") or "",
            "Export type": export_type,
            "Scope": scope,
            "Display mode": display_mode,
            "View mode": self.fp.get_view_mode(),
            "Age threshold": "Off" if not age_months else f"{age_months} months",
            "Search": self.applied_name_filter or "(none)",
            "Extension filter": self.active_extension_filter or "(none)",
            "Folder scope": self.folder_browser_scope or "(root)",
            "Excluded folders": ", ".join(exclusions["folder_names"]) or "(none)",
            "Excluded extensions": ", ".join(exclusions["extensions"]) or "(none)",
            "Minimum file size": exclusions["min_file_size_bytes"],
        }

    def _default_export_filename(self, export_type, scope):
        labels = {
            "listing": scope,
            "file_types": "file_types",
            "folder_summary": "folder_summary",
            "delete_audit": "delete_audit",
            "scan_history": "scan_history",
        }
        suffix = labels.get(export_type, "report")
        return f"storage_scout_export_{suffix}_{datetime.now():%Y-%m-%d}.csv"

    def _close_export_progress(self):
        if self.export_progress:
            self.export_progress._allow_close = True
            self.export_progress.hide()
            self.export_progress.close()
            self.export_progress = None
        self.btn_export.setEnabled(True)

    def _on_export_finished(self, row_count, file_size):
        target_path = self.export_target_path
        self._close_export_progress()
        self.export_target_path = None
        self.lbl_status.setText(
            f"Export complete. {row_count:,} rows written ({format_size(file_size)})."
        )
        message = QMessageBox(self)
        message.setIcon(QMessageBox.Icon.Information)
        message.setWindowTitle("Export complete")
        message.setText(f"Exported {row_count:,} rows ({format_size(file_size)}).")
        message.setInformativeText(target_path or "")
        reveal_button = message.addButton("Show in Explorer", QMessageBox.ButtonRole.ActionRole)
        message.addButton(QMessageBox.StandardButton.Ok)
        message.exec()
        if message.clickedButton() is reveal_button and target_path:
            self._open(target_path, is_dir=False)

    def _on_export_failed(self, error):
        self._close_export_progress()
        self.export_target_path = None
        self.lbl_status.setText("Export failed.")
        QMessageBox.critical(self, "Export failed", error)

    def _on_export_cancelled(self):
        self._close_export_progress()
        self.export_target_path = None
        self.lbl_status.setText("Export cancelled.")

    def _on_export_thread_stopped(self):
        thread = self.sender()
        if self.export_thread is thread:
            self.export_thread = None
        if thread is not None:
            thread.deleteLater()

    def _export_csv(self):
        if self.export_thread and self.export_thread.isRunning():
            return
        has_results = bool(self.proxy_model and self.proxy_model.sourceModel())
        has_scan = bool(getattr(self, "current_scan_root", None))
        if not has_results and not has_scan:
            QMessageBox.information(self, "Export", "Nothing to export - run a scan first.")
            return

        selected_paths = self._selected_roots_for_delete() if has_results else []
        dialog = ExportDialog(
            has_selection=bool(selected_paths),
            has_bulk_scope=bool(self.bulk_delete_scope),
            settings=self.settings,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        export_type = dialog.export_type()
        scope = dialog.export_scope()
        last_directory = self.settings.value(
            "last_export_directory",
            os.path.expanduser("~"),
        )
        default_name = self._default_export_filename(export_type, scope)
        initial_path = os.path.join(str(last_directory), default_name)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save CSV Report",
            initial_path,
            "CSV Files (*.csv)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        self.settings.setValue("last_export_directory", os.path.dirname(path))

        where_sql, params = self._build_bulk_where()
        options = self._page_load_options()
        config = {
            "export_type": export_type,
            "scope": scope,
            "columns": dialog.selected_columns(),
            "include_summary": dialog.include_summary.isChecked(),
            "metadata": self._export_metadata(export_type, scope),
            "scan_root": getattr(self, "current_scan_root", None),
            "age_cutoff": options.get("age_cutoff"),
            "where_sql": where_sql,
            "params": params,
            "current_page_rows": (
                self._current_page_export_rows()
                if export_type == "listing" and scope == "current_page"
                else []
            ),
            "selected_paths": selected_paths if scope == "selected" else [],
        }
        if scope == "bulk_scope" and self.bulk_delete_scope:
            config.update({
                "bulk_where_sql": self.bulk_delete_scope.get("where_sql", ""),
                "bulk_params": list(self.bulk_delete_scope.get("params", [])),
                "folder_delete_mode": self.bulk_delete_scope.get(
                    "folder_delete_mode",
                    "empty_only",
                ),
                "excluded_paths": list(
                    self.bulk_delete_scope.get("excluded_paths", [])
                ),
            })

        self.export_target_path = path
        self.export_progress = ExportProgressDialog(self)
        self.export_thread = ExportThread(path, config, parent=self)
        self.export_thread.progress.connect(self.export_progress.update_progress)
        self.export_thread.export_finished.connect(self._on_export_finished)
        self.export_thread.export_failed.connect(self._on_export_failed)
        self.export_thread.export_cancelled.connect(self._on_export_cancelled)
        self.export_thread.finished.connect(self._on_export_thread_stopped)
        self.export_progress.cancel_requested.connect(self.export_thread.cancel)
        self.btn_export.setEnabled(False)
        self.lbl_status.setText("Exporting CSV report...")
        self.export_thread.start()
        self.export_progress.show()
