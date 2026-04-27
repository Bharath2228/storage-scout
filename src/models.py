from datetime import datetime

from PyQt6.QtCore import Qt, QAbstractItemModel, QModelIndex, QSortFilterProxyModel


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

        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            return item.checkState

        if role == Qt.ItemDataRole.UserRole:
            return item.itemData

        if role == Qt.ItemDataRole.TextAlignmentRole:
            col = index.column()
            if col == 0:
                return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            if col in (1, 2, 5, 6):
                return Qt.AlignmentFlag.AlignCenter
            if col in (3, 4):
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            state = Qt.CheckState(value)
            self.set_check_state(index, state, explicit=True)
            return True
        return False

    def set_check_state(self, index, state, explicit=False):
        if not index.isValid():
            return
        state = Qt.CheckState(state)
        self._set_check_state_recursive(index, state, explicit=explicit)
        self._update_ancestor_states(index.parent())

    def _set_check_state_recursive(self, index, state, explicit=False):
        item = index.internalPointer()
        if item.checkState == state and item.explicitlyChecked == (explicit and state == Qt.CheckState.Checked):
            if item.childCount() == 0:
                return

        self._update_item_check_state(index, state, explicit=explicit)

        for row in range(item.childCount()):
            child_idx = self.index(row, 0, index)
            self._set_check_state_recursive(child_idx, state, explicit=False)

    def _update_item_check_state(self, index, state, explicit=False):
        item = index.internalPointer()
        explicit_checked = bool(explicit) and state == Qt.CheckState.Checked
        if item.checkState == state and item.explicitlyChecked == explicit_checked:
            return

        item.checkState = state
        item.explicitlyChecked = explicit_checked
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])

        action_index = self.index(index.row(), 6, index.parent())
        self.dataChanged.emit(action_index, action_index, [Qt.ItemDataRole.DisplayRole])

    def _update_ancestor_states(self, index):
        while index.isValid():
            item = index.internalPointer()
            child_states = [item.child(row).checkState for row in range(item.childCount())]
            if child_states and all(state == Qt.CheckState.Checked for state in child_states):
                state = Qt.CheckState.Checked
            elif child_states and all(state == Qt.CheckState.Unchecked for state in child_states):
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
            headers = ["Folder / File path", "Type", "Last modified", "Age ↑", "Size", "Status", "Action"]
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
        self.empty_only = False
        self.status_filter = None   # None = all, 'Active', 'Inactive', 'Empty'
        self.date_from_ts = None
        self.date_to_ts = None
        self.older_than_secs = None
        self.older_than_cutoff_ts = None

    def set_filters(self, empty_only,
                    date_from_ts=None, date_to_ts=None,
                    older_than_secs=None,
                    status_filter=None):
        if date_from_ts is not None and date_to_ts is not None and date_from_ts > date_to_ts:
            date_from_ts, date_to_ts = date_to_ts, date_from_ts

        self.empty_only = empty_only
        self.status_filter = status_filter
        self.date_from_ts = date_from_ts
        self.date_to_ts = date_to_ts
        self.older_than_secs = older_than_secs
        self.older_than_cutoff_ts = (
            datetime.now().timestamp() - older_than_secs
            if older_than_secs is not None else None
        )
        self.invalidateFilter()

    def has_active_filters(self):
        return any((
            self.empty_only,
            self.status_filter is not None,
            self.date_from_ts is not None,
            self.date_to_ts is not None,
            self.older_than_cutoff_ts is not None,
        ))

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
        return super().flags(index)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        source_index = self.mapToSource(index)
        if self.is_context_only(source_index):
            if role == Qt.ItemDataRole.DisplayRole and index.column() == 5:
                return "Context"
            if role == Qt.ItemDataRole.ToolTipRole:
                return "Visible because a child item matches the current filters."

        # Dynamic status for 'Show all' mode based on age slider
        if self.status_filter is None and role == Qt.ItemDataRole.DisplayRole and index.column() == 5:
            item_data = self.sourceModel().data(source_index, Qt.ItemDataRole.UserRole)
            if item_data:
                status = item_data.get('status', '')
                if status != 'Empty' and self.older_than_cutoff_ts is not None:
                    ts = item_data.get('last_modified', 0)
                    if ts > 0:
                        if ts <= self.older_than_cutoff_ts:
                            return 'Inactive'
                        else:
                            return 'Active'
                return status

        return super().data(index, role)

    def _accepts(self, source_row, source_parent):
        source_model = self.sourceModel()
        if source_model is None:
            return False

        idx = source_model.index(source_row, 0, source_parent)
        item_data = source_model.data(idx, Qt.ItemDataRole.UserRole)
        if not item_data:
            return False

        if self._matches(item_data):
            return True

        if item_data.get('is_dir'):
            for row in range(source_model.rowCount(idx)):
                if self._accepts(row, idx):
                    return True
        return False

    def _matches(self, item_data):
        status = item_data.get('status', '')
        ts = item_data.get('last_modified', 0)

        if self.empty_only:
            return status == 'Empty' and item_data.get('is_dir', False)

        if self.status_filter is not None and status != self.status_filter:
            return False

        has_time_filter = any(value is not None for value in (
            self.date_from_ts,
            self.date_to_ts,
            self.older_than_cutoff_ts,
        ))
        if has_time_filter and not ts:
            return False

        if self.date_from_ts is not None and ts < self.date_from_ts:
            return False
        if self.date_to_ts is not None and ts > self.date_to_ts:
            return False
        if self.older_than_cutoff_ts is not None and ts > self.older_than_cutoff_ts:
            # If "Show all" is checked (status_filter is None), don't hide items based on age
            if self.status_filter is not None:
                return False

        return True
