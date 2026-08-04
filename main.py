import os
import sys
import ctypes

os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.screen=false")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QIcon
from src.main_window import MainWindow

def main():
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "IBMS.Watchdog"
            )
        except (AttributeError, OSError):
            pass

    app = QApplication(sys.argv)
    icon_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "src",
        "assets",
        "app_icon.png",
    )
    app_icon = QIcon(icon_path)
    app.setWindowIcon(app_icon)
    
    from src.theme import apply_theme, resolve_theme_name
    saved_theme = QSettings("IBMS", "Watchdog").value("theme", "light")
    apply_theme(app, resolve_theme_name(saved_theme))

    window = MainWindow()
    window.setWindowIcon(app_icon)
    screen = app.primaryScreen()
    if screen:
        available = screen.availableGeometry()
        width = min(window.width(), max(900, available.width() - 80))
        height = min(window.height(), max(640, available.height() - 80))
        window.resize(width, height)
        window.move(
            available.left() + max(0, (available.width() - width) // 2),
            available.top() + max(0, (available.height() - height) // 2),
        )
    window.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
