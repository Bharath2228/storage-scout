from PyQt6.QtCore import (
    QSettings,
    Qt,
    pyqtSignal,
)
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...theme import SPACE_LG, SPACE_MD, SPACE_SM, SPACE_XL, SPACE_XS
from ..constants import DEFAULT_EXPORT_COLUMNS, EXPORT_COLUMNS


class ExportDialog(QDialog):
    SCOPE_OPTIONS = (
        ("current_page", "Current page only"),
        ("all_matching", "All items matching current filters"),
        ("entire_scan", "Entire scan"),
        ("selected", "Selected / checked items only"),
        ("bulk_scope", "Current bulk delete scope"),
    )
    TYPE_OPTIONS = (
        ("listing", "Files and folders"),
        ("file_types", "File type breakdown"),
        ("folder_summary", "Folder summary"),
        ("delete_audit", "Delete audit log"),
        ("scan_history", "Scan history"),
    )
    TYPE_DESCRIPTIONS = {
        "listing": "Export file and folder rows with the columns you choose below.",
        "file_types": "Export one summary row per file extension, including size and file count.",
        "folder_summary": "Export folder paths with their total size, file count, and folder count.",
        "delete_audit": "Export saved delete authorization and completion history.",
        "scan_history": "Export one summary row for each previously scanned root folder.",
    }
    SCOPE_DESCRIPTIONS = {
        "current_page": "Only export the rows visible on the current results page.",
        "all_matching": "Export every item matching the current filters and folder scope across all pages.",
        "entire_scan": "Export the complete indexed scan, regardless of the current filters or page.",
        "selected": "Only export items you have manually selected or checked.",
        "bulk_scope": "Export the complete all-pages selection currently prepared for bulk deletion.",
    }

    def __init__(self, has_selection=False, has_bulk_scope=False, settings=None, parent=None):
        super().__init__(parent)
        self.settings = settings or QSettings("StorageScout", "StorageScout")
        self.setWindowTitle("Export CSV")
        self.setModal(True)
        self.setObjectName("modalDialog")
        self.setMinimumSize(400, 360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
        layout.setSpacing(SPACE_MD)

        title = QLabel("Export data")
        title.setObjectName("modalTitle")
        layout.addWidget(title)

        detail = QLabel("Choose the report, scope, and columns to include.")
        detail.setObjectName("modalDetail")
        detail.setWordWrap(True)
        layout.addWidget(detail)

        self.options_scroll = QScrollArea()
        self.options_scroll.setObjectName("exportOptionsScroll")
        self.options_scroll.setWidgetResizable(True)
        self.options_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.options_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.options_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.options_content = QWidget()
        self.options_content.setObjectName("exportOptionsContent")
        options_layout = QVBoxLayout(self.options_content)
        options_layout.setContentsMargins(0, 0, SPACE_XS, 0)
        options_layout.setSpacing(SPACE_MD)
        self.options_scroll.setWidget(self.options_content)
        layout.addWidget(self.options_scroll, 1)

        type_row = QHBoxLayout()
        type_label = QLabel("Export type")
        type_label.setObjectName("sectionLabel")
        self.type_combo = QComboBox()
        for key, label in self.TYPE_OPTIONS:
            self.type_combo.addItem(label, key)
            self.type_combo.setItemData(
                self.type_combo.count() - 1,
                self.TYPE_DESCRIPTIONS[key],
                Qt.ItemDataRole.ToolTipRole,
            )
        type_row.addWidget(type_label)
        type_row.addWidget(self.type_combo, 1)
        options_layout.addLayout(type_row)
        self.type_help = QLabel()
        self.type_help.setObjectName("exportOptionHelp")
        self.type_help.setWordWrap(True)
        options_layout.addWidget(self.type_help)

        self.scope_frame = QFrame()
        self.scope_frame.setObjectName("modalSection")
        scope_layout = QVBoxLayout(self.scope_frame)
        scope_layout.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)
        scope_layout.setSpacing(SPACE_SM)
        scope_title = QLabel("Scope")
        scope_title.setObjectName("sectionLabel")
        scope_layout.addWidget(scope_title)
        self.scope_group = QButtonGroup(self)
        self.scope_buttons = {}
        for index, (key, label) in enumerate(self.SCOPE_OPTIONS):
            button = QRadioButton(label)
            button.setChecked(index == 0)
            button.setToolTip(self.SCOPE_DESCRIPTIONS[key])
            button.setProperty("exportScope", True)
            self.scope_group.addButton(button)
            self.scope_buttons[key] = button
            scope_layout.addWidget(button)
        self.scope_buttons["selected"].setEnabled(has_selection)
        self.scope_buttons["bulk_scope"].setEnabled(has_bulk_scope)
        if not has_selection:
            self.scope_buttons["selected"].setText(
                "Selected / checked items only (Unavailable)"
            )
            self.scope_buttons["selected"].setProperty("unavailable", True)
            self.scope_buttons["selected"].setToolTip(
                "Select or check at least one item before using this export scope."
            )
        if not has_bulk_scope:
            self.scope_buttons["bulk_scope"].setText(
                "Current bulk delete scope (Unavailable)"
            )
            self.scope_buttons["bulk_scope"].setProperty("unavailable", True)
            self.scope_buttons["bulk_scope"].setToolTip(
                "Create an all-pages bulk selection before using this export scope."
            )
        self.scope_help = QLabel()
        self.scope_help.setObjectName("exportOptionHelp")
        self.scope_help.setWordWrap(True)
        scope_layout.addWidget(self.scope_help)
        options_layout.addWidget(self.scope_frame)

        self.columns_frame = QFrame()
        self.columns_frame.setObjectName("modalSection")
        columns_layout = QGridLayout(self.columns_frame)
        columns_layout.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)
        columns_layout.setHorizontalSpacing(SPACE_LG)
        columns_layout.setVerticalSpacing(SPACE_SM)
        columns_title = QLabel("Columns")
        columns_title.setObjectName("sectionLabel")
        columns_layout.addWidget(columns_title, 0, 0, 1, 2)
        stored = self.settings.value("export_columns", list(DEFAULT_EXPORT_COLUMNS))
        if isinstance(stored, str):
            stored = [part for part in stored.split(",") if part]
        stored = set(stored or DEFAULT_EXPORT_COLUMNS)
        self.column_checks = {}
        for index, (key, label) in enumerate(EXPORT_COLUMNS):
            checkbox = QCheckBox(label)
            checkbox.setChecked(key in stored)
            self.column_checks[key] = checkbox
            columns_layout.addWidget(checkbox, 1 + index // 2, index % 2)
        options_layout.addWidget(self.columns_frame)

        self.include_summary = QCheckBox("Include export summary header")
        self.include_summary.setChecked(False)
        options_layout.addWidget(self.include_summary)

        self.validation_label = QLabel("")
        self.validation_label.setObjectName("authMessage")
        self.validation_label.setVisible(False)
        options_layout.addWidget(self.validation_label)
        options_layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setObjectName("modalCancel")
        cancel.clicked.connect(self.reject)
        self.continue_button = QPushButton("Choose file...")
        self.continue_button.setObjectName("primaryBtn")
        self.continue_button.clicked.connect(self._accept_if_valid)
        buttons.addWidget(cancel)
        buttons.addWidget(self.continue_button)
        layout.addLayout(buttons)

        self.type_combo.currentIndexChanged.connect(self._update_type_state)
        self.scope_group.buttonClicked.connect(self._update_option_help)
        for checkbox in self.column_checks.values():
            checkbox.toggled.connect(self._update_validation)
        self._fit_to_available_screen()
        self._update_type_state()

    def _fit_to_available_screen(self):
        screen = self.parentWidget().screen() if self.parentWidget() else None
        screen = screen or QApplication.primaryScreen()
        if screen is None:
            self._listing_height = 720
            self.resize(560, self._listing_height)
            return

        available = screen.availableGeometry()
        max_width = max(360, available.width() - (2 * SPACE_XL))
        max_height = max(360, available.height() - (2 * SPACE_XL))
        self.setMinimumSize(min(400, max_width), min(360, max_height))
        self._listing_height = min(720, max_height)
        self.resize(min(560, max_width), self._listing_height)

    def export_type(self):
        return self.type_combo.currentData()

    def export_scope(self):
        for key, button in self.scope_buttons.items():
            if button.isChecked():
                return key
        return "current_page"

    def selected_columns(self):
        return [
            key for key, _label in EXPORT_COLUMNS
            if self.column_checks[key].isChecked()
        ]

    def _update_type_state(self):
        is_listing = self.export_type() == "listing"
        self.scope_frame.setVisible(is_listing)
        self.columns_frame.setVisible(is_listing)
        self._update_option_help()
        self._update_validation()
        target_height = self._listing_height if is_listing else min(360, self._listing_height)
        self.resize(self.width(), target_height)

    def _update_option_help(self, _button=None):
        export_type = self.export_type()
        self.type_help.setText(self.TYPE_DESCRIPTIONS.get(export_type, ""))
        scope = self.export_scope()
        self.scope_help.setText(self.SCOPE_DESCRIPTIONS.get(scope, ""))

    def _update_validation(self):
        valid = self.export_type() != "listing" or bool(self.selected_columns())
        self.continue_button.setEnabled(valid)
        self.validation_label.setText("" if valid else "Select at least one column.")
        self.validation_label.setVisible(not valid)

    def _accept_if_valid(self):
        if self.export_type() == "listing" and not self.selected_columns():
            self._update_validation()
            return
        self.settings.setValue("export_columns", self.selected_columns())
        self.accept()

class ExportProgressDialog(QDialog):
    cancel_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._allow_close = False
        self.setWindowTitle("Exporting")
        self.setModal(True)
        self.setFixedSize(520, 190)
        self.setObjectName("modalDialog")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        layout.setSpacing(SPACE_MD)
        title = QLabel("Creating CSV report")
        title.setObjectName("modalTitle")
        layout.addWidget(title)
        self.detail_label = QLabel("Preparing export...")
        self.detail_label.setObjectName("modalDetail")
        layout.addWidget(self.detail_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setObjectName("loadingBar")
        layout.addWidget(self.progress)
        row = QHBoxLayout()
        row.addStretch()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("modalCancel")
        self.cancel_button.clicked.connect(self._cancel)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)

    def _cancel(self):
        if not self.cancel_button.isEnabled():
            return
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Stopping...")
        self.cancel_requested.emit()

    def update_progress(self, done, total, detail):
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(min(done, total))
            self.detail_label.setText(f"Exported {done:,} of {total:,} rows")
        else:
            self.progress.setRange(0, 0)
            self.detail_label.setText(detail or f"Exported {done:,} rows")

    def closeEvent(self, event):
        if self._allow_close:
            event.accept()
            return
        self._cancel()
        event.ignore()
