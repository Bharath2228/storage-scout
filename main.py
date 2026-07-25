import sys
from PyQt6.QtWidgets import QApplication
from src.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    
    from src.theme import apply_theme
    apply_theme(app, "dark")

    window = MainWindow()
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
