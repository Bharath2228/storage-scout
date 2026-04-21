import os
import csv
import subprocess
import send2trash
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QRadioButton, QSlider, QDateEdit, QTreeView, QHeaderView,
    QMessageBox, QStyledItemDelegate, QButtonGroup, QApplication, QFileDialog,
    QMenu, QSizePolicy, QFrame
)
from PyQt6.QtCore import Qt, QDate, QRect, QModelIndex, QTimer, QEvent
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush

from .models import WatchdogTreeModel, WatchdogFilterProxyModel
from .scanner import ScannerThread


# ────────────────────────────────────────────────────────────────────────────
# Delegates
# ────────────────────────────────────────────────────────────────────────────

class StatusDelegate(QStyledItemDelegate):
    _COLORS = {
        'Empty':    QColor(248, 81, 73),
        'Inactive': QColor(210, 153, 34),
        'Active':   QColor(46, 160, 67),
    }

    def paint(self, painter, option, index):
        status = index.data(Qt.ItemDataRole.DisplayRole)
        if not status:
            return
        color = self._COLORS.get(status, QColor(100, 100, 100))
        rect = option.rect
        pill = QRect(rect.left() + (rect.width() - 64) // 2,
                     rect.top() + (rect.height() - 22) // 2, 64, 22)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 38)))
        painter.setPen(QPen(color, 1))
        painter.drawRoundedRect(pill, 11, 11)
        f = painter.font()
        f.setBold(True)
        f.setPointSize(9)
        painter.setFont(f)
        painter.setPen(color)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, status)
        painter.restore()


class ActionDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return
        rect = option.rect
        btn = QRect(rect.left() + (rect.width() - 72) // 2,
                    rect.top() + (rect.height() - 24) // 2, 72, 24)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if "queued" in text:
            painter.setPen(QPen(QColor(248, 81, 73)))
            painter.drawText(btn, Qt.AlignmentFlag.AlignCenter, "✕ queued")
        else:
            painter.setPen(QPen(QColor(88, 166, 255)))
            painter.drawRoundedRect(btn, 12, 12)
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
            if self.date() == self.minimumDate():
                current_date = QDate.currentDate()
                self.calendarWidget().setCurrentPage(current_date.year(), current_date.month())
        return super().eventFilter(obj, event)

class FilterPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("filterPanel")
        self.setVisible(False)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 16)
        outer.setSpacing(24)

        # ── Section 1: Display Mode ──────────────────────────────────────────
        sec1 = self._make_section("DISPLAY MODE")
        self.rb_all      = QRadioButton("All")
        self.rb_active   = QRadioButton("Active only")
        self.rb_inactive = QRadioButton("Inactive only")
        self.rb_empty    = QRadioButton("Empty only")
        self.rb_all.setChecked(True)
        self.bg = QButtonGroup()
        for rb in [self.rb_all, self.rb_active, self.rb_inactive, self.rb_empty]:
            self.bg.addButton(rb)
            sec1.addWidget(rb)
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
        sec1.addStretch()
        outer.addLayout(sec1)

        outer.addWidget(self._vline())

        # ── Section 2: Date Range ────────────────────────────────────────────
        sec2 = self._make_section("DATE RANGE")
        self.date_from = AnyDateEdit()
        self.date_from.setDisplayFormat("dd/MM/yyyy")
        self.date_from.setSpecialValueText("Any")
        self.date_from.setDate(self.date_from.minimumDate())
        # Make date widgets wider to improve spacing
        self.date_from.setMinimumWidth(150)

        self.date_to = AnyDateEdit()
        self.date_to.setDisplayFormat("dd/MM/yyyy")
        self.date_to.setSpecialValueText("Any")
        self.date_to.setDate(self.date_to.minimumDate())
        self.date_to.setMinimumWidth(150)
        self.date_from.setCursor(Qt.CursorShape.PointingHandCursor)
        self.date_to.setCursor(Qt.CursorShape.PointingHandCursor)

        for lbl_text, widget in [("From", self.date_from), ("To", self.date_to)]:
            row = QHBoxLayout()
            row.setSpacing(12)
            l = QLabel(lbl_text)
            l.setFixedWidth(40)
            l.setObjectName("mutedLabel")
            row.addWidget(l)
            row.addWidget(widget)
            sec2.addLayout(row)

        btn_clear_dates = QPushButton("Clear")
        btn_clear_dates.setObjectName("linkBtn")
        btn_clear_dates.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clear_dates.clicked.connect(self._clear_dates)
        sec2.addWidget(btn_clear_dates)
        sec2.addStretch()
        outer.addLayout(sec2)

        outer.addWidget(self._vline())

        # ── Section 3: Stale Threshold ───────────────────────────────────────
        sec3 = self._make_section("AGE THRESHOLD")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(1, 12)
        self.slider.setValue(3)
        self.slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider.setTickInterval(1)
        self.slider.setMinimumWidth(180)
        self.slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.lbl_val = QLabel("Older than 3 months")
        self.lbl_val.setObjectName("sliderLabel")
        self.lbl_val.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        def _on_slider(v):
            self.lbl_val.setText(f"Older than {v} months")

        self.slider.valueChanged.connect(_on_slider)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        sec3.addWidget(self.lbl_val)
        sec3.addWidget(self.slider)
        sec3.addStretch()
        outer.addLayout(sec3)

        outer.addStretch()

        # ── Section 4: Actions ───────────────────────────────────────────────
        sec4 = QVBoxLayout()
        sec4.setSpacing(8)
        sec4.addStretch()
        self.btn_apply = QPushButton("Apply")
        self.btn_apply.setObjectName("applyBtn")
        self.btn_apply.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset = QPushButton("Reset")
        self.btn_reset.setObjectName("resetBtn")
        self.btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        sec4.addWidget(self.btn_apply)
        sec4.addWidget(self.btn_reset)
        outer.addLayout(sec4)

    def _clear_dates(self):
        """Reset both date pickers back to 'Any'."""
        self.date_from.setDate(self.date_from.minimumDate())
        self.date_to.setDate(self.date_to.minimumDate())

    def get_date_from_ts(self):
        """Returns timestamp or None if set to 'Any'."""
        d = self.date_from.date()
        if d == self.date_from.minimumDate():
            return None
        return int(d.startOfDay().toSecsSinceEpoch())

    def get_date_to_ts(self):
        """Returns timestamp or None if set to 'Any'."""
        d = self.date_to.date()
        if d == self.date_to.minimumDate():
            return None
        return int(d.endOfDay().toSecsSinceEpoch())

    def get_older_than_secs(self):
        """Returns seconds threshold or None if slider is at minimum (show all)."""
        v = self.slider.value()
        if v == 0:   # 0 = minimum = no filter
            return None
        return v * 30 * 24 * 3600  # months → seconds (approximate)

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
        self._current_theme = "light"

        self.recount_timer = QTimer(self)
        self.recount_timer.setSingleShot(True)
        self.recount_timer.timeout.connect(self._do_recount)

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
        tb.setContentsMargins(14, 8, 14, 8)
        tb.setSpacing(8)

        lbl = QLabel("IBMS Watchdog")
        lbl.setObjectName("appTitle")
        tb.addWidget(lbl)
        tb.addWidget(self._vbar())

        self.txt_path = QLineEdit()
        self.txt_path.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.txt_path.setMinimumWidth(240)
        self.txt_path.setMaximumWidth(520)
        self.txt_path.setPlaceholderText("Enter or browse a folder path…")
        btn_browse = QPushButton("Browse")
        btn_browse.setToolTip("Browse folder")
        btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_browse.clicked.connect(self._browse)
        tb.addWidget(self.txt_path)
        tb.addWidget(btn_browse)

        self.btn_rescan = QPushButton("Re-scan")
        self.btn_rescan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_rescan.clicked.connect(self.start_scan)
        tb.addWidget(self.btn_rescan)

        tb.addWidget(self._vbar())

        self.btn_filter = QPushButton("Filters")
        self.btn_filter.setObjectName("filterBtn")
        self.btn_filter.setCheckable(True)
        self.btn_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_filter.clicked.connect(self._toggle_filters)
        tb.addWidget(self.btn_filter)

        self.btn_expand = QPushButton("Expand All")
        self.btn_expand.setCheckable(True)
        self.btn_expand.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_expand.clicked.connect(self._toggle_expand)

        btn_export = QPushButton("Export CSV")
        btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_export.clicked.connect(self._export_csv)
        tb.addWidget(btn_export)

        self.btn_delete = QPushButton("Delete Selected")
        self.btn_delete.setObjectName("deleteBtn")
        self.btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_delete.clicked.connect(self._delete_selected)
        tb.addWidget(self.btn_delete)

        tb.addStretch()

        # Round theme toggle button — top-right corner
        self.btn_theme = QPushButton()
        self.btn_theme.setObjectName("themeBtn")
        self.btn_theme.setFixedSize(30, 30)
        self.btn_theme.setToolTip("Toggle light / dark theme")
        self.btn_theme.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_theme.clicked.connect(self._toggle_theme)
        self._update_theme_icon()
        tb.addWidget(self.btn_theme)

        vbox.addWidget(topbar)

        # ── Filter panel (instant show/hide) ──────────────────────────────────
        self.fp = FilterPanel()
        self.fp.btn_apply.clicked.connect(self._apply_filters)
        self.fp.btn_reset.clicked.connect(self._reset_filters)
        vbox.addWidget(self.fp)

        # ── Controls row (above tree): expand / select all) ────────────────
        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(12, 6, 12, 6)
        controls_layout.setSpacing(8)
        controls_layout.addWidget(self.btn_expand)
        self.btn_select_all = QPushButton("Select All")
        self.btn_select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select_all.clicked.connect(self._select_all)
        controls_layout.addWidget(self.btn_select_all)
        controls_layout.addStretch()
        vbox.addWidget(controls)

        # ── Tree ──────────────────────────────────────────────────────────────
        self.tree = QTreeView()
        self.tree.setAlternatingRowColors(True)
        # Disable sorting — columns should not be sortable by the user
        self.tree.setSortingEnabled(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(False)
        self.tree.setItemDelegateForColumn(5, StatusDelegate(self.tree))
        self.tree.setItemDelegateForColumn(6, ActionDelegate(self.tree))
        self.tree.clicked.connect(self._on_click)
        self.tree.doubleClicked.connect(self._on_double_click)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self.tree.header().setCursor(Qt.CursorShape.PointingHandCursor)
        # Prevent the user from rearranging columns and keep layout stable
        hdr = self.tree.header()
        hdr.setSectionsMovable(False)
        hdr.setStretchLastSection(False)
        # Make headers non-clickable and hide sort indicator so sorting can't be triggered
        hdr.setSectionsClickable(False)
        try:
            hdr.setSortIndicatorShown(False)
        except Exception:
            pass
        # Center header labels for all columns
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(self.tree, 1)

        # ── Status bar ────────────────────────────────────────────────────────
        sb = QWidget()
        sb.setObjectName("statusbar")
        sb.setFixedHeight(30)
        sbl = QHBoxLayout(sb)
        sbl.setContentsMargins(12, 0, 12, 0)
        sbl.setSpacing(12)
        self.lbl_status = QLabel("Ready — select a folder and click Re-scan")
        self.lbl_status.setObjectName("statusLabel")
        sbl.addWidget(self.lbl_status)
        sbl.addStretch()
        self.chip_empty    = self._chip("Empty: 0",        "chipEmpty")
        self.chip_inactive = self._chip("Inactive: 0",     "chipInactive")
        self.chip_space    = self._chip("Reclaimable: —",  "chipSpace")
        for c in [self.chip_empty, self.chip_inactive, self.chip_space]:
            sbl.addWidget(c)
        vbox.addWidget(sb)

        # Do NOT auto-start scan — let the user enter a path first

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

    def _update_theme_icon(self):
        # Use plain text symbols — no emoji
        self.btn_theme.setText("\u2600" if self._current_theme == "dark" else "\u263D")

    def _toggle_theme(self):
        from src.theme import apply_theme
        self._current_theme = "light" if self._current_theme == "dark" else "dark"
        apply_theme(QApplication.instance(), self._current_theme)
        self._update_theme_icon()

    # ──────────────────────────────────────────────────────────────────────────
    # Filter panel
    # ──────────────────────────────────────────────────────────────────────────

    def _toggle_filters(self):
        visible = not self.fp.isVisible()
        self.fp.setVisible(visible)
        self.btn_filter.setChecked(visible)

    def _apply_filters(self):
        # Determine status filter from radio buttons
        if self.fp.rb_empty.isChecked():
            status_filter = 'Empty'
        elif self.fp.rb_active.isChecked():
            status_filter = 'Active'
        elif self.fp.rb_inactive.isChecked():
            status_filter = 'Inactive'
        else:
            status_filter = None   # All

        self.proxy_model.set_filters(
            empty_only=False,       # now handled by status_filter
            date_from_ts=self.fp.get_date_from_ts(),
            date_to_ts=self.fp.get_date_to_ts(),
            older_than_secs=self.fp.get_older_than_secs(),
            status_filter=status_filter,
        )
        # Do not close panel automatically; wait for the user to toggle the filter button.

    def _reset_filters(self):
        self.fp.rb_all.setChecked(True)
        self.fp._clear_dates()
        self.fp.slider.setValue(3)   # default to 3 months
        self._apply_filters()

    def _toggle_expand(self, checked):
        if checked:
            self.tree.expandAll()
            self.btn_expand.setText("⬍  Collapse All")
        else:
            self.tree.collapseAll()
            self.btn_expand.setText("⬍  Expand All")

    def _select_all(self):
        if not self.tree_model:
            return
        # Determine if we should check or uncheck: if any item unchecked -> check all
        any_unchecked = False
        def find_unchecked(parent=QModelIndex()):
            nonlocal any_unchecked
            for r in range(self.tree_model.rowCount(parent)):
                idx = self.tree_model.index(r, 0, parent)
                if self.tree_model.data(idx, Qt.ItemDataRole.CheckStateRole) != Qt.CheckState.Checked:
                    any_unchecked = True
                    return
                find_unchecked(idx)
        find_unchecked()

        target_state = Qt.CheckState.Checked if any_unchecked else Qt.CheckState.Unchecked

        def set_all(parent=QModelIndex()):
            for r in range(self.tree_model.rowCount(parent)):
                idx = self.tree_model.index(r, 0, parent)
                self.tree_model.set_check_state(idx, target_state)
                set_all(idx)
        set_all()
        # Update select button text and recount
        self.btn_select_all.setText("Unselect All" if target_state == Qt.CheckState.Checked else "Select All")
        self._do_recount()

    # ──────────────────────────────────────────────────────────────────────────
    # Scan
    # ──────────────────────────────────────────────────────────────────────────

    def _browse(self):
        # Use native Windows Explorer dialog
        start_dir = self.txt_path.text().strip() or ""
        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder", start_dir
        )
        if folder:
            # Preserve UNC paths; normpath mangles \\server to \server
            if folder.startswith("//") or folder.startswith("\\\\"):
                self.txt_path.setText(folder.replace("/", "\\"))
            else:
                self.txt_path.setText(os.path.normpath(folder))
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
        self.btn_rescan.setStyleSheet("background-color: #da3633; border-color: #f85149;") # temporary stop style
        
        self.scanner_thread = ScannerThread(path, stale_months=self.fp.slider.value())
        self.scanner_thread.scan_finished.connect(self._on_scan_done)
        self.scanner_thread.scan_progress.connect(self._on_progress)
        self.scanner_thread.start()

    def _on_progress(self, path):
        s = ("…" + path[-72:]) if len(path) > 75 else path
        self.lbl_status.setText(f"Scanning: {s}")

    def _on_scan_done(self, root_node):
        self.btn_rescan.setEnabled(True)
        self.btn_rescan.setText("⟳  Re-scan")
        self.btn_rescan.setStyleSheet("") # reset style
        
        if not root_node:
            if self.scanner_thread and self.scanner_thread.is_cancelled:
                self.lbl_status.setText("Scan stopped by user.")
            else:
                self.lbl_status.setText("Scan failed or folder is empty.")
            return
        self.tree_model = WatchdogTreeModel(root_node)
        self.proxy_model.setSourceModel(self.tree_model)
        self.tree.setModel(self.proxy_model)
        hdr = self.tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, 7):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        # Set sensible initial widths; column 0 stretches but give it an initial baseline
        self.tree.setColumnWidth(0, 420)
        self.tree.setColumnWidth(1, 80)
        self.tree.setColumnWidth(2, 120)
        self.tree.setColumnWidth(3, 80)
        self.tree.setColumnWidth(4, 120)
        self.tree.setColumnWidth(5, 100)
        self.tree.setColumnWidth(6, 120)
        # Apply default filter (show all)
        self._apply_filters()
        # Expand if there is anything to expand; otherwise keep collapsed
        def _has_children(node):
            for c in node.get('children', []):
                # If any child has its own children, or is a directory, consider expandable
                if c.get('children'):
                    return True
                if c.get('is_dir'):
                    # directory without children still shows as expandable in tree
                    return True
            return False

        if _has_children(root_node):
            self.btn_expand.setChecked(True)
            self.tree.expandAll()
            self.btn_expand.setText("⬍  Collapse All")
        else:
            self.btn_expand.setChecked(False)
            self.tree.collapseAll()
            self.btn_expand.setText("⬍  Expand All")
        n = len(root_node.get('children', []))
        self.lbl_status.setText(f"Scan complete — {n} top-level items")
        self._update_chips(root_node)
        self.tree_model.dataChanged.connect(self._on_checked)

    def _update_chips(self, root_node):
        empty_n = inactive_n = reclaim = 0
        def walk(n):
            nonlocal empty_n, inactive_n, reclaim
            s = n.get('status')
            if s == 'Empty': empty_n += 1
            if s == 'Inactive' and n.get('is_dir'): inactive_n += 1
            if s in ('Empty', 'Inactive') and not n.get('is_dir'):
                reclaim += n.get('size', 0)
            for c in n.get('children', []):
                walk(c)
        walk(root_node)
        from .models import format_size
        self.chip_empty.setText(f"Empty: {empty_n}")
        self.chip_inactive.setText(f"Inactive: {inactive_n}")
        self.chip_space.setText(f"Reclaimable: {format_size(reclaim)}")

    # ──────────────────────────────────────────────────────────────────────────
    # Selection count (debounced)
    # ──────────────────────────────────────────────────────────────────────────

    def _on_checked(self, tl, br, roles):
        if Qt.ItemDataRole.CheckStateRole in roles:
            self.recount_timer.start(80)

    def _do_recount(self):
        if not self.tree_model:
            return
        n = 0
        def walk(parent):
            nonlocal n
            for r in range(self.tree_model.rowCount(parent)):
                idx = self.tree_model.index(r, 0, parent)
                if self.tree_model.data(idx, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked:
                    n += 1
                walk(idx)
        walk(QModelIndex())
        self.btn_delete.setText(f"Delete Selected ({n})" if n else "Delete Selected")

    # ──────────────────────────────────────────────────────────────────────────
    # Tree interactions
    # ──────────────────────────────────────────────────────────────────────────

    def _item_data(self, proxy_index):
        if not self.tree_model:
            return None
        src = self.proxy_model.mapToSource(proxy_index)
        return self.tree_model.data(src, Qt.ItemDataRole.UserRole)

    def _on_click(self, index):
        if index.column() == 6:
            # If the action column displays a queued state, do not open
            action_text = self.proxy_model.data(index, Qt.ItemDataRole.DisplayRole)
            if action_text and 'queued' in str(action_text).lower():
                return
            d = self._item_data(self.proxy_model.index(index.row(), 0, index.parent()))
            if d:
                self._open(d['path'], d.get('is_dir', True))

    def _on_double_click(self, index):
        # Prevent opening on double-click if the action column shows queued
        action_idx = self.proxy_model.index(index.row(), 6, index.parent())
        action_text = self.proxy_model.data(action_idx, Qt.ItemDataRole.DisplayRole)
        if action_text and 'queued' in str(action_text).lower():
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

    def _delete_one(self, path):
        r = QMessageBox.question(self, "Confirm Delete",
            f"Send to Recycle Bin?\n\n{path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if r == QMessageBox.StandardButton.Yes:
            try:
                send2trash.send2trash(path)
                self.start_scan()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _delete_selected(self):
        if not self.tree_model:
            return
        paths = []
        def collect(parent):
            for r in range(self.tree_model.rowCount(parent)):
                idx = self.tree_model.index(r, 0, parent)
                if self.tree_model.data(idx, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked:
                    d = self.tree_model.data(idx, Qt.ItemDataRole.UserRole)
                    if d:
                        paths.append(d['path'])
                collect(idx)
        collect(QModelIndex())
        if not paths:
            QMessageBox.information(self, "Delete", "No items selected.")
            return
        r = QMessageBox.question(self, "Confirm Delete",
            f"Send {len(paths)} item(s) to the Recycle Bin?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if r == QMessageBox.StandardButton.Yes:
            for p in paths:
                try:
                    send2trash.send2trash(p)
                except Exception as e:
                    print(f"Error: {e}")
            self.start_scan()

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
