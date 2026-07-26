import os
import stat
from datetime import datetime
from PyQt6.QtCore import QThread, pyqtSignal
from .folder_cache import FolderCache
from .scan_exclusions import ScanExclusions

class ScannerThread(QThread):
    scan_started = pyqtSignal()
    scan_progress = pyqtSignal(str) # current_path
    scan_finished = pyqtSignal() # no root_node anymore!
    scan_exclusions_summary = pyqtSignal(int)
    first_batch_ready = pyqtSignal()
    batch_ready = pyqtSignal()
    
    def __init__(self, start_path, stale_months=6, exclusions: ScanExclusions | None = None):
        super().__init__()
        self.start_path = start_path
        self.stale_months = stale_months
        self.exclusions = exclusions or ScanExclusions()
        self.is_cancelled = False
        self.last_emit_time = 0
        self.cache = FolderCache()
        
    def run(self):
        self.scan_started.emit()
        # To avoid UI freeze, we can build a nested dictionary/object structure
        # Or just yield paths and process in the model.
        # Building the tree structure in the background thread is usually better for performance.
        
        if not os.path.exists(self.start_path):
            self.scan_finished.emit()
            return
            
        self._scan_directory_with_db(self.start_path)
        self.scan_finished.emit()
            
    def cancel(self):
        self.is_cancelled = True

    def _is_hidden(self, name, stat_result):
        if name.startswith('.'):
            return True

        attributes = getattr(stat_result, 'st_file_attributes', 0)
        hidden_mask = (
            getattr(stat, 'FILE_ATTRIBUTE_HIDDEN', 0)
            | getattr(stat, 'FILE_ATTRIBUTE_SYSTEM', 0)
        )
        return bool(attributes & hidden_mask)
        
    def _is_hidden_from_name(self, name):
        return name.startswith('.')
        
    def _scan_directory_with_db(self, start_path):
        from .file_index_tool import FileIndexTool
        tool = FileIndexTool()
        tool.clear_index()

        self.initial_batch_emitted = False
        self.last_emitted_count = 0

        def cancel_cb():
            return self.is_cancelled
            
        def progress_cb(scanned, inserted, current_folder):
            current_time = datetime.now().timestamp()
            if current_time - self.last_emit_time > 0.3:
                self.scan_progress.emit(current_folder)
                self.last_emit_time = current_time
            if inserted >= 500 and not self.initial_batch_emitted:
                self.initial_batch_emitted = True
                self.first_batch_ready.emit()
                self.last_emitted_count = inserted
            elif self.initial_batch_emitted and (inserted - self.last_emitted_count) >= 500:
                self.last_emitted_count = inserted
                self.batch_ready.emit()

        excluded_count = tool.scan(
            root_folder=start_path,
            inactive_months=self.stale_months,
            batch_size=1000,
            cache=self.cache,
            cancel_callback=cancel_cb,
            progress_callback=progress_cb,
            exclusions=self.exclusions,
        )
        self.scan_exclusions_summary.emit(excluded_count)

        tool.close()
