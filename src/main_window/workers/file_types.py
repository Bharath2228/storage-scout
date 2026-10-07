from PyQt6.QtCore import (
    QThread,
    pyqtSignal,
)


class FileTypeBreakdownThread(QThread):
    breakdown_ready = pyqtSignal(int, list)
    breakdown_failed = pyqtSignal(int, str)

    def __init__(self, request_id, parent=None):
        super().__init__(parent)
        self.request_id = request_id
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        from src.file_index_tool import FileIndexTool

        tool = FileIndexTool()
        try:
            if self.is_cancelled:
                return
            rows = tool.extension_breakdown()
            if not self.is_cancelled:
                self.breakdown_ready.emit(self.request_id, rows)
        except Exception as exc:
            if not self.is_cancelled:
                self.breakdown_failed.emit(self.request_id, str(exc))
        finally:
            tool.close()
