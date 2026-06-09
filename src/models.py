import os
from datetime import datetime

from PyQt6.QtCore import Qt, QAbstractItemModel, QModelIndex, QSortFilterProxyModel
from PyQt6.QtGui import QFont, QIcon

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
FOLDER_ICON = QIcon(os.path.join(ASSETS_DIR, "folder_blue.svg"))
FILE_ICON = QIcon(os.path.join(ASSETS_DIR, "file_blue.svg"))

VIDEO_EXTENSIONS = (
    ".3g2", ".3gp", ".avi", ".divx", ".flv", ".m2ts", ".m4v",
    ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".ogv",
    ".rm", ".rmvb", ".ts", ".vob", ".webm", ".wmv",
)


def format_size(size_bytes):
    if size_bytes == 0:
        return "--"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def format_age(timestamp):
    if timestamp == 0:
        return "--"
    now = datetime.now().timestamp()
    diff = now - timestamp
    months = int(diff / (30 * 24 * 3600))
    if months <= 0:
        return "<1mo"
    return f"{months}mo"


class TreeItem:
    def __init__(self, data, parent=None):
        self.parentItem = parent
        self.itemData = data  # Data is the dict from scanner
        self.childItems = []
        self.checkState = Qt.CheckState.Unchecked
        self.explicitlyChecked = False
        self.children_loaded = False
        self.children_loading = False

    def appendChild(self, item):
        self.childItems.append(item)

    def child(self, row):
        if row < 0 or row >= len(self.childItems):
            return None
        return self.childItems[row]

    def childCount(self):
        return len(self.childItems)

    def columnCount(self):
        return 7  # Name, Type, Last Modified, Age, Size, Status, Action

    def data(self, column):
        if column == 0:
            name = self.itemData.get('name', '')
            if self.itemData.get('is_hidden'):
                return f"[Hidden] {name}"
            return name
        if column == 1:
            return "Folder" if self.itemData.get('is_dir') else "File"
        if column == 2:
            ts = self.itemData.get('last_modified', 0)
            if ts == 0:
                return ""
            return datetime.fromtimestamp(ts).strftime("%b %d, %Y")
        if column == 3:
            return format_age(self.itemData.get('last_modified', 0))
        if column == 4:
            return format_size(self.itemData.get('size', 0))
        if column == 5:
            return self.itemData.get('status', '')
        if column == 6:
            return "queued" if self.checkState == Qt.CheckState.Checked else "Open"
        return None

    def row(self):
        if self.parentItem:
            return self.parentItem.childItems.index(self)
        return 0


