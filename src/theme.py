import os
from PyQt6.QtGui import QFont

LIGHT_PALETTE = {
    "name": "light",
    "bg_main": "#ffffff",
    "bg_sec": "#f8fafc",
    "bg_hover": "#f1f5f9",
    "bg_panel": "#fafbfc",
    "border": "#e2e8f0",
    "border_strong": "#cbd5e1",
    "text": "#1e293b",
    "text_muted": "#64748b",
    "text_soft": "#475569",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "accent_pressed": "#1e40af",
    "accent_soft": "#eff6ff",
    "accent_border": "#dbeafe",
    "danger": "#dc2626",
    "danger_hover": "#b91c1c",
    "danger_soft": "#fef2f2",
    "danger_pressed": "#fee2e2",
    "danger_border": "#fecaca",
    "chip_red": "#ef4444",
    "chip_red_bg": "#fef2f2",
    "chip_yellow": "#f59e0b",
    "chip_yellow_bg": "#fffbeb",
    "chip_green": "#10b981",
    "chip_green_bg": "#f0fdf4",
    "tree_alt": "#f8fafc",
    "row_border": "#dbe3ee",
    "header_bg": "#f8fafc",
    "header_border": "#dbe3ee",
    "scroll_handle": "#cbd5e1",
    "scroll_handle_hover": "#94a3b8",
    "scroll_handle_pressed": "#64748b",
    "bar_track": "#e9eef5",
    "bar_fill": "#2f6df6",
    "status_empty_text": "#991b1b",
    "status_empty_bg": "#fef2f2",
    "status_empty_border": "#fee2e2",
    "status_inactive_text": "#92400e",
    "status_inactive_bg": "#fffbeb",
    "status_inactive_border": "#fef3c7",
    "status_active_text": "#065f46",
    "status_active_bg": "#f0fdf4",
    "status_active_border": "#d1fae5",
    "status_context_text": "#1e40af",
    "status_context_bg": "#eff6ff",
    "status_context_border": "#dbeafe",
    "menu_bg": "#ffffff",
    "menu_text": "#1e293b",
    "menu_border": "#cbd5e1",
}

DARK_PALETTE = {
    "name": "dark",
    "bg_main": "#111827",
    "bg_sec": "#0f172a",
    "bg_hover": "#1e293b",
    "bg_panel": "#111827",
    "border": "#334155",
    "border_strong": "#475569",
    "text": "#e5e7eb",
    "text_muted": "#94a3b8",
    "text_soft": "#cbd5e1",
    "accent": "#60a5fa",
    "accent_hover": "#3b82f6",
    "accent_pressed": "#2563eb",
    "accent_soft": "#172554",
    "accent_border": "#1d4ed8",
    "danger": "#f87171",
    "danger_hover": "#ef4444",
    "danger_soft": "#3f1d1d",
    "danger_pressed": "#4c1d1d",
    "danger_border": "#7f1d1d",
    "chip_red": "#f87171",
    "chip_red_bg": "#3f1d1d",
    "chip_yellow": "#fbbf24",
    "chip_yellow_bg": "#3f2f0d",
    "chip_green": "#34d399",
    "chip_green_bg": "#0d3b2a",
    "tree_alt": "#0b1220",
    "row_border": "#243244",
    "header_bg": "#111827",
    "header_border": "#334155",
    "scroll_handle": "#475569",
    "scroll_handle_hover": "#64748b",
    "scroll_handle_pressed": "#94a3b8",
    "bar_track": "#334155",
    "bar_fill": "#60a5fa",
    "status_empty_text": "#fecaca",
    "status_empty_bg": "#3f1d1d",
    "status_empty_border": "#7f1d1d",
    "status_inactive_text": "#fde68a",
    "status_inactive_bg": "#3f2f0d",
    "status_inactive_border": "#854d0e",
    "status_active_text": "#bbf7d0",
    "status_active_bg": "#0d3b2a",
    "status_active_border": "#166534",
    "status_context_text": "#bfdbfe",
    "status_context_bg": "#172554",
    "status_context_border": "#1d4ed8",
    "menu_bg": "#111827",
    "menu_text": "#e5e7eb",
    "menu_border": "#334155",
}

