import os

from PyQt6.QtCore import (
    QEvent,
    QSettings,
    QSignalBlocker,
    QSize,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QIcon,
)
from PyQt6.QtWidgets import (
    QBoxLayout,
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...scan_exclusions import ScanExclusions
from ...theme import SPACE_LG, SPACE_MD, SPACE_SM, SPACE_XS, current_palette
from ..widgets.controls import AccordionHeader, FilterPopover, SegmentedRadioButton


class FilterPanel(QFrame):
    searchCleared = pyqtSignal()
    exclusionChanged = pyqtSignal()

    DEFAULT_STALE_MONTHS = 3
    AGE_FILTER_DISABLED = 0
    MAX_STALE_MONTHS = 24
    MAX_MANUAL_STALE_MONTHS = 240
    DEFAULT_STATUS_FILTER = "Inactive"
    MIN_WIDTH = 240
    DEFAULT_WIDTH = MIN_WIDTH
    MAX_WIDTH = 520
    FILTER_SEGMENT_MAX_WIDTH = 142
    FILTER_SEGMENT_MIN_WIDTH = 72

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("filterDrawer")
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setMaximumWidth(self.MAX_WIDTH)
        self.resize(self.DEFAULT_WIDTH, self.height())
        self._initial_drawer_width_applied = False
        self._exclusions_popup_layout_active = False
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.setVisible(False)
        self.settings = QSettings("StorageScout", "StorageScout")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        drawer_header = QFrame()
        drawer_header.setObjectName("filterDrawerHeader")
        drawer_header_layout = QHBoxLayout(drawer_header)
        self.drawer_header_layout = drawer_header_layout
        drawer_header_layout.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_MD, SPACE_MD)
        drawer_header_layout.setSpacing(SPACE_SM)
        drawer_title_box = QWidget()
        drawer_title_layout = QVBoxLayout(drawer_title_box)
        drawer_title_layout.setContentsMargins(0, 0, 0, 0)
        drawer_title_layout.setSpacing(2)
        drawer_title = QLabel("Filters")
        drawer_title.setObjectName("filterDrawerTitle")
        drawer_title_layout.addWidget(drawer_title)
        drawer_header_layout.addWidget(drawer_title_box, 1)
        outer.addWidget(drawer_header)

        staging = QWidget()
        staging.setMinimumWidth(0)
        staging.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        staging_layout = QVBoxLayout(staging)
        staging_layout.setContentsMargins(0, 0, 0, 0)
        staging_layout.setSpacing(0)

        scroll_content = QWidget()
        scroll_content.setObjectName("filterStagingContent")
        scroll_content.setMinimumWidth(0)
        scroll_content.setMinimumHeight(0)
        scroll_content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        body = QVBoxLayout(scroll_content)
        body.setSizeConstraint(QLayout.SizeConstraint.SetDefaultConstraint)
        body.setContentsMargins(0, SPACE_SM, 0, SPACE_MD)
        body.setSpacing(0)
        staging_layout.addWidget(scroll_content)

        search_section = QWidget()
        search_section.setObjectName("searchSection")
        search_layout = QVBoxLayout(search_section)
        self.search_layout = search_layout
        search_layout.setContentsMargins(SPACE_LG, SPACE_SM, SPACE_LG, SPACE_SM)
        search_layout.setSpacing(SPACE_SM)

        lbl_search = QLabel("SEARCH")
        lbl_search.setObjectName("searchHeader")
        search_layout.addWidget(lbl_search)

        self.search_shell = QFrame()
        self.search_shell.setObjectName("searchFieldShell")
        self.search_shell.setFixedHeight(40)
        self.search_shell.setMinimumWidth(0)
        search_shell_layout = QHBoxLayout(self.search_shell)
        self.search_shell_layout = search_shell_layout
        search_shell_layout.setContentsMargins(SPACE_MD, 0, 4, 0)
        search_shell_layout.setSpacing(SPACE_SM)

        self.search_icon_label = QLabel()
        self.search_icon_label.setObjectName("searchFieldIcon")
        search_icon = QIcon(os.path.join(os.path.join(os.path.dirname(__file__), "..", ".."), "assets", "search.svg"))
        self.search_icon_label.setPixmap(search_icon.pixmap(QSize(18, 18)))
        search_shell_layout.addWidget(self.search_icon_label)

        self.txt_search = QLineEdit()
        self.txt_search.setObjectName("searchFieldInput")
        self.txt_search.setMinimumWidth(0)
        self.txt_search.setPlaceholderText("Search files and folders")
        self.txt_search.setToolTip("Filter the current results by file or folder name")
        self.txt_search.setFixedHeight(36)
        clear_icon = QIcon(
            os.path.join(os.path.join(os.path.dirname(__file__), "..", ".."), "assets", "x-circle-light.svg")
        )
        self.search_clear_action = self.txt_search.addAction(
            clear_icon,
            QLineEdit.ActionPosition.TrailingPosition,
        )
        self.search_clear_action.setToolTip("Clear search")
        self.search_clear_action.setVisible(False)
        self.search_clear_action.triggered.connect(self.clear_search_text)
        for button in self.txt_search.findChildren(QToolButton):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.txt_search.textChanged.connect(
            lambda text: self.search_clear_action.setVisible(bool(text))
        )
        self.txt_search.installEventFilter(self)
        search_shell_layout.addWidget(self.txt_search, 1)

        self.btn_search_submit = QPushButton()
        self.btn_search_submit.setObjectName("searchFieldAction")
        self.btn_search_submit.setToolTip("Apply search")
        self.btn_search_submit.setAccessibleName("Apply search")
        self.btn_search_submit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_search_submit.setFixedSize(32, 32)
        self.btn_search_submit.setIcon(
            QIcon(os.path.join(os.path.join(os.path.dirname(__file__), "..", ".."), "assets", "arrow_right.svg"))
        )
        self.btn_search_submit.setIconSize(QSize(18, 18))
        self.btn_search_submit.clicked.connect(self.txt_search.returnPressed.emit)
        search_shell_layout.addWidget(self.btn_search_submit)

        search_layout.addWidget(self.search_shell)

        body.addWidget(search_section)
        body.addSpacing(SPACE_SM)

        self._age_section_expanded = True
        self._exclusions_section_expanded = False

        # Section 1: Display Mode
        display_section = QWidget()
        display_section.setObjectName("displayModeSection")
        display_layout = QVBoxLayout(display_section)
        self.display_layout = display_layout
        display_layout.setContentsMargins(SPACE_LG, SPACE_SM, SPACE_LG, SPACE_SM)
        display_layout.setSpacing(SPACE_SM)

        lbl_display = QLabel("DISPLAY MODE")
        lbl_display.setObjectName("displayModeHeader")

        display_layout.addWidget(lbl_display)

        self.rb_all      = SegmentedRadioButton("Show all")
        self.rb_inactive = SegmentedRadioButton("Inactive only")
        self.rb_empty    = SegmentedRadioButton("Empty only")
        self.rb_videos   = SegmentedRadioButton("Videos only")
        self.rb_all.setChecked(True)
        self.bg = QButtonGroup()
        display_grid = QGridLayout()
        self.display_grid = display_grid
        display_grid.setContentsMargins(0, 0, 0, 0)
        display_grid.setSpacing(SPACE_SM)
        self.display_buttons = [
            self.rb_all,
            self.rb_inactive,
            self.rb_empty,
            self.rb_videos,
        ]
        for rb in self.display_buttons:
            self.bg.addButton(rb)
            rb.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            rb.setProperty("segment", True)
            rb.setFixedHeight(32)
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rebuild_button_grid(self.display_grid, self.display_buttons, 2)
        display_layout.addLayout(display_grid)
        
        body.addWidget(display_section)
        body.addSpacing(SPACE_SM)

        # Section 1.5: View Mode
        view_section = QWidget()
        view_section.setObjectName("viewModeSection")
        view_layout = QVBoxLayout(view_section)
        self.view_layout = view_layout
        view_layout.setContentsMargins(SPACE_LG, SPACE_SM, SPACE_LG, SPACE_SM)
        view_layout.setSpacing(SPACE_SM)

        lbl_view = QLabel("VIEW MODE")
        lbl_view.setObjectName("viewModeHeader")
        view_layout.addWidget(lbl_view)

        self.rb_view_tree    = SegmentedRadioButton("Tree view")
        self.rb_view_files   = SegmentedRadioButton("Files only")
        self.rb_view_folders = SegmentedRadioButton("Folders only")
        self.rb_view_tree.setChecked(True)
        
        self.bg_view = QButtonGroup()
        view_grid = QGridLayout()
        self.view_grid = view_grid
        view_grid.setContentsMargins(0, 0, 0, 0)
        view_grid.setSpacing(SPACE_SM)
        self.view_buttons = [
            self.rb_view_tree,
            self.rb_view_files,
            self.rb_view_folders,
        ]
        for rb in self.view_buttons:
            self.bg_view.addButton(rb)
            rb.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            rb.setProperty("segment", True)
            rb.setFixedHeight(32)
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rebuild_button_grid(self.view_grid, self.view_buttons, 2)
        view_layout.addLayout(view_grid)
            
        body.addWidget(view_section)
        body.addSpacing(SPACE_SM)



        # Section 2: Date Range
        
        

        # Section 3: Stale Threshold
        age_section = QWidget()
        age_section.setObjectName("ageThresholdSection")
        age_outer_layout = QVBoxLayout(age_section)
        self.age_outer_layout = age_outer_layout
        age_outer_layout.setContentsMargins(SPACE_LG, SPACE_SM, SPACE_LG, SPACE_SM)
        age_outer_layout.setSpacing(SPACE_SM)

        self.age_heading = QLabel("Age: Off")
        self.age_heading.setObjectName("ageSectionHeading")
        self.age_heading.setCursor(Qt.CursorShape.ArrowCursor)
        age_outer_layout.addWidget(self.age_heading)

        self.age_box = QFrame()
        self.age_box.setObjectName("ageControl")
        self.age_box.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Maximum,
        )
        age_layout = QVBoxLayout(self.age_box)
        self.age_layout = age_layout
        age_layout.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)
        age_layout.setSpacing(SPACE_SM)

        age_hdr = QHBoxLayout()
        self.age_summary_layout = age_hdr
        age_hdr.setSpacing(SPACE_SM)
        self.lbl_pill = QLabel()
        self.lbl_pill.setObjectName("agePill")
        self.lbl_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_pill.setFixedHeight(20)
        self.lbl_val = QLabel()
        self.lbl_val.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_val.setWordWrap(False)
        self.lbl_pill.setVisible(False)
        age_hdr.addWidget(self.lbl_val)
        age_hdr.addStretch()
        age_layout.addLayout(age_hdr)
        
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(self.AGE_FILTER_DISABLED, self.MAX_STALE_MONTHS)
        self.slider.setValue(self.AGE_FILTER_DISABLED)
        self.slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider.setTickInterval(1)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider.installEventFilter(self)
        self.slider.valueChanged.connect(self._update_age_label)
        self.slider.valueChanged.connect(self._sync_manual_age_from_slider)
        age_layout.addWidget(self.slider)

        self.age_input = QSpinBox()
        self.age_input.setRange(self.AGE_FILTER_DISABLED, self.MAX_MANUAL_STALE_MONTHS)
        self.age_input.setValue(self.AGE_FILTER_DISABLED)
        self.age_input.setSuffix(" months")
        self.age_input.setCursor(Qt.CursorShape.PointingHandCursor)
        self.age_input.valueChanged.connect(self._sync_slider_from_manual_age)

        self.btn_apply_age = QPushButton("Go")
        self.btn_apply_age.setObjectName("primaryBtn")
        self.btn_apply_age.setFixedWidth(52)
        self.btn_apply_age.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_apply_age.setToolTip("Apply age threshold")
        self.btn_apply_age.setEnabled(False)
        self.applied_age_value = self.AGE_FILTER_DISABLED

        bot_row = QHBoxLayout()
        self.age_manual_row = bot_row
        bot_row.setSpacing(SPACE_SM)
        lbl_manual = QLabel("More than 24 months? Enter here")
        lbl_manual.setObjectName("ageManualHint")
        lbl_manual.setWordWrap(True)
        self.age_manual_label = lbl_manual

        bot_row.addWidget(self.age_input)
        bot_row.addWidget(self.btn_apply_age)
        bot_row.addStretch()
        age_layout.addWidget(lbl_manual)
        age_layout.addLayout(bot_row)
        self.age_box.setMaximumHeight(self.age_box.sizeHint().height())
        age_outer_layout.addWidget(self.age_box)

        self.slider.valueChanged.connect(self._update_age_apply_state)
        self.age_input.valueChanged.connect(self._update_age_apply_state)
        
        body.addWidget(age_section)
        body.addSpacing(SPACE_SM)

        exclusions_section = QWidget()
        exclusions_section.setObjectName("exclusionsSection")
        exclusions_outer_layout = QVBoxLayout(exclusions_section)
        self.exclusions_outer_layout = exclusions_outer_layout
        exclusions_outer_layout.setContentsMargins(SPACE_LG, SPACE_SM, SPACE_LG, SPACE_SM)
        exclusions_outer_layout.setSpacing(SPACE_SM)

        self.btn_exclusions_toggle = self._make_accordion_header("EXCLUSIONS")
        self.btn_exclusions_toggle.clicked.connect(lambda: self._toggle_collapsible_section("exclusions"))
        exclusions_outer_layout.addWidget(self.btn_exclusions_toggle)

        self.exclusions_box = QWidget()
        exclusions_layout = QVBoxLayout(self.exclusions_box)
        exclusions_layout.setContentsMargins(0, SPACE_SM, 0, SPACE_SM)
        exclusions_layout.setSpacing(SPACE_SM)

        lbl_folders = QLabel("Folder names")
        lbl_folders.setObjectName("manualLabel")
        exclusions_layout.addWidget(lbl_folders)

        self.txt_excluded_folders = QLineEdit()
        self.txt_excluded_folders.setObjectName("scanExclusionInput")
        self.txt_excluded_folders.setPlaceholderText(
            "Example: node_modules, *.git*, Temp"
        )
        self.txt_excluded_folders.setToolTip("Folder names or simple patterns to skip on the next scan")
        self.txt_excluded_folders.setFixedHeight(32)
        exclusions_layout.addWidget(self.txt_excluded_folders)

        lbl_extensions = QLabel("File extensions")
        lbl_extensions.setObjectName("manualLabel")
        exclusions_layout.addWidget(lbl_extensions)

        self.txt_excluded_extensions = QLineEdit()
        self.txt_excluded_extensions.setObjectName("scanExclusionInput")
        self.txt_excluded_extensions.setPlaceholderText(
            "Example: .tmp, .log, .iso"
        )
        self.txt_excluded_extensions.setToolTip("File extensions to skip on the next scan")
        self.txt_excluded_extensions.setFixedHeight(32)
        exclusions_layout.addWidget(self.txt_excluded_extensions)

        lbl_min_size = QLabel("Ignore files smaller than")
        lbl_min_size.setObjectName("manualLabel")
        exclusions_layout.addWidget(lbl_min_size)

        size_row = QHBoxLayout()
        self.exclusions_size_row = size_row
        size_row.setSpacing(SPACE_SM)
        self.min_size_input = QSpinBox()
        self.min_size_input.setObjectName("scanExclusionSize")
        self.min_size_input.setRange(0, 999999)
        self.min_size_input.setValue(0)
        self.min_size_input.setFixedWidth(86)
        self.min_size_input.setFixedHeight(36)
        self.min_size_input.setCursor(Qt.CursorShape.PointingHandCursor)
        self.min_size_unit = QComboBox()
        self.min_size_unit.setObjectName("scanExclusionUnit")
        self.min_size_unit.addItems(["KB", "MB", "GB"])
        self.min_size_unit.setFixedWidth(72)
        self.min_size_unit.setFixedHeight(36)
        self.min_size_unit.setCursor(Qt.CursorShape.PointingHandCursor)
        size_row.addWidget(self.min_size_input)
        size_row.addWidget(self.min_size_unit)
        size_row.addStretch()
        exclusions_layout.addLayout(size_row)

        self.lbl_exclusions_hint = QLabel("Exclusion changes apply on next scan - click Re-scan to apply.")
        self.lbl_exclusions_hint.setObjectName("manualLabel")
        self.lbl_exclusions_hint.setWordWrap(True)
        self.lbl_exclusions_hint.setVisible(False)
        exclusions_layout.addWidget(self.lbl_exclusions_hint)

        self.btn_reset_exclusions = QPushButton("Reset to defaults")
        self.btn_reset_exclusions.setObjectName("resetExclusions")
        self.btn_reset_exclusions.setToolTip("Restore the default scan exclusions")
        self.btn_reset_exclusions.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset_exclusions.setFixedHeight(36)

        self.btn_rescan_exclusions = QPushButton("Apply and Re-scan")
        self.btn_rescan_exclusions.setAccessibleName("Apply and re-scan")
        self.btn_rescan_exclusions.setObjectName("primaryBtn")
        self.btn_rescan_exclusions.setToolTip(
            "Apply these exclusions and scan the selected folder again"
        )
        self.btn_rescan_exclusions.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_rescan_exclusions.setFixedHeight(36)
        exclusions_actions = QHBoxLayout()
        self.exclusions_actions_layout = exclusions_actions
        exclusions_actions.setSpacing(SPACE_SM)
        exclusions_actions.addWidget(self.btn_reset_exclusions)
        exclusions_actions.addStretch()
        exclusions_actions.addWidget(self.btn_rescan_exclusions)
        exclusions_layout.addLayout(exclusions_actions)

        exclusions_outer_layout.addWidget(self.exclusions_box)
        body.addWidget(exclusions_section)

        self._update_age_label(self.slider.value())
        self._restore_scan_exclusions()
        self._age_section_expanded = True
        self._exclusions_section_expanded = self._scan_exclusions_from_controls().differs_from_default()
        self._sync_collapsible_sections()
        self.txt_excluded_folders.textChanged.connect(
            lambda _text: self.exclusionChanged.emit()
        )
        self.txt_excluded_extensions.textChanged.connect(
            lambda _text: self.exclusionChanged.emit()
        )
        self.txt_excluded_folders.editingFinished.connect(self._normalize_and_save_scan_exclusions)
        self.txt_excluded_extensions.editingFinished.connect(self._normalize_and_save_scan_exclusions)
        self.min_size_input.valueChanged.connect(self._save_scan_exclusions_from_controls)
        self.min_size_unit.currentTextChanged.connect(self._save_scan_exclusions_from_controls)
        self.btn_reset_exclusions.clicked.connect(self.reset_scan_exclusions_to_defaults)
        body.addStretch()
        self.filter_scroll = QScrollArea()
        self.filter_scroll.setObjectName("filterDrawerScroll")
        self.filter_scroll.setWidgetResizable(True)
        self.filter_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.filter_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.filter_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.filter_scroll.setWidget(staging)
        outer.addWidget(self.filter_scroll, 1)

        # Section 4: Actions
        action_box = QWidget()
        action_box.setObjectName("sidebarActionBox")
        action_layout = QVBoxLayout(action_box)
        self.action_layout = action_layout
        action_layout.setContentsMargins(SPACE_LG, SPACE_MD, SPACE_LG, SPACE_LG)
        action_layout.setSpacing(SPACE_SM)
        
        self.btn_reset = QPushButton("Reset all filters")
        self.btn_reset.setObjectName("resetFilters")

        self.btn_reset.setToolTip("Reset all filters to their default values")
        self.btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close = QPushButton("Close sidebar")
        self.btn_close.setObjectName("closeFilterDrawer")
        self.btn_close.setToolTip("Hide the filter sidebar")
        self.btn_close.setAccessibleName("Close filters")
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setText("×")
        self.btn_close.setFixedSize(32, 32)
        action_layout.addWidget(self.btn_reset)
        drawer_header_layout.addWidget(self.btn_close)
        outer.addWidget(action_box)

    def prepare_exclusions_popup_layout(self):
        """Give the exclusions popover aligned, evenly sized control rows."""
        self._exclusions_popup_layout_active = True
        size_row = self.exclusions_size_row
        while size_row.count():
            size_row.takeAt(0)
        size_row.setDirection(QBoxLayout.Direction.LeftToRight)
        size_row.setContentsMargins(0, 0, 0, 0)
        size_row.setSpacing(SPACE_SM)
        for control in (self.min_size_input, self.min_size_unit):
            control.setFixedHeight(36)
            control.setMinimumWidth(0)
            control.setMaximumWidth(16777215)
            control.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Fixed,
            )
            size_row.addWidget(control, 1)

        actions = self.exclusions_actions_layout
        while actions.count():
            actions.takeAt(0)
        actions.setDirection(QBoxLayout.Direction.LeftToRight)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(SPACE_SM)
        for button in (self.btn_reset_exclusions, self.btn_rescan_exclusions):
            button.setFixedHeight(36)
            button.setMinimumWidth(0)
            button.setMaximumWidth(16777215)
            button.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Fixed,
            )
            actions.addWidget(button, 1)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._initial_drawer_width_applied:
            self.resize(self.DEFAULT_WIDTH, self.height())
            self._initial_drawer_width_applied = True
        self._update_responsive_drawer_layout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_responsive_drawer_layout()

    def _convert_to_horizontal_layout(
        self,
        outer,
        old_scroll,
        old_action_box,
        search_section,
        display_section,
        view_section,
    ):
        """Move the existing filter controls into a compact two-row bar."""
        self.setObjectName("filterBar")
        self.setMinimumWidth(0)
        self.setMaximumWidth(16777215)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        outer.removeWidget(old_scroll)
        outer.removeWidget(old_action_box)

        main_row = QWidget()
        main_row.setObjectName("filterBarMain")
        main_row.setFixedHeight(56)
        main_layout = QHBoxLayout(main_row)
        main_layout.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)
        main_layout.setSpacing(SPACE_SM)

        search_layout = search_section.layout()
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(0)
        search_layout.itemAt(0).widget().setVisible(False)
        self.search_shell.setFixedWidth(240)
        self.txt_search.setFixedHeight(36)
        main_layout.addWidget(
            search_section,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        main_layout.addWidget(self._vline())

        self._make_segment_section_horizontal(
            display_section,
            (self.rb_all, self.rb_inactive, self.rb_empty, self.rb_videos),
        )
        main_layout.addWidget(
            display_section,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        main_layout.addWidget(self._vline())

        self._make_segment_section_horizontal(
            view_section,
            (self.rb_view_tree, self.rb_view_files, self.rb_view_folders),
        )
        main_layout.addWidget(
            view_section,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        main_layout.addWidget(self._vline())
        main_layout.addStretch(1)

        self.age_heading.setParent(main_row)
        self.age_heading.setFixedWidth(116)
        self.age_heading.setFixedHeight(36)
        main_layout.addWidget(
            self.age_heading,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )

        self.btn_exclusions_toggle.setParent(main_row)
        self.btn_exclusions_toggle.setFixedWidth(144)
        self.btn_exclusions_toggle.setFixedHeight(36)
        main_layout.addWidget(
            self.btn_exclusions_toggle,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )

        self.btn_reset.setParent(main_row)
        self.btn_reset.setFixedWidth(116)
        self.btn_reset.setFixedHeight(36)
        main_layout.addWidget(
            self.btn_reset,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )

        self._prepare_filter_popover_contents()

        self.age_popover = FilterPopover(340, self)
        self.age_popover.set_content(self.age_box)
        self.age_popover.closed.connect(
            lambda: self._on_filter_popover_closed("age")
        )

        self.exclusions_box.setObjectName("exclusionsControl")
        self.exclusions_popover = FilterPopover(360, self)
        self.exclusions_popover.set_content(self.exclusions_box)
        self.exclusions_popover.closed.connect(
            lambda: self._on_filter_popover_closed("exclusions")
        )

        self.main_row = main_row
        outer.addWidget(main_row)

        self.btn_close.setParent(None)
        self.btn_close.deleteLater()
        del self.btn_close
        old_scroll.setParent(None)
        old_scroll.deleteLater()
        old_action_box.setParent(None)
        old_action_box.deleteLater()
        self._age_section_expanded = True
        self._exclusions_section_expanded = False
        self._sync_collapsible_sections()

    def _make_segment_section_horizontal(self, section, buttons):
        layout = section.layout()
        label = layout.itemAt(0).widget()
        while layout.count():
            layout.takeAt(0)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        label.setVisible(False)

        segments = QHBoxLayout()
        segments.setContentsMargins(0, 0, 0, 0)
        segments.setSpacing(SPACE_XS)
        for button in buttons:
            button.setFixedWidth(76)
            button.setFixedHeight(36)
            segments.addWidget(button)
        layout.addLayout(segments)

    def _prepare_filter_popover_contents(self):
        age_layout = self.age_box.layout()
        while age_layout.count():
            age_layout.takeAt(0)
        age_layout.setContentsMargins(0, 0, 0, 0)
        age_layout.setSpacing(SPACE_MD)

        age_summary = QHBoxLayout()
        age_summary.setContentsMargins(0, 0, 0, 0)
        age_summary.setSpacing(SPACE_SM)
        self.lbl_pill.setVisible(False)
        self.lbl_val.setMinimumWidth(0)
        self.lbl_val.setWordWrap(False)
        age_summary.addWidget(self.lbl_val, 1)
        age_layout.addLayout(age_summary)

        self.slider.setMinimumWidth(0)
        age_layout.addWidget(self.slider)

        manual_label = getattr(self, "age_manual_label", None)
        manual_row = QHBoxLayout()
        manual_row.setContentsMargins(0, 0, 0, 0)
        manual_row.setSpacing(SPACE_SM)
        if manual_label is not None:
            age_layout.addWidget(manual_label)
        manual_row.addWidget(self.age_input, 1)
        manual_row.addWidget(self.btn_apply_age)
        age_layout.addLayout(manual_row)
        self.age_box.setMaximumHeight(self.age_box.sizeHint().height())

        exclusions_layout = self.exclusions_box.layout()
        while exclusions_layout.count():
            exclusions_layout.takeAt(0)
        exclusions_layout.setContentsMargins(0, 0, 0, 0)
        exclusions_layout.setSpacing(SPACE_SM)

        detail_labels = {
            label.text(): label
            for label in self.exclusions_box.findChildren(QLabel)
            if label.objectName() == "manualLabel"
        }
        folders_label = detail_labels.get("Folder names")
        extensions_label = detail_labels.get("File extensions")
        size_label = detail_labels.get("Ignore files smaller than")
        if folders_label is not None:
            exclusions_layout.addWidget(folders_label)
        self.txt_excluded_folders.setMinimumWidth(0)
        exclusions_layout.addWidget(self.txt_excluded_folders)
        if extensions_label is not None:
            exclusions_layout.addWidget(extensions_label)
        self.txt_excluded_extensions.setMinimumWidth(0)
        exclusions_layout.addWidget(self.txt_excluded_extensions)

        if size_label is not None:
            exclusions_layout.addWidget(size_label)
        size_row = QHBoxLayout()
        self.exclusions_size_row = size_row
        size_row.setContentsMargins(0, 0, 0, 0)
        size_row.setSpacing(SPACE_SM)
        size_row.addWidget(self.min_size_input)
        size_row.addWidget(self.min_size_unit)
        size_row.addStretch()
        exclusions_layout.addLayout(size_row)

        self.lbl_exclusions_hint.setMaximumWidth(16777215)
        exclusions_layout.addWidget(self.lbl_exclusions_hint)
        exclusions_actions = QHBoxLayout()
        self.exclusions_actions_layout = exclusions_actions
        exclusions_actions.setContentsMargins(0, 0, 0, 0)
        exclusions_actions.setSpacing(SPACE_SM)
        exclusions_actions.addWidget(self.btn_reset_exclusions)
        exclusions_actions.addStretch()
        exclusions_actions.addWidget(self.btn_rescan_exclusions)
        exclusions_layout.addLayout(exclusions_actions)

    def _make_accordion_header(self, text):
        return AccordionHeader(text, self)

    def _rebuild_button_grid(self, grid, buttons, columns):
        while grid.count():
            grid.takeAt(0)
        for column in range(4):
            grid.setColumnStretch(column, 0)
        for index, button in enumerate(buttons):
            row = index // columns
            column = index % columns
            grid.addWidget(button, row, column, alignment=Qt.AlignmentFlag.AlignLeft)

    def _set_filter_button_widths(self, buttons, section_margin):
        scrollbar_clearance = 14
        grid_spacing = max(0, self.display_grid.spacing())
        usable_width = (
            self.width()
            - (2 * section_margin)
            - scrollbar_clearance
            - grid_spacing
        )
        button_width = max(
            self.FILTER_SEGMENT_MIN_WIDTH,
            min(self.FILTER_SEGMENT_MAX_WIDTH, usable_width // 2),
        )
        for button in buttons:
            button.setFixedWidth(button_width)

    def _update_responsive_drawer_layout(self):
        compact = self.width() <= 280
        section_margin = SPACE_MD if compact else SPACE_LG
        shell_left_margin = SPACE_SM if compact else SPACE_MD
        shell_right_margin = 2 if compact else 4
        shell_spacing = SPACE_XS if compact else SPACE_SM

        if hasattr(self, "drawer_header_layout"):
            self.drawer_header_layout.setContentsMargins(
                section_margin,
                SPACE_LG if not compact else SPACE_MD,
                SPACE_SM,
                SPACE_MD,
            )
        if hasattr(self, "search_layout"):
            self.search_layout.setContentsMargins(
                section_margin,
                SPACE_SM,
                section_margin,
                SPACE_SM,
            )
        if hasattr(self, "display_layout"):
            self.display_layout.setContentsMargins(
                section_margin,
                SPACE_SM,
                section_margin,
                SPACE_SM,
            )
        if hasattr(self, "view_layout"):
            self.view_layout.setContentsMargins(
                section_margin,
                SPACE_SM,
                section_margin,
                SPACE_SM,
            )
        if hasattr(self, "age_outer_layout"):
            self.age_outer_layout.setContentsMargins(
                section_margin,
                SPACE_SM,
                section_margin,
                SPACE_SM,
            )
        if hasattr(self, "exclusions_outer_layout"):
            self.exclusions_outer_layout.setContentsMargins(
                section_margin,
                SPACE_SM,
                section_margin,
                SPACE_SM,
            )
        if hasattr(self, "action_layout"):
            self.action_layout.setContentsMargins(
                section_margin,
                SPACE_MD,
                section_margin,
                SPACE_LG,
            )
        if hasattr(self, "search_shell_layout"):
            self.search_shell_layout.setContentsMargins(
                shell_left_margin,
                0,
                shell_right_margin,
                0,
            )
            self.search_shell_layout.setSpacing(shell_spacing)

        if hasattr(self, "display_grid") and hasattr(self, "display_buttons"):
            self._set_filter_button_widths(self.display_buttons, section_margin)
            self._rebuild_button_grid(
                self.display_grid,
                self.display_buttons,
                2,
            )
        if hasattr(self, "view_grid") and hasattr(self, "view_buttons"):
            self._set_filter_button_widths(self.view_buttons, section_margin)
            self._rebuild_button_grid(
                self.view_grid,
                self.view_buttons,
                2,
            )

        if hasattr(self, "age_summary_layout"):
            self.age_summary_layout.setDirection(
                QBoxLayout.Direction.TopToBottom if compact
                else QBoxLayout.Direction.LeftToRight
            )
        if hasattr(self, "age_manual_row"):
            self.age_manual_row.setDirection(QBoxLayout.Direction.LeftToRight)
            self.age_manual_row.setSpacing(shell_spacing)
        if hasattr(self, "age_manual_label"):
            self.age_manual_label.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
        if hasattr(self, "btn_apply_age"):
            self.btn_apply_age.setMinimumWidth(52)
            self.btn_apply_age.setMaximumWidth(52)
            self.btn_apply_age.setSizePolicy(
                QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Fixed,
            )

        if hasattr(self, "exclusions_size_row"):
            self.exclusions_size_row.setDirection(
                QBoxLayout.Direction.LeftToRight
                if self._exclusions_popup_layout_active
                else QBoxLayout.Direction.TopToBottom if compact
                else QBoxLayout.Direction.LeftToRight
            )
            self.exclusions_size_row.setSpacing(shell_spacing)
        if hasattr(self, "exclusions_actions_layout"):
            self.exclusions_actions_layout.setDirection(
                QBoxLayout.Direction.LeftToRight
                if self._exclusions_popup_layout_active or not compact
                else QBoxLayout.Direction.TopToBottom
            )
            self.exclusions_actions_layout.setSpacing(shell_spacing)
        if hasattr(self, "min_size_input"):
            expanding = self._exclusions_popup_layout_active or compact
            self.min_size_input.setMinimumWidth(0 if expanding else 86)
            self.min_size_input.setMaximumWidth(16777215 if expanding else 86)
            self.min_size_input.setSizePolicy(
                QSizePolicy.Policy.Ignored
                if self._exclusions_popup_layout_active
                else QSizePolicy.Policy.Expanding if expanding
                else QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Fixed,
            )
        if hasattr(self, "min_size_unit"):
            expanding = self._exclusions_popup_layout_active or compact
            self.min_size_unit.setMinimumWidth(0 if expanding else 72)
            self.min_size_unit.setMaximumWidth(16777215 if expanding else 72)
            self.min_size_unit.setSizePolicy(
                QSizePolicy.Policy.Ignored
                if self._exclusions_popup_layout_active
                else QSizePolicy.Policy.Expanding if expanding
                else QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Fixed,
            )

    def _toggle_collapsible_section(self, section):
        if section == "age":
            self._age_section_expanded = True
        elif section == "exclusions":
            if hasattr(self, "exclusions_popover"):
                should_close = (
                    self.exclusions_popover.isVisible()
                    or self.exclusions_popover.recently_hidden()
                )
            else:
                should_close = self._exclusions_section_expanded
            if should_close:
                self._exclusions_section_expanded = False
            else:
                self._exclusions_section_expanded = True
        self._sync_collapsible_sections()

    def _sync_collapsible_sections(self):
        self._update_popover_summaries()
        if hasattr(self, 'age_popover'):
            self.age_box.setVisible(True)
            self.exclusions_box.setVisible(True)
            if self._age_section_expanded and self.isVisible():
                self.exclusions_popover.hide()
                self.age_popover.show_below(self.age_heading)
            else:
                self.age_popover.hide()
            if self._exclusions_section_expanded and self.isVisible():
                self.age_popover.hide()
                self.exclusions_popover.show_below(self.btn_exclusions_toggle)
            else:
                self.exclusions_popover.hide()
        elif hasattr(self, 'age_box'):
            self.age_box.setVisible(True)
            self.age_box.updateGeometry()
            self.exclusions_box.setVisible(self._exclusions_section_expanded)
            self.exclusions_box.updateGeometry()
        if hasattr(self, 'age_heading'):
            self.age_heading.updateGeometry()
        if hasattr(self, 'btn_exclusions_toggle'):
            self.btn_exclusions_toggle.set_expanded(self._exclusions_section_expanded)
            self.btn_exclusions_toggle.updateGeometry()
        self.updateGeometry()

    def _on_filter_popover_closed(self, section):
        if section == "age":
            self._age_section_expanded = True
        elif section == "exclusions":
            self._exclusions_section_expanded = False
            self.btn_exclusions_toggle.set_expanded(False)

    def close_popovers(self):
        self._age_section_expanded = True
        self._exclusions_section_expanded = False
        if hasattr(self, "age_popover"):
            self.age_popover.hide()
        if hasattr(self, "exclusions_popover"):
            self.exclusions_popover.hide()
        if hasattr(self, "btn_exclusions_toggle"):
            self.btn_exclusions_toggle.set_expanded(False)

    def _update_popover_summaries(self):
        if hasattr(self, "age_heading") and hasattr(self, "applied_age_value"):
            age_text = (
                "Off"
                if self.applied_age_value == self.AGE_FILTER_DISABLED
                else f"{self.applied_age_value}mo"
            )
            self.age_heading.setText(f"Age: {age_text}")
        if hasattr(self, "btn_exclusions_toggle") and hasattr(
            self,
            "txt_excluded_folders",
        ):
            exclusions = self._scan_exclusions_from_controls()
            if exclusions.differs_from_default():
                current = exclusions.to_dict()
                default = ScanExclusions().to_dict()
                rule_count = (
                    len(
                        set(current["folder_names"])
                        ^ set(default["folder_names"])
                    )
                    + len(
                        set(current["extensions"])
                        ^ set(default["extensions"])
                    )
                    + int(
                        current["min_file_size_bytes"]
                        != default["min_file_size_bytes"]
                    )
                )
                summary = f"{rule_count} rule{'s' if rule_count != 1 else ''}"
            else:
                summary = "Default"
            self.btn_exclusions_toggle.set_text(f"Exclusions: {summary}")

    def _update_age_label(self, value):
        palette = current_palette()
        if value == self.AGE_FILTER_DISABLED:
            self.lbl_pill.setText("Off")
            self.lbl_pill.setStyleSheet(
                f"background-color: {palette['text_muted']}; color: {palette['bg']};"
            )
            self.lbl_val.setText("Age filtering is disabled")
        else:
            self.lbl_pill.setText(f"{value}m")
            self.lbl_pill.setStyleSheet(
                f"background-color: {palette['accent']}; color: {palette['on_accent']};"
            )
            
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
        self.slider.setValue(min(value, self.MAX_STALE_MONTHS))
        del blocker
        self._update_age_label(value)

    def _update_age_apply_state(self, _value=None):
        controls_enabled = self.slider.isEnabled() and self.age_input.isEnabled()
        has_pending_value = self.age_input.value() != self.applied_age_value
        self.btn_apply_age.setEnabled(controls_enabled and has_pending_value)

    def get_older_than_secs(self):
        """Returns seconds threshold or None if age filtering is disabled."""
        v = self.applied_age_value
        if v == self.AGE_FILTER_DISABLED:
            return None
        return v * 30 * 24 * 3600  # months to seconds (approximate)

    def get_stale_months_for_scan(self):
        value = self.age_input.value()
        return value if value > 0 else self.DEFAULT_STALE_MONTHS

    def get_view_mode(self):
        """Returns 'Tree', 'Files', or 'Folders'."""
        if self.rb_view_files.isChecked(): return 'Files'
        if self.rb_view_folders.isChecked(): return 'Folders'
        return 'Tree'

    def get_scan_exclusions(self):
        return self._scan_exclusions_from_controls()

    def show_exclusions_rescan_hint(self, visible=True):
        self.lbl_exclusions_hint.setVisible(visible)

    def reset_scan_exclusions_to_defaults(self):
        self._set_scan_exclusions_controls(ScanExclusions())
        self._save_scan_exclusions_from_controls()

    def _restore_scan_exclusions(self):
        # Exclusions are intentionally session-only; every launch starts clean.
        self.settings.remove("scan_exclusions")
        self._set_scan_exclusions_controls(ScanExclusions())

    def _save_scan_exclusions_from_controls(self):
        exclusions = self._scan_exclusions_from_controls()
        self.settings.remove("scan_exclusions")
        self._update_popover_summaries()
        self.exclusionChanged.emit()

    def _normalize_and_save_scan_exclusions(self):
        exclusions = self._scan_exclusions_from_controls()
        self._set_scan_exclusions_controls(exclusions)
        self._save_scan_exclusions_from_controls()

    def _scan_exclusions_from_controls(self):
        folders = [
            item.strip()
            for item in self.txt_excluded_folders.text().split(",")
            if item.strip()
        ]
        extensions = [
            item.strip()
            for item in self.txt_excluded_extensions.text().split(",")
            if item.strip()
        ]
        min_value = self.min_size_input.value()
        unit = self.min_size_unit.currentText()
        multiplier = {"KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}.get(unit, 1024)
        return ScanExclusions(
            folder_names=folders,
            extensions=extensions,
            min_file_size_bytes=min_value * multiplier if min_value > 0 else 0,
        )

    def _set_scan_exclusions_controls(self, exclusions):
        blockers = [
            QSignalBlocker(self.txt_excluded_folders),
            QSignalBlocker(self.txt_excluded_extensions),
            QSignalBlocker(self.min_size_input),
            QSignalBlocker(self.min_size_unit),
        ]
        try:
            exclusions = exclusions or ScanExclusions()
            data = exclusions.to_dict()
            self.txt_excluded_folders.setText(", ".join(data["folder_names"]))
            self.txt_excluded_extensions.setText(", ".join(data["extensions"]))
            size = data["min_file_size_bytes"]
            if size <= 0:
                self.min_size_input.setValue(0)
                self.min_size_unit.setCurrentText("MB")
            else:
                for unit, multiplier in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
                    if size % multiplier == 0:
                        self.min_size_input.setValue(size // multiplier)
                        self.min_size_unit.setCurrentText(unit)
                        break
                else:
                    self.min_size_input.setValue(max(1, size // 1024))
                    self.min_size_unit.setCurrentText("KB")
        finally:
            del blockers

    def apply_default_browse_preset(self):
        self.rb_all.setChecked(True)
        self.rb_view_tree.setChecked(True)
        self.slider.setValue(self.AGE_FILTER_DISABLED)
        
        blocker = QSignalBlocker(self.age_input)
        self.age_input.setValue(self.AGE_FILTER_DISABLED)
        del blocker
        
        self.applied_age_value = self.AGE_FILTER_DISABLED
        self._update_age_label(self.AGE_FILTER_DISABLED)
        self._update_age_apply_state()
        self._update_popover_summaries()

    def eventFilter(self, obj, event):
        if (
            hasattr(self, "slider")
            and obj == self.slider
            and event.type() == QEvent.Type.Wheel
        ):
            event.ignore()
            return True
        if (
            obj == self.txt_search
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
        ):
            self.clear_search_text()
            return True
        return super().eventFilter(obj, event)

    def clear_search_text(self):
        self.txt_search.clear()
        self.searchCleared.emit()

    # helpers
    def _make_section(self, title):
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(SPACE_MD)
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
