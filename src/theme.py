import os

def apply_theme(app, theme_name="dark"):
    base_dir = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
    # Single-theme application: keep only dark mode.
    base_style = ""
    bg_main = "#ffffff"
    bg_sec = "#f6f8fa"
    bg_hover = "#f3f4f6"
    border = "#d0d7de"
    text = "#24292f"
    text_muted = "#57606a"
    accent = "#0969da"
    chip_red = "#cf222e"
    chip_yellow = "#9a6700"
    chip_green = "#1a7f37"
    chip_red_bg = "#ffebe9"
    chip_yellow_bg = "#fff8c5"
    chip_green_bg = "#dafbe1"

    custom_style = f"""
        * {{ font-family: "Segoe UI", "Inter", sans-serif; font-size: 13px; }}
        QMainWindow {{ background-color: {bg_main}; }}
        QLabel {{ color: {text}; }}
        QWidget#topbar {{
            background-color: {bg_sec};
            border-bottom: 1px solid {border};
        }}
        QWidget#filterPanel {{
            background-color: {bg_sec};
            border-bottom: 1px solid {border};
        }}
        QWidget#statusbar {{
            background-color: transparent;
            border-top: none;
            color: {text_muted};
        }}
        QLabel#statusLabel {{ color: {text_muted}; }}
        
        QLabel#chipEmpty {{ 
            background-color: {chip_green_bg}; 
            color: {chip_green}; 
            border: 1px solid #bccdc1;
            border-radius: 6px; 
            padding: 2px 8px; 
            font-weight: 600; 
            font-size: 11px; 
        }}
        QLabel#chipInactive {{ 
            background-color: {chip_yellow_bg}; 
            color: {chip_yellow}; 
            border: 1px solid #d8d0a4;
            border-radius: 6px; 
            padding: 2px 8px; 
            font-weight: 600; 
            font-size: 11px; 
        }}
        QLabel#chipSpace {{ 
            background-color: #f1f5f9; 
            color: {text}; 
            border: 1px solid {border};
            border-radius: 6px; 
            padding: 2px 8px; 
            font-weight: 600; 
            font-size: 11px; 
        }}
        QTreeView {{
            background-color: {bg_main};
            alternate-background-color: {bg_sec};
            border: none;
            border-top: 1px solid {border};
            outline: none;
            color: {text};
        }}
        QTreeView::item {{ padding: 7px 4px; border: none; }}
        QTreeView::item:hover {{ background-color: {bg_hover}; color: {text}; }}
        QTreeView::item:selected {{ background-color: {accent}; color: #fff; }}
        QTreeView::item:selected:hover {{ background-color: #0353a4; color: #fff; }}
        
        QTreeView::indicator {{
            width: 14px;
            height: 14px;
            border: 1px solid {text_muted};
            border-radius: 3px;
            background-color: {bg_main};
        }}
        QTreeView::indicator:hover {{
            border-color: {text};
            background-color: {bg_hover};
        }}
        QTreeView::indicator:checked {{
            background-color: {accent};
            border-color: {accent};
            image: url({base_dir}/check.svg);
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
            background-color: {bg_sec};
            padding: 8px 6px;
            border: none;
            border-bottom: 1px solid {border};
            border-right: 1px solid {border};
            font-weight: bold; color: {text_muted};
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
            border-bottom: 2px solid {border};
            border-radius: 7px;
            padding: 6px 16px;
            color: {text};
            font-weight: 500;
            outline: none;
        }}
        QPushButton:hover {{
            background-color: {bg_hover};
            border-color: #9ca3af;
            border-bottom-color: #9ca3af;
            color: {text};
        }}
        QPushButton:pressed {{
            background-color: {border};
            border-color: {text_muted};
            border-bottom-width: 1px;
            padding: 7px 16px 5px 16px;
            color: {text};
        }}
        QPushButton:disabled {{
            color: #b0b8c1;
            border: 1px dashed {border};
            border-bottom: 1px dashed {border};
            background-color: #f9fafb;
        }}
        QPushButton#primaryBtn {{
            background-color: {accent};
            border: 1px solid #0358b6;
            border-bottom: 2px solid #0256af;
            color: #ffffff;
            font-weight: 600;
        }}
        QPushButton#primaryBtn:hover {{
            background-color: #0761d1;
            border-color: #0358b6;
            border-bottom-color: #024fa3;
            color: #ffffff;
        }}
        QPushButton#primaryBtn:pressed {{
            background-color: #0353a4;
            border-color: #0256af;
            border-bottom-width: 1px;
            color: #ffffff;
            padding: 7px 16px 5px 16px;
        }}
        QPushButton#ghostBtn {{
            background-color: transparent;
            color: {text_muted};
            border: 1px solid {border};
            border-bottom: 2px solid {border};
        }}
        QPushButton#ghostBtn:hover {{
            background-color: {bg_hover};
            color: {text};
            border-color: #9ca3af;
            border-bottom-color: #9ca3af;
        }}
        QPushButton#ghostBtn:pressed {{
            background-color: {bg_sec};
            border-bottom-width: 1px;
            padding: 7px 16px 5px 16px;
        }}

        /* ── Filter toggle ───────────────────────────────────────────────── */
        QPushButton#filterBtn {{
            color: {accent};
            font-weight: 600;
            border: 1px solid {accent};
            border-bottom: 2px solid #0358b6;
            background-color: #eef4fd;
        }}
        QPushButton#filterBtn:hover {{
            background-color: {accent};
            border-bottom-color: #0358b6;
            color: white;
        }}
        QPushButton#filterBtn:checked {{
            background-color: {accent};
            border: 1px solid #0358b6;
            border-bottom: 2px solid #0256af;
            color: white;
        }}
        QPushButton#filterBtn:pressed {{
            background-color: #0353a4;
            border-bottom-width: 1px;
            color: white;
            padding: 7px 14px 5px 14px;
        }}

        /* ── Delete button ───────────────────────────────────────────────── */
        QPushButton#deleteBtn {{
            color: {chip_red};
            font-weight: 600;
            border: 1px solid {chip_red};
            border-bottom: 2px solid #a40e26;
            background-color: #fff0f1;
        }}
        QPushButton#deleteBtn:hover {{
            background-color: {chip_red};
            border-color: #a40e26;
            border-bottom-color: #a40e26;
            color: white;
        }}
        QPushButton#deleteBtn:pressed {{
            background-color: #a40e26;
            border-bottom-width: 1px;
            color: white;
            padding: 7px 14px 5px 14px;
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
            background-color: #fff0f1;
            border-color: {chip_red};
            border-bottom-color: #a40e26;
            color: {chip_red};
        }}
        QPushButton#filterCloseBtn:pressed {{
            background-color: #ffcdd0;
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
            border-color: #9ca3af;
            border-bottom-color: #9ca3af;
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
        }}
        QPushButton#linkBtn:hover {{
            color: {accent};
        }}
        QPushButton#linkBtn:pressed {{
            color: {text};
        }}

        QLabel#appTitle {{
            font-weight: 800;
            font-size: 14px;
            color: {accent};
            text-transform: uppercase;
            letter-spacing: 1px;
            padding-right: 4px;
        }}

        QLabel#sectionLabel {{
            color: {text_muted};
            font-size: 10px;
            font-weight: bold;
            letter-spacing: 0.5px;
        }}

        QLabel#mutedLabel {{
            color: {text_muted};
        }}

        QWidget#emptyState {{
            background-color: {bg_main};
        }}
        QLabel#emptyIcon {{
            font-size: 52px;
            padding-bottom: 4px;
        }}
        QLabel#emptyTitle {{
            font-size: 18px;
            font-weight: 700;
            color: {text};
            padding-bottom: 2px;
        }}
        QLabel#emptySub {{
            font-size: 13px;
            color: {text_muted};
            line-height: 1.5;
        }}

        QLabel#agePill {{
            background-color: #0969da;
            color: #ffffff;
            border-radius: 10px;
            font-weight: bold;
            font-size: 11px;
            padding: 0px 7px;
        }}

        QLabel#statusLabel {{
            color: {text_muted};
            font-size: 12px;
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
        
        QSlider {{ }}
        QSlider::groove:horizontal {{
            background: {border}; height: 4px; border-radius: 2px;
        }}
        QSlider::handle:horizontal {{
            background: {accent}; width: 14px; height: 14px;
            margin: -5px 0; border-radius: 7px;
        }}
        QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; }}
        
        QDateEdit {{
            background-color: {bg_main};
            border: 1px solid {border};
            border-radius: 6px;
            padding: 4px 8px;
            color: {text};
        }}
        QDateEdit::drop-down {{ border: none; }}
        QCalendarWidget {{ background-color: {bg_sec}; color: {text}; }}
    """
    app.setStyleSheet(base_style + custom_style)