_CURRENT_PALETTE = LIGHT_PALETTE


def current_palette():
    return _CURRENT_PALETTE


def resolve_theme_name(theme_name="light"):
    value = str(theme_name or "light").lower()
    return "dark" if value == "dark" else "light"


def apply_theme(app, theme_name="light"):
    global _CURRENT_PALETTE
    base_dir = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
    base_style = ""
    palette = DARK_PALETTE if resolve_theme_name(theme_name) == "dark" else LIGHT_PALETTE
    _CURRENT_PALETTE = palette
    app.setProperty("watchdog_theme", palette["name"])

    bg_main = palette["bg_main"]
    bg_sec = palette["bg_sec"]
    bg_hover = palette["bg_hover"]
    bg_panel = palette["bg_panel"]
    border = palette["border"]
    border_strong = palette["border_strong"]
    text = palette["text"]
    text_muted = palette["text_muted"]
    text_soft = palette["text_soft"]
    accent = palette["accent"]
    accent_hover = palette["accent_hover"]
    accent_pressed = palette["accent_pressed"]
    accent_soft = palette["accent_soft"]
    accent_border = palette["accent_border"]
    danger = palette["danger"]
    danger_hover = palette["danger_hover"]
    danger_soft = palette["danger_soft"]
    danger_pressed = palette["danger_pressed"]
    danger_border = palette["danger_border"]
    chip_red = palette["chip_red"]
    chip_red_bg = palette["chip_red_bg"]
    chip_yellow = palette["chip_yellow"]
    chip_yellow_bg = palette["chip_yellow_bg"]
    chip_green = palette["chip_green"]
    tree_alt = palette["tree_alt"]
    row_border = palette["row_border"]
    header_bg = palette["header_bg"]
    header_border = palette["header_border"]
    scroll_handle = palette["scroll_handle"]
    scroll_handle_hover = palette["scroll_handle_hover"]
    scroll_handle_pressed = palette["scroll_handle_pressed"]

    # Set application-wide font
    font = QFont("Segoe UI")
    font.setPointSize(9)
    app.setFont(font)

    custom_style = f"""
        * {{ font-family: "Segoe UI", "Inter", system-ui, sans-serif; font-size: 9pt; }}
        
        QMessageBox, QDialog {{
            background-color: {bg_main};
        }}
        QMessageBox QLabel, QDialog QLabel {{
            color: {text};
            font-size: 10pt;
        }}
        QMessageBox QPushButton, QDialog QPushButton {{
            background-color: {bg_sec};
            border: 1px solid {border};
            border-radius: 6px;
            padding: 6px 16px;
            min-width: 80px;
            color: {text};
        }}
        QMessageBox QPushButton:hover, QDialog QPushButton:hover {{
            background-color: {bg_hover};
        }}
        QDialog#deleteProgressDialog {{
            background-color: {bg_main};
            border: 1px solid {border};
            border-radius: 8px;
        }}
        QLabel#deleteProgressTitle {{
            color: {text};
            font-size: 15px;
            font-weight: 700;
        }}
        QLabel#deleteProgressCount {{
            color: {text_soft};
            font-size: 12px;
            font-weight: 600;
        }}
        QLabel#deleteProgressPath {{
            color: {text_muted};
            background-color: {bg_sec};
            border: 1px solid {border};
            border-radius: 6px;
            padding: 8px 10px;
            font-size: 11px;
        }}
        QProgressBar#deleteProgressBar {{
            background-color: {border};
            border: none;
            border-radius: 5px;
            height: 10px;
        }}
        QProgressBar#deleteProgressBar::chunk {{
            background-color: {accent};
            border-radius: 5px;
        }}
        QPushButton#deleteProgressCancel {{
            background-color: transparent;
            color: {danger};
            border: 1.5px solid {danger_border};
            border-radius: 6px;
            padding: 8px 14px;
            font-weight: 600;
        }}
        QPushButton#deleteProgressCancel:hover {{
            background-color: {danger_soft};
            border-color: {danger};
        }}
        QPushButton#deleteProgressCancel:disabled {{
            color: {text_muted};
            border-color: {border};
            background-color: {bg_sec};
        }}
        QDialog#loadingDialog {{
            background-color: {bg_main};
            border: 1px solid {border};
            border-radius: 8px;
        }}
        QLabel#loadingTitle {{
            color: {text};
            font-size: 15px;
            font-weight: 700;
        }}
        QLabel#loadingDetail {{
            color: {text_muted};
            font-size: 12px;
            font-weight: 500;
        }}
        QProgressBar#loadingBar {{
            background-color: {border};
            border: none;
            border-radius: 5px;
            height: 10px;
        }}
        QProgressBar#loadingBar::chunk {{
            background-color: {accent};
            border-radius: 5px;
        }}
        QMainWindow {{ background-color: {bg_sec}; }}
        QLabel {{ color: {text}; }}
        QWidget#topbar {{
            background-color: {bg_sec};
            border-bottom: 1px solid {border};
        }}
        QWidget#sidebar {{
            background-color: {bg_panel};
            border-right: 1px solid {border};
        }}
        QScrollArea#filterScroll {{
            background-color: {bg_panel};
            border: none;
        }}
        QWidget#filterScrollContent {{
            background-color: {bg_panel};
        }}
        QWidget#sidebarActionBox {{
            background-color: {bg_panel};
            border-top: 1px solid {border};
        }}
        
        QWidget#contentArea {{
            background-color: {bg_sec};
        }}
        
        QWidget#tableCard {{
            background-color: {bg_main};
            border-radius: 12px;
            border: 1px solid {border};
        }}
        
        QFrame#sidebarDivider {{
            background-color: {border};
            max-height: 1px;
            margin: 8px 16px;
        }}
        
        QWidget#displayModeSection,
        QWidget#viewModeSection,
        QWidget#ageThresholdSection,
        QWidget#searchSection {{
            margin-bottom: 8px;
        }}
        
        QLabel#searchHeader,
        QLabel#displayModeHeader,
        QLabel#viewModeHeader,
        QLabel#ageThresholdHeader {{
            background-color: transparent;
            padding: 10px 16px;
            font-weight: 600;
            font-size: 9pt;
            color: {text_soft};
            margin-bottom: 8px;
        }}

        QLabel#searchHeader {{
            padding: 4px 0;
            margin-bottom: 2px;
        }}

        QCheckBox, QRadioButton {{
            padding: 10px 16px;
            spacing: 10px;
            color: {text_soft};
            font-weight: 500;
        }}
        
        QCheckBox:hover, QRadioButton:hover {{
            background-color: {bg_hover};
            border-radius: 6px;
            color: {text};
        }}
        
        QCheckBox::indicator {{
            width: 18px;
            height: 18px;
            border-radius: 4px;
            border: 2px solid {border_strong};
            background-color: {bg_main};
        }}
        QCheckBox::indicator:hover {{
            border-color: {text_muted};
            background-color: {bg_sec};
        }}
        QCheckBox::indicator:checked {{
            background-color: {accent};
            border-color: {accent};
            image: url("{base_dir}/assets/check.svg");
        }}

        
        QRadioButton::indicator {{
            width: 20px;
            height: 20px;
            border-radius: 10px;
            border: 2px solid {border_strong};
            background-color: {bg_main};
        }}
        QRadioButton::indicator:checked {{
            border: 6px solid {accent};
            background-color: {bg_main};
        }}
        QRadioButton::indicator:hover {{
            border-color: {text_muted};
        }}

        QWidget#statusbar {{
            background-color: {bg_sec};
            border-top: 1px solid {border};
            color: {text_muted};
        }}
        QLabel#statusMessage {{
            color: {text_soft};
            font-weight: 500;
            padding-right: 12px;
        }}
        QLabel#statusSeparator {{
            color: {border_strong};
            font-size: 10pt;
            padding: 0 8px;
        }}

        
        QLabel#chipEmpty, QLabel#chipInactive, QLabel#chipSpace {{ 
            background-color: {bg_sec};
            color: {text};
            border: 1px solid {border};
            border-radius: 12px;
            padding: 4px 12px;
            font-weight: 600;
            font-size: 11px;
        }}
        QLabel#chipEmpty {{
            color: {text_muted};
            background-color: transparent;
            border: 1px dashed {border_strong};
        }}
        QLabel#chipInactive {{
            color: {text_soft};
            background-color: {bg_hover};
            border-color: {border};
        }}
        QLabel#chipSpace {{
            color: {text};
            background-color: {bg_main};
            border-color: {border_strong};
        }}
        QTreeView {{
            background-color: {bg_main};
            alternate-background-color: {tree_alt};
            border: none;
            outline: none;
            color: {text};
            font-weight: 500;
        }}
        QTreeView::viewport,
        QAbstractScrollArea::viewport {{
            background-color: {bg_main};
        }}
        QTreeView::item {{ 
            padding: 10px 4px; 
            border-bottom: 1px solid {row_border}; 
        }}
        QTreeView::item:alternate {{
            background-color: {tree_alt};
        }}
        QTreeView::item:hover {{ background-color: {bg_hover}; color: {text}; }}
        QTreeView::item:selected {{ background-color: {accent}; color: #fff; }}
        QTreeView::item:selected:hover {{ background-color: {accent_pressed}; color: #fff; }}
        
        QTreeView::indicator {{
            width: 16px;
            height: 16px;
            border: 1.5px solid {border_strong};
            border-radius: 4px;
            background-color: {bg_main};
        }}
        QTreeView::indicator:hover {{
            border-color: {text_muted};
            background-color: {bg_sec};
        }}
        QTreeView::indicator:checked {{
            background-color: {accent};
            border-color: {accent};
            image: url("{base_dir}/assets/check.svg");
        }}

        QTreeView::branch {{
            border-bottom: 1px solid {row_border};
        }}
        QTreeView::branch:has-children:closed,
        QTreeView::branch:closed:has-children:has-siblings,
        QTreeView::branch:closed:has-children:!has-siblings {{
            image: url("{base_dir}/assets/tree_chevron_right.svg");
        }}
        QTreeView::branch:has-children:open,
        QTreeView::branch:open:has-children:has-siblings,
        QTreeView::branch:open:has-children:!has-siblings {{
            image: url("{base_dir}/assets/tree_chevron_down.svg");
        }}
        QTreeView::branch:has-children:hover:closed,
        QTreeView::branch:hover:closed:has-children:has-siblings,
        QTreeView::branch:hover:closed:has-children:!has-siblings {{
            image: url("{base_dir}/assets/tree_chevron_right_hover.svg");
        }}
        QTreeView::branch:has-children:hover:open,
        QTreeView::branch:hover:open:has-children:has-siblings,
        QTreeView::branch:hover:open:has-children:!has-siblings {{
            image: url("{base_dir}/assets/tree_chevron_down_hover.svg");
        }}


        /* ── Scrollbar ───────────────────────────────────────────────────── */
        QScrollBar:vertical {{
            background: {bg_sec};
            width: 12px;
            border: none;
            border-left: 1px solid {border};
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {scroll_handle};
            border-radius: 5px;
            min-height: 32px;
            margin: 2px 2px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {scroll_handle_hover};
        }}
        QScrollBar::handle:vertical:pressed {{
            background: {scroll_handle_pressed};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
            background: none;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: none;
        }}
        QScrollBar:horizontal {{
            background: {bg_sec};
            height: 12px;
            border: none;
            border-top: 1px solid {border};
        }}
        QScrollBar::handle:horizontal {{
            background: {scroll_handle};
            border-radius: 5px;
            min-width: 32px;
            margin: 2px 2px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {scroll_handle_hover};
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0;
            background: none;
        }}

        /* ── Floating scroll-to-top button ───────────────────────────────── */
        QPushButton#scrollTopBtn {{
            background-color: {accent};
            border: none;
            border-radius: 19px;
            color: white;
            font-size: 18px;
            font-weight: bold;
            padding: 0;
        }}
        QPushButton#scrollTopBtn:hover {{
            background-color: {accent_hover};
        }}
        QPushButton#scrollTopBtn:pressed {{
            background-color: {accent_pressed};
        }}

        QHeaderView {{
            background-color: {bg_panel};
        }}
        QHeaderView::section {{
            background-color: {header_bg};
            padding: 12px 18px;
            border: none;
            border-right: 1px solid {border};
            border-bottom: 1px solid {header_border};
            font-weight: 600;
            color: {text_soft};
            font-size: 9pt;
        }}

        QLineEdit#filterSearch {{
            padding: 3px 8px;
        }}
        QLineEdit#scanExclusionInput {{
            background-color: {bg_main};
            border: 1px solid {header_border};
            border-radius: 6px;
            color: {text};
            padding: 4px 8px;
            selection-background-color: {accent_border};
        }}
        QLineEdit#scanExclusionInput:focus {{
            border-color: {accent};
        }}
        QHeaderView::section:hover {{
            background-color: {accent_soft};
            color: {accent};
        }}

        QPushButton:disabled {{
            background-color: {bg_sec};
            color: {text_muted};
            border: 1.5px solid {border};
        }}

        QPushButton#collapseAll {{
            background-color: {bg_main};
            color: {text_soft};
            border: 1.5px solid {border};
            border-radius: 6px;
            padding: 8px 16px;
            font-weight: 500;
        }}
        QPushButton#collapseAll:hover {{
            background-color: {bg_sec};
            border-color: {border_strong};
        }}

        QPushButton#pageNavBtn {{
            background-color: {bg_main};
            color: {accent};
            border: 1.5px solid {border_strong};
            border-radius: 7px;
            padding: 0;
            font-size: 13px;
            font-weight: 700;
        }}
        QPushButton#pageNavBtn:hover {{
            background-color: {accent_soft};
            border-color: {accent};
        }}
        QPushButton#pageNavBtn:pressed {{
            background-color: {accent_border};
        }}
        QPushButton#pageNavBtn:disabled {{
            background-color: {bg_sec};
            color: {text_muted};
            border: 1px solid {border};
        }}

        QLabel#pageInfo {{
            background-color: {bg_sec};
            color: {text_soft};
            border: 1px solid {border};
            border-radius: 7px;
            padding: 7px 12px;
            font-size: 12px;
            font-weight: 600;
        }}


        /* ── SpinBox ─────────────────────────────────────────────────────── */
        QSpinBox {{
            background-color: {bg_main};
            border: 1px solid {border};
            border-radius: 6px;
            padding: 4px 8px;
            color: {text};
            selection-background-color: {bg_hover};
            selection-color: {text};
        }}
        QComboBox {{
            background-color: {bg_main};
            border: 1px solid {border};
            border-radius: 6px;
            padding: 5px 8px;
            color: {text};
        }}
        QComboBox:hover {{
            border-color: {border_strong};
            background-color: {bg_hover};
        }}
        QComboBox:focus {{
            border-color: {accent};
        }}
        QComboBox#themeSelector {{
            min-width: 84px;
            padding: 7px 10px;
        }}
        QSpinBox:focus {{
            border-color: {accent};
        }}
        QSpinBox#scanExclusionSize {{
            min-height: 30px;
            max-height: 30px;
        }}
        QComboBox#scanExclusionUnit {{
            background-color: {bg_main};
            border: 1px solid {border};
            border-radius: 6px;
            color: {text};
            padding: 4px 8px;
            min-height: 22px;
        }}
        QComboBox#scanExclusionUnit:focus {{
            border-color: {accent};
        }}
        QSpinBox::up-button, QSpinBox::down-button {{
            background-color: {bg_sec};
            border: none;
            border-left: 1px solid {border};
            width: 20px;
        }}
        QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
            background-color: {bg_hover};
        }}
        QSpinBox::up-button:pressed, QSpinBox::down-button:pressed {{
            background-color: {border};
        }}
        QSpinBox::up-arrow {{
            width: 10px; height: 6px;
            image: url("{base_dir}/assets/up_arrow.svg");
        }}
        QSpinBox::down-arrow {{
            width: 10px; height: 6px;
            image: url("{base_dir}/assets/down_arrow.svg");
        }}

        /* ── Base button — tactile feedback ─────────────────────────────── */
        QPushButton {{
            background-color: {bg_sec};
            border: 1px solid {border};
            border-radius: 6px;
            padding: 8px 16px;
            color: {text};
            font-weight: 500;
            outline: none;
        }}
        QPushButton:hover {{
            background-color: {bg_hover};
            border-color: {border_strong};
            color: {text};
        }}
        QPushButton:pressed {{
            background-color: {border};
            color: {text};
        }}
        QPushButton:disabled {{
            color: {text_muted};
            border: 1px dashed {border};
            background-color: {bg_sec};
        }}
        QPushButton#primaryBtn {{
            background-color: {accent};
            color: white;
            border: 1px solid {accent};
            border-bottom: 3px solid {accent_hover};
            border-radius: 6px;
            padding: 8px 16px;
            font-weight: 600;
        }}
        QPushButton#primaryBtn:hover {{
            background-color: {accent_hover};
        }}
        QPushButton#primaryBtn:pressed {{
            background-color: {accent_pressed};
            border-bottom-width: 1px;
            padding: 9px 16px 7px 16px;
        }}
        
        QPushButton#ghostBtn {{
            background-color: transparent;
            color: {accent};
            border: 1.5px solid {accent};
            border-radius: 6px;
            padding: 7px 16px;
            font-weight: 500;
        }}
        QPushButton#ghostBtn:hover {{
            background-color: {accent_soft};
        }}
        QPushButton#ghostBtn:pressed {{
            background-color: {accent_border};
        }}


        /* ── Filter toggle ───────────────────────────────────────────────── */
        QPushButton#filterBtn {{
            color: {accent};
            font-weight: 600;
            border: 1px solid {accent};
            border-bottom: 2px solid {accent_hover};
            background-color: {accent_soft};
        }}
        QPushButton#filterBtn:hover {{
            background-color: {accent};
            border-bottom-color: {accent_hover};
            color: white;
        }}
        QPushButton#filterBtn:checked {{
            background-color: {accent};
            border: 1px solid {accent_hover};
            border-bottom: 2px solid {accent_pressed};
            color: white;
        }}
        QPushButton#filterBtn:pressed {{
            background-color: {accent_pressed};
            border-bottom-width: 1px;
            color: white;
            padding: 7px 14px 5px 14px;
        }}

        QPushButton#deleteBtn {{
            background-color: transparent;
            color: {danger};
            border: 1.5px solid {danger_border};
            border-radius: 6px;
            padding: 10px 18px;
            font-weight: 500;
        }}
        QPushButton#deleteBtn:hover {{
            background-color: {danger_soft};
            border-color: {danger};
        }}
        QPushButton#deleteBtn:disabled {{
            color: {text_muted};
            border-color: {border};
            background-color: transparent;
        }}

        /* ── Sidebar actions ─────────────────────────────────────────────── */
        QPushButton#closeSidebar {{
            background-color: transparent;
            border: none;
            color: {text_muted};
            padding: 12px;
            font-size: 14px;
            text-align: center;
        }}
        QPushButton#closeSidebar:hover {{
            background-color: {bg_hover};
            color: {text};
        }}

        QPushButton#resetFilters {{
            background-color: transparent;
            color: {accent};
            border: 1.5px solid {accent_border};
            border-radius: 6px;
            padding: 10px 16px;
            font-weight: 500;
        }}
        QPushButton#resetFilters:hover {{
            background-color: {accent_soft};
            border-color: {accent};
        }}
        QPushButton#resetFilters:pressed {{
            background-color: {accent_border};
        }}
        QPushButton#resetExclusions {{
            background-color: transparent;
            color: {accent};
            border: none;
            padding: 2px 0;
            font-size: 8pt;
            font-weight: 500;
            text-align: left;
        }}
        QPushButton#resetExclusions:hover {{
            color: {accent_hover};
            text-decoration: underline;
        }}

        QLabel#manualLabel {{
            color: {text_muted};
            font-size: 8pt;
            font-weight: 400;
        }}

        /* ── Delete button ───────────────────────────────────────────────── */
        QPushButton#deleteBtn {{
            background-color: transparent;
            color: {chip_red};
            border: 1.5px solid {chip_red};
            border-radius: 6px;
            padding: 7px 16px;
            font-weight: 600;
        }}
        QPushButton#deleteBtn:hover {{
            background-color: {danger_soft};
        }}
        QPushButton#deleteBtn:pressed {{
            background-color: {danger_pressed};
        }}
        QPushButton#deleteBtn:disabled {{
            color: {text_muted};
            border: 1px solid {border};
            border-bottom: 2px solid {border};
            background-color: {bg_sec};
        }}

        /* ── Filter Close button ─────────────────────────────────────────── */
        QPushButton#filterCloseBtn {{
            background-color: {bg_sec};
            border: 1px solid {border};
            border-bottom: 2px solid {border};
            color: {text_muted};
            font-weight: 500;
        }}
        QPushButton#filterCloseBtn:hover {{
            background-color: {chip_red_bg};
            border-color: {chip_red};
            border-bottom-color: {danger_hover};
            color: {chip_red};
        }}
        QPushButton#filterCloseBtn:pressed {{
            background-color: {danger_pressed};
            border-bottom-width: 1px;
            color: {chip_red};
            padding: 7px 14px 5px 14px;
        }}

        /* ── Reset (ghost) ───────────────────────────────────────────────── */
        QPushButton#resetBtn {{
            background-color: transparent;
            border: 1px solid {border};
            border-bottom: 2px solid {border};
            color: {text_muted};
        }}
        QPushButton#resetBtn:hover {{
            border-color: {text_muted};
            border-bottom-color: {text_muted};
            background-color: {bg_hover};
            color: {text};
        }}
        QPushButton#resetBtn:pressed {{
            background-color: {bg_sec};
            border-bottom-width: 1px;
            color: {text_muted};
            padding: 7px 14px 5px 14px;
        }}

        /* ── Link / Clear (text-only) ────────────────────────────────────── */
        QPushButton#linkBtn {{
            background: transparent;
            border: none;
            color: {accent};
            font-size: 12px;
            padding: 0;
            text-align: left;
        }}
        QPushButton#linkBtn:hover {{
            color: {accent_hover};
            text-decoration: underline;
        }}
        QPushButton#linkBtn:pressed {{
            color: {text};
        }}

        QPushButton#clearDates {{
            background-color: transparent;
            color: {text_muted};
            border: none;
            padding: 6px 8px;
            font-size: 9pt;
            text-align: left;
            qproperty-icon: url("{base_dir}/assets/x-circle.svg");
            qproperty-iconSize: 14px 14px;
        }}
        
        QPushButton#clearDates:hover {{
            color: {danger};
            background-color: {danger_soft};
            border-radius: 4px;
        }}

        QLabel#appTitle {{
            font-weight: 700;
            font-size: 16px; /* ~12pt Header */
            color: {text};
            letter-spacing: 0px;
            padding-right: 4px;
        }}

        QLabel#sectionLabel {{
            color: {text_muted};
            font-size: 11px; /* ~8pt Label */
            font-weight: 600;
            letter-spacing: 0px;
        }}

        QLabel#mutedLabel {{
            color: {text_muted};
            font-size: 12px;
        }}

        QWidget#emptyState {{
            background-color: {bg_main};
        }}
        QLabel#emptyIcon {{
            font-size: 64px;
            padding: 20px;
            qproperty-alignment: AlignCenter;
        }}
        QLabel#emptyTitle {{
            font-size: 21px; /* ~16pt Header */
            font-weight: 600;
            color: {text};
        }}
        QLabel#emptySub {{
            font-size: 12px; /* ~9pt Body */
            color: {text_muted};
        }}
        QLabel#scanPathValue {{
            background-color: {bg_sec};
            border: 1px solid {border};
            border-radius: 8px;
            padding: 10px 12px;
            color: {text};
            font-size: 12px;
        }}

        QLabel#agePill {{
            background-color: {accent};
            color: white;
            border-radius: 10px;
            padding: 4px 10px;
            font-size: 11px;
            font-weight: 600;
        }}

        QLabel#statusLabel {{
            color: {text_muted};
            font-size: 11px; /* ~8pt Label */
        }}

        QFrame#divider {{
            color: {border};
        }}
        
        QLineEdit {{
            background-color: {bg_main};
            border: 1px solid {border};
            border-radius: 6px;
            padding: 5px 8px;
            color: {text};
        }}
        QLineEdit:focus {{ border-color: {accent}; }}
        
        QRadioButton {{ color: {text}; spacing: 6px; }}
        QRadioButton::indicator {{
            width: 16px;
            height: 16px;
            border-radius: 8px;
            border: 2px solid {text_muted};
            background-color: transparent;
        }}
        QRadioButton::indicator:hover {{
            border-color: {text};
            background-color: {bg_hover};
        }}
        QRadioButton::indicator:checked {{
            border: 2px solid {accent};
            background-color: {accent};
        }}
        
        QSlider {{ background: transparent; }}
        QSlider::groove:horizontal {{
            border: none;
            height: 6px;
            background: {border};
            border-radius: 3px;
        }}
        QSlider::handle:horizontal {{
            background: {accent};
            border: 2px solid {bg_main};
            width: 18px;
            height: 18px;
            margin: -6px 0;
            border-radius: 9px;
        }}
        QSlider::handle:horizontal:hover {{
            background: {accent_hover};
            width: 20px;
            height: 20px;
            margin: -7px 0;
            border-radius: 10px;
        }}
        QSlider::sub-page:horizontal {{
            background: {accent};
            border-radius: 3px;
        }}
        
        QSpinBox {{
            border: 1.5px solid {border};
            border-radius: 6px;
            padding: 6px 10px;
            background-color: {bg_main};
            font-size: 12px;
            color: {text};
        }}
        QSpinBox:focus {{
            border-color: {accent};
        }}
        QSpinBox::up-button, QSpinBox::down-button {{
            width: 20px;
            border: none;
            background-color: {bg_sec};
        }}
        QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
            background-color: {bg_hover};
        }}

        
        
        
        
        /* ── Scrollbars ──────────────────────────────────────────────────── */
        QScrollBar:vertical {{
            border: none;
            background-color: transparent;
            width: 10px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background-color: {scroll_handle};
            border-radius: 5px;
            min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{
            background-color: {scroll_handle_hover};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
        
        QScrollBar:horizontal {{
            border: none;
            background-color: transparent;
            height: 10px;
            margin: 0;
        }}
        QScrollBar::handle:horizontal {{
            background-color: {scroll_handle};
            border-radius: 5px;
            min-width: 30px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background-color: {scroll_handle_hover};
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0px;
        }}
        
        QComboBox QAbstractItemView {{
            border: 1px solid {border};
            border-radius: 6px;
            background-color: {bg_main};
            selection-background-color: {accent_soft};
            selection-color: {text};
            padding: 4px;
        }}
        
        QCalendarWidget {{ 
            background-color: {bg_sec}; 
            color: {text}; 
            font-family: "Segoe UI", sans-serif;
            font-size: 12px;
        }}
        QCalendarWidget QAbstractItemView {{ font-size: 12px; }}
        QCalendarWidget QWidget#qt_calendar_navigationbar {{ font-size: 12px; }}
    """
    app.setStyleSheet(base_style + custom_style)
