import html
import os

from PyQt6.QtCore import (
    QEvent,
    QSettings,
    QSize,
    QTimer,
    Qt,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QPainter,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QSystemTrayIcon,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ...models import StorageScoutFilterProxyModel
from ...theme import SPACE_LG, SPACE_MD, SPACE_SM, SPACE_XL, SPACE_XS, apply_theme, current_palette, resolve_theme_name
from ..path_utils import IndexedPathDict
from ..widgets.controls import FilterPopover
from ..widgets.delegates import SizeBarDelegate, StatusDelegate, TreeRowDelegate
from ..widgets.filter_panel import FilterPanel
from ..widgets.folder_browser import FolderBrowserPanel


class _BaseMixin:
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Storage Scout")
        self.resize(1200, 720)

        self.scanner_thread = None
        self.delete_thread = None
        self.delete_progress = None
        self.page_load_thread = None
        self.page_load_threads = []
        self.page_load_request_id = 0
        self.lazy_child_threads = {}
        self.lazy_child_request_id = 0
        self.folder_browser_threads = {}
        self.folder_browser_request_id = 0
        self.folder_browser_scope = None
        self.folder_browser_live_refresh_at = 0.0
        self.totals_thread = None
        self.totals_threads = []
        self.totals_request_id = 0
        self.totals_refresh_pending = False
        self.totals_refresh_options = None
        self.current_page_total_thread = None
        self.selected_total_thread = None
        self.path_total_threads = []
        self.cached_folder_total = None
        self.cached_folder_total_root = None
        self.cached_selected_total = None
        self.is_scanning = False
        self.has_completed_scan = False
        self.hide_partial_scan_results = False
        self.folder_cache = None
        self.loading_dialog = None
        self.selection_loading_dialog = None
        self.selection_loading_min_visible_until = 0.0
        self.delete_preview_thread = None
        self.pending_delete_preview_dialog = None
        self.file_type_thread = None
        self.file_type_request_id = 0
        self.file_types_dialog = None
        self.export_thread = None
        self.export_progress = None
        self.export_target_path = None
        self.active_extension_filter = None
        self.applied_scan_exclusions = None
        self.scan_exclusions_in_progress = None
        self.settings = QSettings("StorageScout", "StorageScout")
        self.current_theme_name = resolve_theme_name(self.settings.value("theme", "light"))
        self.notifications_enabled = self.settings.value(
            "notifications_enabled",
            True,
            type=bool,
        )
        self.tray_icon = None
        self.bulk_select_thread = None
        self.bulk_select_request_id = 0
        self.bulk_select_active = False
        self.bulk_select_queue = []
        self.bulk_select_state = Qt.CheckState.Unchecked
        self.bulk_select_offer_all_pages = False
        self.bulk_select_current_page_count = 0
        self.bulk_select_label = ""
        self.bulk_select_status = None
        self.bulk_select_videos_only = False
        self.bulk_select_precomputed_scope = None
        self.bulk_select_requested_scope = 'current'
        self.bulk_select_forced_state = None
        self._preserve_results_focus = False
        self.applied_name_filter = ""
        self.source_index_by_path = {}
        self._selection_button_targets_cache = None
        self._selection_sync_suppressed = False
        self.bulk_select_batch_size = 250
        self.bulk_select_batch_delay_ms = 0
        self.tree_model     = None
        self.proxy_model    = StorageScoutFilterProxyModel()
        self.bulk_delete_scope = None
        self.selected_paths = IndexedPathDict()
        self.page_only_selected_paths = {}
        self.excluded_paths = IndexedPathDict()
        self.page_only_selection_page = None
        self.current_total_matches = 0
        self.current_lazy_show_all_tree = False
        self.current_filesystem_scope_fallback = False
        self.refresh_tree_state_key = None
        self.refresh_collapsed_tree_paths = set()
        self.last_scan_excluded_count = 0
        self.last_scan_elapsed_secs = 0.0
        self.last_scan_item_count = 0
        self.scan_progress_was_determinate = False

        self.sort_column = 3 # Default sort by Age
        self.sort_order = Qt.SortOrder.DescendingOrder

        self.recount_timer = QTimer(self)
        self.recount_timer.setSingleShot(True)
        self.recount_timer.timeout.connect(self._do_recount)

        self.search_debounce_timer = QTimer(self)
        self.search_debounce_timer.setSingleShot(True)
        self.search_debounce_timer.setInterval(275)
        self.search_debounce_timer.timeout.connect(self._on_filter_changed)

        self.loading_timer = QTimer(self)
        self.loading_timer.setSingleShot(True)
        self.loading_timer.setInterval(180)
        self.loading_timer.timeout.connect(self._show_loading_dialog)

        self.scan_refresh_timer = QTimer(self)
        self.scan_refresh_timer.setSingleShot(True)
        self.scan_refresh_timer.setInterval(1200)
        self.scan_refresh_timer.timeout.connect(self._refresh_pagination_only)

        self._build_ui()
        self._setup_tray_icon()
        QApplication.instance().installEventFilter(self)
        self.tree.header().sectionClicked.connect(self._on_header_sort_clicked)
        self._apply_sort_indicator()

    def _setup_tray_icon(self):
        self.tray_icon = None
        try:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                return
            icon_path = os.path.join(
                os.path.join(os.path.dirname(__file__), "..", ".."),
                "assets",
                "folder_blue.svg",
            )
            icon = QIcon(icon_path)
            if icon.isNull():
                return
            self.tray_icon = QSystemTrayIcon(icon, self)
            self.tray_icon.setToolTip("Storage Scout")
            self.tray_icon.show()
        except Exception:
            self.tray_icon = None

    def _should_show_completion_notification(self, elapsed_secs=None, cancelled=False):
        if cancelled or not getattr(self, 'notifications_enabled', True):
            return False
        if elapsed_secs is not None and elapsed_secs > 8:
            return True
        return not self.isActiveWindow()

    def _show_system_notification(self, title, message):
        tray_icon = getattr(self, 'tray_icon', None)
        if not getattr(self, 'notifications_enabled', True) or tray_icon is None:
            return False
        try:
            if not tray_icon.isVisible():
                return False
            tray_icon.showMessage(
                title,
                message,
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )
            return True
        except Exception:
            return False

    def _placeholder_icon(self, asset_name, muted=False):
        label = QLabel()
        label.setObjectName("emptyIcon")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_path = os.path.join(os.path.join(os.path.dirname(__file__), "..", ".."), "assets", asset_name)
        pixmap = QIcon(icon_path).pixmap(QSize(56, 56))
        if muted:
            painter = QPainter(pixmap)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(pixmap.rect(), QColor(current_palette()["text_muted"]))
            painter.end()
        label.setPixmap(pixmap)
        label.setFixedSize(80, 80)
        return label

    def _themed_toolbar_icon(self, asset_name, dark_color="#ffffff", light_color="#000000", size=18):
        icon_path = os.path.join(os.path.join(os.path.dirname(__file__), "..", ".."), "assets", asset_name)
        pixmap = QIcon(icon_path).pixmap(QSize(size, size))
        painter = QPainter(pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(
            pixmap.rect(),
            QColor(dark_color if self.current_theme_name == "dark" else light_color),
        )
        painter.end()
        return QIcon(pixmap)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        app_layout = QVBoxLayout(root)
        app_layout.setContentsMargins(0, 0, 0, 0)
        app_layout.setSpacing(0)

        self.app_menu = QFrame()
        self.app_menu.setObjectName("appMenu")
        self.app_menu.setFixedHeight(58)
        menu_layout = QHBoxLayout(self.app_menu)
        menu_layout.setContentsMargins(SPACE_LG, SPACE_XS, SPACE_LG, SPACE_SM)
        menu_layout.setSpacing(SPACE_SM)

        workspace = QWidget()
        workspace.setObjectName("workspace")
        vbox = QVBoxLayout(workspace)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)
        app_layout.addWidget(workspace, 1)

        # Top bar
        self.topbar = QWidget()
        self.topbar.setObjectName("topbar")
        tb = QGridLayout(self.topbar)
        tb.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_LG, SPACE_SM)
        tb.setHorizontalSpacing(SPACE_MD)
        tb.setVerticalSpacing(0)
        tb.setColumnMinimumWidth(0, 170)
        tb.setColumnMinimumWidth(2, 170)
        tb.setColumnStretch(0, 1)
        tb.setColumnStretch(2, 1)

        self.brand_block = QFrame()
        self.brand_block.setObjectName("brandBlock")
        self.brand_block.setFixedWidth(170)
        brand_layout = QHBoxLayout(self.brand_block)
        brand_layout.setContentsMargins(0, SPACE_XS, SPACE_SM, SPACE_XS)
        brand_layout.setSpacing(0)
        self.lbl_app_title = QLabel("Storage Scout")
        self.lbl_app_title.setObjectName("appTitle")
        brand_layout.addWidget(self.lbl_app_title)
        tb.addWidget(
            self.brand_block,
            0,
            0,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )

        self.path_action_layout = QHBoxLayout()
        path_action_layout = self.path_action_layout
        path_action_layout.setContentsMargins(0, 0, 0, 0)
        path_action_layout.setSpacing(SPACE_SM)

        self.path_input_shell = QFrame()
        self.path_input_shell.setObjectName("browsePathShell")
        self.path_input_shell.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.path_input_shell.setMinimumWidth(280)
        self.path_input_shell.setFixedHeight(44)
        path_input_layout = QHBoxLayout(self.path_input_shell)
        path_input_layout.setContentsMargins(SPACE_MD, 0, SPACE_SM, 0)
        path_input_layout.setSpacing(SPACE_SM)

        self.txt_path = QLineEdit()
        self.txt_path.setObjectName("browsePathInput")
        self.txt_path.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.txt_path.setMinimumWidth(0)
        self.txt_path.setFixedHeight(36)
        self.txt_path.setPlaceholderText("Choose a folder to scan")
        self.txt_path.setToolTip("Selected folder path")
        self.txt_path.setReadOnly(True)
        self.txt_path.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.txt_path.textChanged.connect(self._on_path_display_changed)
        path_input_layout.addWidget(self.txt_path)

        self.btn_browse = QPushButton("Browse")
        self.btn_browse.setObjectName("browsePathBtn")
        self.btn_browse.setToolTip("Browse folder")
        self.btn_browse.setAccessibleName("Browse folder")
        self.btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_browse.setFixedHeight(32)
        self.btn_browse.clicked.connect(self._browse)
        path_input_layout.addWidget(self.btn_browse)

        path_action_layout.addWidget(self.path_input_shell)

        self.btn_rescan = QPushButton("Rescan")
        self.btn_rescan.setObjectName("rescanBtn")
        self.btn_rescan.setToolTip("Scan the selected folder again")
        self.btn_rescan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_rescan.setFixedSize(80, 32)
        self.btn_rescan.setIcon(
            QIcon(os.path.join(os.path.join(os.path.dirname(__file__), "..", ".."), "assets", "toolbar_refresh_green.svg"))
        )
        self.btn_rescan.setIconSize(QSize(18, 18))
        self.btn_rescan.clicked.connect(self.start_scan)
        path_action_layout.addWidget(self.btn_rescan)
        tb.addLayout(
            path_action_layout,
            0,
            1,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )

        self.btn_folder_browser = QPushButton("Folders Panel")
        self.btn_folder_browser.setObjectName("filterBtn")
        self.btn_folder_browser.setCheckable(True)
        self.btn_folder_browser.setChecked(True)
        self.btn_folder_browser.setToolTip("Show or hide folder navigation")
        self.btn_folder_browser.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_folder_browser.setIcon(
            QIcon(os.path.join(os.path.join(os.path.dirname(__file__), "..", ".."), "assets", "folder_blue.svg"))
        )
        self.btn_folder_browser.setIconSize(QSize(18, 18))
        self.btn_folder_browser.setFixedHeight(38)
        self.btn_folder_browser.setMinimumWidth(138)
        self.btn_folder_browser.clicked.connect(self._toggle_folder_browser)
        self.btn_folder_browser.setProperty("menuItem", True)
        menu_layout.addWidget(self.btn_folder_browser)

        self.btn_filter = QPushButton("Filters")
        self.btn_filter.setObjectName("ghostBtn")
        self.btn_filter.setCheckable(True)
        self.btn_filter.setToolTip("Toggle filter bar (Alt+F)")
        self.btn_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_filter.setIcon(
            QIcon(os.path.join(os.path.join(os.path.dirname(__file__), "..", ".."), "assets", "toolbar_filters_sliders.svg"))
        )
        self.btn_filter.setIconSize(QSize(18, 18))
        self.btn_filter.setFixedHeight(38)
        self.btn_filter.clicked.connect(self._toggle_filters)
        self.btn_filter.setObjectName("menuFilterBtn")
        self.btn_filter.setProperty("menuItem", True)
        menu_layout.addWidget(self.btn_filter)

        self.btn_file_types = QPushButton("File Extensions")
        self.btn_file_types.setObjectName("filterBtn")
        self.btn_file_types.setCheckable(True)
        self.btn_file_types.setToolTip("Show disk usage by file extension")
        self.btn_file_types.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_file_types.setIcon(
            QIcon(os.path.join(os.path.join(os.path.dirname(__file__), "..", ".."), "assets", "file_blue.svg"))
        )
        self.btn_file_types.setIconSize(QSize(18, 18))
        self.btn_file_types.setFixedHeight(38)
        self.btn_file_types.setEnabled(False)
        self.btn_file_types.clicked.connect(self._show_file_types)
        self.btn_file_types.setProperty("menuItem", True)
        menu_layout.addWidget(self.btn_file_types)

        self.btn_exclusions = QPushButton("Exclusions")
        self.btn_exclusions.setObjectName("filterBtn")
        self.btn_exclusions.setCheckable(True)
        self.btn_exclusions.setToolTip("Choose files and folders to ignore during a scan")
        self.btn_exclusions.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_exclusions.setIcon(self._themed_toolbar_icon("toolbar_shield_lock.svg"))
        self.btn_exclusions.setIconSize(QSize(18, 18))
        self.btn_exclusions.setFixedHeight(38)
        self.btn_exclusions.setMinimumWidth(118)
        self.btn_exclusions.setSizePolicy(
            QSizePolicy.Policy.Minimum,
            QSizePolicy.Policy.Fixed,
        )
        self.btn_exclusions.clicked.connect(self._toggle_exclusions_popup)
        self.btn_exclusions.setProperty("menuItem", True)
        menu_layout.addWidget(self.btn_exclusions)

        self.btn_theme_toggle = QPushButton()
        self.btn_theme_toggle.setObjectName("themeToggleBtn")
        self.btn_theme_toggle.setToolTip("Toggle color scheme")
        self.btn_theme_toggle.setAccessibleName("Toggle color scheme")
        self.btn_theme_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_theme_toggle.setFixedSize(38, 38)
        self.btn_theme_toggle.clicked.connect(self._on_theme_changed)
        tb.addWidget(
            self.btn_theme_toggle,
            0,
            2,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

        self.btn_expand = QPushButton("Expand All")
        self.btn_expand.setObjectName("collapseAll")
        self.btn_expand.setCheckable(True)
        self.btn_expand.setToolTip("Expand or collapse all folders in the view")
        self.btn_expand.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_expand.clicked.connect(self._toggle_expand)

        menu_layout.addStretch()

        self.btn_export = QPushButton("Export CSV")
        self.btn_export.setObjectName("ghostBtn")
        self.btn_export.setToolTip("Export files, summaries, audit data, or scan history to CSV")
        self.btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_export.setIcon(
            QIcon(os.path.join(os.path.join(os.path.dirname(__file__), "..", ".."), "assets", "toolbar_download.svg"))
        )
        self.btn_export.setIconSize(QSize(18, 18))
        self.btn_export.setFixedHeight(38)
        self.btn_export.setFixedWidth(138)
        self.btn_export.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.btn_export.clicked.connect(self._export_csv)
        self.btn_export.setProperty("menuItem", True)
        self.btn_export.setProperty("menuAction", True)
        menu_layout.addWidget(self.btn_export)

        self.btn_delete = QPushButton("Delete Selected")
        self.btn_delete.setObjectName("deleteBtn")
        self.btn_delete.setToolTip("Select items to move to the Recycle Bin")
        self.btn_delete.setAccessibleDescription(
            "Select items to move to the Recycle Bin"
        )
        self.btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete_icon_path = os.path.join(
            os.path.join(os.path.dirname(__file__), "..", ".."),
            "assets",
            "toolbar_trash.svg",
        )
        self._delete_icon_active_path = os.path.join(
            os.path.join(os.path.dirname(__file__), "..", ".."),
            "assets",
            "toolbar_trash_white.svg",
        )
        self.btn_delete.setIcon(QIcon(self._delete_icon_path))
        self.btn_delete.setIconSize(QSize(18, 18))
        self.btn_delete.setFixedHeight(38)
        self.btn_delete.setFixedWidth(176)
        self.btn_delete.clicked.connect(self._delete_selected)
        self._set_delete_armed(False)
        self.btn_delete.setEnabled(False)
        self.btn_delete.setProperty("menuItem", True)
        self.btn_delete.setProperty("menuAction", True)
        menu_layout.addWidget(self.btn_delete)
        self._update_theme_toggle_ui()
        vbox.addWidget(self.topbar)
        vbox.addWidget(self.app_menu)
        self._update_topbar_responsive_typography()

        # Main Content Area (resizable folder browser + content)
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setObjectName("mainSplitter")
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(3)

        # Left sidebar (folder browser only)
        self.folder_browser = FolderBrowserPanel()
        self.folder_browser.closeRequested.connect(self._toggle_folder_browser)
        self.folder_browser.scopeChanged.connect(self._on_folder_browser_scope_changed)
        self.folder_browser.loadRequested.connect(self._load_folder_browser_children)
        self.main_splitter.addWidget(self.folder_browser)

        self.fp = FilterPanel()
        self._filter_panel_width = self.fp.minimumWidth()
        self.fp.btn_close.clicked.connect(self._toggle_filters)
        self.fp.btn_reset.clicked.connect(self._reset_filters)
        # Dynamic filtering
        self.fp.bg.buttonClicked.connect(lambda _btn: self._on_filter_changed())
        self.fp.rb_videos.toggled.connect(self._on_videos_mode_toggled)
        self.fp.txt_search.returnPressed.connect(self._on_search_submitted)
        self.fp.searchCleared.connect(self._clear_search_filter)
        self.fp.btn_apply_age.clicked.connect(self._on_age_filter_apply_clicked)
        self.fp.exclusionChanged.connect(self._on_scan_exclusions_changed)
        self.fp.btn_rescan_exclusions.clicked.connect(self._rescan_from_exclusions)

        exclusions_section = self.fp.btn_exclusions_toggle.parentWidget()
        exclusions_section.setVisible(False)
        self.exclusions_popup_content = QWidget()
        exclusions_popup_layout = QVBoxLayout(self.exclusions_popup_content)
        exclusions_popup_layout.setContentsMargins(0, 0, 0, 0)
        exclusions_popup_layout.setSpacing(SPACE_XS)

        exclusions_title_row = QHBoxLayout()
        exclusions_title_row.setContentsMargins(0, 0, 0, 0)
        exclusions_title_row.setSpacing(SPACE_SM)
        exclusions_popup_title = QLabel("Exclusions")
        exclusions_popup_title.setObjectName("filterDrawerTitle")
        exclusions_title_row.addWidget(exclusions_popup_title)
        exclusions_title_row.addStretch(1)
        self.lbl_exclusions_rule_count = QLabel()
        self.lbl_exclusions_rule_count.setObjectName("exclusionsRuleCount")
        exclusions_title_row.addWidget(self.lbl_exclusions_rule_count)
        exclusions_popup_layout.addLayout(exclusions_title_row)

        exclusions_status_row = QHBoxLayout()
        exclusions_status_row.setContentsMargins(0, 0, 0, 0)
        exclusions_status_row.setSpacing(SPACE_SM)
        self.exclusions_popup_subtitle = QLabel(
            "Changes take effect after a re-scan."
        )
        self.exclusions_popup_subtitle.setObjectName("filterDrawerSubtitle")
        self.exclusions_popup_subtitle.setWordWrap(False)
        exclusions_status_row.addWidget(self.exclusions_popup_subtitle)
        exclusions_status_row.addStretch(1)
        self.lbl_exclusions_pending = QLabel("Re-scan required")
        self.lbl_exclusions_pending.setObjectName("exclusionsPendingBadge")
        self.lbl_exclusions_pending.setVisible(False)
        exclusions_status_row.addWidget(self.lbl_exclusions_pending)
        exclusions_popup_layout.addLayout(exclusions_status_row)
        exclusions_popup_layout.addSpacing(SPACE_SM)
        exclusions_popup_layout.addWidget(self.fp.exclusions_box)
        self.fp.prepare_exclusions_popup_layout()
        self.exclusions_popup = FilterPopover(380, self)
        self.exclusions_popup.set_content(self.exclusions_popup_content)
        self.exclusions_popup.closed.connect(
            lambda: self.btn_exclusions.setChecked(False)
        )
        self._update_exclusions_indicator()
        
        # View mode connections
        self.fp.rb_view_tree.toggled.connect(self._on_filter_changed)
        self.fp.rb_view_files.toggled.connect(self._on_filter_changed)
        self.fp.rb_view_folders.toggled.connect(self._on_filter_changed)
        
        self._apply_default_browse_preset(apply_now=False)
        self._update_age_controls_enabled()
        self._update_expand_control_visibility()
        # Right Content Area
        self.right_content = QWidget()
        self.right_content.setObjectName("contentArea")
        right_v = QVBoxLayout(self.right_content)
        right_v.setContentsMargins(SPACE_XL, SPACE_MD, SPACE_XL, SPACE_MD)
        right_v.setSpacing(SPACE_SM)

        # Controls row (above tree): expand / select all
        self.controls_bar = QWidget()
        controls_layout = QHBoxLayout(self.controls_bar)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(SPACE_XS)
        self.btn_expand.setObjectName("selectionControlBtn")
        self.btn_expand.setFixedSize(112, 34)
        controls_layout.addWidget(self.btn_expand)
        self.btn_select_all = QPushButton("Select All")
        self.btn_select_all.setObjectName("selectionControlBtn")
        self.btn_select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select_all.setFixedSize(112, 34)
        self.btn_select_all.clicked.connect(self._toggle_select_all)
        controls_layout.addWidget(self.btn_select_all)

        self.btn_current_page_selection = QPushButton("Select Current Page")
        self.btn_current_page_selection.setObjectName("selectionControlBtn")
        self.btn_current_page_selection.setToolTip("Select every item shown on this page")
        self.btn_current_page_selection.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_current_page_selection.setFixedSize(164, 34)
        self.btn_current_page_selection.setVisible(False)
        self.btn_current_page_selection.clicked.connect(self._toggle_current_page_selection)
        controls_layout.addWidget(self.btn_current_page_selection)

        self.btn_clear_selection = QPushButton("Unselect All")
        self.btn_clear_selection.setObjectName("selectionControlBtn")
        self.btn_clear_selection.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_selection.setFixedSize(124, 34)
        self.btn_clear_selection.clicked.connect(self._unselect_all)
        self.btn_clear_selection.setVisible(False)
        
        self.btn_select_inactive = QPushButton("Select All Inactive")
        self.btn_select_inactive.setObjectName("selectionControlBtn")
        self.btn_select_inactive.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select_inactive.setFixedSize(160, 34)
        self.btn_select_inactive.clicked.connect(self._select_inactive)
        self.btn_select_inactive.setEnabled(False)
        controls_layout.addWidget(self.btn_select_inactive)

        self.btn_select_empty = QPushButton("Select All Empty")
        self.btn_select_empty.setObjectName("selectionControlBtn")
        self.btn_select_empty.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select_empty.setFixedSize(144, 34)
        self.btn_select_empty.setToolTip("No empty folders are available in the current view.")
        self.btn_select_empty.clicked.connect(self._select_empty)
        self.btn_select_empty.setEnabled(False)
        controls_layout.addWidget(self.btn_select_empty)

        controls_layout.addStretch()

        self.btn_prev_page = QPushButton("<")
        self.btn_prev_page.setObjectName("pageNavBtn")
        self.btn_prev_page.setFixedSize(30, 30)
        self.btn_prev_page.setToolTip("Previous Page")
        self.btn_prev_page.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_prev_page.clicked.connect(self._prev_page)
        controls_layout.addWidget(self.btn_prev_page)

        self.lbl_page_info = QLabel("Page 1")
        self.lbl_page_info.setObjectName("pageInfo")
        self.lbl_page_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_page_info.setMinimumWidth(150)
        self.lbl_page_info.setFixedHeight(30)
        controls_layout.addWidget(self.lbl_page_info)

        self.btn_next_page = QPushButton(">")
        self.btn_next_page.setObjectName("pageNavBtn")
        self.btn_next_page.setFixedSize(30, 30)
        self.btn_next_page.setToolTip("Next Page")
        self.btn_next_page.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next_page.clicked.connect(self._next_page)
        controls_layout.addWidget(self.btn_next_page)

        self.type_filter_banner = QFrame()
        self.type_filter_banner.setObjectName("activeTypeFilter")
        type_filter_layout = QHBoxLayout(self.type_filter_banner)
        type_filter_layout.setContentsMargins(
            SPACE_MD,
            SPACE_XS,
            SPACE_MD,
            SPACE_XS,
        )
        type_filter_layout.setSpacing(SPACE_SM)

        type_filter_title = QLabel("File type filter")
        type_filter_title.setObjectName("activeTypeFilterLabel")
        type_filter_layout.addWidget(type_filter_title)

        self.lbl_active_type_filter = QLabel()
        self.lbl_active_type_filter.setObjectName("activeTypeFilterValue")
        self.lbl_active_type_filter.setWordWrap(False)
        type_filter_layout.addWidget(self.lbl_active_type_filter)

        self.lbl_active_type_filter_meta = QLabel()
        self.lbl_active_type_filter_meta.setObjectName("activeTypeFilterMeta")
        self.lbl_active_type_filter_meta.setWordWrap(False)
        type_filter_layout.addWidget(self.lbl_active_type_filter_meta)
        type_filter_layout.addStretch()

        self.btn_clear_type_filter = QPushButton("Clear type")
        self.btn_clear_type_filter.setObjectName("activeTypeFilterClear")
        self.btn_clear_type_filter.setToolTip("Clear the selected file type")
        self.btn_clear_type_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_type_filter.clicked.connect(self._clear_type_filter)
        type_filter_layout.addWidget(self.btn_clear_type_filter)

        self.type_filter_banner.setVisible(False)
        right_v.addWidget(self.type_filter_banner)

        self.controls_bar.setVisible(False)
        right_v.addWidget(self.controls_bar)

        # Empty state + Tree + Scanning state wrapped in a stacked widget
        self.content_stack = QStackedWidget()
        self.content_stack.setMinimumHeight(0)
        self.content_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Page 0: Empty state
        empty_page = QWidget()
        empty_page.setObjectName("emptyState")
        ep_layout = QVBoxLayout(empty_page)
        ep_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ep_layout.setSpacing(SPACE_LG)

        empty_icon = self._placeholder_icon("folder_blue.svg")

        title_lbl = QLabel("Choose a folder to scan")
        title_lbl.setObjectName("emptyTitle")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub_lbl = QLabel("Find old files, empty folders, videos, and large cleanup targets in one place.")
        sub_lbl.setObjectName("emptySub")
        sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub_lbl.setWordWrap(True)
        sub_lbl.setMaximumWidth(420)

        btn_browse_cta = QPushButton("Browse folder...")
        btn_browse_cta.setObjectName("emptyBrowseBtn")
        btn_browse_cta.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_browse_cta.setFixedWidth(168)
        btn_browse_cta.clicked.connect(self._browse)

        ep_layout.addStretch()
        ep_layout.addWidget(empty_icon, alignment=Qt.AlignmentFlag.AlignCenter)
        ep_layout.addWidget(title_lbl)
        ep_layout.addWidget(sub_lbl, alignment=Qt.AlignmentFlag.AlignCenter)
        ep_layout.addSpacing(SPACE_SM)
        ep_layout.addWidget(btn_browse_cta, alignment=Qt.AlignmentFlag.AlignCenter)
        ep_layout.addStretch()

        # Page 1: Scanning state
        scanning_page = QWidget()
        scanning_page.setObjectName("emptyState")
        sp_layout = QVBoxLayout(scanning_page)
        sp_layout.setContentsMargins(56, 56, 56, 56)
        sp_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sp_layout.setSpacing(0)

        scan_content = QWidget()
        scan_content.setObjectName("scanStateContent")
        scan_content.setMaximumWidth(720)
        scan_content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        scan_layout = QVBoxLayout(scan_content)
        scan_layout.setContentsMargins(0, 0, 0, 0)
        scan_layout.setSpacing(14)

        scan_icon = self._placeholder_icon("toolbar_refresh.svg", muted=True)

        sp_title = QLabel("Scanning in progress")
        sp_title.setObjectName("emptyTitle")
        sp_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.scan_detail = QLabel("Preparing the index and waiting for the first batch of results.")
        self.scan_detail.setObjectName("emptySub")
        self.scan_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scan_detail.setWordWrap(True)
        self.scan_detail.setMaximumWidth(680)
        self.scan_detail.setMinimumHeight(26)

        self.scan_stats = QLabel("Preparing scan...")
        self.scan_stats.setObjectName("scanStats")
        self.scan_stats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scan_stats.setWordWrap(False)
        self.scan_stats.setMaximumWidth(900)
        self.scan_stats.setMinimumHeight(24)

        self.scan_path = QLabel()
        self.scan_path.setObjectName("scanPathValue")
        self.scan_path.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scan_path.setWordWrap(True)
        self.scan_path.setMaximumWidth(700)
        self.scan_path.setMinimumHeight(46)
        self.scan_path.setTextFormat(Qt.TextFormat.RichText)
        self.scan_path.setVisible(False)

        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setTextVisible(False)
        self.scan_progress.setObjectName("loadingBar")
        self.scan_progress.setFixedWidth(480)
        self.scan_progress.setFixedHeight(12)

        sp_layout.addStretch()
        scan_layout.addWidget(scan_icon, alignment=Qt.AlignmentFlag.AlignCenter)
        scan_layout.addWidget(sp_title)
        scan_layout.addWidget(self.scan_detail, alignment=Qt.AlignmentFlag.AlignCenter)
        scan_layout.addSpacing(12)
        scan_layout.addWidget(self.scan_progress, alignment=Qt.AlignmentFlag.AlignCenter)
        scan_layout.addWidget(self.scan_stats, alignment=Qt.AlignmentFlag.AlignCenter)
        sp_layout.addWidget(scan_content, alignment=Qt.AlignmentFlag.AlignCenter)
        sp_layout.addStretch()

        # Page 1: Tree view
        self.tree = QTreeView()
        self.tree.setMinimumHeight(0)
        self.tree.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.tree.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tree.setAlternatingRowColors(False)
        self.tree.setSortingEnabled(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(False)
        self.tree.setRootIsDecorated(True)
        self.tree.setItemsExpandable(True)
        self.tree.setExpandsOnDoubleClick(True)
        self.tree.setIndentation(16)
        self.tree.setMouseTracking(True)
        self.tree.viewport().setMouseTracking(True)
        self.tree.setItemDelegate(TreeRowDelegate(self.tree))
        self.tree.setItemDelegateForColumn(4, SizeBarDelegate(self.tree))
        self.tree.setItemDelegateForColumn(5, StatusDelegate(self.tree))
        self.tree.clicked.connect(self._on_click)
        self.tree.doubleClicked.connect(self._on_double_click)
        self.tree.expanded.connect(self._on_tree_expanded)
        self.tree.collapsed.connect(self._on_tree_collapsed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self.tree.viewport().installEventFilter(self)
        self.tree.setIconSize(QSize(18, 18))
        hdr = self.tree.header()
        hdr.setSectionsMovable(False)
        hdr.setStretchLastSection(False)
        hdr.setSectionsClickable(True)
        try:
            hdr.setSortIndicatorShown(False)
        except Exception:
            pass
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_status_column_visibility()

        # Page 2: No results (filter produced zero matches)
        no_results_page = QWidget()
        no_results_page.setObjectName("emptyState")
        nr_layout = QVBoxLayout(no_results_page)
        nr_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nr_layout.setSpacing(SPACE_LG)
        nr_title = QLabel("No matching items")
        nr_title.setObjectName("noResultsTitle")
        nr_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.nr_sub = QLabel("Try adjusting your filters or age threshold.")
        self.nr_sub.setObjectName("noResultsSub")
        self.nr_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.nr_sub.setWordWrap(True)
        self.nr_sub.setMaximumWidth(400)
        btn_reset_nr = QPushButton("Reset filters")
        btn_reset_nr.setObjectName("primaryBtn")
        btn_reset_nr.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_reset_nr.setFixedWidth(160)
        btn_reset_nr.clicked.connect(self._reset_filters)
        nr_layout.addStretch()
        nr_layout.addWidget(nr_title)
        nr_layout.addWidget(self.nr_sub, alignment=Qt.AlignmentFlag.AlignCenter)
        nr_layout.addSpacing(SPACE_MD)
        nr_layout.addWidget(btn_reset_nr, alignment=Qt.AlignmentFlag.AlignCenter)
        nr_layout.addStretch()

        self.content_stack.addWidget(empty_page)      # index 0
        self.content_stack.addWidget(self.tree)       # index 1
        self.content_stack.addWidget(no_results_page) # index 2
        self.content_stack.addWidget(scanning_page)   # index 3

        self.content_stack.setCurrentIndex(0)

        # Wrap content_stack in container so we can overlay the floating button
        self.tree_container = QWidget()
        self.tree_container.setObjectName("tableCard")
        self.tree_container.setMinimumHeight(0)
        self.tree_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        tc_layout = QVBoxLayout(self.tree_container)
        tc_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        tc_layout.setContentsMargins(SPACE_XS, SPACE_XS, SPACE_XS, SPACE_XS) # Internal border gap
        tc_layout.setSpacing(0)
        tc_layout.addWidget(self.content_stack)


        # Floating scroll-to-top button (child of tree_container for z-order)
        self.btn_scroll_top = QPushButton("^")
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
        self.main_splitter.addWidget(self.right_content)
        self.main_splitter.addWidget(self.fp)
        self.main_splitter.setCollapsible(0, False)
        self.main_splitter.setCollapsible(1, False)
        self.main_splitter.setCollapsible(2, False)
        self.main_splitter.splitterMoved.connect(self._remember_filter_panel_width)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        self.main_splitter.setSizes([self.folder_browser.minimumWidth(), 1000, 0])

        content_shell = QWidget()
        content_shell.setObjectName("contentShell")
        content_shell_layout = QHBoxLayout(content_shell)
        content_shell_layout.setContentsMargins(0, 0, 0, 0)
        content_shell_layout.setSpacing(0)
        content_shell_layout.addWidget(self.main_splitter, 1)
        vbox.addWidget(content_shell, 1)

        # Status bar
        sb = QWidget()
        sb.setObjectName("statusbar")
        sb.setFixedHeight(40)
        sbl = QHBoxLayout(sb)
        sbl.setContentsMargins(SPACE_MD, SPACE_XS, SPACE_MD, SPACE_XS)
        sbl.setSpacing(SPACE_SM)
        self.lbl_status = QLabel("Ready - select a folder and click Re-scan")
        self.lbl_status.setObjectName("statusMessage")

        sbl.addWidget(self.lbl_status)
        sbl.addStretch()
        
        self.chip_empty = self._chip("Empty: 0 items", "chipEmpty")
        self.chip_inactive_folders = self._chip(
            "Inactive: 0 folders · 0 files",
            "chipInactive",
        )
        self.chip_inactive_files = self.chip_inactive_folders
        self.chip_browse_size = self._chip("Total Size: --", "chipSpace")
        self.chip_folder_size = self._chip("Folder Size --", "chipSpace")
        self.chip_folder_size.setVisible(False)
        self.chip_filtered_size = self._chip("Filtered Size --", "chipSpace")
        self.chip_filtered_size.setVisible(False)
        self.chip_page_size = self._chip("Current Page Size: --", "chipSpace")
        self.chip_selected_size = self._chip(
            "Selected: 0 files, 0 folders · 0 B",
            "chipSpace",
        )

        sbl.addWidget(self.chip_empty)
        sbl.addWidget(self.chip_inactive_folders)
        sbl.addWidget(self.chip_browse_size)
        sbl.addWidget(self.chip_folder_size)
        sbl.addWidget(self.chip_filtered_size)
        sbl.addWidget(self.chip_page_size)
        sbl.addWidget(self.chip_selected_size)
        self._update_status_metrics_visibility()
        vbox.addWidget(sb)

    def _on_theme_changed(self):
        next_theme = "dark" if self.current_theme_name == "light" else "light"
        self.current_theme_name = resolve_theme_name(next_theme)
        self.settings.setValue("theme", self.current_theme_name)
        apply_theme(QApplication.instance(), self.current_theme_name)
        self._update_theme_toggle_ui()
        if hasattr(self, 'fp'):
            self.fp._update_age_label(self.fp.age_input.value())
        if self.scanner_thread and self.scanner_thread.isRunning():
            self._set_rescan_stop_ui()
        if hasattr(self, 'tree'):
            self._update_status_column_visibility()
            self.tree.viewport().update()
            self.tree.header().viewport().update()
        if self.file_types_dialog:
            self.file_types_dialog.update()

    def _update_theme_toggle_ui(self):
        if not hasattr(self, 'btn_theme_toggle'):
            return
        icon_name = "theme_moon.svg" if self.current_theme_name == "light" else "theme_sun.svg"
        icon_path = os.path.join(os.path.join(os.path.dirname(__file__), "..", ".."), "assets", icon_name)
        self.btn_theme_toggle.setIcon(QIcon(icon_path))
        self.btn_theme_toggle.setIconSize(QSize(22, 22))
        if hasattr(self, "btn_exclusions"):
            self.btn_exclusions.setIcon(self._themed_toolbar_icon("toolbar_shield_lock.svg"))
            self.btn_exclusions.setIconSize(QSize(18, 18))
        button_label = "Switch to dark mode" if self.current_theme_name == "light" else "Switch to light mode"
        self.btn_theme_toggle.setToolTip(button_label)
        self.btn_theme_toggle.setAccessibleName(button_label)

    def _set_rescan_idle_ui(self):
        if not hasattr(self, 'btn_rescan'):
            return
        self.btn_rescan.setObjectName("rescanBtn")
        self.btn_rescan.setText("Rescan")
        self.btn_rescan.setToolTip("Scan the selected folder again")
        self.btn_rescan.setIcon(
            QIcon(os.path.join(os.path.join(os.path.dirname(__file__), "..", ".."), "assets", "toolbar_refresh_green.svg"))
        )
        self.btn_rescan.setIconSize(QSize(18, 18))
        self.btn_rescan.style().unpolish(self.btn_rescan)
        self.btn_rescan.style().polish(self.btn_rescan)

    def _set_rescan_stop_ui(self):
        if not hasattr(self, 'btn_rescan'):
            return
        self.btn_rescan.setObjectName("stopScanBtn")
        self.btn_rescan.setText("Stop")
        self.btn_rescan.setToolTip("Stop the active scan")
        self.btn_rescan.setIcon(
            QIcon(os.path.join(os.path.join(os.path.dirname(__file__), "..", ".."), "assets", "toolbar_stop_red.svg"))
        )
        self.btn_rescan.setIconSize(QSize(18, 18))
        self.btn_rescan.style().unpolish(self.btn_rescan)
        self.btn_rescan.style().polish(self.btn_rescan)

    def _sep(self):
        l = QLabel("-")
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
        l.setToolTip(text)
        if obj_name == "chipSpace":
            l.setMinimumWidth(112)
            l.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        return l

    def _set_chip_text(self, chip, text):
        chip.setText(text)
        chip.setToolTip(text)

    def _format_chip_size(self, size):
        from ...models import format_size

        if size is None:
            return "--"
        if isinstance(size, str):
            return size
        if size == 0:
            return "0 B"
        return format_size(int(size))

    def _set_total_summary_chip(self, text):
        if text is None:
            display_text = "Total Size: --"
        elif isinstance(text, (int, float)):
            display_text = f"Total Size: {self._format_chip_size(int(text))}"
        else:
            display_text = text if str(text).startswith("Total Size: ") else f"Total Size: {text}"
        self._set_chip_text(self.chip_browse_size, display_text)

    def _known_browse_total(self):
        root_path = getattr(self, 'current_scan_root', None)
        root_key = self._path_key(root_path) if root_path else None
        if (
            root_key
            and root_key == self.cached_folder_total_root
            and self.cached_folder_total is not None
        ):
            return self.cached_folder_total

        cache = getattr(self, 'folder_cache', None)
        if cache is None:
            return None
        if root_path and hasattr(cache, 'folder_metadata'):
            metadata = cache.folder_metadata(root_path)
            if metadata is not None:
                return metadata[0]
        return getattr(cache, 'running_total_size', None)

    def _set_scanning_total_chip(self):
        running_total = 0
        if self.folder_cache is not None:
            running_total = getattr(self.folder_cache, "running_total_size", 0) or 0
        self._set_chip_text(self.chip_browse_size, f"Scanning… {self._format_chip_size(running_total)} so far")

        self.chip_folder_size.setVisible(False)
        self.chip_filtered_size.setVisible(False)

    def _set_selected_summary_chip(self, size_text=None, folder_count=None, file_count=None):
        if self.is_scanning:
            self.chip_selected_size.setVisible(False)
            return

        size_part = "--" if size_text is None else size_text
        folders_part = "--" if folder_count is None else f"{folder_count:,}"
        files_part = "--" if file_count is None else f"{file_count:,}"
        text = (
            f"Selected: {files_part} files, {folders_part} folders · {size_part}"
        )
        self._set_chip_text(self.chip_selected_size, text)
        self.chip_selected_size.setVisible(True)

    def _set_empty_result_metrics(self):
        self.totals_request_id += 1
        self._cancel_running_totals_thread()
        self.totals_refresh_pending = False
        self.totals_refresh_options = None
        self._set_chip_text(self.chip_empty, "Empty: 0 items")
        self._set_chip_text(
            self.chip_inactive_folders,
            "Inactive: 0 folders · 0 files",
        )
        self._set_chip_text(self.chip_page_size, "Current Page Size: 0 B")
        self.cached_selected_total = 0
        self._set_selected_summary_chip("0 B", 0, 0)
        if self.active_extension_filter is not None:
            extension = self.active_extension_filter or "(no extension)"
            self._set_chip_text(
                self.chip_filtered_size,
                f"Type {extension} Size 0 B",
            )
            self.chip_filtered_size.setVisible(True)
        elif self.applied_name_filter:
            self._set_chip_text(self.chip_filtered_size, "Search Size 0 B")
            self.chip_filtered_size.setVisible(True)
        else:
            self.chip_filtered_size.setVisible(False)

    def _scan_path_html(self, path):
        palette = current_palette()
        if not path:
            return f"<span style='color:{palette['text_muted']};'>Current folder<br>--</span>"

        escaped = html.escape(path)
        for separator in ("\\", "/", "_", "-", "."):
            escaped = escaped.replace(separator, f"{separator}<wbr>")
        return (
            f"<span style='font-weight:600;color:{palette['text_muted']};'>Current folder</span>"
            f"<br><span style='color:{palette['text_muted']};'>{escaped}</span>"
        )

    def _set_scanning_panel(self, path=None, detail=None):
        if hasattr(self, 'scan_path'):
            self.scan_path.setText(self._scan_path_html(path))
            self.scan_path.setToolTip(path or "")
        if detail and hasattr(self, 'scan_detail'):
            self.scan_detail.setText(detail)

    def _format_scan_duration(self, seconds):
        seconds = max(0.0, float(seconds or 0.0))
        if seconds < 10:
            if seconds < 1:
                return f"{seconds:.2f}s"
            return f"{seconds:.1f}s"
        whole_seconds = int(seconds)
        if whole_seconds < 60:
            return f"{whole_seconds}s"
        hours = whole_seconds // 3600
        minutes = (whole_seconds % 3600) // 60
        secs = whole_seconds % 60
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    def _scan_stats_text(self, detail=None):
        if not detail:
            return "Preparing the index and waiting for the first batch of results."
        elapsed = 0.0 if not detail else detail.get("elapsed_secs", 0.0)
        rate = 0.0 if not detail else detail.get("rate", 0.0)
        scanned = 0 if not detail else detail.get("scanned", 0) or 0

        rate_text = "rate calculating..." if rate <= 0 else f"{rate:,.0f} items/sec"
        parts = [f"{scanned:,} items scanned", rate_text, f"{self._format_scan_duration(elapsed)} elapsed"]
        return " - ".join(parts)

    def _set_scan_stats(self, detail=None):
        percent = None if not detail else detail.get("percent")

        if hasattr(self, 'scan_progress'):
            if percent is None:
                self.scan_progress.setRange(0, 0)
            else:
                self.scan_progress_was_determinate = True
                self.scan_progress.setRange(0, 100)
                self.scan_progress.setValue(max(0, min(99, int(percent))))

        if hasattr(self, 'scan_stats'):
            self.scan_stats.setText(self._scan_stats_text(detail))

    def _set_size_totals_pending(self, browse=False, page=False, selected=False):
        if browse:
            if self.is_scanning:
                self._set_scanning_total_chip()
            else:
                known_total = self._known_browse_total()
                self._set_total_summary_chip(
                    "--" if known_total is None else known_total
                )
                if self.folder_browser_scope:
                    cached_scoped_total = self._cached_scoped_folder_total()
                    self._set_chip_text(
                        self.chip_folder_size,
                        "Folder Size calculating..."
                        if cached_scoped_total is None
                        else (
                            "Folder Size "
                            f"{self._format_chip_size(cached_scoped_total)}"
                        ),
                    )
                    self.chip_folder_size.setVisible(True)
                else:
                    self.chip_folder_size.setVisible(False)
                if self.active_extension_filter is not None:
                    extension = self.active_extension_filter or "(no extension)"
                    self._set_chip_text(
                        self.chip_filtered_size,
                        f"Type {extension} Size calculating...",
                    )
                    self.chip_filtered_size.setVisible(True)
                elif self.applied_name_filter:
                    self._set_chip_text(self.chip_filtered_size, "Search Size calculating...")
                    self.chip_filtered_size.setVisible(True)
                else:
                    self.chip_filtered_size.setVisible(False)
        if page:
            self._set_chip_text(self.chip_page_size, "Current Page Size: calculating...")
        if selected:
            if self.is_scanning:
                self.chip_selected_size.setVisible(False)
            elif self._selected_roots_for_delete():
                self._set_chip_text(self.chip_selected_size, "Selected: calculating...")
            else:
                self.cached_selected_total = 0
                self._set_selected_summary_chip("0 B", 0, 0)

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

    def _restore_tree_scroll_position(self, vertical_value, horizontal_value=0):
        if not hasattr(self, 'tree'):
            return
        vbar = self.tree.verticalScrollBar()
        hbar = self.tree.horizontalScrollBar()
        vbar.setValue(max(vbar.minimum(), min(vertical_value, vbar.maximum())))
        hbar.setValue(max(hbar.minimum(), min(horizontal_value, hbar.maximum())))

    def _clear_path_focus_for_external_click(self, obj, event):
        if event.type() != QEvent.Type.MouseButtonPress:
            return
        if not hasattr(self, 'txt_path') or not self.txt_path.hasFocus():
            return
        if not isinstance(obj, QWidget):
            return
        if obj == self.txt_path or self.txt_path.isAncestorOf(obj):
            return

        self._clear_path_input_focus()

    def _clear_path_input_focus(self):
        if not hasattr(self, 'txt_path'):
            return
        self.txt_path.deselect()
        self.txt_path.clearFocus()

    def eventFilter(self, obj, event):
        self._clear_path_focus_for_external_click(obj, event)
        if hasattr(self, 'tree') and obj == self.tree.viewport():
            if event.type() == QEvent.Type.MouseMove:
                pos = event.position().toPoint() if hasattr(event, 'position') else event.pos()
                self._update_tree_cursor(self.tree.indexAt(pos))
            elif event.type() == QEvent.Type.Leave:
                self.tree.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        if hasattr(self, 'tree_container') and obj == self.tree_container:
            if event.type() == QEvent.Type.Resize:
                self._reposition_scroll_top_btn()
                QTimer.singleShot(0, self._fit_tree_columns_to_viewport)
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        self._update_topbar_responsive_typography()
        self._update_path_field_width()
        super().resizeEvent(event)

    def _on_path_display_changed(self, path):
        self.txt_path.setToolTip(path or "Selected folder path")
        self._update_path_field_width()

    def _update_path_field_width(self):
        if not hasattr(self, 'path_input_shell'):
            return

        path = self.txt_path.text().strip()
        text_width = self.txt_path.fontMetrics().horizontalAdvance(
            path or self.txt_path.placeholderText()
        )
        browse_width = self.btn_browse.width() if hasattr(self, 'btn_browse') else 74
        desired_width = max(420, text_width + browse_width + 64)

        title_width = self.lbl_app_title.sizeHint().width() if hasattr(self, 'lbl_app_title') else 0
        theme_width = self.btn_theme_toggle.width() if hasattr(self, 'btn_theme_toggle') else 38
        rescan_width = self.btn_rescan.width() if hasattr(self, 'btn_rescan') else 80
        available_width = max(
            280,
            self.width() - title_width - theme_width - rescan_width - 128,
        )
        self.path_input_shell.setFixedWidth(
            min(desired_width, 720, available_width)
        )

    def _update_topbar_responsive_typography(self):
        if not hasattr(self, 'topbar'):
            return

        width = max(720, self.width())
        if width >= 1400:
            title_size = 16
            control_size = 12
            compact_size = 11
        elif width >= 1200:
            title_size = 15
            control_size = 11
            compact_size = 10
        elif width >= 980:
            title_size = 14
            control_size = 10
            compact_size = 9
        else:
            title_size = 13
            control_size = 9
            compact_size = 8
        self._topbar_control_font_px = control_size

        title_font = QFont(self.lbl_app_title.font())
        title_font.setPixelSize(title_size)
        self.lbl_app_title.setFont(title_font)

        control_targets = (
            self.txt_path,
            self.btn_browse,
            self.btn_rescan,
            self.btn_folder_browser,
            self.btn_filter,
            self.btn_file_types,
            self.btn_exclusions,
            self.btn_export,
            self.btn_delete,
        )
        for widget in control_targets:
            font = QFont(widget.font())
            font.setPixelSize(control_size)
            widget.setFont(font)
        toggle_font = QFont(self.btn_theme_toggle.font())
        toggle_font.setPixelSize(compact_size)
        self.btn_theme_toggle.setFont(toggle_font)

        browse_width = max(66, self.btn_browse.fontMetrics().horizontalAdvance("Browse") + 26)
        self.btn_browse.setFixedWidth(browse_width)
        self._update_delete_button_layout()
        self._update_path_field_width()

    def _update_delete_button_layout(self):
        if not hasattr(self, 'btn_delete'):
            return

        base_size = getattr(
            self,
            '_topbar_control_font_px',
            self.btn_delete.font().pixelSize(),
        )
        if base_size <= 0:
            base_size = 10
        fitted_size = base_size
        min_size = 7
        available_text_width = max(
            96,
            self.btn_delete.width() - self.btn_delete.iconSize().width() - 54,
        )
        button_text = self.btn_delete.text()

        while fitted_size > min_size:
            fitted_font = QFont(self.btn_delete.font())
            fitted_font.setPixelSize(fitted_size)
            if QFontMetrics(fitted_font).horizontalAdvance(button_text) <= available_text_width:
                break
            fitted_size -= 1

        final_font = QFont(self.btn_delete.font())
        final_font.setPixelSize(fitted_size)
        self.btn_delete.setFont(final_font)

    def _update_delete_button_copy(self, total=0, all_pages=False):
        self.btn_delete.setText("Delete Selected")
        total = max(0, int(total or 0))
        if total <= 0:
            tooltip = "Select items to move to the Recycle Bin"
        elif all_pages:
            tooltip = (
                f"Move {total:,} matching items across all pages "
                "to the Recycle Bin"
            )
        else:
            tooltip = (
                f"Move {total:,} selected item"
                f"{'s' if total != 1 else ''} to the Recycle Bin"
            )
        self.btn_delete.setToolTip(tooltip)
        self.btn_delete.setAccessibleDescription(tooltip)
        self._update_delete_button_layout()

    def _fit_tree_columns_to_viewport(self):
        if not hasattr(self, 'tree') or self.tree.model() is None:
            return

        header = self.tree.header()
        view_mode = self.fp.get_view_mode() if hasattr(self, 'fp') else 'Tree'
        viewport_width = max(0, self.tree.viewport().width() - 18)

        if view_mode == 'Tree':
            fixed_widths = {
                1: 96,
                2: 150,
                3: 86,
                4: 132,
                5: 118,
            }
            min_name_width = 420
        else:
            fixed_widths = {
                1: min(420, max(260, viewport_width // 4)),
                2: 150,
                3: 86,
                4: 132,
                5: 118,
            }
            min_name_width = 260

        visible_fixed = 0
        for column, width in fixed_widths.items():
            if self.tree.isColumnHidden(column):
                continue
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.tree.setColumnWidth(column, width)
            visible_fixed += width

        name_width = max(min_name_width, viewport_width - visible_fixed)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(0, name_width)
