import os

def apply_theme(app, theme_name="dark"):
    base_dir = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
    # Single-theme application: keep only dark mode.
    base_style = ""
    bg_main = "#0d1117"
    bg_sec = "#161b22"
    bg_hover = "#21262d"
    border = "#2b313a"
    text = "#c9d1d9"
    text_muted = "#8b949e"
    accent = "#58a6ff"
    chip_red = "#f85149"
    chip_yellow = "#e3b341"
    chip_green = "#3fb950"
    chip_red_bg = "#3d1817"
    chip_yellow_bg = "#3d2a10"
    chip_green_bg = "#164b35"

    custom_style = f"""
        * {{ font-family: "Segoe UI", "Inter", sans-serif; font-size: 13px; }}
        QMainWindow {{ background-color: {bg_main}; }}
        QWidget#topbar {{
            background-color: {bg_sec};
            border-bottom: 1px solid {border};
        }}
        QWidget#filterPanel {{
            background-color: {bg_main};
            border-bottom: 2px solid {accent};
        }}
        QWidget#statusbar {{
            background-color: transparent;
            border-top: none;
            color: {text_muted};
        }}
        QLabel#statusLabel {{ color: {text_muted}; }}
        
        QLabel#chipEmpty {{ background-color: transparent; color: {text_muted}; border-radius: 10px; padding: 2px 10px; font-weight: 600; font-size: 12px; border: none; }}
        QLabel#chipInactive {{ background-color: transparent; color: {text_muted}; border-radius: 10px; padding: 2px 10px; font-weight: 600; font-size: 12px; border: none; }}
        QLabel#chipSpace {{ background-color: transparent; color: {text_muted}; border-radius: 10px; padding: 2px 10px; font-weight: 600; font-size: 12px; border: none; }}
        QTreeView {{
            background-color: {bg_main};
            alternate-background-color: {bg_sec};
            border: none; outline: none;
            color: {text};
        }}
        QTreeView::item {{ padding: 7px 4px; border-bottom: 1px solid {border}; }}
        QTreeView::item:selected {{ background-color: {accent}; color: #fff; }}
        QTreeView::item:hover {{ background-color: #1f2a39; }}
        
        QHeaderView::section {{
            background-color: {bg_sec};
            padding: 8px 6px;
            border: none;
            border-bottom: 1px solid {border};
            border-right: 1px solid {border};
            font-weight: bold; color: {text_muted};
        }}
        
        /* ── Base button — tactile feedback ─────────────────────────────── */
        QPushButton {{
            background-color: {bg_hover};
            border: 1px solid {border};
            border-radius: 6px;
            padding: 6px 14px;
            color: {text};
            font-weight: 500;
            outline: none;
        }}
        QPushButton:hover {{
            background-color: {bg_sec};
            border-color: {accent};
            color: {accent};
        }}
        QPushButton:pressed {{
            background-color: {bg_main};
            border-color: {accent};
            color: {text};
            padding: 7px 14px 5px 14px;
        }}
        QPushButton:disabled {{
            color: {text_muted};
            border-color: {border};
            background-color: {bg_main};
        }}
        QPushButton#primaryBtn {{
            background-color: {accent};
            border-color: {accent};
            color: #ffffff;
            font-weight: 600;
        }}
        QPushButton#primaryBtn:hover {{
            background-color: #79b8ff;
            border-color: #79b8ff;
            color: #0d1117;
        }}
        QPushButton#primaryBtn:pressed {{
            background-color: #3b82d6;
            border-color: #3b82d6;
            color: #ffffff;
        }}
        QPushButton#ghostBtn {{
            background-color: transparent;
            color: {text_muted};
            border-color: {border};
        }}
        QPushButton#ghostBtn:hover {{
            background-color: {bg_hover};
            color: {text};
            border-color: {text_muted};
        }}

        /* ── Filter toggle ───────────────────────────────────────────────── */
        QPushButton#filterBtn {{
            color: {accent};
            font-weight: bold;
            border-color: {accent};
        }}
        QPushButton#filterBtn:hover {{
            background-color: {accent};
            color: white;
        }}
        QPushButton#filterBtn:checked {{
            background-color: {accent};
            color: white;
            border-color: {accent};
        }}
        QPushButton#filterBtn:pressed {{
            background-color: {bg_sec};
            color: {accent};
            padding: 7px 14px 5px 14px;
        }}

        /* ── Delete button ───────────────────────────────────────────────── */
        QPushButton#deleteBtn {{
            color: #f85149;
            font-weight: bold;
            border-color: #da3633;
        }}
        QPushButton#deleteBtn:hover {{
            background-color: #da3633;
            border-color: #f85149;
            color: white;
        }}
        QPushButton#deleteBtn:pressed {{
            background-color: #b91c1c;
            border-color: #da3633;
            color: white;
            padding: 7px 14px 5px 14px;
        }}

        /* ── Apply (accent-filled) ───────────────────────────────────────── */
        QPushButton#applyBtn {{
            background-color: {accent};
            border: 1px solid {accent};
            color: white;
            font-weight: 600;
            padding: 6px 16px;
        }}
        QPushButton#applyBtn:hover {{
            background-color: {text};
            border-color: {text};
            color: {bg_main};
        }}
        QPushButton#applyBtn:pressed {{
            background-color: {border};
            border-color: {border};
            color: {bg_main};
            padding: 7px 16px 5px 16px;
        }}

        /* ── Reset (ghost) ───────────────────────────────────────────────── */
        QPushButton#resetBtn {{
            background-color: transparent;
            border: 1px solid {border};
            color: {text_muted};
        }}
        QPushButton#resetBtn:hover {{
            border-color: {text_muted};
            background-color: {bg_hover};
            color: {text};
        }}
        QPushButton#resetBtn:pressed {{
            background-color: {bg_sec};
            color: {text_muted};
            padding: 7px 14px 5px 14px;
        }}

        /* ── Link / Clear (text-only) ────────────────────────────────────── */
        QPushButton#linkBtn {{
            background: transparent;
            border: none;
            color: {text_muted};
            font-size: 11px;
            text-decoration: underline;
            padding: 0;
        }}
        QPushButton#linkBtn:hover {{
            color: {accent};
        }}
        QPushButton#linkBtn:pressed {{
            color: {text};
        }}

        QLabel#appTitle {{
            font-weight: bold;
            font-size: 15px;
            color: {accent};
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

        QLabel#agePill {{
            background-color: #f97316;
            color: #ffffff;
            border-radius: 11px;
            font-weight: bold;
            font-size: 11px;
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
            border: 2px solid #9fb3c8;
            background-color: transparent;
        }}
        QRadioButton::indicator:hover {{
            border-color: #c2d2e2;
            background-color: #1b2431;
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
        QSpinBox {{
            background-color: {bg_main};
            border: 1px solid {border};
            border-radius: 4px;
            padding: 4px;
            color: {text};
        }}
        QSpinBox:focus {{ border-color: {accent}; }}
        QSpinBox::up-button, QSpinBox::down-button {{
            width: 20px;
            background: transparent;
            border-left: 1px solid {border};
        }}
        QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
            background: {bg_sec};
        }}
        QSpinBox::up-arrow {{
            width: 10px; height: 6px;
            image: url({base_dir}/up_arrow.svg);
        }}
        QSpinBox::down-arrow {{
            width: 10px; height: 6px;
            image: url({base_dir}/down_arrow.svg);
        }}
        QSpinBox::up-arrow:hover {{
            image: url({base_dir}/up_arrow_hover.svg);
        }}
        QSpinBox::down-arrow:hover {{
            image: url({base_dir}/down_arrow_hover.svg);
        }}
        QCalendarWidget {{ background-color: {bg_sec}; color: {text}; }}
    """
    app.setStyleSheet(base_style + custom_style)
