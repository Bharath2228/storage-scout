import os
from PyQt6.QtGui import QFont

FONT_FAMILY = "Segoe UI"
TYPE_SCALE = {
    "title": {"size_px": 16, "point_size": 12, "weight": 700},
    "header": {"size_px": 11, "point_size": 8, "weight": 600},
    "body": {"size_px": 12, "point_size": 9, "weight": 500},
    "muted": {"size_px": 11, "point_size": 8, "weight": 400},
}


def safe_point_size(value, fallback=9):
    try:
        point_size = int(value)
    except (TypeError, ValueError):
        return fallback
    return point_size if point_size > 0 else fallback

SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 24

LIGHT_PALETTE = {
    "name": "light",
    "bg": "#f3f5f7",
    "surface": "#ffffff",
    "surface_hover": "#eef2f6",
    "border": "#dbe2ea",
    "text": "#1e293b",
    "text_muted": "#64748b",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "accent_pressed": "#1e40af",
    "accent_tint": "#eff6ff",
    "on_accent": "#ffffff",
    "status_success": "#059669",
    "status_success_bg": "#ecfdf5",
    "status_success_border": "#d1fae5",
    "status_warning": "#b45309",
    "status_warning_bg": "#fffbeb",
    "status_warning_border": "#fde68a",
    "status_danger": "#dc2626",
    "status_danger_bg": "#fef2f2",
    "status_danger_border": "#fecaca",
}

