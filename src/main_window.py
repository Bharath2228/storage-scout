import os
import csv
import subprocess
import send2trash
from datetime import datetime
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QRadioButton, QSlider, QDateEdit, QTreeView, QHeaderView,
    QMessageBox, QStyledItemDelegate, QButtonGroup, QApplication, QFileDialog,
    QSpinBox, QAbstractItemView, QStackedWidget,
    QMenu, QSizePolicy, QFrame, QStyle
)
from PyQt6.QtCore import Qt, QDate, QRect, QModelIndex, QTimer, QEvent, QSignalBlocker
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QIcon, QFont

from .models import WatchdogTreeModel, WatchdogFilterProxyModel
from .scanner import ScannerThread


# ────────────────────────────────────────────────────────────────────────────
# Delegates
# ────────────────────────────────────────────────────────────────────────────

class StatusDelegate(QStyledItemDelegate):
    # (text_color, bg_color, border_color)
    _COLORS = {
        'Empty':    (QColor("#991b1b"), QColor("#fef2f2"), QColor("#fee2e2")), # Danger Red
        'Inactive': (QColor("#92400e"), QColor("#fffbeb"), QColor("#fef3c7")), # Warning Amber
        'Pending':  (QColor("#92400e"), QColor("#fffbeb"), QColor("#fef3c7")), # Warning Amber
        'Active':   (QColor("#065f46"), QColor("#f0fdf4"), QColor("#d1fae5")), # Success Emerald
        'Context':  (QColor("#1e40af"), QColor("#eff6ff"), QColor("#dbeafe")), # Info Blue
    }

    def paint(self, painter, option, index):
        status = index.data(Qt.ItemDataRole.DisplayRole)
        if not status:
            return
        colors = self._COLORS.get(status, (QColor(0x57, 0x60, 0x6a), QColor(0xf6, 0xf8, 0xfa), QColor(0xd0, 0xd7, 0xde)))
        text_color, bg_color, border_color = colors
        rect = option.rect
        pill = QRect(rect.left() + (rect.width() - 64) // 2,
                     rect.top() + (rect.height() - 20) // 2, 64, 20)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(bg_color))
        painter.setPen(QPen(border_color, 1))
        painter.drawRoundedRect(pill, 10, 10)

        
        # Colored dot
        dot_size = 6
        dot_rect = QRect(pill.left() + 8, pill.top() + (pill.height() - dot_size) // 2, dot_size, dot_size)
        painter.setBrush(QBrush(text_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(dot_rect)
        
        f = QFont("Segoe UI", 8)
        f.setBold(True)


        painter.setFont(f)
        painter.setPen(text_color)
        
        text_rect = QRect(pill.left() + 18, pill.top(), pill.width() - 18, pill.height())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, status)
        painter.restore()


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
        
        if "queued" in text:
            # Draw red badge for queued items
            painter.setBrush(QBrush(QColor("#fef2f2")))
            painter.setPen(QPen(QColor("#ef4444"), 1))
            painter.drawRoundedRect(btn, 10, 10) # Rounded capsule
            f = QFont("Segoe UI", 8)
            f.setBold(True)


            painter.setFont(f)
            painter.setPen(QColor("#ef4444"))
            painter.drawText(btn, Qt.AlignmentFlag.AlignCenter, "✕ Queued")
        else:
            # Modern Small Outline Button for "Open"
            bg = QColor("#eff6ff") if is_hovered else QColor("transparent")
            painter.setBrush(QBrush(bg))
            painter.setPen(QPen(QColor("#2563eb"), 1.2))
            painter.drawRoundedRect(btn, 5, 5)
            f = QFont("Segoe UI", 8)
            f.setBold(True)
            painter.setFont(f)
            painter.setPen(QColor("#2563eb"))
            painter.drawText(btn, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

# ────────────────────────────────────────────────────────────────────────────
# Filter Panel (standalone widget)
# ────────────────────────────────────────────────────────────────────────────

class AnyDateEdit(QDateEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCalendarPopup(True)
        self.calendarWidget().installEventFilter(self)
        self.lineEdit().setReadOnly(True)
        # Prevent keyboard focus on the line edit so it doesn't blink a cursor
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def eventFilter(self, obj, event):
        if obj == self.calendarWidget() and event.type() == QEvent.Type.Show:
            # Force a valid font to avoid setPointSize(-1) warnings from Qt internals
            f = self.calendarWidget().font()
            if f.pointSize() <= 0:
                f.setPointSize(9)
                self.calendarWidget().setFont(f)
                
            if self.date() == self.minimumDate():
                current_date = QDate.currentDate()
                self.calendarWidget().setCurrentPage(current_date.year(), current_date.month())
        return super().eventFilter(obj, event)

class FilterPanel(QFrame):
    DEFAULT_STALE_MONTHS = 3
    AGE_FILTER_DISABLED = 0
    MAX_STALE_MONTHS = 24
    DEFAULT_STATUS_FILTER = "Inactive"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setVisible(False)
        self.setFixedWidth(280)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 16, 0, 16)
        outer.setSpacing(4)

        # ── Section 1: Display Mode ──────────────────────────────────────────
        display_section = QWidget()
        display_section.setObjectName("displayModeSection")
        display_layout = QVBoxLayout(display_section)
        display_layout.setContentsMargins(0, 0, 0, 0)
        display_layout.setSpacing(4)

        lbl_display = QLabel("DISPLAY MODE")
        lbl_display.setObjectName("displayModeHeader")

        display_layout.addWidget(lbl_display)

        self.rb_all      = QRadioButton("Show all")
        self.rb_inactive = QRadioButton("Inactive only")
        self.rb_empty    = QRadioButton("Empty only")
        self.rb_all.setChecked(True)
        self.bg = QButtonGroup()
        for rb in [self.rb_all, self.rb_inactive, self.rb_empty]:
            self.bg.addButton(rb)
            display_layout.addWidget(rb)
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
        
        outer.addWidget(display_section)
        outer.addWidget(self._hline())



        # ── Section 2: Date Range ────────────────────────────────────────────
        date_section = QWidget()
        date_section.setObjectName("dateRangeSection")
        date_layout = QVBoxLayout(date_section)
        date_layout.setContentsMargins(0, 0, 0, 0)
        date_layout.setSpacing(4)

        lbl_date = QLabel("DATE RANGE")
        lbl_date.setObjectName("dateRangeHeader")

        date_layout.addWidget(lbl_date)

        self.date_from = AnyDateEdit()
        self.date_from.setDisplayFormat("dd/MM/yyyy")
        self.date_from.setSpecialValueText("Any")
        self.date_from.setDate(self.date_from.minimumDate())
        self.date_from.setMinimumWidth(150)

        self.date_to = AnyDateEdit()
        self.date_to.setDisplayFormat("dd/MM/yyyy")
        self.date_to.setSpecialValueText("Any")
        self.date_to.setDate(self.date_to.minimumDate())
        self.date_to.setMinimumWidth(150)
        self.date_from.setObjectName("dateRangeInput")
        self.date_to.setObjectName("dateRangeInput")
        self.date_from.setCursor(Qt.CursorShape.PointingHandCursor)
        self.date_to.setCursor(Qt.CursorShape.PointingHandCursor)


        for lbl_text, widget in [("From", self.date_from), ("To", self.date_to)]:
            box = QWidget()
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(16, 4, 16, 4)
            box_layout.setSpacing(4)
            l = QLabel(lbl_text)
            l.setObjectName(f"{lbl_text.lower()}Label")

            box_layout.addWidget(l)
            box_layout.addWidget(widget)
            date_layout.addWidget(box)

        btn_box = QWidget()
        btn_layout = QHBoxLayout(btn_box)
        btn_layout.setContentsMargins(16, 0, 16, 0)
        self.btn_clear_dates = QPushButton("Clear dates")
        self.btn_clear_dates.setObjectName("clearDates")
        self.btn_clear_dates.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_dates.clicked.connect(self._clear_dates)
        btn_layout.addWidget(self.btn_clear_dates)
        btn_layout.addStretch()
        date_layout.addWidget(btn_box)
        
        outer.addWidget(date_section)
        outer.addWidget(self._hline())

        # ── Section 3: Stale Threshold ───────────────────────────────────────
        age_section = QWidget()
        age_section.setObjectName("ageThresholdSection")
        age_outer_layout = QVBoxLayout(age_section)
        age_outer_layout.setContentsMargins(0, 0, 0, 0)
        age_outer_layout.setSpacing(4)

        lbl_age = QLabel("AGE THRESHOLD")
        lbl_age.setObjectName("ageThresholdHeader")

        age_outer_layout.addWidget(lbl_age)

        age_box = QWidget()
        age_layout = QVBoxLayout(age_box)
        age_layout.setContentsMargins(16, 4, 16, 4)
        age_layout.setSpacing(8)

        age_hdr = QHBoxLayout()
        age_hdr.setSpacing(6)
        self.lbl_pill = QLabel()
        self.lbl_pill.setObjectName("agePill")
        self.lbl_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_pill.setFixedHeight(20)
        self.lbl_val = QLabel()
        self.lbl_val.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        age_hdr.addWidget(self.lbl_pill)
        age_hdr.addWidget(self.lbl_val)
        age_hdr.addStretch()
        age_layout.addLayout(age_hdr)
        
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(self.AGE_FILTER_DISABLED, self.MAX_STALE_MONTHS)
        self.slider.setValue(self.AGE_FILTER_DISABLED)
        self.slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider.setTickInterval(1)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider.valueChanged.connect(self._update_age_label)
        self.slider.valueChanged.connect(self._sync_manual_age_from_slider)
        age_layout.addWidget(self.slider)

        self.age_input = QSpinBox()
        self.age_input.setRange(self.AGE_FILTER_DISABLED, self.MAX_STALE_MONTHS)
        self.age_input.setValue(self.AGE_FILTER_DISABLED)
        self.age_input.setSuffix(" months")
        self.age_input.setCursor(Qt.CursorShape.PointingHandCursor)
        self.age_input.valueChanged.connect(self._sync_slider_from_manual_age)

        bot_row = QHBoxLayout()
        bot_row.setSpacing(8)
        lbl_manual = QLabel("Manual:")
        lbl_manual.setObjectName("manualLabel")

        bot_row.addWidget(lbl_manual)
        bot_row.addWidget(self.age_input)
        bot_row.addStretch()
        age_layout.addLayout(bot_row)
        age_outer_layout.addWidget(age_box)
        
        outer.addWidget(age_section)

        self._update_age_label(self.slider.value())
        outer.addStretch()

        # ── Section 4: Actions ───────────────────────────────────────────────
        action_box = QWidget()
        action_layout = QVBoxLayout(action_box)
        action_layout.setContentsMargins(16, 16, 16, 16)
        action_layout.setSpacing(10)
        
        self.btn_reset = QPushButton("↺  Reset all filters")
        self.btn_reset.setObjectName("resetFilters")

        self.btn_reset.setToolTip("Reset all filters to their default values")
        self.btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close = QPushButton("Close sidebar")
        self.btn_close.setObjectName("closeSidebar")
        self.btn_close.setToolTip("Hide the filter sidebar")
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        action_layout.addWidget(self.btn_reset)
        action_layout.addWidget(self.btn_close)
        outer.addWidget(action_box)

    def _clear_dates(self):
        """Reset both date pickers back to 'Any'."""
        self.date_from.setDate(self.date_from.minimumDate())
        self.date_to.setDate(self.date_to.minimumDate())

    def _update_age_label(self, value):
        if value == self.AGE_FILTER_DISABLED:
            self.lbl_pill.setText("Off")
            self.lbl_pill.setStyleSheet("background-color: #64748b; color: white;")
            self.lbl_val.setText("Age filtering is disabled")
        else:
            self.lbl_pill.setText(f"{value}m")
            self.lbl_pill.setStyleSheet("background-color: #2563eb; color: white;")
            
            years = value // 12
            months = value % 12
            if years > 0 and months > 0:
                text = f"Older than {years}y {months}m"
            elif years > 0:
                text = f"Older than {years}y"
            else:
                text = f"Older than {value} month{'s' if value > 1 else ''}"
            self.lbl_val.setText(text)

    def _hline(self):
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        f.setObjectName("sidebarDivider")
        f.setFixedHeight(1)
        return f

    def _sync_manual_age_from_slider(self, value):
        blocker = QSignalBlocker(self.age_input)
        self.age_input.setValue(value)
        del blocker

    def _sync_slider_from_manual_age(self, value):
        blocker = QSignalBlocker(self.slider)
        self.slider.setValue(value)
        del blocker
        self._update_age_label(value)

    def get_date_range_ts(self):
        from_date = self.date_from.date()
        to_date = self.date_to.date()
        minimum_date = self.date_from.minimumDate()

        if from_date != minimum_date and to_date != minimum_date and from_date > to_date:
            from_blocker = QSignalBlocker(self.date_from)
            to_blocker = QSignalBlocker(self.date_to)
            self.date_from.setDate(to_date)
            self.date_to.setDate(from_date)
            del from_blocker
            del to_blocker
            from_date, to_date = to_date, from_date

        date_from_ts = None if from_date == minimum_date else int(from_date.startOfDay().toSecsSinceEpoch())
        date_to_ts = None if to_date == minimum_date else int(to_date.endOfDay().toSecsSinceEpoch())
        return date_from_ts, date_to_ts

    def get_older_than_secs(self):
        """Returns seconds threshold or None if slider is at minimum (show all)."""
        v = self.slider.value()
        if v == self.AGE_FILTER_DISABLED:
            return None
        return v * 30 * 24 * 3600  # months → seconds (approximate)

    def get_stale_months_for_scan(self):
        value = self.slider.value()
        return value if value > 0 else self.DEFAULT_STALE_MONTHS

    def apply_default_browse_preset(self):
        self.rb_inactive.setChecked(True)
        self._clear_dates()
        self.slider.setValue(self.DEFAULT_STALE_MONTHS)
        
        blocker = QSignalBlocker(self.age_input)
        self.age_input.setValue(self.DEFAULT_STALE_MONTHS)
        del blocker
        
        self._update_age_label(self.DEFAULT_STALE_MONTHS)

    # helpers
    def _make_section(self, title):
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)
        lbl = QLabel(title)
        lbl.setObjectName("sectionLabel")
        col.addWidget(lbl)
        return col

    def _vline(self):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFixedWidth(1)
        line.setObjectName("divider")
        return line


# ────────────────────────────────────────────────────────────────────────────
# Main Window
# ────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("IBMS Folder Watchdog — Exchange Drive Scanner")
        self.resize(1200, 720)

        self.scanner_thread = None
        self.tree_model     = None
        self.proxy_model    = WatchdogFilterProxyModel()

        self.recount_timer = QTimer(self)
        self.recount_timer.setSingleShot(True)
        self.recount_timer.timeout.connect(self._do_recount)

        # Debounce timer for age threshold — avoids refiltering on every tick
        self.filter_debounce_timer = QTimer(self)
        self.filter_debounce_timer.setSingleShot(True)
        self.filter_debounce_timer.setInterval(180)  # ms after last change
        self.filter_debounce_timer.timeout.connect(self._on_filter_changed)

        self._build_ui()

    # ──────────────────────────────────────────────────────────────────────────
    # UI
    # ──────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        vbox = QVBoxLayout(root)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        # ── Top bar ───────────────────────────────────────────────────────────
        topbar = QWidget()
        topbar.setObjectName("topbar")
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(16, 12, 16, 12)
        tb.setSpacing(12)

        lbl = QLabel("IBMS Watchdog")
        lbl.setObjectName("appTitle")
        tb.addWidget(lbl)
        tb.addWidget(self._vbar())

        self.txt_path = QLineEdit()
        self.txt_path.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.txt_path.setMinimumWidth(240)
        self.txt_path.setPlaceholderText("Enter or browse a folder path…")
        
        # Add folder icon to the left of the path input
        path_icon = QIcon.fromTheme("folder-open", QIcon.fromTheme("folder"))
        self.txt_path.addAction(path_icon, QLineEdit.ActionPosition.LeadingPosition)

        btn_browse = QPushButton("📂  Browse")
        btn_browse.setObjectName("primaryBtn")
        btn_browse.setToolTip("Browse folder")
        btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_browse.clicked.connect(self._browse)
        tb.addWidget(self.txt_path)
        tb.addWidget(btn_browse)

        self.btn_rescan = QPushButton("⟳  Re-scan")
        self.btn_rescan.setObjectName("primaryBtn")
        self.btn_rescan.setToolTip("Start scanning the selected folder path")
        self.btn_rescan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_rescan.clicked.connect(self.start_scan)
        tb.addWidget(self.btn_rescan)

        tb.addWidget(self._vbar())

        self.btn_filter = QPushButton("⚙  Filters")
        self.btn_filter.setObjectName("ghostBtn")
        self.btn_filter.setCheckable(True)
        self.btn_filter.setToolTip("Toggle filter sidebar (Alt+F)")
        self.btn_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_filter.clicked.connect(self._toggle_filters)
        tb.addWidget(self.btn_filter)

        self.btn_expand = QPushButton("Expand All")
        self.btn_expand.setObjectName("collapseAll")
        self.btn_expand.setCheckable(True)
        self.btn_expand.setToolTip("Expand or collapse all folders in the view")
        self.btn_expand.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_expand.clicked.connect(self._toggle_expand)

        # ── Push Export + Delete to the far right ──────────────────────────
        tb.addStretch()
        tb.addWidget(self._vbar())

        btn_export = QPushButton("📊  Export CSV")
        btn_export.setObjectName("primaryBtn")
        btn_export.setToolTip("Export the current view to CSV")
        btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_export.clicked.connect(self._export_csv)
        tb.addWidget(btn_export)

        self.btn_delete = QPushButton("🗑  Delete Selected")
        self.btn_delete.setObjectName("deleteBtn")
        self.btn_delete.setToolTip("Permanently delete selected items")
        self.btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_delete.clicked.connect(self._delete_selected)
        self.btn_delete.setEnabled(False)
        tb.addWidget(self.btn_delete)

        vbox.addWidget(topbar)

        # ── Main Content Area (Sidebar + Content) ──────────────────────────────
        main_area = QHBoxLayout()
        main_area.setContentsMargins(0, 0, 0, 0)
        main_area.setSpacing(0)

        # ── Left Sidebar (Filters) ───────────────────────────────────────────
        self.fp = FilterPanel()
        self.fp.btn_close.clicked.connect(self._toggle_filters)
        self.fp.btn_reset.clicked.connect(self._reset_filters)
        # Dynamic filtering
        self.fp.bg.buttonClicked.connect(lambda _btn: self._on_filter_changed())
        self.fp.date_from.dateChanged.connect(lambda _d: self._on_filter_changed())
        self.fp.date_to.dateChanged.connect(lambda _d: self._on_filter_changed())
        # Age controls: debounced
        self.fp.slider.valueChanged.connect(lambda _v: self.filter_debounce_timer.start())
        self.fp.age_input.valueChanged.connect(lambda _v: self.filter_debounce_timer.start())
        self.fp.btn_clear_dates.clicked.connect(lambda: self._on_filter_changed())
        self._apply_default_browse_preset(apply_now=False)
        main_area.addWidget(self.fp)

        # ── Right Content Area ────────────────────────────────────────────────
        self.right_content = QWidget()
        self.right_content.setObjectName("contentArea")
        right_v = QVBoxLayout(self.right_content)
        right_v.setContentsMargins(24, 24, 24, 24)
        right_v.setSpacing(16)

        # ── Controls row (above tree): expand / select all) ────────────────
        self.controls_bar = QWidget()
        controls_layout = QHBoxLayout(self.controls_bar)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(12)
        controls_layout.addWidget(self.btn_expand)
        self.btn_select_all = QPushButton("Select All")
        self.btn_select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select_all.clicked.connect(self._select_all)
        controls_layout.addWidget(self.btn_select_all)
        
        self.btn_select_inactive = QPushButton("Select All Inactive")
        self.btn_select_inactive.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select_inactive.clicked.connect(self._select_inactive)
        self.btn_select_inactive.setEnabled(False)
        controls_layout.addWidget(self.btn_select_inactive)

        self.btn_select_empty = QPushButton("Select All Empty")
        self.btn_select_empty.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select_empty.clicked.connect(self._select_empty)
        self.btn_select_empty.setEnabled(False)
        controls_layout.addWidget(self.btn_select_empty)

        controls_layout.addStretch()

        # Integrated Pagination
        self.btn_prev_page = QPushButton("◀")
        self.btn_prev_page.setFixedWidth(30)
        self.btn_prev_page.setToolTip("Previous Page")
        self.btn_prev_page.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_prev_page.clicked.connect(self._prev_page)
        controls_layout.addWidget(self.btn_prev_page)

        self.lbl_page_info = QLabel("Page 1")
        self.lbl_page_info.setStyleSheet("color: #64748b; font-weight: 500; font-size: 12px; margin: 0 8px;")
        controls_layout.addWidget(self.lbl_page_info)

        self.btn_next_page = QPushButton("▶")
        self.btn_next_page.setFixedWidth(30)
        self.btn_next_page.setToolTip("Next Page")
        self.btn_next_page.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next_page.clicked.connect(self._next_page)
        controls_layout.addWidget(self.btn_next_page)

        self.controls_bar.setVisible(False)
        right_v.addWidget(self.controls_bar)

        # ── Empty state + Tree wrapped in a stacked widget ───────────────────
        self.content_stack = QStackedWidget()

        # Page 0: Empty state
        empty_page = QWidget()
        empty_page.setObjectName("emptyState")
        ep_layout = QVBoxLayout(empty_page)
        ep_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ep_layout.setSpacing(16)

        icon_lbl = QLabel("📁")
        icon_lbl.setObjectName("emptyIcon")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_lbl = QLabel("READY TO SCAN")
        title_lbl.setObjectName("emptyTitle")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub_lbl = QLabel("Select a folder above to analyze its contents.\nInactive and empty folders will be highlighted automatically.")
        sub_lbl.setObjectName("emptySub")
        sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub_lbl.setWordWrap(True)
        sub_lbl.setMaximumWidth(420)

        btn_browse_cta = QPushButton("📂  Browse folder...")
        btn_browse_cta.setObjectName("primaryBtn")
        btn_browse_cta.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_browse_cta.setFixedWidth(180)
        btn_browse_cta.clicked.connect(self._browse)

        ep_layout.addStretch()
        ep_layout.addWidget(icon_lbl)
        ep_layout.addWidget(title_lbl)
        ep_layout.addWidget(sub_lbl, alignment=Qt.AlignmentFlag.AlignCenter)
        ep_layout.addSpacing(8)
        ep_layout.addWidget(btn_browse_cta, alignment=Qt.AlignmentFlag.AlignCenter)
        ep_layout.addStretch()

        # Page 1: Tree view
        self.tree = QTreeView()
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tree.setAlternatingRowColors(True)
        # Enable sorting
        self.tree.setSortingEnabled(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(False)
        self.tree.setMouseTracking(True)
        self.tree.viewport().setMouseTracking(True)
        self.tree.setItemDelegateForColumn(5, StatusDelegate(self.tree))
        self.tree.setItemDelegateForColumn(6, ActionDelegate(self.tree))
        self.tree.clicked.connect(self._on_click)
        self.tree.doubleClicked.connect(self._on_double_click)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self.tree.viewport().installEventFilter(self)
        hdr = self.tree.header()
        hdr.setSectionsMovable(False)
        hdr.setStretchLastSection(False)
        hdr.setSectionsClickable(True)
        try:
            hdr.setSortIndicatorShown(True)
            hdr.setSortIndicator(3, Qt.SortOrder.DescendingOrder) # Default sort by Age
        except Exception:
            pass
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)

        # Page 2: No results (filter produced zero matches)
        no_results_page = QWidget()
        no_results_page.setObjectName("emptyState")
        nr_layout = QVBoxLayout(no_results_page)
        nr_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nr_layout.setSpacing(14)
        nr_icon = QLabel("🔍")
        nr_icon.setObjectName("emptyIcon")
        nr_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nr_title = QLabel("No matching items")
        nr_title.setObjectName("emptyTitle")
        nr_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.nr_sub = QLabel("Try adjusting your filters or age threshold.")
        self.nr_sub.setObjectName("emptySub")
        self.nr_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.nr_sub.setWordWrap(True)
        self.nr_sub.setMaximumWidth(400)
        btn_reset_nr = QPushButton("↺  Reset filters")
        btn_reset_nr.setObjectName("primaryBtn")
        btn_reset_nr.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_reset_nr.setFixedWidth(160)
        btn_reset_nr.clicked.connect(self._reset_filters)
        nr_layout.addStretch()
        nr_layout.addWidget(nr_icon)
        nr_layout.addWidget(nr_title)
        nr_layout.addWidget(self.nr_sub, alignment=Qt.AlignmentFlag.AlignCenter)
        nr_layout.addSpacing(8)
        nr_layout.addWidget(btn_reset_nr, alignment=Qt.AlignmentFlag.AlignCenter)
        nr_layout.addStretch()

        self.content_stack.addWidget(empty_page)      # index 0
        self.content_stack.addWidget(self.tree)       # index 1
        self.content_stack.addWidget(no_results_page) # index 2

        right_v.addWidget(self.content_stack)


        self.content_stack.addWidget(self.tree)        # index 1
        self.content_stack.addWidget(no_results_page)  # index 2
        self.content_stack.setCurrentIndex(0)

        # ── Wrap content_stack in container so we can overlay the floating button ─
        self.tree_container = QWidget()
        self.tree_container.setObjectName("tableCard")
        tc_layout = QVBoxLayout(self.tree_container)
        tc_layout.setContentsMargins(1, 1, 1, 1) # Internal border gap
        tc_layout.setSpacing(0)
        tc_layout.addWidget(self.content_stack)


        # Floating scroll-to-top button (child of tree_container for z-order)
        self.btn_scroll_top = QPushButton("↑")
        self.btn_scroll_top.setObjectName("scrollTopBtn")
        self.btn_scroll_top.setParent(self.tree_container)
        self.btn_scroll_top.setFixedSize(38, 38)
        self.btn_scroll_top.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scroll_top.setToolTip("Back to top")
        self.btn_scroll_top.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_scroll_top.setVisible(False)
        self.btn_scroll_top.clicked.connect(self._scroll_to_top)
        self.btn_scroll_top.raise_()

        # Show/hide based on scroll position
        self.tree.verticalScrollBar().valueChanged.connect(self._on_tree_scroll)
        self.tree_container.installEventFilter(self)

        right_v.addWidget(self.tree_container, 1)
        main_area.addWidget(self.right_content, 1)

        vbox.addLayout(main_area, 1)

        # ── Status bar ────────────────────────────────────────────────────────
        sb = QWidget()
        sb.setObjectName("statusbar")
        sb.setFixedHeight(36)
        sbl = QHBoxLayout(sb)
        sbl.setContentsMargins(12, 0, 12, 0)
        sbl.setSpacing(12)
        self.lbl_status = QLabel("Ready — select a folder and click Re-scan")
        self.lbl_status.setObjectName("statusMessage")

        sbl.addWidget(self.lbl_status)
        sbl.addStretch()
        
        self.chip_empty = self._chip("Empty: 0", "chipEmpty")
        self.chip_inactive_folders = self._chip("Inactive folders: 0", "chipInactive")
        self.chip_inactive_files = self._chip("Inactive files: 0", "chipInactive")
        self.chip_space = self._chip("Reclaimable: —", "chipSpace")
        
        sbl.addWidget(self._sep())
        sbl.addWidget(self.chip_empty)
        sbl.addWidget(self._sep())
        sbl.addWidget(self.chip_inactive_folders)
        sbl.addWidget(self._sep())
        sbl.addWidget(self.chip_inactive_files)
        sbl.addWidget(self._sep())
        sbl.addWidget(self.chip_space)
        vbox.addWidget(sb)

        # Do NOT auto-start scan — let the user enter a path first

    def _sep(self):
        l = QLabel("•")
        l.setObjectName("statusSeparator")
        return l


    def _vbar(self):
        f = QFrame()
        f.setFrameShape(QFrame.Shape.VLine)
        f.setFixedWidth(1)
        f.setFixedHeight(24)
        f.setObjectName("divider")
        return f

    def _chip(self, text, obj_name):
        l = QLabel(text)
        l.setObjectName(obj_name)
        return l

    def _on_tree_scroll(self, value):
        """Show/hide the scroll-to-top button based on vertical scroll position."""
        visible = value > 80
        self.btn_scroll_top.setVisible(visible)
        if visible:
            self._reposition_scroll_top_btn()

    def _reposition_scroll_top_btn(self):
        """Keep the floating button pinned to the bottom-right of the tree container."""
        btn = self.btn_scroll_top
        c = self.tree_container
        margin = 18
        x = c.width() - btn.width() - margin
        y = c.height() - btn.height() - margin
        btn.move(x, y)
        btn.raise_()

    def _scroll_to_top(self):
        self.tree.scrollToTop()

    def eventFilter(self, obj, event):
        if hasattr(self, 'tree') and obj == self.tree.viewport():
            if event.type() == QEvent.Type.MouseMove:
                pos = event.position().toPoint() if hasattr(event, 'position') else event.pos()
                self._update_tree_cursor(self.tree.indexAt(pos))
            elif event.type() == QEvent.Type.Leave:
                self.tree.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        if hasattr(self, 'tree_container') and obj == self.tree_container:
            if event.type() == QEvent.Type.Resize:
                self._reposition_scroll_top_btn()
        return super().eventFilter(obj, event)

    # ──────────────────────────────────────────────────────────────────────────
    # Filter panel
    # ──────────────────────────────────────────────────────────────────────────

    def _toggle_filters(self):
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
        
        is_visible = self.fp.isVisible()
        target_width = 280 if not is_visible else 0
        
        # Ensure it's ready for animation
        if not is_visible:
            self.fp.setVisible(True)
            self.fp.setMinimumWidth(0)
            self.fp.setMaximumWidth(0)
            
        self.sidebar_anim = QPropertyAnimation(self.fp, b"maximumWidth")
        self.sidebar_anim.setDuration(250)
        self.sidebar_anim.setStartValue(self.fp.width())
        self.sidebar_anim.setEndValue(target_width)
        self.sidebar_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        
        if is_visible:
            self.sidebar_anim.finished.connect(lambda: self.fp.setVisible(False))
            
        self.sidebar_anim.start()
        self.btn_filter.setChecked(not is_visible)

    def _apply_filters(self):
        # 1. Update proxy model so it can format the Status column correctly
        age_secs = self.fp.get_older_than_secs()
        if hasattr(self.fp, 'rb_all') and self.fp.rb_all.isChecked():
            status_filter = None
        elif self.fp.rb_empty.isChecked():
            status_filter = 'Empty'
        else:
            status_filter = 'Inactive'

        date_from_ts, date_to_ts = self.fp.get_date_range_ts()
        self.proxy_model.set_filters(
            empty_only=False,
            date_from_ts=date_from_ts,
            date_to_ts=date_to_ts,
            older_than_secs=age_secs,
            status_filter=status_filter,
        )

        # 2. Fetch the paginated data from SQL
        self.current_page = 0
        if self.content_stack.currentIndex() != 0:
            self._load_page()

    def _apply_default_browse_preset(self, apply_now=True):
        blockers = [
            QSignalBlocker(self.fp.bg),
            QSignalBlocker(self.fp.date_from),
            QSignalBlocker(self.fp.date_to),
            QSignalBlocker(self.fp.slider),
            QSignalBlocker(self.fp.btn_clear_dates),
        ]
        try:
            self.fp.apply_default_browse_preset()
        finally:
            del blockers

        if apply_now:
            self._clear_all_checks()
            self._apply_filters()

    def _reset_filters(self):
        self._apply_default_browse_preset()

    def _toggle_expand(self, checked):
        self._set_expand_state(checked)

    def _update_content_page(self):
        """Switch between tree (page 1) and no-results (page 2) based on current proxy row count.
        Only acts when data is loaded (page 0 = no scan yet is handled separately)."""
        if self.content_stack.currentIndex() == 0:
            return  # still on the welcome page, no scan done yet
        if self._has_visible_rows():
            self.content_stack.setCurrentIndex(1)
        else:
            self.content_stack.setCurrentIndex(2)

    def _has_visible_rows(self):
        if not self.proxy_model:
            return False
        return self.proxy_model.rowCount(QModelIndex()) > 0

    def _set_expand_state(self, expanded):
        if not hasattr(self, 'tree'):
            return

        if expanded:
            if self.proxy_model and self.proxy_model.rowCount() > 0:
                # Pages are capped at 2000 rows so expandAll is safe
                self.tree.expandAll()
        else:
            self.tree.collapseAll()

        if hasattr(self, 'btn_expand'):
            blocker = QSignalBlocker(self.btn_expand)
            self.btn_expand.setChecked(expanded)
            self.btn_expand.setText("⬍  Collapse All" if expanded else "⬍  Expand All")
            self.btn_expand.setEnabled(self._has_visible_rows())
            del blocker

    def _select_all(self):
        if not self.tree_model:
            return
        indices = self._collect_bulk_target_indices()
        if not indices:
            self._refresh_selection_buttons()
            return

        target_state = (
            Qt.CheckState.Unchecked
            if self._are_all_indices_checked(indices)
            else Qt.CheckState.Checked
        )
        if target_state == Qt.CheckState.Checked:
            self._clear_all_checks()
        self._set_indices_checked(indices, target_state)
        self._do_recount()

    def _clear_all_checks(self):
        if not self.tree_model:
            return
        def clear(parent=QModelIndex()):
            for r in range(self.tree_model.rowCount(parent)):
                idx = self.tree_model.index(r, 0, parent)
                if self.tree_model.data(idx, Qt.ItemDataRole.CheckStateRole) != Qt.CheckState.Unchecked:
                    self.tree_model.set_check_state(idx, Qt.CheckState.Unchecked)
                clear(idx)
        clear()
        self._do_recount()

    def _on_filter_changed(self):
        # User manually changed a filter control — clear selections and apply
        self._clear_all_checks()
        self._apply_filters()

    def _collect_bulk_target_indices(self, status=None):
        if not self.tree_model or self.proxy_model.sourceModel() is None:
            return []

        targets = []
        seen_paths = set()
        exact_only = self.proxy_model.has_active_filters() or status is not None
        include_non_empty_directories = not exact_only

        def walk(parent=QModelIndex()):
            for row in range(self.proxy_model.rowCount(parent)):
                proxy_index = self.proxy_model.index(row, 0, parent)
                source_index = self.proxy_model.mapToSource(proxy_index)
                item_data = self.tree_model.data(source_index, Qt.ItemDataRole.UserRole)
                if item_data:
                    path = item_data.get('path')
                    is_exact_match = self.proxy_model.matches_source_index(source_index)
                    is_non_empty_dir = item_data.get('is_dir', False) and self.tree_model.rowCount(source_index) > 0
                    if (
                        path and path not in seen_paths
                        and not item_data.get('_is_context_fetched', False)
                        and (status is None or item_data.get('status') == status)
                        and (not exact_only or is_exact_match)
                        and (include_non_empty_directories or not is_non_empty_dir)
                    ):
                        seen_paths.add(path)
                        targets.append(source_index)
                if self.proxy_model.hasChildren(proxy_index):
                    walk(proxy_index)

        walk()
        return targets

    def _are_all_indices_checked(self, indices):
        return bool(indices) and all(
            self.tree_model.data(index, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
            for index in indices
        )

    def _set_indices_checked(self, indices, state):
        if not indices:
            return
        to_change = [idx for idx in indices if self.tree_model.data(idx, Qt.ItemDataRole.CheckStateRole) != state]
        if to_change:
            self.tree_model.set_indices_check_state(to_change, state, explicit=True)

    def _set_status_filter(self, status):
        if status == 'Inactive' and not self.fp.rb_inactive.isChecked():
            self.fp.rb_inactive.setChecked(True)
            return True
        if status == 'Empty' and not self.fp.rb_empty.isChecked():
            self.fp.rb_empty.setChecked(True)
            return True
        if status is None and hasattr(self.fp, 'rb_all') and not self.fp.rb_all.isChecked():
            self.fp.rb_all.setChecked(True)
            return True
        return False

    def _refresh_selection_buttons(self):
        if not hasattr(self, 'btn_select_all'):
            return

        all_targets = self._collect_bulk_target_indices()
        inactive_targets = self._collect_bulk_target_indices(status='Inactive')
        empty_targets = self._collect_bulk_target_indices(status='Empty')

        self.btn_select_all.setEnabled(bool(all_targets))
        self.btn_select_all.setText(
            "Deselect All" if self._are_all_indices_checked(all_targets) else "Select All"
        )

        self.btn_select_inactive.setEnabled(bool(inactive_targets))
        self.btn_select_inactive.setText(
            "Deselect All Inactive"
            if self._are_all_indices_checked(inactive_targets)
            else "Select All Inactive"
        )

        self.btn_select_empty.setEnabled(bool(empty_targets))
        self.btn_select_empty.setText(
            "Deselect All Empty"
            if self._are_all_indices_checked(empty_targets)
            else "Select All Empty"
        )

    def _collect_checked_source_indices(self):
        if not self.tree_model:
            return []

        checked_indices = []
        stack = [QModelIndex()]
        while stack:
            parent = stack.pop()
            for row in range(self.tree_model.rowCount(parent)):
                index = self.tree_model.index(row, 0, parent)
                if self.tree_model.data(index, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked:
                    checked_indices.append(index)
                if self.tree_model.hasChildren(index):
                    stack.append(index)
        return checked_indices

    def _prune_paths(self, paths):
        pruned_paths = []
        selected_roots = []
        for path in sorted(paths, key=lambda value: (len(os.path.normpath(value)), value.lower())):
            normalized = os.path.normcase(os.path.normpath(path))
            if any(
                normalized == root or normalized.startswith(root + os.sep)
                for root in selected_roots
            ):
                continue
            selected_roots.append(normalized)
            pruned_paths.append(path)
        return pruned_paths

    def _checked_delete_paths(self):
        if not self.tree_model:
            return []

        paths = []
        for index in self._collect_checked_source_indices():
            item_data = self.tree_model.data(index, Qt.ItemDataRole.UserRole)
            is_context_fetched = item_data and item_data.get('_is_context_fetched', False)
            if (
                self.proxy_model.sourceModel() is self.tree_model
                and (self.proxy_model.is_context_only(index) or is_context_fetched)
                and not self.tree_model.is_explicitly_checked(index)
            ):
                continue
            if item_data and item_data.get('path'):
                paths.append(item_data['path'])
        return self._prune_paths(paths)

    def _checked_delete_summary(self):
        """Return delete targets and effective folder/file totals."""
        if not self.tree_model:
            return [], 0, 0

        target_paths = self._checked_delete_paths()
        if not target_paths:
            return [], 0, 0

        # Build path map iteratively
        path_to_index = {}
        stack = [QModelIndex()]
        while stack:
            parent = stack.pop()
            for row in range(self.tree_model.rowCount(parent)):
                index = self.tree_model.index(row, 0, parent)
                item_data = self.tree_model.data(index, Qt.ItemDataRole.UserRole)
                if item_data and item_data.get('path'):
                    path_to_index[item_data['path']] = index
                if self.tree_model.hasChildren(index):
                    stack.append(index)

        def count_subtree_iterative(root_index):
            f_count = d_count = 0
            s = [root_index]
            while s:
                curr = s.pop()
                d = self.tree_model.data(curr, Qt.ItemDataRole.UserRole)
                if not d: continue
                if d.get('is_dir'): d_count += 1
                else: f_count += 1
                for r in range(self.tree_model.rowCount(curr)):
                    s.append(self.tree_model.index(r, 0, curr))
            return d_count, f_count

        total_folders = 0
        total_files = 0
        for path in target_paths:
            idx = path_to_index.get(path)
            if idx and idx.isValid():
                fld, fil = count_subtree_iterative(idx)
                total_folders += fld
                total_files += fil
            else:
                if os.path.isdir(path): total_folders += 1
                else: total_files += 1

        return target_paths, total_folders, total_files

    def _select_by_status(self, status):
        if not self.tree_model:
            return

        if self._set_status_filter(status):
            self._apply_filters()

        indices = self._collect_bulk_target_indices(status=status)
        if not indices:
            self._refresh_selection_buttons()
            return

        target_state = (
            Qt.CheckState.Unchecked
            if self._are_all_indices_checked(indices)
            else Qt.CheckState.Checked
        )
        if target_state == Qt.CheckState.Checked:
            self._clear_all_checks()
        self._set_indices_checked(indices, target_state)
        self._do_recount()

    def _select_inactive(self):
        self._select_by_status('Inactive')

    def _select_empty(self):
        self._select_by_status('Empty')

    # ──────────────────────────────────────────────────────────────────────────
    # Scan
    # ──────────────────────────────────────────────────────────────────────────

    def _reset_view(self):
        """Clear all displayed data and return to the empty/welcome page."""
        # Stop any running scan first
        if self.scanner_thread and self.scanner_thread.isRunning():
            self.scanner_thread.cancel()

        # Clear the tree model
        self.tree_model = None
        self.proxy_model.setSourceModel(None)

        # Reset pagination state
        self.current_page = 0
        self.is_scanning  = False

        # Reset status chips
        self.chip_empty.setText("Empty: 0")
        self.chip_inactive_folders.setText("Inactive folders: 0")
        self.chip_inactive_files.setText("Inactive files: 0")
        self.chip_space.setText("Reclaimable: —")

        # Hide controls, show empty page
        self.controls_bar.setVisible(False)
        self.content_stack.setCurrentIndex(0)
        self.lbl_status.setText("Ready — select a folder and click Re-scan")

    def _browse(self):
        # Use native Windows Explorer dialog
        start_dir = self.txt_path.text().strip() or ""
        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder", start_dir
        )
        if folder:
            # Clear any previous scan results immediately
            self._reset_view()

            # Preserve UNC paths; normpath mangles \\server to \server
            if folder.startswith("//") or folder.startswith("\\\\"):
                self.txt_path.setText(folder.replace("/", "\\"))
            else:
                self.txt_path.setText(os.path.normpath(folder))
            self._apply_default_browse_preset()
            # Automatically start a scan once the user has selected a folder
            self.start_scan()

    def start_scan(self):
        # If currently scanning, this button acts as a Stop button
        if self.scanner_thread and self.scanner_thread.isRunning():
            self.scanner_thread.cancel()
            self.lbl_status.setText("Stopping scan…")
            self.btn_rescan.setEnabled(False)
            return

        path = self.txt_path.text().strip()
        if not path:
            self.lbl_status.setText("Please enter a folder path.")
            return
        
        # Normalize slashes but preserve UNC prefix (\\server\share)
        if path.startswith("\\\\") or path.startswith("//"):
            # UNC path — keep as-is but normalize forward slashes to back
            path = path.replace("/", "\\")
        else:
            path = os.path.normpath(path)

        # Probe the path — use scandir which works reliably for both local and UNC
        try:
            with os.scandir(path):
                pass   # path is accessible
        except PermissionError:
            self.lbl_status.setText(f"Access denied: {path}")
            return
        except Exception:
            self.lbl_status.setText(f"Path not found or not accessible: {path}")
            return
        
        self.lbl_status.setText("Scanning…")
        self.btn_rescan.setText("⏹  Stop")
        self.btn_rescan.setStyleSheet("background-color: #da3633; border-color: #f85149;")

        self.current_page = 0
        self.total_scanned = 0
        self.is_scanning = True

        self.scanner_thread = ScannerThread(path, stale_months=self.fp.get_stale_months_for_scan())
        self.scanner_thread.scan_finished.connect(self._on_scan_done)
        self.scanner_thread.scan_progress.connect(self._on_progress)
        self.scanner_thread.first_batch_ready.connect(self._on_first_batch_ready)
        self.scanner_thread.batch_ready.connect(self._on_batch_ready)
        self.scanner_thread.start()

    def _on_progress(self, path):
        s = ("…" + path[-72:]) if len(path) > 75 else path
        self.lbl_status.setText(f"Scanning: {s}")

    def _on_first_batch_ready(self):
        if self.current_page == 0 and self.content_stack.currentIndex() == 0:
            self._load_page()

    def _on_batch_ready(self):
        """Called during scanning when a new batch of 2,000 items is indexed."""
        # Only refresh if we are currently looking at the tree
        if self.content_stack.currentIndex() == 1:
            self._refresh_pagination_only()

    def _refresh_pagination_only(self):
        """Update pagination buttons/info without reloading the whole tree."""
        from src.file_index_tool import FileIndexTool
        tool = FileIndexTool()
        cursor = tool.conn.cursor()

        # We need the same WHERE clause as _load_page
        where_clauses = []
        params = []
        age_secs = self.fp.get_older_than_secs()
        age_cutoff = (datetime.now().timestamp() - age_secs) if age_secs is not None else None
        
        # Determine status filter (simplified mirror of _load_page logic)
        if hasattr(self.fp, 'rb_all') and self.fp.rb_all.isChecked(): status_filter = None
        elif self.fp.rb_empty.isChecked(): status_filter = 'Empty'
        else: status_filter = 'Inactive'

        if status_filter == 'Inactive':
            if age_cutoff is not None:
                where_clauses.append("modified_time <= ?")
                params.append(age_cutoff)
            else: where_clauses.append("1=1")
        elif status_filter == 'Active':
            if age_cutoff is not None:
                where_clauses.append("modified_time > ?")
                params.append(age_cutoff)
            else: where_clauses.append("1=0")
        
        date_from, date_to = self.fp.get_date_range_ts()
        if date_from:
            where_clauses.append("modified_time >= ?")
            params.append(date_from)
        if date_to:
            where_clauses.append("modified_time <= ?")
            params.append(date_to)
        if status_filter == 'Empty': where_clauses.append("is_folder = 1")

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        cursor.execute("SELECT COUNT(*) FROM file_index" + where_sql, params)
        total_matches = cursor.fetchone()[0]
        tool.close()

        # Update UI controls
        limit = 2000
        offset = self.current_page * limit
        has_multiple_pages = total_matches > limit
        self.btn_prev_page.setVisible(has_multiple_pages)
        self.btn_next_page.setVisible(has_multiple_pages)
        self.lbl_page_info.setVisible(has_multiple_pages)
        
        if has_multiple_pages:
            # Re-fetch rows count on current page is tricky without query, 
            # but we can approximate or just show total_matches
            self.lbl_page_info.setText(f"Showing page {self.current_page+1} of {total_matches}")
            self.btn_prev_page.setEnabled(self.current_page > 0)
            self.btn_next_page.setEnabled(offset + limit < total_matches)
        
        self.lbl_status.setText(f"Scanning... Found {total_matches} matching items")

    def _on_scan_done(self):
        self.btn_rescan.setEnabled(True)
        self.btn_rescan.setText("⟳  Re-scan")
        self.btn_rescan.setStyleSheet("") # reset style
        self.is_scanning = False
        
        if self.scanner_thread and self.scanner_thread.is_cancelled:
            self.lbl_status.setText("Scan stopped by user.")
        else:
            self.lbl_status.setText("Scan complete.")

        if self.content_stack.currentIndex() == 0:
            self._load_page()
            
    def _prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self._load_page()

    def _next_page(self):
        self.current_page += 1
        self._load_page()

    def _load_page(self):
        limit = 2000
        offset = self.current_page * limit
        
        from src.file_index_tool import FileIndexTool
        tool = FileIndexTool()
        cursor = tool.conn.cursor()
        
        # Build SQL query based on active filters
        query = "SELECT path, name, is_folder, size, modified_time, parent_path FROM file_index"
        count_query = "SELECT COUNT(*) FROM file_index"
        
        where_clauses = []
        params = []
        
        age_secs = self.fp.get_older_than_secs()
        age_cutoff = (datetime.now().timestamp() - age_secs) if age_secs is not None else None
        
        if hasattr(self.fp, 'rb_all') and self.fp.rb_all.isChecked():
            status_filter = None
        elif self.fp.rb_empty.isChecked():
            status_filter = 'Empty'
        else:
            status_filter = 'Inactive'
        
        if status_filter == 'Inactive':
            if age_cutoff is not None:
                where_clauses.append("modified_time <= ?")
                params.append(age_cutoff)
            else:
                where_clauses.append("1=1") # Everything is inactive if age limit is 0
        elif status_filter == 'Active':
            if age_cutoff is not None:
                where_clauses.append("modified_time > ?")
                params.append(age_cutoff)
            else:
                where_clauses.append("1=0") # Nothing is active
                
        date_from, date_to = self.fp.get_date_range_ts()
        
        if date_from:
            where_clauses.append("modified_time >= ?")
            params.append(date_from)
        if date_to:
            where_clauses.append("modified_time <= ?")
            params.append(date_to)
            
        if status_filter == 'Empty':
            where_clauses.append("is_folder = 1")
            
        if where_clauses:
            where_sql = " WHERE " + " AND ".join(where_clauses)
            query += where_sql
            count_query += where_sql
            
        # Get total matching rows count
        cursor.execute(count_query, params)
        total_matches = cursor.fetchone()[0]
        
        query += f" ORDER BY path LIMIT {limit} OFFSET {offset}"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        # --- Restore Tree Context by fetching missing parents ---
        fetched_paths = {r[0] for r in rows}
        missing_parents = set()
        
        for r in rows:
            p_path = r[5]
            while p_path and p_path not in fetched_paths and p_path not in missing_parents:
                missing_parents.add(p_path)
                p_path = os.path.dirname(p_path) if '\\' in p_path or '/' in p_path else None
                
        # Build a set of paths that were originally fetched so we can mark others as context
        original_fetched_paths = {r[0] for r in rows}
        
        if missing_parents:
            # Fetch parents in batches to avoid SQLite variable limits
            parents_list = list(missing_parents)
            for i in range(0, len(parents_list), 900):
                batch = parents_list[i:i+900]
                placeholders = ','.join('?' * len(batch))
                cursor.execute(f"SELECT path, name, is_folder, size, modified_time, parent_path FROM file_index WHERE path IN ({placeholders})", batch)
                rows.extend(cursor.fetchall())
        
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
            if status_filter == 'Empty' and is_folder:
                status = 'Empty'
            
            node = {
                'name': name,
                'path': path,
                'is_dir': bool(is_folder),
                'size': size if not is_folder else 0,
                'last_modified': modified_time,
                'status': status,
                'children': [],
                '_parent_path': parent_path,
                '_is_context_fetched': path not in original_fetched_paths
            }
            nodes_by_path[path] = node

        for path, node in nodes_by_path.items():
            parent_path = node.pop('_parent_path', None)
            parent_node = nodes_by_path.get(parent_path)
            if parent_node:
                parent_node['children'].append(node)
            else:
                # If parent isn't in the current page, attach it to root
                root_node['children'].append(node)
            
        if not rows and self.current_page > 0:
            self.current_page -= 1
            return
            
        if not rows and self.current_page == 0:
            if self.is_scanning:
                # Still scanning, just wait
                pass
            else:
                self.content_stack.setCurrentIndex(2) # No results page
                self.controls_bar.setVisible(False)
            return

        # The proxy model now correctly handles contextual filtering and UI status overrides
        self.tree_model = WatchdogTreeModel(root_node)
        self.proxy_model.setSourceModel(self.tree_model)
        self.tree.setModel(self.proxy_model)
        
        # Update pagination controls
        has_multiple_pages = total_matches > limit
        self.btn_prev_page.setVisible(has_multiple_pages)
        self.btn_next_page.setVisible(has_multiple_pages)
        self.lbl_page_info.setVisible(has_multiple_pages)
        
        if has_multiple_pages:
            self.lbl_page_info.setText(f"{offset + 1}-{offset + len(rows)} of {total_matches}")
            self.btn_prev_page.setEnabled(self.current_page > 0)
            self.btn_next_page.setEnabled(offset + len(rows) < total_matches)
        
        self.content_stack.setCurrentIndex(1)  # show tree
        self.controls_bar.setVisible(True)
        
        hdr = self.tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, 7):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            
        self.tree.setColumnWidth(0, 420)
        self.tree.setColumnWidth(1, 74)
        self.tree.setColumnWidth(2, 116)
        self.tree.setColumnWidth(3, 70)
        self.tree.setColumnWidth(4, 96)
        self.tree.setColumnWidth(5, 108)
        self.tree.setColumnWidth(6, 98)
        
        # Expand top level
        self._set_expand_state(True)
        
        self.lbl_status.setText(f"Found {total_matches} matching items")
        self._update_chips_sql()
        
        self.tree_model.dataChanged.connect(self._on_checked)

    def _update_chips_sql(self):
        from src.file_index_tool import FileIndexTool
        tool = FileIndexTool()
        cursor = tool.conn.cursor()
        
        age_secs = self.fp.get_older_than_secs()
        age_cutoff = (datetime.now().timestamp() - age_secs) if age_secs is not None else None
        
        # Inactive files
        if age_cutoff is not None:
            cursor.execute("SELECT COUNT(*), SUM(size) FROM file_index WHERE is_folder = 0 AND modified_time <= ?", (age_cutoff,))
        else:
            cursor.execute("SELECT COUNT(*), SUM(size) FROM file_index WHERE is_folder = 0")
        row = cursor.fetchone()
        inactive_files = row[0] or 0
        reclaim = row[1] or 0
        
        # Inactive folders
        if age_cutoff is not None:
            cursor.execute("SELECT COUNT(*) FROM file_index WHERE is_folder = 1 AND modified_time <= ?", (age_cutoff,))
        else:
            cursor.execute("SELECT COUNT(*) FROM file_index WHERE is_folder = 1")
        inactive_folders = cursor.fetchone()[0] or 0
        
        # Empty
        cursor.execute("SELECT COUNT(*) FROM file_index WHERE is_folder = 1 AND size = 0")
        empty_n = cursor.fetchone()[0] or 0
        
        tool.close()
        
        from .models import format_size
        self.chip_empty.setText(f"Empty: {empty_n}")
        self.chip_inactive_folders.setText(f"Inactive folders: {inactive_folders}")
        self.chip_inactive_files.setText(f"Inactive files: {inactive_files}")
        self.chip_space.setText(f"Reclaimable: {format_size(reclaim)}")

        try:
            self.btn_select_inactive.setEnabled((inactive_folders + inactive_files) > 0)
            self.btn_select_empty.setEnabled(empty_n > 0)
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────────────
    # Selection count (debounced)
    # ──────────────────────────────────────────────────────────────────────────

    def _on_checked(self, tl=None, br=None, roles=None):
        if roles is None or Qt.ItemDataRole.CheckStateRole in roles:
            self.recount_timer.start(80)

    def _do_recount(self):
        if not self.tree_model:
            self.btn_delete.setText("Delete Selected")
            self.btn_delete.setEnabled(False)
            self._refresh_selection_buttons()
            return
        paths, folder_count, file_count = self._checked_delete_summary()
        total = len(paths)
        if total:
            self.btn_delete.setText(f"🗑  Delete Selected (Folders: {folder_count}, Files: {file_count})  [Current Page]")
        else:
            self.btn_delete.setText("Delete Selected")
        self.btn_delete.setEnabled(total > 0)
        self._refresh_selection_buttons()
        self._update_chips_sql()

    # ──────────────────────────────────────────────────────────────────────────
    # Tree interactions
    # ──────────────────────────────────────────────────────────────────────────

    def _item_data(self, proxy_index):
        if not self.tree_model:
            return None
        src = self.proxy_model.mapToSource(proxy_index)
        return self.tree_model.data(src, Qt.ItemDataRole.UserRole)

    def _is_tree_index_clickable(self, index):
        if not index.isValid():
            return False
        if index.column() == 0:
            return True
        if index.column() == 6:
            action_text = self.proxy_model.data(index, Qt.ItemDataRole.DisplayRole)
            return not (action_text and 'queued' in str(action_text).lower())
        return False

    def _update_tree_cursor(self, index):
        cursor = (
            Qt.CursorShape.PointingHandCursor
            if self._is_tree_index_clickable(index)
            else Qt.CursorShape.ArrowCursor
        )
        self.tree.viewport().setCursor(cursor)

    def _on_click(self, index):
        if index.column() == 6 and self._is_tree_index_clickable(index):
            d = self._item_data(self.proxy_model.index(index.row(), 0, index.parent()))
            if d:
                self._open(d['path'], d.get('is_dir', True))

    def _on_double_click(self, index):
        if not self._is_tree_index_clickable(index):
            return
        d = self._item_data(index)
        if d:
            self._open(d['path'], d.get('is_dir', True))

    def _context_menu(self, pos):
        idx = self.tree.indexAt(pos)
        if not idx.isValid():
            return
        d = self._item_data(idx)
        if not d:
            return
        menu = QMenu()
        menu.setStyleSheet("""
            QMenu { background:#1e293b; color:#c9d1d9; border:1px solid #334155;
                    border-radius:6px; padding:4px; }
            QMenu::item { padding:6px 16px; border-radius:4px; }
            QMenu::item:selected { background:#3b82f6; }
            QMenu::separator { background:#334155; height:1px; margin:4px 0; }
        """)
        a_open   = menu.addAction("📂  Open Location")
        a_copy   = menu.addAction("📋  Copy Path")
        menu.addSeparator()
        a_delete = menu.addAction("🗑  Delete  (Recycle Bin)")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen == a_open:
            self._open(d['path'], d.get('is_dir', True))
        elif chosen == a_copy:
            QApplication.clipboard().setText(d['path'])
        elif chosen == a_delete:
            self._delete_one(d['path'])

    def _open(self, path, is_dir=True):
        if not os.path.exists(path):
            return
        path = os.path.normpath(path)
        try:
            if is_dir:
                os.startfile(path)
            else:
                subprocess.Popen(f'explorer /select,"{path}"')
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────────────
    # Delete
    # ──────────────────────────────────────────────────────────────────────────

    def _remove_path_from_db(self, path):
        from src.file_index_tool import FileIndexTool
        tool = FileIndexTool()
        tool.conn.execute("DELETE FROM file_index WHERE path = ?", (path,))
        tool.conn.execute("DELETE FROM file_index WHERE path LIKE ?", (path + '\\%',))
        tool.conn.commit()
        tool.close()

    def _delete_one(self, path):
        r = QMessageBox.question(self, "Confirm Delete",
            f"Send to Recycle Bin?\n\n{path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if r == QMessageBox.StandardButton.Yes:
            try:
                send2trash.send2trash(path)
                self._remove_path_from_db(path)
                self._load_page()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _delete_selected(self):
        if not self.tree_model:
            return
        paths, folder_count, file_count = self._checked_delete_summary()
        if not paths:
            QMessageBox.information(self, "Delete", "No items selected.")
            return
        r = QMessageBox.question(self, "Confirm Delete",
            (
                "Send selected items to the Recycle Bin?\n\n"
                f"Folders: {folder_count}\n"
                f"Files: {file_count}"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if r == QMessageBox.StandardButton.Yes:
            errors = []
            for p in paths:
                try:
                    send2trash.send2trash(p)
                    self._remove_path_from_db(p)
                except Exception as e:
                    errors.append(str(e))
                    print(f"Error: {e}")
            self._load_page()

    # ──────────────────────────────────────────────────────────────────────────
    # Export CSV
    # ──────────────────────────────────────────────────────────────────────────

    def _export_csv(self):
        if not self.proxy_model or not self.proxy_model.sourceModel():
            QMessageBox.information(self, "Export", "Nothing to export — run a scan first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Report", "", "CSV Files (*.csv)")
        if not path:
            return
        # Columns to exclude from export: 5 = Status, 6 = Action
        _SKIP_COLS = {5, 6}
        try:
            with open(path, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow([self.proxy_model.headerData(i, Qt.Orientation.Horizontal)
                            for i in range(self.proxy_model.columnCount())
                            if i not in _SKIP_COLS])
                def write_rows(parent):
                    for r in range(self.proxy_model.rowCount(parent)):
                        row_data = [self.proxy_model.data(self.proxy_model.index(r, c, parent))
                                    for c in range(self.proxy_model.columnCount())
                                    if c not in _SKIP_COLS]
                        w.writerow(row_data)
                        child = self.proxy_model.index(r, 0, parent)
                        if self.proxy_model.hasChildren(child):
                            write_rows(child)
                write_rows(QModelIndex())
            QMessageBox.information(self, "Exported", "CSV report saved.")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))
