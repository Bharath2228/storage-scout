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
        self.filesystem_snapshots = {}

    def _key(self, path):
        if not path:
            return ""
        normalized = os.path.normcase(os.path.normpath(path))
        drive, tail = os.path.splitdrive(normalized)
        if drive.startswith("\\\\") and tail in ("", "\\", "/"):
            return drive.rstrip("\\/")
        return normalized

    def clear(self):
        with self._lock:
            self.children.clear()
            self.items.clear()
            self.folder_sizes.clear()
            self.folder_counts.clear()
            self.running_total_size = 0
            self.filesystem_snapshots.clear()

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

    def set_folder_summary(
        self,
        path,
        total_size,
        file_count,
        folder_count,
        child_count=None,
        physical_child_count=None,
    ):
        key = self._key(path)
        with self._lock:
            self.folder_sizes[key] = total_size or 0
            counts = {
                'files': file_count or 0,
                'folders': folder_count or 0,
                'children': child_count or 0,
            }
            if physical_child_count is not None:
                counts['physical_children'] = int(physical_child_count)
            self.folder_counts[key] = counts
            item = self.items.get(key)
            if item:
                item['size'] = total_size or 0

    def remove_path(self, path):
        if not path:
            return False

        key = self._key(path)
        with self._lock:
            self.filesystem_snapshots.clear()
            item = self.items.get(key)
            parent_path = item.get('location') if item else os.path.dirname(path)
            parent_key = self._key(parent_path)
            exists = item is not None or key in self.children
            if not exists:
                siblings = self.children.get(parent_key, [])
                if key not in siblings:
                    return False

            subtree_keys = []
            pending = [key]
            visited = set()
            while pending:
                current_key = pending.pop()
                if current_key in visited:
                    continue
                visited.add(current_key)
                subtree_keys.append(current_key)
                pending.extend(self.children.get(current_key, []))

            removed_size = 0
            removed_files = 0
            removed_folders = 0
            for subtree_key in subtree_keys:
                subtree_item = self.items.get(subtree_key)
                if not subtree_item:
                    continue
                if subtree_item.get('is_dir'):
                    removed_folders += 1
                else:
                    removed_files += 1
                    removed_size += subtree_item.get('size', 0) or 0

            if item and item.get('is_dir'):
                summary_counts = self.folder_counts.get(key, {})
                removed_size = max(removed_size, self.folder_sizes.get(key, 0) or 0)
                removed_files = max(removed_files, summary_counts.get('files', 0) or 0)
                removed_folders = max(
                    removed_folders,
                    summary_counts.get('folders', 0) or 0,
                )

            siblings = self.children.get(parent_key)
            if siblings is not None:
                self.children[parent_key] = [
                    child_key for child_key in siblings if child_key != key
                ]

            ancestor_key = parent_key
            ancestor_seen = set()
            while ancestor_key and ancestor_key not in ancestor_seen:
                ancestor_seen.add(ancestor_key)
                ancestor_item = self.items.get(ancestor_key)
                if not ancestor_item:
                    break

                updated_size = max(
                    0,
                    (self.folder_sizes.get(ancestor_key, 0) or 0) - removed_size,
                )
                self.folder_sizes[ancestor_key] = updated_size
                ancestor_item['size'] = updated_size

                counts = self.folder_counts.get(ancestor_key)
                if counts is not None:
                    counts['files'] = max(
                        0,
                        (counts.get('files', 0) or 0) - removed_files,
                    )
                    counts['folders'] = max(
                        0,
                        (counts.get('folders', 0) or 0) - removed_folders,
                    )
                    if ancestor_key == parent_key:
                        counts['children'] = len(self.children.get(parent_key, []))

                ancestor_path = ancestor_item.get('location')
                if ancestor_path is None:
                    break
                ancestor_key = self._key(ancestor_path)

            for subtree_key in subtree_keys:
                self.items.pop(subtree_key, None)
                self.children.pop(subtree_key, None)
                self.folder_sizes.pop(subtree_key, None)
                self.folder_counts.pop(subtree_key, None)

            self.running_total_size = max(
                0,
                self.running_total_size - removed_size,
            )
            return True

    def has_children_for(self, path):
        with self._lock:
            return self._key(path) in self.children

    def child_count(self, path):
        with self._lock:
            return len(self.children.get(self._key(path), []))

    def has_folder_children(self, path):
        with self._lock:
            return any(
                self.items.get(child_key, {}).get('is_dir', False)
                for child_key in self.children.get(self._key(path), [])
            )

    def item_count(self):
        with self._lock:
            return len(self.items)

    def descendant_count(self, path):
        key = self._key(path)
        with self._lock:
            count = 0
            pending = list(self.children.get(key, []))
            visited = set()
            while pending:
                child_key = pending.pop()
                if child_key in visited:
                    continue
                visited.add(child_key)
                count += 1
                pending.extend(self.children.get(child_key, []))
            return count

    def snapshot_subtree(self, path):
        key = self._key(path)
        with self._lock:
            if key not in self.children:
                return None

            rows = {}
            physical_child_keys = set()
            visible_child_keys = set()
            pending = [key]
            visited = set()
            while pending:
                parent_key = pending.pop()
                if parent_key in visited:
                    continue
                visited.add(parent_key)

                child_keys = list(self.children.get(parent_key, []))
                if child_keys:
                    visible_child_keys.add(parent_key)
                physical_count = self.folder_counts.get(parent_key, {}).get(
                    'physical_children',
                    -1,
                )
                if physical_count != 0:
                    physical_child_keys.add(parent_key)

                for child_key in child_keys:
                    item = self.items.get(child_key)
                    if not item:
                        continue
                    is_folder = bool(item.get('is_dir', False))
                    size = (
                        self.folder_sizes.get(child_key, item.get('size', 0))
                        if is_folder
                        else item.get('size', 0)
                    ) or 0
                    rows[child_key] = (
                        item.get('path', ''),
                        item.get('name', ''),
                        int(is_folder),
                        size,
                        item.get('last_modified', 0),
                        item.get('location'),
                    )
                    if is_folder:
                        pending.append(child_key)

            return rows, physical_child_keys, visible_child_keys

    def filesystem_snapshot(self, key):
        with self._lock:
            return self.filesystem_snapshots.get(key)

    def store_filesystem_snapshot(self, key, snapshot):
        with self._lock:
            if (
                key not in self.filesystem_snapshots
                and len(self.filesystem_snapshots) >= 3
            ):
                oldest_key = next(iter(self.filesystem_snapshots))
                self.filesystem_snapshots.pop(oldest_key, None)
            self.filesystem_snapshots[key] = snapshot

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