class WatchdogTreeModel(QAbstractItemModel):
    def __init__(self, root_data, parent=None):
        super().__init__(parent)
        self.view_mode = 'Tree'
        self.rootItem = TreeItem({'name': 'Root'})
        self._setupModelData(root_data, self.rootItem)

    def _setupModelData(self, root_data, root_item):
        if not root_data:
            return
        # Iterative setup to avoid recursion limits
        stack = [(root_data, root_item)]
        while stack:
            data_node, parent_item = stack.pop()
            for child_data in data_node.get('children', []):
                child_item = TreeItem(child_data, parent_item)
                parent_item.appendChild(child_item)
                child_item.children_loaded = bool(child_data.get('_children_loaded', False))
                if child_data.get('is_dir', False):
                    if not child_data.get('children') and not child_item.children_loaded:
                        dummy_data = {'name': 'Loading...', 'is_dir': False, '_is_dummy': True}
                        child_item.appendChild(TreeItem(dummy_data, child_item))
                    else:
                        stack.append((child_data, child_item))


    def columnCount(self, parent=QModelIndex()):
        if parent.isValid():
            return parent.internalPointer().columnCount()
        return self.rootItem.columnCount()

    def rowCount(self, parent=QModelIndex()):
        if parent.column() > 0:
            return 0
        if not parent.isValid():
            parentItem = self.rootItem
        else:
            parentItem = parent.internalPointer()
        return parentItem.childCount()

    def hasChildren(self, parent=QModelIndex()):
        if not parent.isValid():
            return self.rootItem.childCount() > 0
        item = parent.internalPointer()
        if not item.itemData.get('is_dir'):
            return False
        # If already loaded and empty, hide the expand chevron
        if getattr(item, 'children_loaded', False) and item.childCount() == 0:
            return False
        return True

    def load_children(self, parent_index):
        if not parent_index.isValid():
            return
        
        item = parent_index.internalPointer()
        if getattr(item, 'children_loaded', False):
            return
            
        item.children_loaded = True
        path = item.itemData.get('path')
        if not path:
            return

        # Remove dummy child if present
        if item.childCount() == 1 and item.child(0).itemData.get('_is_dummy'):
            self.beginRemoveRows(parent_index, 0, 0)
            item.childItems.pop(0)
            self.endRemoveRows()
            
        from .file_index_tool import FileIndexTool
        tool = FileIndexTool()
        cursor = tool.conn.cursor()
        try:
            cursor.execute(
                "SELECT f.path, f.name, f.is_folder, f.size, f.modified_time, f.parent_path, "
                "(SELECT 1 FROM file_index child WHERE child.parent_path = f.path LIMIT 1) "
                "FROM file_index f WHERE f.parent_path = ? "
                "ORDER BY f.is_folder DESC, f.name ASC",
                (path,)
            )
            rows = cursor.fetchall()
        except Exception:
            rows = []
        finally:
            tool.close()
            
        if not rows:
            # Emit layoutChanged to tell QTreeView that chevron should be hidden
            self.layoutChanged.emit()
            return
            
        options = getattr(self, 'options', {})
        age_cutoff = options.get('age_cutoff')
        status_filter = options.get('status_filter')
        videos_only = options.get('videos_only', False)
        
        new_items = []
        for c_path, c_name, c_is_folder, c_size, c_modified_time, c_parent_path, c_has_child in rows:
            # Video filter
            if videos_only and not c_is_folder:
                ext = os.path.splitext(c_name)[1].lower()
                if ext not in VIDEO_EXTENSIONS:
                    continue
            
            # Age filter
            is_stale = (c_modified_time <= age_cutoff) if age_cutoff else True
            status = 'Inactive' if is_stale else 'Active'
            
            # Empty folder check using subquery result
            if c_is_folder:
                if c_has_child is None:
                    status = 'Empty'
            
            # Status filter
            if status_filter == 'Inactive' and status == 'Active':
                continue
            if status_filter == 'Empty' and status != 'Empty':
                continue
            if status_filter == 'Active' and status != 'Active':
                continue
                
            child_data = {
                'name': c_name,
                'path': c_path,
                'is_dir': bool(c_is_folder),
                'size': c_size if not c_is_folder else 0,
                'last_modified': c_modified_time,
                'status': status,
                'children': [],
                '_is_page_result': True,
                'location': c_parent_path,
                'display_location': c_parent_path,
            }
            new_items.append(child_data)
            
        if not new_items:
            # Emit layoutChanged to tell QTreeView that chevron should be hidden
            self.layoutChanged.emit()
            return
            
        self.beginInsertRows(parent_index, item.childCount(), item.childCount() + len(new_items) - 1)
        for child_data in new_items:
            child_item = TreeItem(child_data, item)
            # Inherit parent checkstate
            child_item.checkState = item.checkState
            if child_data.get('is_dir'):
                dummy_data = {'name': 'Loading...', 'is_dir': False, '_is_dummy': True}
                child_item.appendChild(TreeItem(dummy_data, child_item))
            item.appendChild(child_item)
        self.endInsertRows()

    def begin_async_child_load(self, parent_index):
        if not parent_index.isValid():
            return None

        item = parent_index.internalPointer()
        if (
            not item.itemData.get('is_dir')
            or item.children_loaded
            or item.children_loading
        ):
            return None

        item.children_loading = True
        path = item.itemData.get('path')

        if item.childCount() == 1 and item.child(0).itemData.get('_is_dummy'):
            self.beginRemoveRows(parent_index, 0, 0)
            item.childItems.pop(0)
            self.endRemoveRows()

        if path:
            loading_data = {'name': 'Loading...', 'is_dir': False, '_is_dummy': True}
            self.beginInsertRows(parent_index, 0, 0)
            item.appendChild(TreeItem(loading_data, item))
            self.endInsertRows()
            return path

        item.children_loading = False
        item.children_loaded = True
        return None

    def finish_async_child_load(self, parent_index, child_nodes):
        if not parent_index.isValid():
            return

        item = parent_index.internalPointer()

        if item.childCount() == 1 and item.child(0).itemData.get('_is_dummy'):
            self.beginRemoveRows(parent_index, 0, 0)
            item.childItems.pop(0)
            self.endRemoveRows()

        item.children_loading = False
        item.children_loaded = True

        if not child_nodes:
            self.layoutChanged.emit()
            return

        self.beginInsertRows(parent_index, item.childCount(), item.childCount() + len(child_nodes) - 1)
        for child_data in child_nodes:
            child_item = TreeItem(child_data, item)
            child_item.checkState = item.checkState
            child_item.children_loaded = bool(child_data.get('_children_loaded', False))
            if child_data.get('is_dir') and not child_item.children_loaded:
                dummy_data = {'name': 'Loading...', 'is_dir': False, '_is_dummy': True}
                child_item.appendChild(TreeItem(dummy_data, child_item))
            item.appendChild(child_item)
        self.endInsertRows()

    def fail_async_child_load(self, parent_index):
        if not parent_index.isValid():
            return

        item = parent_index.internalPointer()
        item.children_loading = False

        if item.childCount() == 0:
            dummy_data = {'name': 'Loading...', 'is_dir': False, '_is_dummy': True}
            self.beginInsertRows(parent_index, 0, 0)
            item.appendChild(TreeItem(dummy_data, item))
            self.endInsertRows()

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        item = index.internalPointer()
        if item.itemData.get('_is_dummy'):
            if role == Qt.ItemDataRole.DisplayRole and index.column() == 0:
                return "Loading..."
            return None

        col = index.column()
        is_tree = self.view_mode == 'Tree'

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                name = item.itemData.get('name', '')
                if item.itemData.get('is_hidden'): return f"[Hidden] {name}"
                return name
            
            if is_tree:
                if col == 1: return "Folder" if item.itemData.get('is_dir') else "File"
            else:
                if col == 1: return item.itemData.get('display_location') or item.itemData.get('location', '')

            # Common columns shifted by 0
            if col == 2:
                ts = item.itemData.get('last_modified', 0)
                return datetime.fromtimestamp(ts).strftime("%b %d, %Y") if ts else ""
            if col == 3: return format_age(item.itemData.get('last_modified', 0))
            if col == 4: return format_size(item.itemData.get('size', 0))
            if col == 5: return item.itemData.get('status', '')
            if col == 6: return "queued" if item.checkState == Qt.CheckState.Checked else "Open"
            return None

        if role == Qt.ItemDataRole.CheckStateRole and col == 0:
            return item.checkState

        if role == Qt.ItemDataRole.UserRole:
            return item.itemData

        if role == Qt.ItemDataRole.ToolTipRole:
            if not is_tree and col == 1:
                return item.itemData.get('location', '')
            if col == 0:
                return item.itemData.get('path', '')

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col == 0 or (not is_tree and col == 1):
                return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            if col in (1, 2, 5, 6): return Qt.AlignmentFlag.AlignCenter
            if col in (3, 4): return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

        if role == Qt.ItemDataRole.FontRole:
            if item.itemData.get('is_dir', False) and col == 0:
                font = QFont()
                font.setBold(True)
                return font

        if role == Qt.ItemDataRole.DecorationRole and col == 0:
            is_dir = item.itemData.get('is_dir', False)
            if is_dir:
                return FOLDER_ICON
            return FILE_ICON

        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            state = Qt.CheckState(value)
            self.set_check_state(index, state, explicit=True)
            return True
        return False

    def set_check_state(self, index, state, explicit=False):
        self.set_indices_check_state([index], state, explicit=explicit)

    def set_indices_check_state(self, indices, state, explicit=False):
        if not indices:
            return
        state = Qt.CheckState(state)
        
        self.layoutAboutToBeChanged.emit()
        try:
            for index in indices:
                if not index.isValid():
                    continue
                self._set_check_state_recursive(index, state, explicit=explicit)
                self._update_ancestor_states(index.parent())
        finally:
            self.layoutChanged.emit()

    def set_indices_check_state_direct(self, indices, state, explicit=False):
        if not indices:
            return
        state = Qt.CheckState(state)

        self.layoutAboutToBeChanged.emit()
        try:
            ancestors = {}
            for index in indices:
                if not index.isValid():
                    continue

                item = index.internalPointer()
                explicit_checked = bool(explicit) and state == Qt.CheckState.Checked
                item.checkState = state
                item.explicitlyChecked = explicit_checked

                parent = index.parent()
                while parent.isValid():
                    ancestors[id(parent.internalPointer())] = parent
                    parent = parent.parent()

            for index in ancestors.values():
                self._update_ancestor_states_for_direct_bulk(index)
        finally:
            self.layoutChanged.emit()

    def _update_ancestor_states_for_direct_bulk(self, index):
        while index.isValid():
            item = index.internalPointer()
            child_states = [item.child(row).checkState for row in range(item.childCount())]
            if item.explicitlyChecked:
                state = Qt.CheckState.Checked
            elif child_states and all(state == Qt.CheckState.Unchecked for state in child_states):
                state = Qt.CheckState.Unchecked
            else:
                state = Qt.CheckState.PartiallyChecked

            self._update_item_check_state(index, state, explicit=item.explicitlyChecked)
            index = index.parent()

    def _set_check_state_recursive(self, start_index, state, explicit=False):
        # Iterative implementation to avoid recursion and signal storms
        stack = [(start_index, explicit)]
        while stack:
            index, is_explicit = stack.pop()
            item = index.internalPointer()
            
            explicit_checked = bool(is_explicit) and state == Qt.CheckState.Checked
            if item.checkState != state or item.explicitlyChecked != explicit_checked:
                item.checkState = state
                item.explicitlyChecked = explicit_checked
                # We don't emit dataChanged here; layoutChanged at the end handles it
                
            for row in range(item.childCount()):
                stack.append((self.index(row, 0, index), False))


    def _update_item_check_state(self, index, state, explicit=False):
        # This is now only used for single-item updates from ancestors
        item = index.internalPointer()
        explicit_checked = bool(explicit) and state == Qt.CheckState.Checked
        if item.checkState == state and item.explicitlyChecked == explicit_checked:
            return
        item.checkState = state
        item.explicitlyChecked = explicit_checked


    def _update_ancestor_states(self, index):
        while index.isValid():
            item = index.internalPointer()
            child_states = [item.child(row).checkState for row in range(item.childCount())]
            if child_states and all(state == Qt.CheckState.Unchecked for state in child_states):
                state = Qt.CheckState.Unchecked
            else:
                state = Qt.CheckState.PartiallyChecked

            self._update_item_check_state(index, state, explicit=False)
            index = index.parent()

    def is_explicitly_checked(self, index):
        if not index.isValid():
            return False
        return bool(index.internalPointer().explicitlyChecked)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if self.view_mode == 'Tree':
                headers = ["Folder / File path", "Type", "Last modified", "Age", "Size", "Status", "Action"]
            else:
                headers = ["Name", "Location", "Last modified", "Age", "Size", "Status", "Action"]
            if section < len(headers):
                return headers[section]
        return None

    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()

        if not parent.isValid():
            parentItem = self.rootItem
        else:
            parentItem = parent.internalPointer()

        childItem = parentItem.child(row)
        if childItem:
            return self.createIndex(row, column, childItem)
        return QModelIndex()

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()

        childItem = index.internalPointer()
        parentItem = childItem.parentItem

        if parentItem == self.rootItem or parentItem is None:
            return QModelIndex()

        return self.createIndex(parentItem.row(), 0, parentItem)

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags

        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 0:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags


class WatchdogFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRecursiveFilteringEnabled(True)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._reset()

    def _sort_key(self, item_data, column):
        if column == 4:
            return item_data.get('size', 0) or 0
        if column == 2:
            return item_data.get('last_modified', 0) or 0
        if column == 3:
            ts = item_data.get('last_modified', 0) or 0
            if not ts:
                return float('inf')
            return max(0.0, datetime.now().timestamp() - ts)
        return None

    def lessThan(self, left, right):
        left_data = self.sourceModel().data(left, Qt.ItemDataRole.UserRole)
        right_data = self.sourceModel().data(right, Qt.ItemDataRole.UserRole)
        
        col = left.column()
        left_key = self._sort_key(left_data, col)
        right_key = self._sort_key(right_data, col)
        if left_key is not None and right_key is not None:
            if left_key == right_key:
                return (left_data.get('name', '') or '').lower() < (right_data.get('name', '') or '').lower()
            return left_key < right_key
            
        return super().lessThan(left, right)

    def _reset(self):
        self.empty_only = False
        self.status_filter = None   # None = all, 'Active', 'Inactive', 'Empty'
        self.older_than_secs = None
        self.older_than_cutoff_ts = None
        self.view_mode = 'Tree'
        self._accepts_cache = {}

    def set_filters(self, empty_only, older_than_secs=None, status_filter=None, view_mode='Tree'):
        self.empty_only = empty_only
        self.status_filter = status_filter
        self.view_mode = view_mode
        self.older_than_secs = older_than_secs
        self.older_than_cutoff_ts = (
            datetime.now().timestamp() - older_than_secs
            if older_than_secs is not None else None
        )
        self._accepts_cache.clear()
        self.invalidateFilter()

    def has_active_filters(self):
        return any((
            self.empty_only,
            self.status_filter is not None,
            self.older_than_cutoff_ts is not None,
        ))

    def hasChildren(self, parent=QModelIndex()):
        if not parent.isValid():
            return super().hasChildren(parent)
        source_parent = self.mapToSource(parent)
        if not source_parent.isValid():
            return False
        source_model = self.sourceModel()
        if source_model is None:
            return False
        return source_model.hasChildren(source_parent)

    def matches_source_index(self, source_index):
        source_model = self.sourceModel()
        if source_model is None or not source_index.isValid():
            return False
        item_data = source_model.data(source_index, Qt.ItemDataRole.UserRole)
        return bool(item_data) and self._matches(item_data)

    def is_context_only(self, source_index):
        return self.has_active_filters() and not self.matches_source_index(source_index)

    def filterAcceptsRow(self, source_row, source_parent):
        return self._accepts(source_row, source_parent)

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and index.column() == 0:
            source_index = self.mapToSource(index)
            if self.is_context_only(source_index):
                flags &= ~Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        source_index = self.mapToSource(index)
        if self.is_context_only(source_index):
            if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
                return None
            if role == Qt.ItemDataRole.DisplayRole and index.column() == 5:
                return "Context"
            if role == Qt.ItemDataRole.ToolTipRole:
                return "Visible because a child item matches the current filters."

        # Dynamic status based on age slider (applies regardless of filter mode)
        if role == Qt.ItemDataRole.DisplayRole and index.column() == 5:
            item_data = self.sourceModel().data(source_index, Qt.ItemDataRole.UserRole)
            if item_data:
                status = item_data.get('status', '')
                if status != 'Empty':
                    if self.older_than_cutoff_ts is not None:
                        ts = item_data.get('last_modified', 0)
                        if ts > 0:
                            return 'Inactive' if ts <= self.older_than_cutoff_ts else 'Active'
                    else:
                        # Age threshold is "Off" (None) or 0 -> everything non-empty is Inactive.
                        return 'Inactive'
                return status

        return super().data(index, role)

    def _accepts(self, source_row, source_parent):
        source_model = self.sourceModel()
        if source_model is None:
            return False
            
        start_idx = source_model.index(source_row, 0, source_parent)
        
        # Check cache (we can use internalPointer to identify the item uniquely)
        item = start_idx.internalPointer()
        if item in self._accepts_cache:
            return self._accepts_cache[item]
            
        # If it doesn't match immediately, we must check descendants
        # To avoid O(N^2) redundant scanning, we can do a post-order traversal
        # But since PyQt calls this dynamically, caching the result of the subtree search is enough.
        
        visited_items = []
        stack = [start_idx]
        while stack:
            idx = stack.pop()
            current_item = idx.internalPointer()
            if current_item in self._accepts_cache:
                if self._accepts_cache[current_item]:
                    self._accepts_cache[item] = True
                    return True
                continue
                
            visited_items.append(current_item)
            item_data = source_model.data(idx, Qt.ItemDataRole.UserRole)
            if not item_data: continue
            
            if self._matches(item_data):
                self._accepts_cache[item] = True
                self._accepts_cache[current_item] = True
                return True
                
            if item_data.get('is_dir'):
                for row in range(source_model.rowCount(idx)):
                    stack.append(source_model.index(row, 0, idx))
                    
        for v in visited_items:
            self._accepts_cache[v] = False
        return False


    def _matches(self, item_data):
        if item_data.get('_is_dummy'):
            return True
        if (
            self.view_mode == 'Tree'
            and self.status_filter == 'Inactive'
            and item_data.get('is_dir', False)
        ):
            return False
        status = item_data.get('status', '')
        ts = item_data.get('last_modified', 0)

        # Dynamic status adjustment for filtering
        if status != 'Empty':
            if self.older_than_cutoff_ts is not None:
                if ts > 0:
                    status = 'Inactive' if ts <= self.older_than_cutoff_ts else 'Active'
            else:
                # User requested: if age threshold is "Off" or 0, treat everything as Inactive.
                status = 'Inactive'

        if self.empty_only:
            return status == 'Empty' and item_data.get('is_dir', False)

        if self.status_filter is not None and status != self.status_filter:
            return False

        return True
