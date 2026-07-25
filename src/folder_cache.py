import os
import threading


class FolderCache:
    def __init__(self):
        self._lock = threading.RLock()
        self.children = {}
        self.items = {}
        self.folder_sizes = {}
        self.folder_counts = {}
        self.running_total_size = 0

    def _key(self, path):
        return os.path.normcase(os.path.normpath(path)) if path else ""

    def clear(self):
        with self._lock:
            self.children.clear()
            self.items.clear()
            self.folder_sizes.clear()
            self.folder_counts.clear()
            self.running_total_size = 0

    def add_item(self, path, name, is_folder, size, modified_time, parent_path, status="Active"):
        if not path:
            return

        key = self._key(path)
        parent_key = self._key(parent_path)
        item = {
            'name': name,
            'path': path,
            'is_dir': bool(is_folder),
            'size': size or 0,
            'last_modified': modified_time,
            'status': status,
            'children': [],
            '_children_loaded': False,
            '_is_page_result': True,
            'location': parent_path,
            'display_location': parent_path,
        }

        with self._lock:
            self.items[key] = item
            if parent_path is not None:
                siblings = self.children.setdefault(parent_key, [])
                if key not in siblings:
                    siblings.append(key)
            if is_folder:
                self.children.setdefault(key, [])
                self.folder_sizes.setdefault(key, 0)
                self.folder_counts.setdefault(key, {'files': 0, 'folders': 1, 'children': 0})
            else:
                self.running_total_size += size or 0

    def set_folder_summary(self, path, total_size, file_count, folder_count, child_count=None):
        key = self._key(path)
        with self._lock:
            self.folder_sizes[key] = total_size or 0
            self.folder_counts[key] = {
                'files': file_count or 0,
                'folders': folder_count or 0,
                'children': child_count or 0,
            }
            item = self.items.get(key)
            if item:
                item['size'] = total_size or 0

    def has_children_for(self, path):
        with self._lock:
            return self._key(path) in self.children

    def child_count(self, path):
        with self._lock:
            return len(self.children.get(self._key(path), []))

    def item_count(self):
        with self._lock:
            return len(self.items)

    def children_for(self, path, sort_column=0, sort_desc=False):
        with self._lock:
            child_keys = list(self.children.get(self._key(path), []))
            items = [dict(self.items[key]) for key in child_keys if key in self.items]

        for item in items:
            key = self._key(item.get('path'))
            item['_children_loaded'] = not bool(self.child_count(item.get('path')))
            item['children'] = []
            if item.get('is_dir'):
                item['size'] = self.folder_sizes.get(key, item.get('size', 0)) or 0

        items.sort(key=lambda item: self._sort_key(item, sort_column), reverse=sort_desc)
        return items

    def _sort_key(self, item, sort_column):
        if sort_column == 1:
            return (0 if item.get('is_dir') else 1, (item.get('name') or '').lower())
        if sort_column in (2, 3, 5):
            return (item.get('last_modified') or 0, (item.get('name') or '').lower())
        if sort_column == 4:
            return (item.get('size') or 0, (item.get('name') or '').lower())
        return ((item.get('name') or '').lower(), (item.get('path') or '').lower())
