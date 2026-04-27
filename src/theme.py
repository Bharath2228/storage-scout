import os
from PyQt6.QtGui import QFont

def apply_theme(app, theme_name="dark"):
    base_dir = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
    # Single-theme application: keep only dark mode.
    base_style = ""
    # Modern Color Palette
    bg_main = "#ffffff"       # background
    bg_sec = "#f8fafc"        # surface
    bg_hover = "#f1f5f9"      # subtle hover
    border = "#e2e8f0"        # border
    text = "#1e293b"          # text_primary
    text_muted = "#64748b"    # text_secondary
    accent = "#2563eb"        # primary blue
    
    chip_red = "#ef4444"      # danger
    chip_yellow = "#f59e0b"   # warning
    chip_green = "#10b981"    # success
    
    # Derived light backgrounds for chips
    chip_red_bg = "#fef2f2"
    chip_yellow_bg = "#fffbeb"
    chip_green_bg = "#f0fdf4"

    # Set application-wide font
    font = QFont("Segoe UI")
    font.setPointSize(9)
    app.setFont(font)

    custom_style = f"""
        * {{ font-family: "Segoe UI", "Inter", system-ui, sans-serif; font-size: 9pt; }}
        
        QMessageBox, QDialog {{
            background-color: white;
        }}
        QMessageBox QLabel, QDialog QLabel {{
            color: #1e293b;
            font-size: 10pt;
        }}
        QMessageBox QPushButton, QDialog QPushButton {{
            background-color: #f1f5f9;
            border: 1px solid #e2e8f0;
            border-radius: 6px;
            padding: 6px 16px;
            min-width: 80px;
            color: #1e293b;
        }}
        QMessageBox QPushButton:hover, QDialog QPushButton:hover {{
            background-color: #e2e8f0;
        }}
        QMainWindow {{ background-color: #f1f5f9; }}
        QLabel {{ color: {text}; }}
        QWidget#topbar {{
            background-color: {bg_sec};
            border-bottom: 1px solid {border};
        }}
        QWidget#sidebar {{
            background-color: #fafbfc;
            border-right: 1px solid {border};
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
            background-color: #e2e8f0;
            max-height: 1px;
            margin: 8px 16px;
        }}
        
        QWidget#displayModeSection,
        QWidget#dateRangeSection,
        QWidget#ageThresholdSection {{
            margin-bottom: 8px;
        }}
        
        QLabel#displayModeHeader,
        QLabel#dateRangeHeader,
        QLabel#ageThresholdHeader {{
            background-color: #f1f5f9;
            padding: 10px 16px;
            font-weight: 600;
            font-size: 10pt;
            color: #1e293b;
            margin-bottom: 12px;
            border-radius: 6px;
        }}

        QLabel#fromLabel, QLabel#toLabel {{
            color: #64748b;
            font-size: 9pt;
            font-weight: 500;
            padding-left: 2px;
            margin-bottom: 4px;
            margin-top: 8px;
        }}


        
        QCheckBox, QRadioButton {{
            padding: 10px 16px;
            spacing: 10px;
            color: #475569;
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
            border: 2px solid #cbd5e1;
            background-color: {bg_main};
        }}
        QCheckBox::indicator:hover {{
            border-color: #94a3b8;
            background-color: #f8fafc;
        }}
        QCheckBox::indicator:checked {{
            background-color: {accent};
            border-color: {accent};
            image: url({base_dir}/check.svg);
        }}

        
        QRadioButton::indicator {{
            width: 20px;
            height: 20px;
            border-radius: 10px;
            border: 2px solid #cbd5e1;
            background-color: {bg_main};
        }}
        QRadioButton::indicator:checked {{
            border: 6px solid {accent};
            background-color: white;
        }}
        QRadioButton::indicator:hover {{
            border-color: #94a3b8;
        }}

        QWidget#statusbar {{
            background-color: {bg_sec};
            border-top: 1px solid {border};
            color: {text_muted};
        }}
        QLabel#statusMessage {{
            color: #475569;
            font-weight: 500;
            padding-right: 24px;
        }}
        QLabel#statusSeparator {{
            color: #cbd5e1;
            font-size: 10pt;
            padding: 0 8px;
        }}

        
        QLabel#chipEmpty, QLabel#chipInactive, QLabel#chipSpace {{ 
            background-color: transparent;
            color: {text_muted};
            padding: 0 4px;
            font-weight: 600;
            font-size: 11px;
        }}
        QTreeView {{
            background-color: transparent;
            alternate-background-color: rgba(248, 250, 252, 0.5);
            border: none;
            outline: none;
            color: {text};
        }}
        QTreeView::item {{ 
            padding: 10px 4px; 
            border-bottom: 1px solid #f1f5f9; 
        }}
        QTreeView::item:hover {{ background-color: {bg_hover}; color: {text}; }}
        QTreeView::item:selected {{ background-color: {accent}; color: #fff; }}
        QTreeView::item:selected:hover {{ background-color: #0353a4; color: #fff; }}
        
        QTreeView::indicator {{
            width: 16px;
            height: 16px;
            border: 1.5px solid #cbd5e1;
            border-radius: 4px;
            background-color: {bg_main};
        }}
        QTreeView::indicator:hover {{
            border-color: #94a3b8;
            background-color: #f8fafc;
        }}
        QTreeView::indicator:checked {{
            background-color: {accent};
            border-color: {accent};
            image: url({base_dir}/check.svg);
        }}

        QTreeView::branch {{
            border-bottom: 1px solid #f1f5f9;
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
            background: #c0c8d2;
            border-radius: 5px;
            min-height: 32px;
            margin: 2px 2px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: #9ca3af;
        }}
        QScrollBar::handle:vertical:pressed {{
            background: #6b7280;
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
            background: #c0c8d2;
            border-radius: 5px;
            min-width: 32px;
            margin: 2px 2px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: #9ca3af;
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
            background-color: #0761d1;
        }}
        QPushButton#scrollTopBtn:pressed {{
            background-color: #0353a4;
        }}

        QHeaderView::section {{
            background-color: #fafbfc;
            padding: 14px 16px;
            border: none;
            border-bottom: 2px solid #e2e8f0;
            font-weight: 600;
            color: #64748b;
            font-size: 9pt;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        QHeaderView::section:hover {{
            background-color: #f1f5f9;
        }}

        QPushButton:disabled {{
            background-color: #f8fafc;
            color: #cbd5e1;
            border: 1.5px solid #f1f5f9;
        }}

        QPushButton#collapseAll {{
            background-color: white;
            color: #475569;
            border: 1.5px solid #e2e8f0;
            border-radius: 6px;
            padding: 8px 16px;
            font-weight: 500;
        }}
        QPushButton#collapseAll:hover {{
            background-color: #f8fafc;
            border-color: #cbd5e1;
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
        QSpinBox:focus {{
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
            image: url({base_dir}/up_arrow.svg);
        }}
        QSpinBox::down-arrow {{
            width: 10px; height: 6px;
            image: url({base_dir}/down_arrow.svg);
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
            border-color: #cbd5e1;
            color: {text};
        }}
        QPushButton:pressed {{
            background-color: {border};
            color: {text};
        }}
        QPushButton:disabled {{
            color: #cbd5e1;
            border: 1px dashed {border};
            background-color: #f8fafc;
        }}
        QPushButton#primaryBtn {{
            background-color: {accent};
            color: white;
            border: 1px solid {accent};
            border-bottom: 3px solid #1d4ed8;
            border-radius: 6px;
            padding: 8px 16px;
            font-weight: 600;
        }}
        QPushButton#primaryBtn:hover {{
            background-color: #1d4ed8;
        }}
        QPushButton#primaryBtn:pressed {{
            background-color: #1e40af;
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
            background-color: #eff6ff;
        }}
        QPushButton#ghostBtn:pressed {{
            background-color: #dbeafe;
        }}


        /* ── Filter toggle ───────────────────────────────────────────────── */
        QPushButton#filterBtn {{
            color: {accent};
            font-weight: 600;
            border: 1px solid {accent};
            border-bottom: 2px solid #1d4ed8;
            background-color: #eff6ff;
        }}
        QPushButton#filterBtn:hover {{
            background-color: {accent};
            border-bottom-color: #1d4ed8;
            color: white;
        }}
        QPushButton#filterBtn:checked {{
            background-color: {accent};
            border: 1px solid #1d4ed8;
            border-bottom: 2px solid #1e40af;
            color: white;
        }}
        QPushButton#filterBtn:pressed {{
            background-color: #1e40af;
            border-bottom-width: 1px;
            color: white;
            padding: 7px 14px 5px 14px;
        }}

        QPushButton#deleteBtn {{
            background-color: transparent;
            color: #dc2626;
            border: 1.5px solid #fecaca;
            border-radius: 6px;
            padding: 10px 18px;
            font-weight: 500;
        }}
        QPushButton#deleteBtn:hover {{
            background-color: #fef2f2;
            border-color: #dc2626;
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
            color: #2563eb;
            border: 1.5px solid #dbeafe;
            border-radius: 6px;
            padding: 10px 16px;
            font-weight: 500;
        }}
        QPushButton#resetFilters:hover {{
            background-color: #eff6ff;
            border-color: #2563eb;
        }}
        QPushButton#resetFilters:pressed {{
            background-color: #dbeafe;
        }}

        QLabel#manualLabel {{
            color: #64748b;
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
            background-color: #fef2f2;
        }}
        QPushButton#deleteBtn:pressed {{
            background-color: #fee2e2;
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
            border-bottom-color: #b91c1c;
            color: {chip_red};
        }}
        QPushButton#filterCloseBtn:pressed {{
            background-color: #fee2e2;
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
            border-color: #94a3b8;
            border-bottom-color: #94a3b8;
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
            color: #1d4ed8;
            text-decoration: underline;
        }}
        QPushButton#linkBtn:pressed {{
            color: {text};
        }}

        QPushButton#clearDates {{
            background-color: transparent;
            color: #64748b;
            border: none;
            padding: 6px 8px;
            font-size: 9pt;
            text-align: left;
            qproperty-icon: url({base_dir}/x-circle.svg);
            qproperty-iconSize: 14px 14px;
        }}
        
        QPushButton#clearDates:hover {{
            color: #dc2626;
            background-color: #fef2f2;
            border-radius: 4px;
        }}

        QLabel#appTitle {{
            font-weight: 700;
            font-size: 16px; /* ~12pt Header */
            color: {accent};
            text-transform: uppercase;
            letter-spacing: 0.5px;
            padding-right: 4px;
        }}

        QLabel#sectionLabel {{
            color: {text_muted};
            font-size: 11px; /* ~8pt Label */
            font-weight: 600;
            letter-spacing: 0.5px;
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
            background: #e2e8f0;
            border-radius: 3px;
        }}
        QSlider::handle:horizontal {{
            background: {accent};
            border: 2px solid white;
            width: 18px;
            height: 18px;
            margin: -6px 0;
            border-radius: 9px;
        }}
        QSlider::handle:horizontal:hover {{
            background: #1d4ed8;
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

        
        QComboBox#dateRangeInput,
        QDateEdit#dateRangeInput {{
            border: 1.5px solid #e2e8f0;
            border-radius: 6px;
            padding: 10px 12px;
            background-color: white;
            color: #334155;
            font-size: 9pt;
            min-height: 20px;
        }}
        
        QComboBox#dateRangeInput:hover,
        QDateEdit#dateRangeInput:hover {{
            border-color: #cbd5e1;
            background-color: #fafbfc;
        }}
        
        QComboBox#dateRangeInput:focus,
        QDateEdit#dateRangeInput:focus {{
            border-color: #2563eb;
            background-color: white;
        }}
        
        QComboBox#dateRangeInput::drop-down,
        QDateEdit#dateRangeInput::drop-down {{
            border: none;
            width: 30px;
            padding-right: 8px;
        }}
        
        QComboBox#dateRangeInput::down-arrow,
        QDateEdit#dateRangeInput::down-arrow {{
            image: url({base_dir}/calendar.svg);
            width: 16px;
            height: 16px;
        }}
        /* ── Scrollbars ──────────────────────────────────────────────────── */
        QScrollBar:vertical {{
            border: none;
            background-color: transparent;
            width: 10px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background-color: #cbd5e1;
            border-radius: 5px;
            min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{
            background-color: #94a3b8;
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
            background-color: #cbd5e1;
            border-radius: 5px;
            min-width: 30px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background-color: #94a3b8;
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0px;
        }}
        
        QComboBox QAbstractItemView {{
            border: 1px solid {border};
            border-radius: 6px;
            background-color: {bg_main};
            selection-background-color: #eff6ff;
            selection-color: #1e293b;
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
