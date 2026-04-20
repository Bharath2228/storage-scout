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
from PyQt6.QtCore import Qt, QDate, QRect, QModelIndex, QTimer
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

class FilterPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("filterPanel")
        self.setVisible(False)          # instant show/hide — no animation lag

        outer = QHBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 14)
        outer.setSpacing(0)

        # ── Section 1: Display Mode ──────────────────────────────────────────
        sec1 = self._make_section("DISPLAY MODE")
        self.rb_all      = QRadioButton("All folders && files")
        self.rb_empty    = QRadioButton("Empty only")
        self.rb_active   = QRadioButton("Active only")
        self.rb_inactive = QRadioButton("Inactive only")
        self.rb_all.setChecked(True)
        self.bg = QButtonGroup()
        self.bg.addButton(self.rb_all)
        self.bg.addButton(self.rb_empty)
        self.bg.addButton(self.rb_active)
        self.bg.addButton(self.rb_inactive)
        sec1.addWidget(self.rb_all)
        sec1.addWidget(self.rb_active)
        sec1.addWidget(self.rb_inactive)
        sec1.addWidget(self.rb_empty)
        sec1.addStretch()
        outer.addLayout(sec1, 2)

        outer.addWidget(self._vline())

        # ── Section 2: Date Range ────────────────────────────────────────────
        sec2 = self._make_section("DATE RANGE  (last modified between)")
        self._date_from_active = False
        self._date_to_active   = False

        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDisplayFormat("dd/MM/yyyy")
        self.date_from.setSpecialValueText("Any")
        self.date_from.setDate(self.date_from.minimumDate())   # shows "Any"

        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDisplayFormat("dd/MM/yyyy")
        self.date_to.setSpecialValueText("Any")
        self.date_to.setDate(self.date_to.minimumDate())       # shows "Any"

        for lbl_text, widget in [("From:", self.date_from), ("To:", self.date_to)]:
            row = QHBoxLayout()
            row.setSpacing(8)
            l = QLabel(lbl_text)
            l.setFixedWidth(34)
            l.setStyleSheet("color:#8b949e;")
            row.addWidget(l)
            row.addWidget(widget)
            sec2.addLayout(row)
        
        # Clear/reset button for date range
        btn_clear_dates = QPushButton("Clear dates")
        btn_clear_dates.setStyleSheet(
            "background:transparent; color:#8b949e; border:none; "
            "text-decoration:underline; font-size:11px; padding:0;"
        )
        btn_clear_dates.clicked.connect(self._clear_dates)
        sec2.addWidget(btn_clear_dates)
        sec2.addStretch()
        outer.addLayout(sec2, 3)

        outer.addWidget(self._vline())

        # ── Section 3: Stale Threshold ───────────────────────────────────────
        sec3 = self._make_section("STALE AGE THRESHOLD  (for next Re-scan)")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(3, 24)
        self.slider.setValue(6)
        self.slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.lbl_val = QLabel("Show all ages")
        self.lbl_val.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_val.setStyleSheet(
            "color:#58a6ff; font-weight:bold; font-size:13px; padding:0 6px;"
        )

        def _on_slider(v):
            if v <= 3:
                self.lbl_val.setText("Show all ages")
            else:
                self.lbl_val.setText(f"Older than {v} months  (hides recent {v}mo)")

        self.slider.valueChanged.connect(_on_slider)

        val_row = QHBoxLayout()
        val_row.addWidget(self.lbl_val)
        val_row.addStretch()
        sec3.addLayout(val_row)
        sec3.addWidget(self.slider)

        # Tick marks
        tick_row = QHBoxLayout()
        tick_row.setContentsMargins(0, 2, 0, 0)
        tick_row.setSpacing(0)
        for t in ["3mo", "6mo", "12mo", "18mo", "24mo"]:
            tl = QLabel(t)
            tl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tl.setStyleSheet("color:#8b949e; font-size:11px;")
            tick_row.addWidget(tl)
        sec3.addLayout(tick_row)
        sec3.addStretch()
        outer.addLayout(sec3, 4)

        outer.addWidget(self._vline())

        # ── Section 4: Actions ───────────────────────────────────────────────
        sec4 = QVBoxLayout()
        sec4.setSpacing(8)
        sec4.addStretch()
        self.btn_apply = QPushButton("✓  Apply Filters")
        self.btn_apply.setObjectName("primaryBtn")
        self.btn_reset = QPushButton("⟲  Reset")
        sec4.addWidget(self.btn_apply)
        sec4.addWidget(self.btn_reset)
        outer.addLayout(sec4, 2)

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
        if v <= 3:   # 3 = minimum = no filter
            return None
        return v * 30 * 24 * 3600  # months → seconds (approximate)

    # helpers
    def _make_section(self, title):
        col = QVBoxLayout()
        col.setContentsMargins(12, 0, 12, 0)
        col.setSpacing(8)
        lbl = QLabel(title)
        lbl.setStyleSheet("color:#8b949e; font-size:10px; font-weight:bold; letter-spacing:0.5px;")
        col.addWidget(lbl)
        return col

    def _vline(self):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFixedWidth(1)
        line.setStyleSheet("color:#30363d;")
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
        topbar.setFixedHeight(54)
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(14, 0, 14, 0)
        tb.setSpacing(8)

        lbl = QLabel("📁  IBMS Watchdog")
        lbl.setStyleSheet("font-weight:bold; font-size:15px; color:#58a6ff; padding-right:8px;")
        tb.addWidget(lbl)
        tb.addWidget(self._vbar())

        self.txt_path = QLineEdit(r"\\srv01\exchange")
        self.txt_path.setFixedWidth(360)
        self.txt_path.setPlaceholderText("Enter or browse folder path…")
        btn_browse = QPushButton("…")
        btn_browse.setFixedWidth(32)
        btn_browse.setToolTip("Browse folder")
        btn_browse.clicked.connect(self._browse)
        tb.addWidget(self.txt_path)
        tb.addWidget(btn_browse)

        self.btn_rescan = QPushButton("⟳  Re-scan")
        self.btn_rescan.setObjectName("primaryBtn")
        self.btn_rescan.clicked.connect(self.start_scan)
        tb.addWidget(self.btn_rescan)

        tb.addStretch()

        # summary chips
        self.chip_empty = self._chip("Empty: 0",    "#f85149", "#2d1313")
        self.chip_inactive = self._chip("Inactive: 0", "#e3b341", "#2a2200")
        self.chip_space = self._chip("Reclaimable: —", "#3fb950", "#0d2a13")
        for c in [self.chip_empty, self.chip_inactive, self.chip_space]:
            tb.addWidget(c)

        tb.addSpacing(8)

        self.btn_filter = QPushButton("⚙  Filters")
        self.btn_filter.setObjectName("filterBtn")
        self.btn_filter.setCheckable(True)
        self.btn_filter.clicked.connect(self._toggle_filters)
        tb.addWidget(self.btn_filter)

        btn_export = QPushButton("↧  Export CSV")
        btn_export.clicked.connect(self._export_csv)
        tb.addWidget(btn_export)

        self.btn_delete = QPushButton("Delete Selected")
        self.btn_delete.setObjectName("deleteBtn")
        self.btn_delete.clicked.connect(self._delete_selected)
        tb.addWidget(self.btn_delete)

        vbox.addWidget(topbar)

        # ── Filter panel (instant show/hide) ──────────────────────────────────
        self.fp = FilterPanel()
        self.fp.btn_apply.clicked.connect(self._apply_filters)
        self.fp.btn_reset.clicked.connect(self._reset_filters)
        vbox.addWidget(self.fp)

        # ── Tree ──────────────────────────────────────────────────────────────
        self.tree = QTreeView()
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(False)          # prevents slow expand animation
        self.tree.setItemDelegateForColumn(5, StatusDelegate(self.tree))
        self.tree.setItemDelegateForColumn(6, ActionDelegate(self.tree))
        self.tree.clicked.connect(self._on_click)
        self.tree.doubleClicked.connect(self._on_double_click)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        vbox.addWidget(self.tree, 1)

        # ── Status bar ────────────────────────────────────────────────────────
        sb = QWidget()
        sb.setObjectName("statusbar")
        sb.setFixedHeight(28)
        sbl = QHBoxLayout(sb)
        sbl.setContentsMargins(12, 0, 12, 0)
        self.lbl_status = QLabel("Ready — select a folder and click Re-scan")
        self.lbl_status.setStyleSheet("color:#8b949e; font-size:12px;")
        sbl.addWidget(self.lbl_status)
        sbl.addStretch()
        for color, text in [("#f85149","● Empty"), ("#e3b341","● Inactive"), ("#3fb950","● Active")]:
            l = QLabel(text)
            l.setStyleSheet(f"color:{color}; font-size:12px; padding:0 8px;")
            sbl.addWidget(l)
        vbox.addWidget(sb)

        # kick off scan
        self.start_scan()

    def _vbar(self):
        f = QFrame()
        f.setFrameShape(QFrame.Shape.VLine)
        f.setFixedWidth(1)
        f.setFixedHeight(28)
        f.setStyleSheet("color:#30363d;")
        return f

    def _chip(self, text, fg, bg):
        l = QLabel(text)
        l.setStyleSheet(
            f"background:{bg}; color:{fg}; border:1px solid {fg};"
            "border-radius:10px; padding:2px 10px; font-weight:bold; font-size:12px;"
        )
        return l

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
        # Close panel
        self.fp.setVisible(False)
        self.btn_filter.setChecked(False)

    def _reset_filters(self):
        self.fp.rb_all.setChecked(True)
        self.fp._clear_dates()
        self.fp.slider.setValue(3)   # minimum = no age filter
        self._apply_filters()

    # ──────────────────────────────────────────────────────────────────────────
    # Scan
    # ──────────────────────────────────────────────────────────────────────────

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder", self.txt_path.text())
        if folder:
            self.txt_path.setText(os.path.normpath(folder))

    def start_scan(self):
        path = self.txt_path.text().strip()
        if not path or not os.path.exists(path):
            path = os.getcwd()
            self.txt_path.setText(path)
        self.lbl_status.setText("Scanning…")
        self.btn_rescan.setEnabled(False)
        if self.scanner_thread and self.scanner_thread.isRunning():
            self.scanner_thread.cancel()
            self.scanner_thread.wait()
        self.scanner_thread = ScannerThread(path, stale_months=self.fp.slider.value())
        self.scanner_thread.scan_finished.connect(self._on_scan_done)
        self.scanner_thread.scan_progress.connect(self._on_progress)
        self.scanner_thread.start()

    def _on_progress(self, path):
        s = ("…" + path[-72:]) if len(path) > 75 else path
        self.lbl_status.setText(f"Scanning: {s}")

    def _on_scan_done(self, root_node):
        self.btn_rescan.setEnabled(True)
        if not root_node:
            self.lbl_status.setText("Scan failed or folder is empty.")
            return
        self.tree_model = WatchdogTreeModel(root_node)
        self.proxy_model.setSourceModel(self.tree_model)
        self.tree.setModel(self.proxy_model)
        hdr = self.tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, 7):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        # Apply default filter (show all)
        self._apply_filters()
        self.tree.expandToDepth(2)   # expand top 3 levels by default
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
            d = self._item_data(self.proxy_model.index(index.row(), 0, index.parent()))
            if d:
                self._open(d['path'], d.get('is_dir', True))

    def _on_double_click(self, index):
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
        try:
            with open(path, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow([self.proxy_model.headerData(i, Qt.Orientation.Horizontal)
                            for i in range(self.proxy_model.columnCount())])
                def write_rows(parent):
                    for r in range(self.proxy_model.rowCount(parent)):
                        row_data = [self.proxy_model.data(self.proxy_model.index(r, c, parent))
                                    for c in range(self.proxy_model.columnCount())]
                        w.writerow(row_data)
                        child = self.proxy_model.index(r, 0, parent)
                        if self.proxy_model.hasChildren(child):
                            write_rows(child)
                write_rows(QModelIndex())
            QMessageBox.information(self, "Exported", "CSV report saved.")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))
