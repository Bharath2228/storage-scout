import json
import os

from PyQt6.QtCore import (
    QThread,
    pyqtSignal,
)

from ..constants import EMPTY_FOLDER_SQL, VIDEO_EXTENSIONS
from ..path_utils import _path_key, equivalent_path_variants
from ..sql_utils import build_sort_order_clause, descendant_scope_sql, escape_sql_like, filesystem_folder_has_visible_entries, sort_tree_siblings, tree_sort_value


class PageLoadThread(QThread):
    page_ready = pyqtSignal(int, dict)
    page_failed = pyqtSignal(int, str)

    class _Cancelled(Exception):
        pass

    def __init__(self, request_id, options, parent=None):
        super().__init__(parent)
        self.request_id = request_id
        self.options = options
        self.is_cancelled = False
        self._connection = None

    def cancel(self):
        self.is_cancelled = True
        connection = self._connection
        if connection is not None:
            try:
                connection.interrupt()
            except Exception:
                pass

    def _raise_if_cancelled(self):
        if self.is_cancelled:
            raise PageLoadThread._Cancelled()

    def _relative_display_location(self, folder_path):
        if not folder_path:
            return ""

        root = self.options.get('scan_root') or ""
        normalized_folder = os.path.normpath(folder_path)

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

    def run(self):
        try:
            self.page_ready.emit(self.request_id, self._load())
        except PageLoadThread._Cancelled:
            return
        except Exception as exc:
            if not self.is_cancelled:
                self.page_failed.emit(self.request_id, str(exc))

    def _load(self):
        from src.file_index_tool import FileIndexTool

        self._raise_if_cancelled()
        limit = self.options['limit']
        offset = self.options['offset']
        paginated = self.options.get('paginated', True)
        view_mode = self.options['view_mode']
        status_filter = self.options['status_filter']
        age_cutoff = self.options['age_cutoff']
        videos_only = self.options['videos_only']
        name_filter = (self.options.get('name_filter') or '').strip()
        extension_filter = self.options.get('extension_filter')
        folder_scope = self.options.get('folder_scope')
        lazy_show_all_tree = self.options.get('lazy_show_all_tree', False)
        filtered_expanded_tree = self.options.get('filtered_expanded_tree', False)

        if lazy_show_all_tree:
            return self._load_lazy_show_all_tree()

        query = "SELECT path, name, is_folder, size, modified_time, parent_path FROM file_index"
        count_query = "SELECT COUNT(*) FROM file_index"
        where_clauses = []
        params = []

        if status_filter == 'Inactive':
            if view_mode == 'Tree':
                where_clauses.append("is_folder = 0")
            if age_cutoff is not None:
                where_clauses.append("modified_time <= ?")
                params.append(age_cutoff)
            else:
                where_clauses.append("1=1")
        elif status_filter == 'Empty':
            if age_cutoff is not None:
                where_clauses.append("modified_time <= ?")
                params.append(age_cutoff)
            where_clauses.append(EMPTY_FOLDER_SQL)
        elif status_filter == 'Active':
            if age_cutoff is not None:
                where_clauses.append("modified_time > ?")
                params.append(age_cutoff)
            else:
                where_clauses.append("1=0")
        elif age_cutoff is not None and not videos_only:
            where_clauses.append("modified_time <= ?")
            params.append(age_cutoff)

        if videos_only:
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

        if name_filter:
            where_clauses.append("name LIKE ? ESCAPE '\\' COLLATE NOCASE")
            params.append(f"%{escape_sql_like(name_filter)}%")

        if extension_filter is not None:
            where_clauses.append("is_folder = 0")
            where_clauses.append("extension = ? COLLATE NOCASE")
            params.append(extension_filter)

        if folder_scope:
            scope_sql, scope_params = descendant_scope_sql(folder_scope)
            where_clauses.append(f"({scope_sql})")
            params.extend(scope_params)

        scan_root = self.options.get('scan_root')
        if scan_root:
            root_variants = equivalent_path_variants(scan_root)
            placeholders = ",".join("?" * len(root_variants))
            where_clauses.append(
                f"path COLLATE NOCASE NOT IN ({placeholders})"
            )
            params.extend(root_variants)

        if where_clauses:
            where_sql = " WHERE " + " AND ".join(where_clauses)
            query += where_sql
            count_query += where_sql

        folders_with_children = set()
        filesystem_scope_fallback = False
        folder_cache = self.options.get('folder_cache')
        cached_scope_result = None
        if (
            folder_scope
            and folder_cache is not None
            and hasattr(folder_cache, 'summary_descendant_count')
            and folder_cache.summary_descendant_count(folder_scope) is not None
        ):
            cached_scope_result = self._load_filtered_filesystem_scope(folder_scope)

        if cached_scope_result is not None:
            (
                rows,
                original_fetched_order,
                original_fetched_path_keys,
                folders_with_children,
                total_matches,
            ) = cached_scope_result
            filesystem_scope_fallback = True
        else:
            tool = FileIndexTool()
            self._connection = tool.conn
            try:
                cursor = tool.conn.cursor()
                self._raise_if_cancelled()
                cursor.execute(count_query, params)
                total_matches = cursor.fetchone()[0]

                order_by_sql = build_sort_order_clause(
                    view_mode,
                    self.options['sort_column'],
                    self.options['sort_desc'],
                )
                query += f" ORDER BY {order_by_sql}"
                if paginated:
                    query += f" LIMIT {limit} OFFSET {offset}"
                cursor.execute(query, params)
                rows = cursor.fetchall()
                self._raise_if_cancelled()
                if scan_root:
                    scan_root_key = _path_key(scan_root)
                    filtered_rows = [
                        row for row in rows
                        if _path_key(row[0]) != scan_root_key
                    ]
                    total_matches = max(
                        0,
                        total_matches - (len(rows) - len(filtered_rows)),
                    )
                    rows = filtered_rows
                if not rows and total_matches == 0 and folder_scope:
                    filesystem_result = self._load_filtered_filesystem_scope(folder_scope)
                    if filesystem_result is not None:
                        (
                            rows,
                            original_fetched_order,
                            original_fetched_path_keys,
                            folders_with_children,
                            total_matches,
                        ) = filesystem_result
                        filesystem_scope_fallback = True

                if not filesystem_scope_fallback:
                    original_fetched_order = {
                        _path_key(row[0]): index
                        for index, row in enumerate(rows)
                    }

                    fetched_paths = {row[0] for row in rows}
                    missing_parents = set()
                    for row in rows:
                        parent_path = row[5]
                        while parent_path and parent_path not in fetched_paths and parent_path not in missing_parents:
                            missing_parents.add(parent_path)
                            parent_path = os.path.dirname(parent_path) if '\\' in parent_path or '/' in parent_path else None

                    original_fetched_paths = {row[0] for row in rows}
                    original_fetched_path_keys = {
                        _path_key(path)
                        for path in original_fetched_paths
                    }

                    if missing_parents and view_mode == 'Tree':
                        parents_list = list(missing_parents)
                        for index in range(0, len(parents_list), 900):
                            batch = parents_list[index:index + 900]
                            placeholders = ','.join('?' * len(batch))
                            cursor.execute(
                                f"SELECT path, name, is_folder, size, modified_time, parent_path FROM file_index WHERE path COLLATE NOCASE IN ({placeholders})",
                                batch,
                            )
                            rows.extend(cursor.fetchall())

                    folder_paths = [row[0] for row in rows if row[2]]
                    for index in range(0, len(folder_paths), 900):
                        batch = folder_paths[index:index + 900]
                        placeholders = ",".join("?" * len(batch))
                        cursor.execute(
                            f"SELECT DISTINCT parent_path FROM file_index "
                            f"WHERE parent_path COLLATE NOCASE IN ({placeholders})",
                            batch,
                        )
                        folders_with_children.update(
                            _path_key(parent_path)
                            for (parent_path,) in cursor.fetchall()
                            if parent_path
                        )
            finally:
                self._connection = None
                tool.close()

        root_node = {'name': 'root', 'is_dir': True, 'path': 'C:/', 'status': 'Active', 'children': []}
        nodes_by_path = {}

        for path, name, is_folder, size, modified_time, parent_path in rows:
            is_stale = False
            if age_cutoff and modified_time <= age_cutoff:
                is_stale = True
            elif not age_cutoff:
                is_stale = True

            status = 'Inactive' if is_stale else 'Active'
            path_key = os.path.normcase(os.path.normpath(path))
            if is_folder and folder_cache is not None:
                size = getattr(folder_cache, 'folder_sizes', {}).get(path_key, size or 0)
            if status_filter == 'Empty' and is_folder and path_key in original_fetched_path_keys:
                status = 'Empty'

            is_context = (path_key not in original_fetched_path_keys) if view_mode == 'Tree' else False
            actual_parent = parent_path if view_mode == 'Tree' else None
            display_location = self._relative_display_location(parent_path) if view_mode != 'Tree' else None

            nodes_by_path[path] = {
                'name': name,
                'path': path,
                'is_dir': bool(is_folder),
                'size': size or 0,
                'last_modified': modified_time,
                'status': status,
                'children': [],
                '_children_loaded': (
                    (bool(filtered_expanded_tree) and not filesystem_scope_fallback)
                    or not (is_folder and path_key in folders_with_children)
                ),
                '_page_order': original_fetched_order.get(path_key),
                '_parent_path': actual_parent,
                '_is_context_fetched': is_context,
                '_is_page_result': path_key in original_fetched_path_keys,
                'location': parent_path if view_mode != 'Tree' else None,
                'display_location': display_location,
            }

        for path, node in nodes_by_path.items():
            parent_path = node.pop('_parent_path', None)
            parent_node = nodes_by_path.get(parent_path)
            if parent_node:
                parent_node['children'].append(node)
            else:
                root_node['children'].append(node)

        if filesystem_scope_fallback and view_mode == 'Tree':
            for node in nodes_by_path.values():
                if node.get('children'):
                    node['_children_loaded'] = True

        if name_filter and view_mode == 'Tree':
            for node in nodes_by_path.values():
                if node.get('children'):
                    node['_children_loaded'] = True

        def sort_tree_for_page(node):
            children = node.get('children', [])
            if not children:
                return node.get('_page_order', float('inf'))

            child_orders = [sort_tree_for_page(child) for child in children]
            node_order = node.get('_page_order')
            if node_order is None:
                node_order = min(child_orders) if child_orders else float('inf')
            node['_page_order'] = node_order

            children.sort(key=lambda child: (
                child.get('_page_order', float('inf')),
                1 if child.get('_is_context_fetched') else 0,
                str(child.get('path', '')).lower(),
            ))
            return node_order

        if view_mode == 'Tree':
            self._hide_scan_root_context(root_node)
            if folder_scope:
                self._hide_folder_scope_context(root_node, folder_scope)
            sort_tree_siblings(
                root_node,
                self.options['sort_column'],
                self.options['sort_desc'],
            )

        return {
            'root_node': root_node,
            'view_mode': view_mode,
            'rows_count': len(rows),
            'total_matches': total_matches,
            'limit': limit,
            'offset': offset,
            'page': self.options['page'],
            'paginated': paginated,
            'lazy_show_all_tree': False,
            'filtered_expanded_tree': filtered_expanded_tree,
            'name_filter': name_filter,
            'extension_filter': extension_filter,
            'debug_info': "filtered_source=filesystem" if filesystem_scope_fallback else None,
            'filesystem_scope_fallback': filesystem_scope_fallback,
        }

    def _load_filtered_filesystem_scope(self, folder_scope):
        cache = self.options.get('folder_cache')
        exclusions = self.options.get('scan_exclusions')
        exclusion_key = json.dumps(
            exclusions.to_dict() if exclusions is not None else {},
            sort_keys=True,
        )
        snapshot_key = (_path_key(folder_scope), exclusion_key)
        cached_snapshot = None
        if cache is not None and hasattr(cache, 'filesystem_snapshot'):
            cached_snapshot = cache.filesystem_snapshot(snapshot_key)
        if (
            cached_snapshot is None
            and cache is not None
            and hasattr(cache, 'snapshot_subtree')
        ):
            cached_snapshot = cache.snapshot_subtree(folder_scope)
            if (
                cached_snapshot is not None
                and hasattr(cache, 'store_filesystem_snapshot')
            ):
                cache.store_filesystem_snapshot(snapshot_key, cached_snapshot)
        if cached_snapshot is not None:
            all_rows, physical_child_keys, visible_child_keys = cached_snapshot
        else:
            all_rows = {}
            physical_child_keys = set()
            visible_child_keys = set()
            pending = [os.path.normpath(folder_scope)]
            visited = set()

            while pending:
                self._raise_if_cancelled()
                folder_path = pending.pop()
                folder_key = _path_key(folder_path)
                if folder_key in visited:
                    continue
                visited.add(folder_key)
                try:
                    with os.scandir(folder_path) as entries:
                        folder_entries = list(entries)
                except OSError:
                    continue

                if folder_entries:
                    physical_child_keys.add(folder_key)
                for entry in folder_entries:
                    self._raise_if_cancelled()
                    try:
                        is_folder = entry.is_dir(follow_symlinks=True)
                        stat_result = entry.stat(follow_symlinks=True)
                    except OSError:
                        continue
                    if self._filesystem_entry_is_excluded(
                        entry.name,
                        is_folder,
                        0 if is_folder else stat_result.st_size,
                    ):
                        continue
                    visible_child_keys.add(folder_key)
                    row = (
                        entry.path,
                        entry.name,
                        int(is_folder),
                        0 if is_folder else stat_result.st_size,
                        stat_result.st_mtime,
                        folder_path,
                    )
                    all_rows[_path_key(entry.path)] = row
                    if is_folder:
                        pending.append(entry.path)

            all_rows = self._prune_exclusion_only_folders(
                all_rows,
                physical_child_keys,
            )
            visible_child_keys = {
                _path_key(row[5])
                for row in all_rows.values()
                if row[5]
            }
            folder_sizes = {
                key: 0
                for key, row in all_rows.items()
                if row[2]
            }
            for key, row in sorted(
                all_rows.items(),
                key=lambda item: item[0].count(os.sep),
                reverse=True,
            ):
                self._raise_if_cancelled()
                path, name, is_folder, size, modified_time, parent_path = row
                effective_size = (
                    folder_sizes.get(key, 0)
                    if is_folder
                    else (size or 0)
                )
                if is_folder:
                    all_rows[key] = (
                        path,
                        name,
                        is_folder,
                        effective_size,
                        modified_time,
                        parent_path,
                    )
                parent_key = _path_key(parent_path)
                if parent_key in folder_sizes:
                    folder_sizes[parent_key] += effective_size
            if cache is not None and hasattr(cache, 'store_filesystem_snapshot'):
                cache.store_filesystem_snapshot(
                    snapshot_key,
                    (all_rows, physical_child_keys, visible_child_keys),
                )

        matched_rows = [
            row for row in all_rows.values()
            if self._filesystem_row_matches(
                row,
                _path_key(row[0]) in physical_child_keys,
            )
        ]
        matched_rows.sort(
            key=lambda row: (
                tree_sort_value(
                    {
                        'path': row[0],
                        'name': row[1],
                        'is_dir': bool(row[2]),
                        'size': row[3] or 0,
                        'last_modified': row[4],
                        'status': 'Active',
                    },
                    self.options['sort_column'],
                ),
                (row[1] or '').lower(),
                (row[0] or '').lower(),
            ),
            reverse=self.options['sort_desc'],
        )
        total_matches = len(matched_rows)
        if self.options.get('paginated', True):
            offset = self.options['offset']
            matched_rows = matched_rows[offset:offset + self.options['limit']]

        original_order = {
            _path_key(row[0]): index
            for index, row in enumerate(matched_rows)
        }
        original_keys = set(original_order)
        rows_by_key = {_path_key(row[0]): row for row in matched_rows}
        if self.options['view_mode'] == 'Tree':
            scope_key = _path_key(folder_scope)
            for row in matched_rows:
                parent_path = row[5]
                while parent_path and _path_key(parent_path) != scope_key:
                    parent_key = _path_key(parent_path)
                    parent_row = all_rows.get(parent_key)
                    if parent_row is None:
                        break
                    rows_by_key.setdefault(parent_key, parent_row)
                    parent_path = parent_row[5]

        return (
            list(rows_by_key.values()),
            original_order,
            original_keys,
            visible_child_keys,
            total_matches,
        )

    def _prune_exclusion_only_folders(self, all_rows, physical_child_keys):
        children_by_parent = {}
        for key, row in all_rows.items():
            children_by_parent.setdefault(_path_key(row[5]), []).append((key, row))

        visible_folder_keys = set()
        hidden_folder_keys = set()
        folder_rows = [
            (key, row)
            for key, row in all_rows.items()
            if row[2]
        ]
        folder_rows.sort(
            key=lambda item: item[0].count(os.sep),
            reverse=True,
        )
        for key, _row in folder_rows:
            children = children_by_parent.get(key, [])
            is_physically_empty = key not in physical_child_keys
            has_visible_file = any(not child_row[2] for _child_key, child_row in children)
            has_visible_folder = any(
                child_key in visible_folder_keys
                for child_key, child_row in children
                if child_row[2]
            )
            if is_physically_empty or has_visible_file or has_visible_folder:
                visible_folder_keys.add(key)
            else:
                hidden_folder_keys.add(key)

        return {
            key: row
            for key, row in all_rows.items()
            if key not in hidden_folder_keys
        }

    def _filesystem_row_matches(self, row, has_children):
        _path, name, is_folder, _size, modified_time, _parent_path = row
        is_folder = bool(is_folder)
        view_mode = self.options['view_mode']
        status_filter = self.options['status_filter']
        age_cutoff = self.options['age_cutoff']
        videos_only = self.options['videos_only']
        name_filter = (self.options.get('name_filter') or '').strip().casefold()
        extension_filter = self.options.get('extension_filter')
        extension = os.path.splitext(name)[1].lower() if not is_folder else ""

        if view_mode == 'Files' and is_folder:
            return False
        if view_mode == 'Folders' and not is_folder:
            return False
        if name_filter and name_filter not in name.casefold():
            return False
        if extension_filter is not None and (is_folder or extension != extension_filter.lower()):
            return False
        if videos_only and (is_folder or extension not in VIDEO_EXTENSIONS):
            return False

        if status_filter == 'Empty':
            if (
                not is_folder
                or has_children
                or name.lower() in ('.git', '__pycache__', 'venv', '.venv', 'node_modules')
            ):
                return False
            return age_cutoff is None or modified_time <= age_cutoff
        if status_filter == 'Inactive':
            if view_mode == 'Tree' and is_folder:
                return False
            return age_cutoff is None or modified_time <= age_cutoff
        if status_filter == 'Active':
            return age_cutoff is not None and modified_time > age_cutoff
        if age_cutoff is not None:
            return modified_time <= age_cutoff
        return True

    def _filesystem_entry_is_excluded(self, name, is_folder, size):
        exclusions = self.options.get('scan_exclusions')
        if exclusions is None:
            return False
        if is_folder:
            return exclusions.matches_excluded_folder(name)
        extension = os.path.splitext(name)[1].lower()
        return (
            exclusions.matches_excluded_extension(extension)
            or (
                exclusions.min_file_size_bytes > 0
                and (size or 0) < exclusions.min_file_size_bytes
            )
        )

    def _filesystem_folder_size(self, folder_path):
        cache = self.options.get('folder_cache')
        if cache is not None:
            folder_sizes = getattr(cache, 'folder_sizes', {})
            folder_key = _path_key(folder_path)
            if folder_key in folder_sizes:
                return folder_sizes[folder_key] or 0
        total = 0
        for current, dir_names, file_names in os.walk(folder_path):
            self._raise_if_cancelled()
            dir_names[:] = [
                name for name in dir_names
                if not self._filesystem_entry_is_excluded(name, True, 0)
            ]
            for file_name in file_names:
                file_path = os.path.join(current, file_name)
                try:
                    size = os.path.getsize(file_path)
                except OSError:
                    continue
                if not self._filesystem_entry_is_excluded(file_name, False, size):
                    total += size
        return total

    def _hide_scan_root_context(self, root_node):
        scan_root = self.options.get('scan_root')
        if not scan_root:
            return

        scan_root_key = os.path.normcase(os.path.normpath(scan_root))
        children = root_node.get('children', [])
        replacement_children = []

        for child in children:
            child_path = child.get('path')
            if child_path and os.path.normcase(os.path.normpath(child_path)) == scan_root_key:
                replacement_children.extend(child.get('children', []))
            else:
                replacement_children.append(child)

        root_node['children'] = replacement_children

    def _hide_folder_scope_context(self, root_node, folder_scope):
        scope_key = _path_key(folder_scope)
        stack = list(root_node.get('children', []))
        while stack:
            node = stack.pop()
            node_path = node.get('path')
            if node_path and _path_key(node_path) == scope_key:
                root_node['children'] = list(node.get('children', []))
                return
            stack.extend(node.get('children', []))

    def _load_lazy_show_all_tree(self):
        from src.file_index_tool import FileIndexTool

        root_path = self.options.get('folder_scope') or self.options.get('scan_root') or ""
        root_path = os.path.normpath(root_path) if root_path else root_path
        is_scoped = bool(self.options.get('folder_scope'))
        cache = self.options.get('folder_cache')
        cached_children = None
        if cache and cache.child_count(root_path) > 0:
            cached_children = cache.children_for(
                root_path,
                self.options['sort_column'],
                self.options['sort_desc'],
            )
        if cached_children:
            total_matches = (
                cache.descendant_count(root_path)
                if is_scoped
                else max(cache.item_count() - 1, 0)
            )
            root_node = {
                'name': 'root',
                'is_dir': True,
                'path': root_path,
                'status': 'Active',
                'children': cached_children,
                '_children_loaded': True,
            }
            return {
                'root_node': root_node,
                'view_mode': 'Tree',
                'rows_count': len(cached_children),
                'total_matches': total_matches,
                'limit': max(total_matches, len(cached_children), 1),
                'offset': 0,
                'page': 0,
                'paginated': False,
                'lazy_show_all_tree': True,
                'debug_info': f"show_all_source=cache children={len(cached_children)} total={total_matches}",
            }

        tool = FileIndexTool()
        self._connection = tool.conn
        try:
            cursor = tool.conn.cursor()
            self._raise_if_cancelled()
            if is_scoped:
                stored_root_path = root_path
                total_matches = (
                    cache.summary_descendant_count(root_path)
                    if cache is not None
                    and hasattr(cache, 'summary_descendant_count')
                    else None
                )
                if total_matches is None:
                    path_variants = equivalent_path_variants(root_path)
                    placeholders = ",".join("?" * len(path_variants))
                    cursor.execute(
                        "SELECT file_count, folder_count FROM folder_summary "
                        f"WHERE path IN ({placeholders}) LIMIT 1",
                        path_variants,
                    )
                    summary_row = cursor.fetchone()
                    total_matches = (
                        max(0, int(summary_row[0] or 0) + int(summary_row[1] or 0) - 1)
                        if summary_row
                        else None
                    )
            else:
                cursor.execute("SELECT COUNT(*) FROM file_index")
                total_rows = cursor.fetchone()[0] or 0

            if not is_scoped:
                cursor.execute(
                    """
                    SELECT CASE
                               WHEN path = ? COLLATE NOCASE THEN path
                               ELSE root
                           END
                    FROM file_index
                    WHERE path = ? COLLATE NOCASE OR root = ? COLLATE NOCASE
                    ORDER BY CASE WHEN path = ? COLLATE NOCASE THEN 0 ELSE 1 END,
                             length(path) ASC
                    LIMIT 1
                    """,
                    (root_path, root_path, root_path, root_path),
                )
                stored_root_row = cursor.fetchone()
                if stored_root_row:
                    stored_root_path = stored_root_row[0]
                else:
                    cursor.execute(
                        """
                        SELECT root
                        FROM file_index
                        GROUP BY root
                        ORDER BY COUNT(*) DESC, length(root) ASC
                        LIMIT 1
                        """
                    )
                    fallback_root_row = cursor.fetchone()
                    stored_root_path = fallback_root_row[0] if fallback_root_row else root_path

                cursor.execute(
                    "SELECT 1 FROM file_index WHERE path = ? COLLATE NOCASE LIMIT 1",
                    (stored_root_path,),
                )
                total_matches = max(total_rows - (1 if cursor.fetchone() else 0), 0)

            parent_variants = (
                equivalent_path_variants(stored_root_path)
                if is_scoped
                else [stored_root_path]
            )
            parent_placeholders = ",".join("?" * len(parent_variants))
            cursor.execute(
                "SELECT f.path, f.name, f.is_folder, f.size, f.modified_time, f.parent_path, "
                "(SELECT 1 FROM file_index child WHERE child.parent_path = f.path COLLATE NOCASE LIMIT 1) "
                "FROM file_index f "
                f"WHERE f.parent_path COLLATE NOCASE IN ({parent_placeholders}) "
                f"ORDER BY {build_sort_order_clause('Tree', self.options['sort_column'], self.options['sort_desc'])}",
                parent_variants,
            )
            rows = cursor.fetchall()
            load_source = "parent_path"
            if not rows and total_matches and not is_scoped:
                rows = self._load_direct_children_from_root_index(cursor, stored_root_path)
                load_source = "root_index_direct"
            if not rows and total_matches and not is_scoped:
                rows = self._load_direct_children_by_path(cursor, stored_root_path)
                load_source = "path_prefix_direct"
            if not rows and total_matches and not is_scoped:
                rows = self._load_indexed_rows_for_root(cursor, stored_root_path)
                load_source = "root_index_all"
            if not rows and is_scoped:
                rows = self._load_direct_children_from_filesystem(
                    stored_root_path,
                    cursor=cursor,
                )
                if rows:
                    load_source = "filesystem"
            if rows:
                folder_metadata = self._folder_metadata_for_filesystem_rows(
                    cursor,
                    [row[0] for row in rows if row[2]],
                )
                if folder_metadata:
                    enriched_rows = []
                    for row in rows:
                        path, name, is_folder, size, modified_time, parent_path, has_child = row
                        metadata = (
                            folder_metadata.get(_path_key(path))
                            if is_folder
                            else None
                        )
                        if metadata is not None:
                            cached_size, child_count = metadata
                            size = cached_size
                            has_child = int((child_count or 0) > 0)
                        enriched_rows.append((
                            path,
                            name,
                            is_folder,
                            size,
                            modified_time,
                            parent_path,
                            has_child,
                        ))
                    rows = enriched_rows
            if is_scoped and total_matches is None:
                total_matches = len(rows)
        finally:
            self._connection = None
            tool.close()

        root_node = {
            'name': 'root',
            'is_dir': True,
            'path': stored_root_path,
            'status': 'Active',
            'children': [],
            '_children_loaded': True,
        }

        for path, name, is_folder, size, modified_time, parent_path, has_child in rows:
            root_node['children'].append({
                'name': name,
                'path': path,
                'is_dir': bool(is_folder),
                'size': size or 0,
                'last_modified': modified_time,
                'status': 'Active',
                'children': [],
                '_children_loaded': not (is_folder and has_child),
                '_is_page_result': True,
                'location': parent_path,
                'display_location': parent_path,
            })

        if not is_scoped:
            self._hide_scan_root_context(root_node)

        return {
            'root_node': root_node,
            'view_mode': 'Tree',
            'rows_count': len(rows),
            'total_matches': total_matches,
            'limit': max(total_matches, len(rows), 1),
            'offset': 0,
            'page': 0,
            'paginated': False,
            'lazy_show_all_tree': True,
            'debug_info': f"show_all_source={load_source} rows={len(rows)} total={total_matches} root={stored_root_path}",
        }

    def _is_direct_child_path(self, root_path, child_path):
        root = os.path.normpath(str(root_path or "")).rstrip("\\/")
        child = os.path.normpath(str(child_path or "")).rstrip("\\/")
        if not root or not child:
            return False
        if os.path.normcase(root) == os.path.normcase(child):
            return False

        try:
            relative = os.path.relpath(child, root)
        except ValueError:
            relative = ""

        if relative and relative != "." and not relative.startswith(".."):
            return "\\" not in relative and "/" not in relative

        root_key = os.path.normcase(root)
        child_key = os.path.normcase(child)
        for separator in ("\\", "/"):
            prefix = root_key + separator
            if child_key.startswith(prefix):
                remainder = child_key[len(prefix):]
                return "\\" not in remainder and "/" not in remainder
        return False

    def _load_direct_children_from_filesystem(self, root_path, cursor=None):
        entries_data = []
        cache = self.options.get('folder_cache')
        try:
            with os.scandir(root_path) as entries:
                for entry in entries:
                    self._raise_if_cancelled()
                    try:
                        is_folder = entry.is_dir(follow_symlinks=True)
                        stat_result = entry.stat(follow_symlinks=True)
                    except OSError:
                        continue
                    if is_folder and self._filesystem_entry_is_excluded(
                        entry.name,
                        True,
                        0,
                    ):
                        continue
                    if is_folder:
                        if (
                            cache is not None
                            and hasattr(cache, 'is_exclusion_hidden')
                            and cache.is_exclusion_hidden(entry.path)
                        ):
                            continue
                    size = 0 if is_folder else stat_result.st_size
                    if not is_folder and self._filesystem_entry_is_excluded(
                        entry.name,
                        False,
                        size,
                    ):
                        continue
                    entries_data.append((
                        entry.path,
                        entry.name,
                        int(is_folder),
                        size,
                        stat_result.st_mtime,
                        root_path,
                    ))
        except OSError:
            return []

        folder_metadata = self._folder_metadata_for_filesystem_rows(
            cursor,
            [row[0] for row in entries_data if row[2]],
        )
        rows = []
        for path, name, is_folder, size, modified_time, parent_path in entries_data:
            has_child = 0
            if is_folder:
                metadata = folder_metadata.get(_path_key(path))
                if metadata is not None:
                    size, child_count = metadata
                    has_child = int((child_count or 0) > 0)
                else:
                    has_child = int(self._filesystem_path_has_children(path))
            rows.append((
                path,
                name,
                is_folder,
                size,
                modified_time,
                parent_path,
                has_child,
            ))
        return self._sort_show_all_rows(rows)

    def _folder_metadata_for_filesystem_rows(self, cursor, folder_paths):
        metadata = {}
        cache = self.options.get('folder_cache')
        unresolved = []
        indexed_root = None
        display_root = self.options.get('scan_root') or ""
        if cursor is not None and display_root:
            try:
                cursor.execute(
                    """
                    SELECT root
                    FROM file_index
                    GROUP BY root
                    ORDER BY COUNT(*) DESC, length(root) ASC
                    LIMIT 1
                    """
                )
                indexed_root_row = cursor.fetchone()
                indexed_root = indexed_root_row[0] if indexed_root_row else None
            except Exception:
                indexed_root = None

        def indexed_alias(path):
            if not indexed_root or not display_root:
                return path
            try:
                relative = os.path.relpath(
                    os.path.normpath(path),
                    os.path.normpath(display_root),
                )
            except ValueError:
                return path
            if relative == ".":
                return os.path.normpath(indexed_root)
            if relative.startswith(".."):
                return path
            return os.path.normpath(os.path.join(indexed_root, relative))

        for path in folder_paths:
            key = _path_key(path)
            alias = indexed_alias(path)
            cached_metadata = (
                cache.folder_metadata(path)
                if cache is not None and hasattr(cache, 'folder_metadata')
                else None
            )
            if (
                cached_metadata is None
                and cache is not None
                and hasattr(cache, 'folder_metadata')
                and _path_key(alias) != key
            ):
                cached_metadata = cache.folder_metadata(alias)
            if cached_metadata is None:
                unresolved.append((path, alias))
                continue
            metadata[key] = cached_metadata

        if cursor is None:
            return metadata
        for start in range(0, len(unresolved), 900):
            batch = unresolved[start:start + 900]
            if not batch:
                continue
            lookup_paths = list(dict.fromkeys(
                candidate
                for original, alias in batch
                for candidate in (original, alias)
            ))
            placeholders = ",".join("?" * len(lookup_paths))
            cursor.execute(
                "SELECT path, total_size, child_count FROM folder_summary "
                f"WHERE path COLLATE NOCASE IN ({placeholders})",
                lookup_paths,
            )
            rows_by_key = {
                _path_key(path): (total_size or 0, child_count or 0)
                for path, total_size, child_count in cursor.fetchall()
            }
            for original, alias in batch:
                resolved = (
                    rows_by_key.get(_path_key(original))
                    or rows_by_key.get(_path_key(alias))
                )
                if resolved is not None:
                    metadata[_path_key(original)] = resolved
        return metadata

    def _filesystem_path_has_children(self, folder_path, folders_only=False):
        try:
            with os.scandir(folder_path) as entries:
                for entry in entries:
                    try:
                        is_folder = entry.is_dir(follow_symlinks=True)
                        size = 0 if is_folder else entry.stat(follow_symlinks=True).st_size
                    except OSError:
                        continue
                    if (
                        is_folder
                        and self.options.get('folder_cache') is not None
                        and hasattr(
                            self.options.get('folder_cache'),
                            'is_exclusion_hidden',
                        )
                        and self.options.get(
                            'folder_cache',
                        ).is_exclusion_hidden(entry.path)
                    ):
                        continue
                    if self._filesystem_entry_is_excluded(entry.name, is_folder, size):
                        continue
                    if not folders_only or is_folder:
                        return True
        except OSError:
            return False
        return False

    def _load_direct_children_from_root_index(self, cursor, root_path):
        cursor.execute(
            """
            SELECT f.path, f.name, f.is_folder, f.size, f.modified_time, f.parent_path,
                   (SELECT 1 FROM file_index child WHERE child.parent_path = f.path COLLATE NOCASE LIMIT 1)
            FROM file_index f
            WHERE f.root = ? COLLATE NOCASE
              AND f.path != ? COLLATE NOCASE
            """,
            (root_path, root_path),
        )

        rows = []
        seen = set()
        for row in cursor.fetchall():
            path = row[0]
            if not self._is_direct_child_path(root_path, path):
                continue
            key = os.path.normcase(os.path.normpath(path))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)

        return self._sort_show_all_rows(rows)

    def _load_indexed_rows_for_root(self, cursor, root_path):
        cursor.execute(
            """
            SELECT f.path, f.name, f.is_folder, f.size, f.modified_time, f.parent_path,
                   (SELECT 1 FROM file_index child WHERE child.parent_path = f.path COLLATE NOCASE LIMIT 1)
            FROM file_index f
            WHERE f.root = ? COLLATE NOCASE
              AND f.path != ? COLLATE NOCASE
            """,
            (root_path, root_path),
        )
        return self._sort_show_all_rows(cursor.fetchall())

    def _load_direct_children_by_path(self, cursor, root_path):
        rows = []
        seen = set()
        base = str(root_path).rstrip("\\/")

        for separator in ("\\", "/"):
            prefix = base + separator
            pattern = escape_sql_like(prefix) + "%"
            relative_start = len(prefix) + 1
            cursor.execute(
                """
                SELECT f.path, f.name, f.is_folder, f.size, f.modified_time, f.parent_path,
                       (SELECT 1 FROM file_index child WHERE child.parent_path = f.path COLLATE NOCASE LIMIT 1)
                FROM file_index f
                WHERE f.path LIKE ? ESCAPE '\\'
                  AND instr(substr(f.path, ?), '\\') = 0
                  AND instr(substr(f.path, ?), '/') = 0
                """,
                (pattern, relative_start, relative_start),
            )
            for row in cursor.fetchall():
                key = os.path.normcase(os.path.normpath(row[0]))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)

        return self._sort_show_all_rows(rows)

    def _sort_show_all_rows(self, rows):
        rows.sort(key=lambda row: (
            tree_sort_value(
                {
                    'path': row[0],
                    'name': row[1],
                    'is_dir': bool(row[2]),
                    'size': row[3] or 0,
                    'last_modified': row[4],
                    'status': 'Active',
                },
                self.options['sort_column'],
            ),
            (row[1] or '').lower(),
            (row[0] or '').lower(),
        ), reverse=self.options['sort_desc'])
        return rows

