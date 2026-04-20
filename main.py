import sys
import qdarktheme
from PyQt6.QtWidgets import QApplication
from src.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    
    # Setup dark theme styling
    base_style = qdarktheme.load_stylesheet("dark")
    
    # Custom styles to match the mockup
    app.setStyleSheet(base_style + """
        * { font-family: "Segoe UI", "Inter", sans-serif; font-size: 13px; }

        QMainWindow { background-color: #0d1117; }

        QWidget#topbar {
            background-color: #161b22;
            border-bottom: 1px solid #30363d;
        }
        QWidget#filterPanel {
            background-color: #0d1117;
            border-bottom: 2px solid #58a6ff;
        }
        QWidget#statusbar {
            background-color: #161b22;
            border-top: 1px solid #30363d;
            color: #8b949e;
        }

        QTreeView {
            background-color: #0d1117;
            alternate-background-color: #161b22;
            border: none; outline: none;
            color: #c9d1d9;
        }
        QTreeView::item { padding: 7px 4px; border-bottom: 1px solid #21262d; }
        QTreeView::item:selected { background-color: #1f6feb; color: #fff; }
        QTreeView::item:hover { background-color: #21262d; }

        QHeaderView::section {
            background-color: #161b22;
            padding: 8px 6px;
            border: none;
            border-bottom: 1px solid #30363d;
            border-right: 1px solid #30363d;
            font-weight: bold; color: #8b949e;
        }

        QPushButton {
            background-color: #21262d;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 6px 14px;
            color: #c9d1d9;
            font-weight: 500;
        }
        QPushButton:hover { background-color: #30363d; border-color: #8b949e; }
        QPushButton:pressed { background-color: #161b22; }

        QPushButton#primaryBtn {
            background-color: #238636;
            border: 1px solid #2ea043;
            color: white; font-weight: bold;
        }
        QPushButton#primaryBtn:hover { background-color: #2ea043; }

        QPushButton#deleteBtn {
            background-color: #da3633;
            border: 1px solid #f85149;
            color: white; font-weight: bold;
        }
        QPushButton#deleteBtn:hover { background-color: #f85149; }

        QPushButton#filterBtn {
            background-color: #21262d;
            border: 1px solid #58a6ff;
            color: #58a6ff; font-weight: bold;
        }
        QPushButton#filterBtn:checked {
            background-color: #1c3a5c;
            color: #79c0ff;
        }

        QLineEdit {
            background-color: #0d1117;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 5px 8px;
            color: #c9d1d9;
        }
        QLineEdit:focus { border-color: #58a6ff; }

        QRadioButton { color: #c9d1d9; spacing: 6px; }
        QRadioButton::indicator { width: 14px; height: 14px; }

        QSlider::groove:horizontal {
            background: #30363d; height: 4px; border-radius: 2px;
        }
        QSlider::handle:horizontal {
            background: #58a6ff; width: 14px; height: 14px;
            margin: -5px 0; border-radius: 7px;
        }
        QSlider::sub-page:horizontal { background: #58a6ff; border-radius: 2px; }

        QDateEdit {
            background-color: #0d1117;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 4px 8px;
            color: #c9d1d9;
        }
        QDateEdit::drop-down { border: none; }
        QCalendarWidget { background-color: #161b22; color: #c9d1d9; }
    """)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