DARK_PALETTE = {
    "name": "dark",
    "bg": "#0b1220",
    "surface": "#131d2e",
    "surface_hover": "#243247",
    "border": "#475569",
    "text": "#f1f5f9",
    "text_muted": "#cbd5e1",
    "accent": "#60a5fa",
    "accent_hover": "#3b82f6",
    "accent_pressed": "#2563eb",
    "accent_tint": "#1e3a5f",
    "on_accent": "#07111f",
    "status_success": "#34d399",
    "status_success_bg": "#0d3b2a",
    "status_success_border": "#166534",
    "status_warning": "#fbbf24",
    "status_warning_bg": "#3f2f0d",
    "status_warning_border": "#854d0e",
    "status_danger": "#f87171",
    "status_danger_bg": "#3f1d1d",
    "status_danger_border": "#7f1d1d",
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

    bg = palette["bg"]
    surface = palette["surface"]
    surface_hover = palette["surface_hover"]
    border = palette["border"]
    text = palette["text"]
    text_muted = palette["text_muted"]
    accent = palette["accent"]
    accent_hover = palette["accent_hover"]
    accent_pressed = palette["accent_pressed"]
    accent_tint = palette["accent_tint"]
    on_accent = palette["on_accent"]
    combo_arrow = "down_arrow_dark.svg" if palette["name"] == "dark" else "down_arrow.svg"
    spin_up_arrow = "up_arrow_dark.svg" if palette["name"] == "dark" else "up_arrow.svg"
    tree_right_arrow = "tree_chevron_right_dark.svg" if palette["name"] == "dark" else "tree_chevron_right.svg"
    tree_down_arrow = "tree_chevron_down_dark.svg" if palette["name"] == "dark" else "tree_chevron_down.svg"
    tree_right_hover = "tree_chevron_right_dark_hover.svg" if palette["name"] == "dark" else "tree_chevron_right_hover.svg"
    tree_down_hover = "tree_chevron_down_dark_hover.svg" if palette["name"] == "dark" else "tree_chevron_down_hover.svg"
    status_success = palette["status_success"]
    status_success_bg = palette["status_success_bg"]
    status_success_border = palette["status_success_border"]
    status_warning = palette["status_warning"]
    status_warning_bg = palette["status_warning_bg"]
    status_warning_border = palette["status_warning_border"]
    status_danger = palette["status_danger"]
    status_danger_bg = palette["status_danger_bg"]
    status_danger_border = palette["status_danger_border"]
    title_size = TYPE_SCALE["title"]["size_px"]
    title_weight = TYPE_SCALE["title"]["weight"]
    header_size = TYPE_SCALE["header"]["size_px"]
    header_weight = TYPE_SCALE["header"]["weight"]
    body_size = TYPE_SCALE["body"]["size_px"]
    body_point_size = TYPE_SCALE["body"]["point_size"]
    body_weight = TYPE_SCALE["body"]["weight"]
    muted_size = TYPE_SCALE["muted"]["size_px"]
    muted_weight = TYPE_SCALE["muted"]["weight"]

    # Set application-wide font
    font = QFont(FONT_FAMILY)
    font.setPointSize(safe_point_size(body_point_size))
    font.setWeight(QFont.Weight.Medium)
    app.setFont(font)

    custom_style = f"""
        * {{
            font-family: "{FONT_FAMILY}", "Inter", system-ui, sans-serif;
            font-size: {body_size}px;
            font-weight: {body_weight};
        }}
        
        QMessageBox, QDialog {{
            background-color: {surface};
        }}
        QToolTip {{
            background-color: {surface_hover};
            color: {text};
            border: 1px solid {border};
            padding: {SPACE_XS}px {SPACE_SM}px;
        }}
        QMenu {{
            background-color: {surface};
            color: {text};
            border: 1px solid {border};
            padding: {SPACE_XS}px;
        }}
        QMenu::item {{
            padding: {SPACE_SM}px {SPACE_LG}px;
            border-radius: 4px;
        }}
        QMenu::item:selected {{
            background-color: {accent_tint};
            color: {text};
        }}
        QMessageBox QLabel, QDialog QLabel {{
            color: {text};
            font-size: {body_size}px;
            font-weight: {body_weight};
        }}
        QMessageBox QPushButton, QDialog QPushButton {{
            background-color: {bg};
            border: 1px solid {border};
            border-radius: 6px;
            padding: {SPACE_MD}px {SPACE_LG}px;
            min-width: 80px;
            color: {text};
        }}
        QMessageBox QPushButton:hover, QDialog QPushButton:hover {{
            background-color: {surface_hover};
        }}
        QDialog#modalDialog {{
            background-color: {surface};
            border: 1px solid {border};
            border-radius: 8px;
        }}
        QFrame#modalSection {{
            background-color: {bg};
            border: 1px solid {border};
            border-radius: 6px;
        }}
        QFrame#modalSection QLabel,
        QFrame#modalSection QCheckBox,
        QFrame#modalSection QRadioButton {{
            background-color: transparent;
            border: none;
        }}
        QLabel#modalTitle {{
            color: {text};
            font-size: {header_size}px;
            font-weight: {header_weight};
            letter-spacing: 0.4px;
        }}
        QLabel#modalDetail {{
            color: {text_muted};
            font-size: {body_size}px;
            font-weight: {body_weight};
        }}
        QLabel#authMessage {{
            color: {status_danger};
            font-size: {muted_size}px;
            font-weight: {muted_weight};
        }}
        QLabel#modalSecondary {{
            color: {text_muted};
            background-color: transparent;
            border: none;
            padding: {SPACE_XS}px 0;
            font-size: {muted_size}px;
            font-weight: {muted_weight};
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
        QPushButton#modalCancel {{
            background-color: transparent;
            color: {text_muted};
            border: 1px solid {border};
            border-radius: 6px;
            padding: {SPACE_MD}px {SPACE_LG}px;
            font-weight: {body_weight};
        }}
        QPushButton#modalCancel:hover {{
            background-color: {surface_hover};
            border-color: {border};
            color: {text};
        }}
        QPushButton#modalCancel:disabled {{
            color: {text_muted};
            border-color: {border};
            background-color: {bg};
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
        QMainWindow {{ background-color: {bg}; }}
        QLabel {{ color: {text}; }}
        QWidget#topbar {{
            background-color: {bg};
            border-bottom: 1px solid {border};
        }}
        QPushButton#toolbarStyleBtn {{
            background-color: transparent;
            color: {text_muted};
            border: none;
            padding: {SPACE_SM}px;
            font-weight: {muted_weight};
        }}
        QPushButton#toolbarStyleBtn:hover {{
            background-color: {surface_hover};
            color: {text};
        }}
        QWidget#sidebar {{
            background-color: {surface};
            border-right: none;
        }}
        QFrame#filterBar,
        QWidget#filterBarMain {{
            background-color: {surface};
        }}
        QFrame#filterBar {{
            border-bottom: 1px solid {border};
        }}
        QFrame#filterBar QFrame#divider {{
            background-color: {border};
            margin-top: {SPACE_SM}px;
            margin-bottom: {SPACE_SM}px;
        }}
        QFrame#filterBar QFrame#accordionHeader {{
            background-color: {bg};
            border: 1px solid {border};
            border-radius: 6px;
            padding: 0 {SPACE_SM}px;
        }}
        QFrame#filterBar QFrame#accordionHeader:hover {{
            background-color: {surface_hover};
            border-color: {accent};
        }}
        QWidget#filterPopover {{
            background-color: transparent;
        }}
        QFrame#filterPopoverCard {{
            background-color: {surface};
            border: 1px solid {border};
            border-radius: 8px;
        }}
        QFrame#filterPopoverCard QFrame#ageControl,
        QFrame#filterPopoverCard QWidget#exclusionsControl {{
            background-color: transparent;
            border: none;
        }}
        QWidget#sidebarActionBox {{
            background-color: {surface};
            border-top: 1px solid {border};
        }}
        
        QWidget#contentArea {{
            background-color: {bg};
        }}
        
        QWidget#tableCard {{
            background-color: {surface};
            border-radius: 8px;
            border: 1px solid {border};
        }}
        
        QFrame#sidebarDivider {{
            background-color: {border};
            max-height: 1px;
            margin: {SPACE_SM}px {SPACE_LG}px;
        }}
        
        QWidget#displayModeSection,
        QWidget#viewModeSection,
        QWidget#ageThresholdSection,
        QWidget#exclusionsSection,
        QWidget#searchSection {{
            background-color: transparent;
            margin-bottom: 0;
        }}
        
        QLabel#searchHeader,
        QLabel#displayModeHeader,
        QLabel#viewModeHeader,
        QLabel#ageThresholdHeader {{
            background-color: transparent;
            padding: 0;
            font-weight: {header_weight};
            font-size: {header_size}px;
            letter-spacing: 0.4px;
            color: {text_muted};
            margin-bottom: {SPACE_SM}px;
        }}

        QLabel#searchHeader {{
            padding: 0;
            margin-bottom: {SPACE_SM}px;
        }}

        QCheckBox, QRadioButton {{
            padding: {SPACE_MD}px {SPACE_MD}px;
            spacing: {SPACE_SM}px;
            color: {text_muted};
            font-size: {body_size}px;
            font-weight: {body_weight};
            border-radius: 6px;
        }}
        
        QCheckBox:hover, QRadioButton:hover {{
            background-color: {surface_hover};
            border-radius: 6px;
            color: {text};
        }}

        QCheckBox:checked,
        QRadioButton:checked {{
            background-color: {accent_tint};
            color: {text};
        }}
        
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
            border-radius: 4px;
            border: 1px solid {border};
            background-color: {surface};
        }}
        QCheckBox::indicator:hover {{
            border-color: {text_muted};
            background-color: {bg};
        }}
        QCheckBox::indicator:checked {{
            background-color: {accent};
            border-color: {accent};
            image: url("{base_dir}/assets/check.svg");
        }}

        
        QRadioButton::indicator {{
            width: 18px;
            height: 18px;
            border-radius: 9px;
            border: 2px solid {text_muted};
            background-color: transparent;
            image: none;
        }}
        QRadioButton::indicator:hover {{
            border-color: {accent};
            background-color: transparent;
            image: none;
        }}
        QRadioButton::indicator:checked {{
            border: 6px solid {accent};
            border-radius: 15px;
            background-color: {surface};
            image: none;
        }}
        QRadioButton[segment="true"] {{
            background-color: {bg};
            border: 1px solid {border};
            border-radius: 6px;
            color: {text_muted};
            font-size: {muted_size}px;
            font-weight: {muted_weight};
            padding: 6px {SPACE_XS}px;
            spacing: 0;
        }}
        QRadioButton[segment="true"]:hover {{
            background-color: {surface_hover};
            color: {text};
        }}
        QRadioButton[segment="true"]:checked {{
            background-color: {accent};
            border-color: {accent};
            color: {on_accent};
        }}
        QRadioButton[segment="true"]::indicator {{
            width: 0;
            height: 0;
            margin: 0;
            padding: 0;
            border: none;
            image: none;
        }}

        QWidget#statusbar {{
            background-color: {bg};
            border-top: 1px solid {border};
            color: {text_muted};
        }}
        QSplitter#mainSplitter::handle {{
            background-color: {border};
            width: 1px;
            margin: 0 2px;
        }}
        QSplitter#mainSplitter::handle:hover,
        QSplitter#mainSplitter::handle:pressed {{
            background-color: {accent};
        }}

        QFrame#ageControl {{
            background-color: {bg};
            border-radius: 8px;
        }}
        QLabel#statusMessage {{
            color: {text_muted};
            font-size: {muted_size}px;
            font-weight: {muted_weight};
            padding-right: 12px;
        }}
        QLabel#statusSeparator {{
            color: {border};
            font-size: {muted_size}px;
            padding: 0 {SPACE_SM}px;
        }}

        
        QLabel#chipEmpty, QLabel#chipInactive, QLabel#chipSpace {{ 
            background-color: transparent;
            color: {text_muted};
            border: none;
            border-radius: 0;
            padding: {SPACE_XS}px {SPACE_SM}px;
            font-size: {muted_size}px;
            font-weight: {muted_weight};
        }}
        QTreeView {{
            background-color: {surface};
            alternate-background-color: {bg};
            border: none;
            outline: none;
            color: {text};
            font-weight: {body_weight};
        }}
        QTreeView::viewport,
        QAbstractScrollArea::viewport {{
            background-color: {surface};
        }}
        QTreeView::item {{ 
            padding: {SPACE_MD}px {SPACE_SM}px;
            border: none;
        }}
        QTreeView::item:alternate {{
            background-color: {bg};
        }}
        QTreeView::item:hover {{ background-color: {surface_hover}; color: {text}; }}
        QTreeView::item:selected {{ background-color: {accent}; color: {on_accent}; }}
        QTreeView::item:selected:hover {{ background-color: {accent_pressed}; color: #fff; }}
        
        QTreeView::indicator {{
            width: 16px;
            height: 16px;
            border: 1px solid {border};
            border-radius: 4px;
            background-color: {surface};
        }}
        QTreeView::indicator:hover {{
            border-color: {text_muted};
            background-color: {bg};
        }}
        QTreeView::indicator:checked {{
            background-color: {accent};
            border-color: {accent};
            image: url("{base_dir}/assets/check.svg");
        }}

        QTreeView::branch {{
            border: none;
        }}
        QTreeView::branch:has-children:closed,
        QTreeView::branch:closed:has-children:has-siblings,
        QTreeView::branch:closed:has-children:!has-siblings {{
            image: url("{base_dir}/assets/{tree_right_arrow}");
        }}
        QTreeView::branch:has-children:open,
        QTreeView::branch:open:has-children:has-siblings,
        QTreeView::branch:open:has-children:!has-siblings {{
            image: url("{base_dir}/assets/{tree_down_arrow}");
        }}
        QTreeView::branch:has-children:hover:closed,
        QTreeView::branch:hover:closed:has-children:has-siblings,
        QTreeView::branch:hover:closed:has-children:!has-siblings {{
            image: url("{base_dir}/assets/{tree_right_hover}");
        }}
        QTreeView::branch:has-children:hover:open,
        QTreeView::branch:hover:open:has-children:has-siblings,
        QTreeView::branch:hover:open:has-children:!has-siblings {{
            image: url("{base_dir}/assets/{tree_down_hover}");
        }}


        /* ── Scrollbar ───────────────────────────────────────────────────── */
        QScrollBar:vertical {{
            background: {bg};
            width: 12px;
            border: none;
            border-left: 1px solid {border};
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {border};
            border-radius: 5px;
            min-height: 32px;
            margin: {SPACE_XS}px {SPACE_XS}px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {text_muted};
        }}
        QScrollBar::handle:vertical:pressed {{
            background: {text_muted};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
            background: none;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: none;
        }}
        QScrollBar:horizontal {{
            background: {bg};
            height: 12px;
            border: none;
            border-top: 1px solid {border};
        }}
        QScrollBar::handle:horizontal {{
            background: {border};
            border-radius: 5px;
            min-width: 32px;
            margin: {SPACE_XS}px {SPACE_XS}px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {text_muted};
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
            font-size: {title_size}px;
            font-weight: {header_weight};
            padding: 0;
        }}
        QPushButton#scrollTopBtn:hover {{
            background-color: {accent_hover};
        }}
        QPushButton#scrollTopBtn:pressed {{
            background-color: {accent_pressed};
        }}

        QHeaderView {{
            background-color: {surface};
        }}
        QHeaderView::section {{
            background-color: {surface};
            color: {text};
            padding: {SPACE_MD}px {SPACE_LG}px;
            border: none;
            border-bottom: 1px solid {border};
            font-weight: {header_weight};
            color: {text_muted};
            font-size: {header_size}px;
            letter-spacing: 0.4px;
        }}

        QLineEdit#filterSearch {{
            padding: {SPACE_XS}px {SPACE_SM}px;
        }}
        QLineEdit#scanExclusionInput {{
            background-color: {surface};
            border: 1px solid {border};
            border-radius: 6px;
            color: {text};
            padding: {SPACE_XS}px {SPACE_SM}px;
            selection-background-color: {accent_tint};
        }}
        QLineEdit#scanExclusionInput:focus {{
            border-color: {accent};
        }}
        QHeaderView::section:hover {{
            background-color: {accent_tint};
            color: {accent};
        }}

        QPushButton:disabled {{
            background-color: {bg};
            color: {text_muted};
            border: 1px solid {border};
        }}

        QPushButton#collapseAll {{
            background-color: {surface};
            color: {text_muted};
            border: 1px solid {border};
            border-radius: 6px;
            padding: {SPACE_MD}px {SPACE_LG}px;
            font-weight: {body_weight};
        }}
        QPushButton#collapseAll:hover {{
            background-color: {bg};
            border-color: {border};
        }}

        QPushButton#pageNavBtn {{
            background-color: {surface};
            color: {accent};
            border: 1px solid {border};
            border-radius: 7px;
            padding: 0;
            font-size: {body_size}px;
            font-weight: {body_weight};
        }}
        QPushButton#pageNavBtn:hover {{
            background-color: {accent_tint};
            border-color: {accent};
        }}
        QPushButton#pageNavBtn:pressed {{
            background-color: {accent_tint};
        }}
        QPushButton#pageNavBtn:disabled {{
            background-color: {bg};
            color: {text_muted};
            border: 1px solid {border};
        }}

        QLabel#pageInfo {{
            background-color: {bg};
            color: {text_muted};
            border: 1px solid {border};
            border-radius: 7px;
            padding: {SPACE_SM}px {SPACE_MD}px;
            font-size: {muted_size}px;
            font-weight: {muted_weight};
        }}


        /* ── SpinBox ─────────────────────────────────────────────────────── */
        QSpinBox {{
            background-color: {surface};
            border: 1px solid {border};
            border-radius: 6px;
            padding: {SPACE_XS}px {SPACE_SM}px;
            color: {text};
            selection-background-color: {surface_hover};
            selection-color: {text};
        }}
        QComboBox {{
            background-color: {surface};
            border: 1px solid {border};
            border-radius: 6px;
            padding: {SPACE_SM}px {SPACE_XL}px {SPACE_SM}px {SPACE_SM}px;
            color: {text};
        }}
        QComboBox:hover {{
            border-color: {border};
            background-color: {surface_hover};
        }}
        QComboBox:focus {{
            border-color: {accent};
        }}
        QComboBox#themeSelector {{
            min-width: 84px;
            padding: {SPACE_SM}px {SPACE_XL}px {SPACE_SM}px {SPACE_MD}px;
        }}
        QComboBox::drop-down {{
            border: none;
            width: 28px;
            subcontrol-origin: padding;
            subcontrol-position: top right;
        }}
        QComboBox::down-arrow {{
            image: url("{base_dir}/assets/{combo_arrow}");
            width: 10px;
            height: 6px;
        }}
        QSpinBox:focus {{
            border-color: {accent};
        }}
        QSpinBox#scanExclusionSize {{
            min-height: 30px;
            max-height: 30px;
        }}
        QComboBox#scanExclusionUnit {{
            background-color: {surface};
            border: 1px solid {border};
            border-radius: 6px;
            color: {text};
            padding: {SPACE_XS}px {SPACE_SM}px;
            min-height: 22px;
        }}
        QComboBox#scanExclusionUnit:focus {{
            border-color: {accent};
        }}
        QSpinBox::up-button, QSpinBox::down-button {{
            background-color: {bg};
            border: none;
            border-left: 1px solid {border};
            width: 20px;
        }}
        QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
            background-color: {surface_hover};
        }}
        QSpinBox::up-button:pressed, QSpinBox::down-button:pressed {{
            background-color: {border};
        }}
        QSpinBox::up-arrow {{
            width: 10px; height: 6px;
            image: url("{base_dir}/assets/{spin_up_arrow}");
        }}
        QSpinBox::down-arrow {{
            width: 10px; height: 6px;
            image: url("{base_dir}/assets/down_arrow.svg");
        }}

        /* ── Base button — tactile feedback ─────────────────────────────── */
        QPushButton {{
            background-color: {bg};
            border: 1px solid {border};
            border-radius: 6px;
            padding: {SPACE_MD}px {SPACE_LG}px;
            color: {text};
            font-weight: {body_weight};
            outline: none;
        }}
        QPushButton:hover {{
            background-color: {surface_hover};
            border-color: {border};
            color: {text};
        }}
        QPushButton:pressed {{
            background-color: {border};
            color: {text};
        }}
        QPushButton:disabled {{
            color: {text_muted};
            border: 1px dashed {border};
            background-color: {bg};
        }}
        QPushButton#primaryBtn {{
            background-color: {accent};
            color: {on_accent};
            border: 1px solid {accent};
            border-radius: 6px;
            padding: {SPACE_MD}px {SPACE_LG}px;
            font-weight: {body_weight};
        }}
        QPushButton#primaryBtn:hover {{
            background-color: {accent_hover};
            border-color: {accent_hover};
        }}
        QPushButton#primaryBtn:pressed {{
            background-color: {accent_pressed};
            border-color: {accent_pressed};
        }}
        QPushButton#destructiveBtn {{
            background-color: {status_danger};
            color: white;
            border: 1px solid {status_danger};
            border-radius: 6px;
            padding: {SPACE_MD}px {SPACE_LG}px;
            font-weight: {body_weight};
        }}
        QPushButton#destructiveBtn:hover {{
            background-color: {status_danger};
            border-color: {status_danger};
            color: white;
        }}
        QPushButton#destructiveBtn:pressed {{
            background-color: {status_danger_bg};
            border-color: {status_danger};
            color: {status_danger};
        }}
        QPushButton#destructiveBtn:disabled {{
            background-color: transparent;
            color: {text_muted};
            border: 1px solid {border};
        }}
        
        QPushButton#ghostBtn,
        QPushButton#collapseAll,
        QPushButton#resetFilters,
        QPushButton#resetBtn,
        QPushButton#filterCloseBtn,
        QPushButton#closeSidebar,
        QPushButton#deleteBtn {{
            background-color: transparent;
            color: {text_muted};
            border: 1px solid {border};
            border-radius: 6px;
            padding: {SPACE_MD}px {SPACE_LG}px;
            font-weight: {body_weight};
        }}
        QPushButton#ghostBtn:hover,
        QPushButton#collapseAll:hover,
        QPushButton#resetFilters:hover,
        QPushButton#resetBtn:hover,
        QPushButton#filterCloseBtn:hover,
        QPushButton#closeSidebar:hover,
        QPushButton#deleteBtn:hover {{
            background-color: {surface_hover};
            border-color: {border};
            color: {text};
        }}
        QPushButton#ghostBtn:pressed,
        QPushButton#collapseAll:pressed,
        QPushButton#resetFilters:pressed,
        QPushButton#resetBtn:pressed,
        QPushButton#filterCloseBtn:pressed,
        QPushButton#closeSidebar:pressed,
        QPushButton#deleteBtn:pressed {{
            background-color: {bg};
            color: {text};
        }}


        /* ── Filter toggle ───────────────────────────────────────────────── */
        QPushButton#filterBtn {{
            background-color: transparent;
            color: {text_muted};
            font-weight: {body_weight};
            border: 1px solid {border};
            border-radius: 6px;
            padding: {SPACE_MD}px {SPACE_LG}px;
        }}
        QPushButton#filterBtn:hover {{
            background-color: {surface_hover};
            border-color: {border};
            color: {text};
        }}
        QPushButton#filterBtn:checked {{
            background-color: {accent};
            border: 1px solid {accent};
            color: {on_accent};
        }}
        QPushButton#filterBtn:pressed {{
            background-color: {accent_pressed};
            border-color: {accent_pressed};
            color: white;
        }}

        QPushButton#deleteBtn:disabled {{
            color: {text_muted};
            border: 1px solid {border};
            background-color: transparent;
        }}
        QPushButton#deleteBtn[armed="true"] {{
            background-color: {status_danger};
            color: white;
            border: 1px solid {status_danger};
        }}
        QPushButton#deleteBtn[armed="true"]:hover {{
            background-color: {status_danger};
            border-color: {status_danger};
            color: white;
        }}
        QPushButton#deleteBtn[armed="true"]:pressed {{
            background-color: {status_danger_bg};
            border-color: {status_danger};
            color: {status_danger};
        }}
        QPushButton#deleteBtn[armed="true"]:disabled {{
            background-color: transparent;
            color: {text_muted};
            border: 1px solid {border};
        }}

        /* ── Sidebar actions ─────────────────────────────────────────────── */
        QPushButton#resetExclusions {{
            background-color: transparent;
            color: {accent};
            border: none;
            padding: {SPACE_XS}px 0;
            font-size: {muted_size}px;
            font-weight: {muted_weight};
            text-align: left;
        }}
        QPushButton#resetExclusions:hover {{
            color: {accent_hover};
            text-decoration: underline;
        }}
        QFrame#accordionHeader {{
            background-color: transparent;
            border: none;
            border-radius: 6px;
            padding: {SPACE_SM}px 0;
        }}
        QFrame#accordionHeader:hover {{
            background-color: {surface_hover};
        }}
        QLabel#accordionChevron {{
            background-color: transparent;
            color: {text_muted};
            font-size: {header_size}px;
            font-weight: {header_weight};
        }}
        QLabel#accordionTitle {{
            background-color: transparent;
            color: {text_muted};
            font-size: {header_size}px;
            font-weight: {header_weight};
            letter-spacing: 0.4px;
        }}
        QFrame#accordionHeader:hover QLabel#accordionChevron,
        QFrame#accordionHeader:hover QLabel#accordionTitle {{
            color: {text};
        }}

        QLabel#manualLabel {{
            color: {text_muted};
            font-size: {muted_size}px;
            font-weight: {muted_weight};
        }}

        /* ── Delete button ───────────────────────────────────────────────── */
        /* ── Filter Close button ─────────────────────────────────────────── */
        /* ── Reset (ghost) ───────────────────────────────────────────────── */
        /* ── Link / Clear (text-only) ────────────────────────────────────── */
        QPushButton#linkBtn {{
            background: transparent;
            border: none;
            color: {accent};
            font-size: {muted_size}px;
            font-weight: {muted_weight};
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
            padding: {SPACE_SM}px {SPACE_SM}px;
            font-size: {muted_size}px;
            font-weight: {muted_weight};
            text-align: left;
            qproperty-icon: url("{base_dir}/assets/x-circle.svg");
            qproperty-iconSize: 14px 14px;
        }}
        
        QPushButton#clearDates:hover {{
            color: {text};
            background-color: {surface_hover};
            border-radius: 4px;
        }}

        QLabel#appTitle {{
            font-weight: {title_weight};
            font-size: {title_size}px;
            color: {text};
            letter-spacing: 0px;
            padding-right: 4px;
        }}

        QLabel#sectionLabel {{
            color: {text_muted};
            font-size: {header_size}px;
            font-weight: {header_weight};
            letter-spacing: 0.4px;
        }}

        QLabel#mutedLabel {{
            color: {text_muted};
            font-size: {muted_size}px;
            font-weight: {muted_weight};
        }}

        QWidget#emptyState {{
            background-color: {surface};
        }}
        QLabel#emptyIcon {{
            padding: 0;
            qproperty-alignment: AlignCenter;
        }}
        QLabel#emptyTitle {{
            font-size: {title_size}px;
            font-weight: {title_weight};
            color: {text};
        }}
        QLabel#emptySub {{
            font-size: {muted_size}px;
            font-weight: {muted_weight};
            color: {text_muted};
        }}
        QLabel#scanStats {{
            font-size: {body_size}px;
            font-weight: 600;
            color: {text_muted};
        }}
        QLabel#noResultsTitle {{
            font-size: {title_size + 2}px;
            font-weight: {title_weight};
            color: {text};
        }}
        QLabel#noResultsSub {{
            font-size: {body_size}px;
            font-weight: {muted_weight};
            color: {text_muted};
        }}
        QLabel#scanPathValue {{
            background-color: transparent;
            border: none;
            padding: 0;
            color: {text_muted};
            font-size: {muted_size}px;
            font-weight: {muted_weight};
        }}

        QLabel#agePill {{
            background-color: {accent};
            color: {on_accent};
            border-radius: 10px;
            padding: {SPACE_XS}px {SPACE_MD}px;
            font-size: {muted_size}px;
            font-weight: {muted_weight};
        }}

        QLabel#statusLabel {{
            color: {text_muted};
            font-size: {muted_size}px;
            font-weight: {muted_weight};
        }}

        QFrame#divider {{
            background-color: {border};
        }}
        
        QLineEdit {{
            background-color: {surface};
            border: 1px solid {border};
            border-radius: 6px;
            padding: {SPACE_SM}px {SPACE_SM}px;
            color: {text};
        }}
        QLineEdit:focus {{ border-color: {accent}; }}
        
        QSlider {{ background: transparent; }}
        QSlider::groove:horizontal {{
            border: none;
            height: 6px;
            background: {border};
            border-radius: 3px;
        }}
        QSlider::handle:horizontal {{
            background: {accent};
            border: 2px solid {surface};
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
            border: 1px solid {border};
            border-radius: 6px;
            padding: {SPACE_SM}px {SPACE_MD}px;
            background-color: {surface};
            font-size: {body_size}px;
            color: {text};
        }}
        QSpinBox:focus {{
            border-color: {accent};
        }}
        QSpinBox::up-button, QSpinBox::down-button {{
            width: 20px;
            border: none;
            background-color: {bg};
        }}
        QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
            background-color: {surface_hover};
        }}

        
        
        
        
        /* ── Scrollbars ──────────────────────────────────────────────────── */
        QScrollBar:vertical {{
            border: none;
            background-color: transparent;
            width: 10px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background-color: {border};
            border-radius: 5px;
            min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{
            background-color: {text_muted};
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
            background-color: {border};
            border-radius: 5px;
            min-width: 30px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background-color: {text_muted};
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0px;
        }}
        
        QComboBox QAbstractItemView {{
            border: 1px solid {border};
            border-radius: 6px;
            background-color: {surface};
            color: {text};
            outline: none;
            selection-background-color: {accent_tint};
            selection-color: {text};
            padding: {SPACE_XS}px;
        }}
        QComboBox QAbstractItemView::item {{
            color: {text};
            background-color: transparent;
            border: none;
            outline: none;
            padding: {SPACE_SM}px {SPACE_MD}px;
            min-height: 20px;
        }}
        QComboBox QAbstractItemView::item:selected {{
            color: {text};
            background-color: {accent_tint};
            border: none;
            outline: none;
        }}
        
        QCalendarWidget {{ 
            background-color: {bg}; 
            color: {text}; 
            font-family: "{FONT_FAMILY}", sans-serif;
            font-size: {body_size}px;
        }}
        QCalendarWidget QAbstractItemView {{ font-size: {body_size}px; }}
        QCalendarWidget QWidget#qt_calendar_navigationbar {{ font-size: {body_size}px; }}
    """
    app.setStyleSheet(base_style + custom_style)



