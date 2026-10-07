import os

from PyQt6.QtCore import (
    QAbstractItemModel,
    QModelIndex,
    QSignalBlocker,
    QSize,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QIcon,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ...theme import SPACE_LG, SPACE_MD, SPACE_SM
from ..path_utils import _path_key


class FolderBrowserNode:
    def __init__(self, name="", path="", parent=None, has_children=False):
        self.name = name
        self.path = path
        self.parent = parent
        self.children = []
        self.has_children = bool(has_children)
        self.loaded = not self.has_children
        self.loading = False

    def row(self):
        if self.parent is None:
            return 0
        try:
            return self.parent.children.index(self)
        except ValueError:
            return 0

class FolderBrowserTreeModel(QAbstractItemModel):
    loadRequested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.root_node = FolderBrowserNode()
        self.nodes_by_path = {}
        self.folder_icon = QIcon(
            os.path.join(os.path.join(os.path.dirname(__file__), "..", ".."), "assets", "folder_blue.svg")
        )

    def columnCount(self, parent=QModelIndex()):
        return 1

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid() and parent.column() != 0:
            return 0
        node = parent.internalPointer() if parent.isValid() else self.root_node
        return len(node.children)

    def index(self, row, column, parent=QModelIndex()):
        if column != 0 or row < 0:
            return QModelIndex()
        parent_node = parent.internalPointer() if parent.isValid() else self.root_node
        if row >= len(parent_node.children):
            return QModelIndex()
        return self.createIndex(row, column, parent_node.children[row])

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        node = index.internalPointer()
        parent_node = node.parent
        if parent_node is None or parent_node is self.root_node:
            return QModelIndex()
        return self.createIndex(parent_node.row(), 0, parent_node)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node = index.internalPointer()
        if role == Qt.ItemDataRole.DisplayRole:
            return node.name
        if role == Qt.ItemDataRole.DecorationRole:
            return self.folder_icon
        if role in (Qt.ItemDataRole.UserRole, Qt.ItemDataRole.ToolTipRole):
            return node.path
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def hasChildren(self, parent=QModelIndex()):
        if not parent.isValid():
            return bool(self.root_node.children)
        node = parent.internalPointer()
        return bool(node.children) or node.has_children

    def canFetchMore(self, parent):
        if not parent.isValid():
            return False
        node = parent.internalPointer()
        return node.has_children and not node.loaded and not node.loading

    def fetchMore(self, parent):
        if not self.canFetchMore(parent):
            return
        node = parent.internalPointer()
        node.loading = True
        self.loadRequested.emit(node.path)

    def reset_root(self, path=None):
        self.beginResetModel()
        self.root_node = FolderBrowserNode()
        self.nodes_by_path = {}
        if path:
            normalized = os.path.normpath(path)
            name = os.path.basename(normalized.rstrip("\\/")) or normalized
            root = FolderBrowserNode(
                name=name,
                path=normalized,
                parent=self.root_node,
                has_children=True,
            )
            self.root_node.children.append(root)
            self.nodes_by_path[_path_key(normalized)] = root
        self.endResetModel()

    def index_for_path(self, path):
        node = self.nodes_by_path.get(_path_key(path))
        if node is None:
            return QModelIndex()
        return self.createIndex(node.row(), 0, node)

    def apply_children(self, parent_path, children):
        parent_node = self.nodes_by_path.get(_path_key(parent_path))
        if parent_node is None:
            return
        folder_children = [child for child in children if child.get('is_dir', False)]
        parent_index = self.index_for_path(parent_path)
        if parent_node.children:
            self._remove_children(parent_node, parent_index)
        if folder_children:
            self.beginInsertRows(parent_index, 0, len(folder_children) - 1)
            for child in folder_children:
                node = FolderBrowserNode(
                    name=child.get('name') or os.path.basename(child.get('path', '')),
                    path=child.get('path', ''),
                    parent=parent_node,
                    has_children=not child.get('_children_loaded', True),
                )
                parent_node.children.append(node)
                self.nodes_by_path[_path_key(node.path)] = node
            self.endInsertRows()
        parent_node.loaded = True
        parent_node.loading = False
        parent_node.has_children = bool(folder_children)
        self.dataChanged.emit(parent_index, parent_index, [])

    def merge_children(self, parent_path, children):
        parent_node = self.nodes_by_path.get(_path_key(parent_path))
        if parent_node is None:
            return
        parent_index = self.index_for_path(parent_path)
        existing_keys = {
            _path_key(child.path)
            for child in parent_node.children
        }
        incoming_by_key = {
            _path_key(child.get('path', '')): child
            for child in children
            if child.get('is_dir', False)
        }
        for existing in parent_node.children:
            incoming = incoming_by_key.get(_path_key(existing.path))
            if incoming and not incoming.get('_children_loaded', True):
                existing.has_children = True
        folder_children = sorted(
            (
                child for child in children
                if child.get('is_dir', False)
                and _path_key(child.get('path', '')) not in existing_keys
            ),
            key=lambda child: (
                (child.get('name') or '').lower(),
                (child.get('path') or '').lower(),
            ),
        )
        for child in folder_children:
            name = child.get('name') or os.path.basename(child.get('path', ''))
            insert_at = len(parent_node.children)
            sort_key = (name.lower(), (child.get('path') or '').lower())
            for index, existing in enumerate(parent_node.children):
                existing_key = (existing.name.lower(), existing.path.lower())
                if sort_key < existing_key:
                    insert_at = index
                    break
            self.beginInsertRows(parent_index, insert_at, insert_at)
            node = FolderBrowserNode(
                name=name,
                path=child.get('path', ''),
                parent=parent_node,
                has_children=not child.get('_children_loaded', True),
            )
            parent_node.children.insert(insert_at, node)
            self.nodes_by_path[_path_key(node.path)] = node
            self.endInsertRows()
        parent_node.loaded = True
        parent_node.loading = False
        parent_node.has_children = bool(parent_node.children)
        self.dataChanged.emit(parent_index, parent_index, [])

    def defer_load(self, path):
        node = self.nodes_by_path.get(_path_key(path))
        if node is not None:
            node.loading = False
            node.loaded = False
            node.has_children = True

    def mark_load_failed(self, path):
        node = self.nodes_by_path.get(_path_key(path))
        if node is not None:
            node.loading = False
            node.loaded = True

    def refresh_path(self, path):
        node = self.nodes_by_path.get(_path_key(path))
        if node is None:
            return QModelIndex()
        index = self.index_for_path(path)
        self._remove_children(node, index)
        node.loaded = False
        node.loading = False
        node.has_children = True
        self.dataChanged.emit(index, index, [])
        return index

    def remove_path(self, path):
        node = self.nodes_by_path.get(_path_key(path))
        if node is None or node.parent is None:
            return False
        parent_node = node.parent
        parent_index = (
            QModelIndex()
            if parent_node is self.root_node
            else self.index_for_path(parent_node.path)
        )
        row = node.row()
        self.beginRemoveRows(parent_index, row, row)
        parent_node.children.pop(row)
        stack = [node]
        while stack:
            current = stack.pop()
            stack.extend(current.children)
            self.nodes_by_path.pop(_path_key(current.path), None)
        self.endRemoveRows()
        if parent_node is not self.root_node:
            parent_node.has_children = bool(parent_node.children)
            self.dataChanged.emit(parent_index, parent_index, [])
        return True

    def _remove_children(self, node, parent_index):
        if not node.children:
            return
        self.beginRemoveRows(parent_index, 0, len(node.children) - 1)
        stack = list(node.children)
        while stack:
            child = stack.pop()
            stack.extend(child.children)
            self.nodes_by_path.pop(_path_key(child.path), None)
        node.children = []
        self.endRemoveRows()

class FolderBrowserTreeView(QTreeView):
    def mouseMoveEvent(self, event):
        cursor = (
            Qt.CursorShape.PointingHandCursor
            if self.indexAt(event.position().toPoint()).isValid()
            else Qt.CursorShape.ArrowCursor
        )
        self.viewport().setCursor(cursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        if (
            event.button() == Qt.MouseButton.LeftButton
            and index.isValid()
            and event.position().x() < self.visualRect(index).left()
        ):
            self.setExpanded(index, not self.isExpanded(index))
            event.accept()
            return
        super().mousePressEvent(event)

class FolderBrowserPanel(QFrame):
    MIN_WIDTH = 160
    DEFAULT_WIDTH = MIN_WIDTH

    scopeChanged = pyqtSignal(str)
    closeRequested = pyqtSignal()
    loadRequested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Expanding,
        )
        self.resize(self.DEFAULT_WIDTH, self.height())
        self.root_path = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("searchSection")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_MD)
        title = QLabel("Folders Panel")
        title.setObjectName("filterDrawerTitle")
        header_layout.addWidget(title)
        layout.addWidget(header)

        self.tree = FolderBrowserTreeView()
        self.tree.setObjectName("folderBrowserTree")
        self.tree.setMouseTracking(True)
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(False)
        self.tree.setIndentation(16)
        self.tree.setIconSize(QSize(18, 18))
        self.tree.setViewportMargins(SPACE_SM, 0, 0, 0)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setExpandsOnDoubleClick(True)
        self.model = FolderBrowserTreeModel(self)
        self.tree.setModel(self.model)
        self.model.loadRequested.connect(self.loadRequested)
        self.tree.selectionModel().currentChanged.connect(self._on_current_changed)
        layout.addWidget(self.tree, 1)

        self.empty_label = QLabel("Folder navigation is available after a scan completes.")
        self.empty_label.setObjectName("modalSecondary")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
        layout.addWidget(self.empty_label, 1)

        action_box = QWidget()
        action_box.setObjectName("sidebarActionBox")
        self.action_box = action_box
        action_layout = QVBoxLayout(action_box)
        action_layout.setContentsMargins(SPACE_SM, SPACE_MD, SPACE_SM, SPACE_SM)
        self.btn_close = QPushButton("Close Folders Panel")
        self.btn_close.setObjectName("closeSidebar")
        self.btn_close.setToolTip("Hide the folder browser")
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setFixedHeight(32)
        self.btn_close.setMinimumWidth(0)
        self.btn_close.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.btn_close.clicked.connect(self.closeRequested)
        action_layout.addWidget(self.btn_close)
        layout.addWidget(action_box)

        self.set_root(None)
        self._update_close_button_width()

    def sizeHint(self):
        return QSize(self.DEFAULT_WIDTH, 0)

    def minimumSizeHint(self):
        return QSize(0, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_close_button_width()

    def _update_close_button_width(self):
        if not hasattr(self, "btn_close"):
            return
        available_width = max(120, self.width() - (2 * SPACE_SM))
        self.btn_close.setMaximumWidth(min(172, available_width))

    def set_root(self, path):
        self.root_path = os.path.normpath(path) if path else None
        blocker = QSignalBlocker(self.tree.selectionModel())
        self.model.reset_root(self.root_path)
        self.tree.setVisible(bool(self.root_path))
        self.empty_label.setVisible(not self.root_path)
        if self.root_path:
            root_index = self.model.index(0, 0)
            self.tree.setCurrentIndex(root_index)
            self.tree.expand(root_index)
        del blocker

    def select_root(self):
        if not self.root_path:
            return
        index = self.model.index_for_path(self.root_path)
        if index.isValid():
            blocker = QSignalBlocker(self.tree.selectionModel())
            self.tree.setCurrentIndex(index)
            del blocker

    def apply_children(self, parent_path, children):
        self.model.apply_children(parent_path, children)

    def merge_children(self, parent_path, children):
        self.model.merge_children(parent_path, children)

    def defer_load(self, path):
        self.model.defer_load(path)

    def mark_load_failed(self, path):
        self.model.mark_load_failed(path)

    def refresh_paths(self, parent_paths):
        for path in dict.fromkeys(parent_paths):
            index = self.model.refresh_path(path)
            if index.isValid() and self.tree.isExpanded(index):
                self.model.fetchMore(index)

    def remove_paths(self, paths):
        for path in sorted(
            dict.fromkeys(path for path in paths if path),
            key=lambda value: len(os.path.normpath(value)),
            reverse=True,
        ):
            self.model.remove_path(path)

    def _on_current_changed(self, current, previous):
        path = self.model.data(current, Qt.ItemDataRole.UserRole)
        if path:
            self.scopeChanged.emit(path)