class LazyChildrenLoadThread(QThread):
    children_ready = pyqtSignal(int, str, list)
    children_failed = pyqtSignal(int, str, str)

    def __init__(
        self,
        request_id,
        folder_path,
        sort_column=0,
        sort_desc=False,
        options=None,
        cache=None,
        folders_only=False,
        filesystem_fallback=False,
        scan_exclusions=None,
        force_filesystem=False,
        parent=None,
    ):
        super().__init__(parent)
        self.request_id = request_id
        self.folder_path = folder_path
        self.sort_column = sort_column
        self.sort_desc = sort_desc
        self.options = dict(options or {})
        self.apply_filter_options = options is not None
        self.cache = cache
        self.folders_only = folders_only
        self.filesystem_fallback = filesystem_fallback
        self.scan_exclusions = scan_exclusions
        self.force_filesystem = force_filesystem

    def _child_paths_with_children(self, cursor, child_paths):
        folders_with_children = set()
        for start in range(0, len(child_paths), 900):
            batch = child_paths[start:start + 900]
            if not batch:
                continue
            placeholders = ",".join("?" * len(batch))
            folders_only_sql = " AND is_folder = 1" if self.folders_only else ""
            cursor.execute(
                f"""
                SELECT DISTINCT parent_path
                FROM file_index
                WHERE parent_path COLLATE NOCASE IN ({placeholders})
                {folders_only_sql}
                """,
                batch,
            )
            folders_with_children.update(
                os.path.normcase(os.path.normpath(path))
                for (path,) in cursor.fetchall()
                if path
            )
        return folders_with_children

    def _physically_empty_folder_paths(self, cursor, folder_paths):
        empty_paths = set()
        for start in range(0, len(folder_paths), 900):
            batch = folder_paths[start:start + 900]
            if not batch:
                continue
            placeholders = ",".join("?" * len(batch))
            try:
                cursor.execute(
                    "SELECT path FROM folder_summary "
                    f"WHERE path COLLATE NOCASE IN ({placeholders}) "
                    "AND physical_child_count = 0",
                    batch,
                )
            except Exception:
                return set()
            empty_paths.update(
                _path_key(path)
                for (path,) in cursor.fetchall()
                if path
            )
        return empty_paths

    def _parent_path_variants(self):
        normalized = os.path.normpath(self.folder_path)
        variants = [self.folder_path, normalized]
        drive, tail = os.path.splitdrive(normalized)
        if drive.startswith("\\\\") and tail in ("", "\\", "/"):
            variants.extend((drive.rstrip("\\/"), drive.rstrip("\\/") + "\\"))
        return list(dict.fromkeys(variants))

    def _filesystem_folder_has_children(self, folder_path):
        try:
            with os.scandir(folder_path) as entries:
                for entry in entries:
                    try:
                        is_folder = entry.is_dir(follow_symlinks=True)
                        size = 0 if is_folder else entry.stat(follow_symlinks=True).st_size
                    except OSError:
                        continue
                    if (
                        is_folder
                        and self.cache is not None
                        and hasattr(self.cache, 'is_exclusion_hidden')
                        and self.cache.is_exclusion_hidden(entry.path)
                    ):
                        continue
                    if self._filesystem_entry_is_excluded(entry.name, is_folder, size):
                        continue
                    if not self.folders_only or is_folder:
                        return True
        except OSError:
            return False
        return False

    def _filesystem_entry_is_excluded(self, name, is_folder, size):
        exclusions = self.scan_exclusions
        if exclusions is None:
            return False
        if is_folder:
            return exclusions.matches_excluded_folder(name)
        extension = os.path.splitext(name)[1].lower()
        return (
            exclusions.matches_excluded_extension(extension)
            or (
                exclusions.min_file_size_bytes > 0
                and (size or 0) < exclusions.min_file_size_bytes
            )
        )

    def _filesystem_folder_size(self, folder_path):
        if self.cache is not None:
            folder_sizes = getattr(self.cache, 'folder_sizes', {})
            folder_key = _path_key(folder_path)
            if folder_key in folder_sizes:
                return folder_sizes[folder_key] or 0
        total = 0
        for current, dir_names, file_names in os.walk(folder_path):
            dir_names[:] = [
                name for name in dir_names
                if not self._filesystem_entry_is_excluded(name, True, 0)
            ]
            for file_name in file_names:
                file_path = os.path.join(current, file_name)
                try:
                    size = os.path.getsize(file_path)
                except OSError:
                    continue
                if not self._filesystem_entry_is_excluded(file_name, False, size):
                    total += size
        return total

    def _folder_metadata_for_paths(self, folder_paths, allow_database=True):
        metadata = {}
        unresolved = []
        for path in folder_paths:
            cached_metadata = (
                self.cache.folder_metadata(path)
                if self.cache is not None
                and hasattr(self.cache, 'folder_metadata')
                else None
            )
            if cached_metadata is None:
                unresolved.append(path)
            else:
                metadata[_path_key(path)] = cached_metadata

        if not unresolved or not allow_database:
            return metadata
        from src.file_index_tool import FileIndexTool

        tool = FileIndexTool()
        try:
            cursor = tool.conn.cursor()
            for start in range(0, len(unresolved), 900):
                batch = unresolved[start:start + 900]
                placeholders = ",".join("?" * len(batch))
                try:
                    cursor.execute(
                        "SELECT path, total_size, child_count FROM folder_summary "
                        f"WHERE path IN ({placeholders})",
                        batch,
                    )
                except Exception:
                    return metadata
                for path, total_size, child_count in cursor.fetchall():
                    metadata[_path_key(path)] = (
                        total_size or 0,
                        child_count or 0,
                    )
        finally:
            tool.close()
        return metadata

    def _status_for_child(self, is_folder, modified_time, has_child):
        if not self.apply_filter_options:
            return 'Active'

        options = self.options
        age_cutoff = options.get('age_cutoff')

        is_stale = (modified_time <= age_cutoff) if age_cutoff else True
        status = 'Inactive' if is_stale else 'Active'
        if is_folder and not has_child:
            status = 'Empty'
        return status

    def _child_matches_options(self, name, is_folder, status):
        options = self.options
        status_filter = options.get('status_filter')
        videos_only = options.get('videos_only', False)

        if videos_only and not is_folder:
            ext = os.path.splitext(name)[1].lower()
            if ext not in VIDEO_EXTENSIONS:
                return False
        if status_filter == 'Inactive' and status == 'Active':
            return False
        if status_filter == 'Empty' and status != 'Empty':
            return False
        if status_filter == 'Active' and status != 'Active':
            return False
        return True

    def run(self):
        if (
            not self.force_filesystem
            and self.cache
            and self.cache.has_children_for(self.folder_path)
        ):
            children = self.cache.children_for(self.folder_path, self.sort_column, self.sort_desc)
            if self.folders_only:
                children = [
                    child for child in children
                    if child.get('is_dir', False)
                ]
            if self.apply_filter_options:
                filtered = []
                for child in children:
                    status = self._status_for_child(
                        child.get('is_dir', False),
                        child.get('last_modified', 0),
                        not child.get('_children_loaded', True),
                    )
                    if self._child_matches_options(child.get('name', ''), child.get('is_dir', False), status):
                        child['status'] = status
                        filtered.append(child)
                children = filtered
            if children:
                self.children_ready.emit(self.request_id, self.folder_path, children)
                return

        rows = []
        folders_with_children = set()
        physically_empty_folders = set()
        used_filesystem_rows = False
        if not self.force_filesystem:
            from src.file_index_tool import FileIndexTool

            tool = FileIndexTool()
            try:
                cursor = tool.conn.cursor()
                folders_only_sql = " AND is_folder = 1" if self.folders_only else ""
                parent_variants = self._parent_path_variants()
                placeholders = ",".join("?" * len(parent_variants))
                cursor.execute(
                    "SELECT path, name, is_folder, size, modified_time, parent_path "
                    "FROM file_index "
                    f"WHERE parent_path COLLATE NOCASE IN ({placeholders}) "
                    f"{folders_only_sql} "
                    f"ORDER BY {build_sort_order_clause('Tree', self.sort_column, self.sort_desc)}",
                    parent_variants,
                )
                rows = cursor.fetchall()
                child_folder_paths = [
                    path
                    for path, _name, is_folder, _size, _modified_time, _parent_path in rows
                    if is_folder
                ]
                folders_with_children = self._child_paths_with_children(
                    cursor,
                    child_folder_paths,
                )
                physically_empty_folders = self._physically_empty_folder_paths(
                    cursor,
                    child_folder_paths,
                )
            except Exception as exc:
                self.children_failed.emit(
                    self.request_id,
                    self.folder_path,
                    str(exc),
                )
                return
            finally:
                tool.close()

        if self.force_filesystem or (not rows and self.filesystem_fallback):
            used_filesystem_rows = True
            try:
                with os.scandir(self.folder_path) as entries:
                    rows = []
                    for entry in entries:
                        try:
                            is_folder = entry.is_dir(follow_symlinks=True)
                            if self.folders_only and not is_folder:
                                continue
                            try:
                                stat_result = entry.stat(follow_symlinks=True)
                                modified_time = stat_result.st_mtime
                            except OSError:
                                stat_result = None
                                modified_time = 0
                            if is_folder and self._filesystem_entry_is_excluded(
                                entry.name,
                                True,
                                0,
                            ):
                                continue
                            if (
                                is_folder
                                and self.cache is not None
                                and hasattr(self.cache, 'is_exclusion_hidden')
                                and self.cache.is_exclusion_hidden(entry.path)
                            ):
                                continue
                            cache_scope_complete = (
                                self.cache is not None
                                and hasattr(self.cache, 'summary_descendant_count')
                                and self.cache.summary_descendant_count(
                                    self.folder_path
                                ) is not None
                            )
                            if (
                                is_folder
                                and not self.force_filesystem
                                and not cache_scope_complete
                                and not filesystem_folder_has_visible_entries(
                                    entry.path,
                                    self.scan_exclusions,
                                )
                            ):
                                continue
                            size = (
                                0
                                if is_folder or stat_result is None
                                else stat_result.st_size
                            )
                            if not is_folder and self._filesystem_entry_is_excluded(
                                entry.name,
                                False,
                                size,
                            ):
                                continue
                            rows.append(
                                (
                                    entry.path,
                                    entry.name,
                                    int(is_folder),
                                    size,
                                    modified_time,
                                    self.folder_path,
                                )
                            )
                        except OSError:
                            continue
                rows.sort(key=lambda row: ((row[1] or "").lower(), (row[0] or "").lower()))
                folder_metadata = self._folder_metadata_for_paths(
                    [row[0] for row in rows if row[2]],
                    allow_database=not self.force_filesystem,
                )
                updated_rows = []
                for path, name, is_folder, size, modified_time, parent_path in rows:
                    if is_folder:
                        metadata = folder_metadata.get(_path_key(path))
                        if metadata is not None:
                            cached_size, child_count = metadata
                            size = 0 if self.folders_only else cached_size
                            if child_count:
                                folders_with_children.add(_path_key(path))
                        elif self._filesystem_folder_has_children(path):
                            folders_with_children.add(_path_key(path))
                    updated_rows.append(
                        (path, name, is_folder, size, modified_time, parent_path)
                    )
                rows = updated_rows
            except OSError as exc:
                self.children_failed.emit(self.request_id, self.folder_path, str(exc))
                return

        children = []
        for path, name, is_folder, size, modified_time, parent_path in rows:
            is_folder = bool(is_folder)
            has_child = os.path.normcase(os.path.normpath(path)) in folders_with_children
            status_has_child = has_child
            if is_folder and self.options.get('status_filter') == 'Empty':
                if used_filesystem_rows:
                    try:
                        with os.scandir(path) as entries:
                            status_has_child = next(entries, None) is not None
                    except OSError:
                        status_has_child = True
                else:
                    status_has_child = _path_key(path) not in physically_empty_folders
            status = self._status_for_child(
                is_folder,
                modified_time,
                status_has_child,
            )
            if not self._child_matches_options(name, is_folder, status):
                continue
            children.append({
                'name': name,
                'path': path,
                'is_dir': is_folder,
                'size': size or 0,
                'last_modified': modified_time,
                'status': status,
                'children': [],
                '_children_loaded': not (is_folder and has_child),
                '_is_page_result': True,
                'location': parent_path,
                'display_location': parent_path,
            })

        self.children_ready.emit(self.request_id, self.folder_path, children)
