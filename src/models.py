from PyQt6.QtCore import Qt, QAbstractItemModel, QModelIndex, QSortFilterProxyModel
from datetime import datetime

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
    if months == 0:
        return "<1mo"
    return f"{months}mo"

class TreeItem:
    def __init__(self, data, parent=None):
        self.parentItem = parent
        self.itemData = data # Data is the dict from scanner
        self.childItems = []
        self.checkState = Qt.CheckState.Unchecked

    def appendChild(self, item):
        self.childItems.append(item)

    def child(self, row):
        if row < 0 or row >= len(self.childItems):
            return None
        return self.childItems[row]

    def childCount(self):
        return len(self.childItems)

    def columnCount(self):
        return 7 # Name, Type, Last Modified, Age, Size, Status, Action

    def data(self, column):
        if column == 0:
            return self.itemData.get('name', '')
        elif column == 1:
            return "Folder" if self.itemData.get('is_dir') else "File"
        elif column == 2:
            ts = self.itemData.get('last_modified', 0)
            if ts == 0: return ""
            return datetime.fromtimestamp(ts).strftime("%b %d, %Y")
        elif column == 3:
            return format_age(self.itemData.get('last_modified', 0))
        elif column == 4:
            return format_size(self.itemData.get('size', 0))
        elif column == 5:
            return self.itemData.get('status', '')
        elif column == 6:
            return "queued" if self.checkState == Qt.CheckState.Checked else "Open \u2192"
        return None

    def row(self):
        if self.parentItem:
            return self.parentItem.childItems.index(self)
        return 0

class WatchdogTreeModel(QAbstractItemModel):
    def __init__(self, root_data, parent=None):
        super().__init__(parent)
        self.rootItem = TreeItem({'name': 'Root'})
        self._setupModelData(root_data, self.rootItem)

    def _setupModelData(self, data_node, parent):
        if not data_node:
            return
        for child_data in data_node.get('children', []):
            child_item = TreeItem(child_data, parent)
            parent.appendChild(child_item)
            self._setupModelData(child_data, child_item)

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

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        item = index.internalPointer()

        if role == Qt.ItemDataRole.DisplayRole:
            return item.data(index.column())
            
        elif role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            return item.checkState
            
        elif role == Qt.ItemDataRole.UserRole:
            return item.itemData
            
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            col = index.column()
            if col == 0:  # Name / path: left-align for readability
                return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            if col in (1, 2, 5, 6):  # Type, Last modified, Status, Action: center
                return Qt.AlignmentFlag.AlignCenter
            if col in (3, 4):  # Age and Size: right-align numeric values
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            item = index.internalPointer()
            state = Qt.CheckState(value)
            self.set_check_state(index, state)
            return True
        return False

    def set_check_state(self, index, state):
        item = index.internalPointer()
        if item.checkState == state:
            return
            
        item.checkState = state
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
        
        # Action column changes when checked
        action_index = self.index(index.row(), 6, index.parent())
        self.dataChanged.emit(action_index, action_index, [Qt.ItemDataRole.DisplayRole])
        
        # Optional: check/uncheck children automatically
        for row in range(item.childCount()):
            child_idx = self.index(row, 0, index)
            self.set_check_state(child_idx, state)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            headers = ["Folder / File path", "Type", "Last modified", "Age \u2191", "Size", "Status", "Action"]
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
        self._reset()

    def _reset(self):
        self.empty_only      = False
        self.status_filter   = None   # None = all, 'Active', 'Inactive', 'Empty'
        self.date_from_ts    = None
        self.date_to_ts      = None
        self.older_than_secs = None

    def set_filters(self, empty_only,
                    date_from_ts=None, date_to_ts=None,
                    older_than_secs=None,
                    status_filter=None):
        self.empty_only      = empty_only
        self.status_filter   = status_filter
        self.date_from_ts    = date_from_ts
        self.date_to_ts      = date_to_ts
        self.older_than_secs = older_than_secs
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        return self._accepts(source_row, source_parent)

    def _accepts(self, source_row, source_parent):
        idx = self.sourceModel().index(source_row, 0, source_parent)
        item_data = self.sourceModel().data(idx, Qt.ItemDataRole.UserRole)
        if not item_data:
            return False
        if self._matches(item_data):
            return True
        # Show parent directories if any child matches
        if item_data.get('is_dir'):
            for r in range(self.sourceModel().rowCount(idx)):
                if self._accepts(r, idx):
                    return True
        return False

    def _matches(self, d):
        status = d.get('status', '')
        ts     = d.get('last_modified', 0)

        # ── Filter 1: empty-only mode (legacy radio) ─────────────────────────
        if self.empty_only:
            return status == 'Empty' and d.get('is_dir', False)

        # ── Filter 2: status filter (Active / Inactive / Empty) ──────────────
        if self.status_filter is not None:
            return status == self.status_filter

        # ── Filter 3: date range ─────────────────────────────────────────────
        if ts and self.date_from_ts is not None:
            if ts < self.date_from_ts:
                return False
        if ts and self.date_to_ts is not None:
            if ts > self.date_to_ts:
                return False

        # ── Filter 4: older-than slider ──────────────────────────────────────
        if self.older_than_secs is not None and ts:
            from datetime import datetime
            cutoff = datetime.now().timestamp() - self.older_than_secs
            if ts > cutoff:
                return False

        return True
