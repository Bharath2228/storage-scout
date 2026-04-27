import sys
from PyQt6.QtWidgets import QApplication
from src.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    
    from src.theme import apply_theme
    apply_theme(app, "dark")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
