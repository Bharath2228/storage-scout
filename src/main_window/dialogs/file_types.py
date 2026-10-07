from PyQt6.QtCore import (
    QRect,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QPainter,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...models import format_size
from ...theme import SPACE_LG, SPACE_MD, SPACE_SM, SPACE_XL, current_palette


class FileTypeBarDelegate(QStyledItemDelegate):
    @staticmethod
    def format_share(share):
        if share <= 0:
            return "0%"
        if share < 0.001:
            return "<0.1%"
        return f"{share * 100:.1f}%"

    def paint(self, painter, option, index):
        painter.save()
        try:
            share = float(index.data(Qt.ItemDataRole.UserRole) or 0.0)
        except (TypeError, ValueError):
            share = 0.0

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = current_palette()

        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor(palette["accent_tint"]))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(option.rect, QColor(palette["surface_hover"]))

        content_rect = option.rect.adjusted(12, 0, -12, 0)
        percentage_width = 54
        track_rect = QRect(
            content_rect.left(),
            content_rect.center().y() - 4,
            max(0, content_rect.width() - percentage_width - SPACE_SM),
            8,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(palette["border"]))
        painter.drawRoundedRect(track_rect, 4, 4)

        if share > 0:
            width = max(2, int(track_rect.width() * min(1.0, share)))
            bar_rect = QRect(
                track_rect.left(),
                track_rect.top(),
                width,
                track_rect.height(),
            )
            painter.setBrush(QColor(palette["accent"]))
            painter.drawRoundedRect(bar_rect, 4, 4)

        percentage_rect = QRect(
            track_rect.right() + SPACE_SM,
            option.rect.top(),
            percentage_width,
            option.rect.height(),
        )
        painter.setPen(QColor(palette["text_muted"]))
        painter.drawText(
            percentage_rect,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            self.format_share(share),
        )
        painter.restore()

class NumericTableWidgetItem(QTableWidgetItem):
    def __init__(self, text, numeric_value):
        super().__init__(text)
        self.numeric_value = numeric_value

    def __lt__(self, other):
        if isinstance(other, NumericTableWidgetItem):
            return self.numeric_value < other.numeric_value
        return super().__lt__(other)

class FileTypesDialog(QDialog):
    extension_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("File Types")
        self.setModal(False)
        self.resize(700, 560)
        self.setMinimumSize(600, 420)
        self.setObjectName("modalDialog")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        layout.setSpacing(SPACE_MD)

        title = QLabel("File Types")
        title.setObjectName("modalTitle")
        layout.addWidget(title)

        self.detail_label = QLabel("Calculating file type breakdown...")
        self.detail_label.setObjectName("modalDetail")
        layout.addWidget(self.detail_label)

        self.summary_strip = QFrame()
        self.summary_strip.setObjectName("fileTypesSummary")
        summary_layout = QHBoxLayout(self.summary_strip)
        summary_layout.setContentsMargins(SPACE_LG, SPACE_SM, SPACE_LG, SPACE_SM)
        summary_layout.setSpacing(SPACE_LG)
        self.summary_types_value, types_metric = self._make_summary_metric("Types")
        self.summary_files_value, files_metric = self._make_summary_metric("Files")
        self.summary_size_value, size_metric = self._make_summary_metric("Total size")
        summary_layout.addWidget(types_metric)
        summary_layout.addWidget(self._summary_divider())
        summary_layout.addWidget(files_metric)
        summary_layout.addWidget(self._summary_divider())
        summary_layout.addWidget(size_metric)
        summary_layout.addStretch(1)
        self.summary_strip.setVisible(False)
        layout.addWidget(self.summary_strip)

        self.table = QTableWidget(0, 4)
        self.table.setObjectName("fileTypesTable")
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.setHorizontalHeaderLabels(["Type", "Share", "Size", "Files"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.setShowGrid(False)
        self.table.setMouseTracking(True)
        self.table.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self.table.setItemDelegateForColumn(1, FileTypeBarDelegate(self.table))
        header = self.table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 200)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 80)
        self.table.setSortingEnabled(True)
        header.setSortIndicator(2, Qt.SortOrder.DescendingOrder)
        self.table.cellClicked.connect(self._activate_row)
        layout.addWidget(self.table, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(SPACE_MD)
        self.hint_label = QLabel("Select a type to show its matching files.")
        self.hint_label.setObjectName("modalSecondary")
        footer.addWidget(self.hint_label, 1)
        self.close_btn = QPushButton("Close")
        self.close_btn.setObjectName("modalCancel")
        self.close_btn.setFixedSize(100, 36)
        self.close_btn.clicked.connect(self.reject)
        footer.addWidget(self.close_btn)
        layout.addLayout(footer)

    @staticmethod
    def _summary_divider():
        divider = QFrame()
        divider.setObjectName("fileTypesSummaryDivider")
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFixedWidth(1)
        return divider

    @staticmethod
    def _make_summary_metric(label_text):
        container = QWidget()
        metric_layout = QVBoxLayout(container)
        metric_layout.setContentsMargins(0, 0, 0, 0)
        metric_layout.setSpacing(0)
        value = QLabel("--")
        value.setObjectName("fileTypesMetricValue")
        label = QLabel(label_text)
        label.setObjectName("fileTypesMetricLabel")
        metric_layout.addWidget(value)
        metric_layout.addWidget(label)
        return value, container

    def set_loading(self):
        self.detail_label.setText("Calculating file type breakdown...")
        self.detail_label.setVisible(True)
        self.summary_strip.setVisible(False)
        self.table.setRowCount(0)

    def set_error(self, message):
        self.detail_label.setText(f"Could not load file types: {message}")
        self.detail_label.setVisible(True)
        self.summary_strip.setVisible(False)
        self.table.setRowCount(0)

    def set_rows(self, rows):
        total_size = sum(size for _label, size, _count in rows) or 0
        total_files = sum(count for _label, _size, count in rows) or 0
        self.detail_label.setVisible(False)
        self.summary_types_value.setText(f"{len(rows):,}")
        self.summary_files_value.setText(f"{total_files:,}")
        self.summary_size_value.setText(format_size(total_size))
        self.summary_strip.setVisible(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for row, (label, total_size_for_type, file_count) in enumerate(rows):
            raw_extension = None
            if label == "(no extension)":
                raw_extension = ""
            else:
                raw_extension = label

            display_label = label
            if label != "(no extension)" and not label.startswith("."):
                display_label = f".{label}"
            type_item = QTableWidgetItem(display_label)
            type_item.setData(Qt.ItemDataRole.UserRole, raw_extension)
            type_item.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            self.table.setItem(row, 0, type_item)

            share = (total_size_for_type / total_size) if total_size else 0.0
            share_item = NumericTableWidgetItem("", share)
            share_item.setData(Qt.ItemDataRole.UserRole, share)
            self.table.setItem(row, 1, share_item)

            size_item = NumericTableWidgetItem(
                format_size(total_size_for_type),
                total_size_for_type,
            )
            size_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.table.setItem(row, 2, size_item)
            files_item = NumericTableWidgetItem(f"{file_count:,}", file_count)
            files_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.table.setItem(row, 3, files_item)
        self.table.setSortingEnabled(True)
        self.table.sortItems(2, Qt.SortOrder.DescendingOrder)

    def _activate_row(self, row, _column):
        item = self.table.item(row, 0)
        if item is None:
            return
        raw_extension = item.data(Qt.ItemDataRole.UserRole)
        if raw_extension is None:
            return
        self.extension_selected.emit(raw_extension)
        self.accept()
