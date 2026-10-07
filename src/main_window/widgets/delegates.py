from PyQt6.QtCore import (
    QRect,
    Qt,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
)
from PyQt6.QtWidgets import (
    QApplication,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
)

from ...theme import FONT_FAMILY, TYPE_SCALE, current_palette, safe_point_size
from ..export_utils import paint_tree_row_border


class TreeRowDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.backgroundBrush = QBrush(Qt.BrushStyle.NoBrush)
        super().paint(painter, opt, index)
        paint_tree_row_border(painter, option)

class StatusDelegate(QStyledItemDelegate):
    def _colors_for_status(self, status):
        palette = current_palette()
        mapping = {
            'Empty': (
                palette["status_danger"],
                palette["status_danger_bg"],
            ),
            'Inactive': (
                palette["status_warning"],
                palette["status_warning_bg"],
            ),
            'Pending': (
                palette["status_warning"],
                palette["status_warning_bg"],
            ),
            'Active': (
                palette["status_success"],
                palette["status_success_bg"],
            ),
            'Context': (
                palette["accent"],
                palette["accent_tint"],
            ),
        }
        return tuple(QColor(value) for value in mapping.get(
            status,
            (palette["text_muted"], palette["surface"]),
        ))

    def paint(self, painter, option, index):
        status = index.data(Qt.ItemDataRole.DisplayRole)
        if not status:
            paint_tree_row_border(painter, option)
            return
        text_color, bg_color = self._colors_for_status(status)
        rect = option.rect
        pill = QRect(rect.left() + (rect.width() - 64) // 2,
                     rect.top() + (rect.height() - 20) // 2, 64, 20)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(bg_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(pill, 10, 10)
        
        f = QFont(FONT_FAMILY)
        f.setPointSize(safe_point_size(TYPE_SCALE["muted"]["point_size"], 8))
        f.setWeight(QFont.Weight(TYPE_SCALE["body"]["weight"]))


        painter.setFont(f)
        painter.setPen(text_color)
        
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, status)
        painter.restore()
        paint_tree_row_border(painter, option)

class SizeBarDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)

    def paint(self, painter, option, index):
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.backgroundBrush = QBrush(Qt.BrushStyle.NoBrush)
        opt.text = ""

        widget = opt.widget
        style = widget.style() if widget else QApplication.style()

        painter.save()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        display_font = index.data(Qt.ItemDataRole.FontRole) or opt.font
        painter.setFont(display_font)

        content_rect = opt.rect.adjusted(10, 5, -18, -5)

        text_color = (
            opt.palette.color(opt.palette.ColorRole.HighlightedText)
            if opt.state & QStyle.StateFlag.State_Selected
            else QColor(current_palette()["text"])
        )
        painter.setPen(text_color)
        painter.drawText(
            content_rect,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            text,
        )
        painter.restore()
        paint_tree_row_border(painter, option)

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        hint.setHeight(max(hint.height(), 34))
        return hint

class ActionDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return
        rect = option.rect
        
        # Determine if hovered
        is_hovered = option.state & QStyle.StateFlag.State_MouseOver

        # Smaller professional button: 64x22
        btn_w, btn_h = 64, 22
        btn = QRect(rect.left() + (rect.width() - btn_w) // 2,
                    rect.top() + (rect.height() - btn_h) // 2, btn_w, btn_h)
        
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = current_palette()
        
        if "queued" in text:
            # Draw red badge for queued items
            painter.setBrush(QBrush(QColor(palette["status_danger_bg"])))
            painter.setPen(QPen(QColor(palette["status_danger"]), 1))
            painter.drawRoundedRect(btn, 10, 10) # Rounded capsule
            f = QFont(FONT_FAMILY)
            f.setPointSize(safe_point_size(TYPE_SCALE["muted"]["point_size"], 8))
            f.setWeight(QFont.Weight(TYPE_SCALE["body"]["weight"]))


            painter.setFont(f)
            painter.setPen(QColor(palette["status_danger"]))
            painter.drawText(btn, Qt.AlignmentFlag.AlignCenter, "X Queued")
        else:
            if not is_hovered:
                painter.restore()
                return
            # Modern Small Outline Button for "Open"
            bg = QColor(palette["accent_tint"])
            painter.setBrush(QBrush(bg))
            painter.setPen(QPen(QColor(palette["accent"]), 1.2))
            painter.drawRoundedRect(btn, 5, 5)
            f = QFont(FONT_FAMILY)
            f.setPointSize(safe_point_size(TYPE_SCALE["muted"]["point_size"], 8))
            f.setWeight(QFont.Weight(TYPE_SCALE["body"]["weight"]))
            painter.setFont(f)
            painter.setPen(QColor(palette["accent"]))
            painter.drawText(btn, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()
