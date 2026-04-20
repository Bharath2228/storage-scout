import os
from datetime import datetime
from PyQt6.QtCore import QThread, pyqtSignal

class ScannerThread(QThread):
    scan_started = pyqtSignal()
    scan_progress = pyqtSignal(str) # current_path
    scan_finished = pyqtSignal(object) # passing the root node data structure
    
    def __init__(self, start_path, stale_months=6):
        super().__init__()
        self.start_path = start_path
        self.stale_months = stale_months
        self.is_cancelled = False
        self.last_emit_time = 0
        
    def run(self):
        self.scan_started.emit()
        # To avoid UI freeze, we can build a nested dictionary/object structure
        # Or just yield paths and process in the model.
        # Building the tree structure in the background thread is usually better for performance.
        
        if not os.path.exists(self.start_path):
            self.scan_finished.emit(None)
            return
            
        root_node = self._scan_directory(self.start_path)
        if not self.is_cancelled:
            self.scan_finished.emit(root_node)
            
    def cancel(self):
        self.is_cancelled = True
        
    def _scan_directory(self, path):
        # A node is a dict: {'name': str, 'path': str, 'is_dir': bool, 'size': int, 'last_modified': float, 'status': str, 'children': list}
        try:
            stat = os.stat(path)
            node = {
                'name': os.path.basename(path) or path,
                'path': path,
                'is_dir': True,
                'size': 0,
                'last_modified': stat.st_mtime,
                'status': 'Active',
                'children': []
            }
        except Exception:
            return None
            
        try:
            items = os.listdir(path)
        except PermissionError:
            return node
            
        if not items:
            node['status'] = 'Empty'
            return node
            
        total_size = 0
        latest_mod_time = 0
        all_stale = True
        has_files = False
        
        current_time = datetime.now().timestamp()
        stale_threshold = self.stale_months * 30 * 24 * 3600 # rough approximation
        
        for item in items:
            if self.is_cancelled:
                return None
                
            item_path = os.path.join(path, item)
            current_time_emit = datetime.now().timestamp()
            if current_time_emit - self.last_emit_time > 0.05: # emit roughly every 50ms
                self.scan_progress.emit(item_path)
                self.last_emit_time = current_time_emit
                
            try:
                item_stat = os.stat(item_path)
                is_dir = os.path.isdir(item_path)
                
                if is_dir:
                    child_node = self._scan_directory(item_path)
                    if child_node:
                        node['children'].append(child_node)
                        total_size += child_node['size']
                        if child_node['last_modified'] > latest_mod_time:
                            latest_mod_time = child_node['last_modified']
                        if child_node['status'] != 'Empty':
                            has_files = True
                        if child_node['status'] == 'Active':
                            all_stale = False
                else:
                    has_files = True
                    mod_time = item_stat.st_mtime
                    size = item_stat.st_size
                    total_size += size
                    
                    if mod_time > latest_mod_time:
                        latest_mod_time = mod_time
                        
                    is_stale = (current_time - mod_time) > stale_threshold
                    if not is_stale:
                        all_stale = False
                        
                    child_node = {
                        'name': item,
                        'path': item_path,
                        'is_dir': False,
                        'size': size,
                        'last_modified': mod_time,
                        'status': 'Inactive' if is_stale else 'Active',
                        'children': []
                    }
                    node['children'].append(child_node)
            except Exception:
                continue
                
        node['size'] = total_size
        if latest_mod_time > 0:
            node['last_modified'] = latest_mod_time
            
        if not has_files and len(node['children']) == 0:
            node['status'] = 'Empty'
        elif has_files and all_stale:
            node['status'] = 'Inactive'
        else:
            node['status'] = 'Active'
            
        return node
