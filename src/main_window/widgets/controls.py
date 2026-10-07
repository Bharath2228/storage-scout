import time

from PyQt6.QtCore import (
    QRectF,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QPainter,
    QPen,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ...theme import SPACE_MD, SPACE_SM, current_palette


class AccordionHeader(QFrame):
    clicked = pyqtSignal()

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setObjectName("accordionHeader")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_SM)

        self.chevron = QLabel()
        self.chevron.setObjectName("accordionChevron")
        self.chevron.setFixedWidth(18)
        self.chevron.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title = QLabel(text)
        self.title.setObjectName("accordionTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(self.chevron)
        layout.addWidget(self.title, 1)

    def set_expanded(self, expanded):
        self.chevron.setText("▾" if expanded else "▸")

    def set_text(self, text):
        self.title.setText(text)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

class SegmentedRadioButton(QRadioButton):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setMouseTracking(True)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        palette = current_palette()
        # Draw one inset outline on half pixels so the one-pixel border stays
        # crisp while its rounded corners remain smoothly anti-aliased.
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        checked = self.isChecked()
        hovered = self.underMouse()

        background = QColor(0, 0, 0, 0)
        border = palette["border"]
        text = palette["text"]

        if checked:
            background = QColor(palette["accent_tint"])
            border = palette["accent"]
            text = palette["accent"]
        elif hovered:
            background = QColor(palette["accent_tint"])
            border = palette["accent"]
            text = palette["text"]

        pen = QPen(QColor(border), 1.0)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 8, 8)
        painter.setPen(QColor(text))
        painter.setFont(self.font())
        painter.drawText(rect.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignCenter, self.text())

class FilterPopover(QWidget):
    closed = pyqtSignal()

    def __init__(self, width, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint,
        )
        self.setObjectName("filterPopover")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(width)
        self._last_hidden_at = 0.0
        self._shown_once = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 12)
        outer.setSpacing(0)

        self.card = QFrame()
        self.card.setObjectName("filterPopoverCard")
        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)
        self.card_layout.setSpacing(0)
        outer.addWidget(self.card)

        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 90))
        self.card.setGraphicsEffect(shadow)

    def set_content(self, widget):
        self.card_layout.addWidget(widget)

    def show_below(self, anchor):
        self.layout().activate()
        self.adjustSize()
        position = anchor.mapToGlobal(anchor.rect().bottomLeft())
        screen = QApplication.screenAt(position)
        if screen is not None:
            available = screen.availableGeometry()
            x = min(
                max(position.x(), available.left()),
                available.right() - self.width() + 1,
            )
            y = position.y()
            if y + self.height() > available.bottom():
                y = anchor.mapToGlobal(anchor.rect().topLeft()).y() - self.height()
            position.setX(x)
            position.setY(max(available.top(), y))
        self.move(position)
        self._shown_once = True
        self.show()
        self.raise_()

    def recently_hidden(self, threshold=0.18):
        return time.monotonic() - self._last_hidden_at < threshold

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event):
        super().hideEvent(event)
        if not self._shown_once:
            return
        self._shown_once = False
        self._last_hidden_at = time.monotonic()
        self.closed.emit()
