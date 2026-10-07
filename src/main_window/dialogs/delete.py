import os
import time

from PyQt6.QtCore import (
    QSize,
    QTimer,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QIcon,
)
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from ...auth import AuthStore, append_delete_audit
from ...models import format_size
from ...theme import SPACE_LG, SPACE_MD, SPACE_SM, SPACE_XL, SPACE_XS


class DeleteProgressDialog(QDialog):
    cancel_requested = pyqtSignal()

    def __init__(self, total, parent=None):
        super().__init__(parent)
        self._allow_close = False
        self.total = int(total or 0)
        self.done = 0
        self.current_path = ""
        self.started_at = time.time()
        self.current_started_at = self.started_at
        self.cancel_requested_flag = False
        self.setWindowTitle("Deleting items")
        self.setModal(True)
        self.setFixedSize(620, 310)
        self.setObjectName("modalDialog")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        layout.setSpacing(SPACE_MD)

        item_word = "item" if self.total == 1 else "items"
        self.title_label = QLabel(f"Moving {self.total:,} {item_word} to the Recycle Bin")
        self.title_label.setObjectName("modalTitle")
        layout.addWidget(self.title_label)

        self.count_label = QLabel(f"Moved 0 of {self.total:,} {item_word}")
        self.count_label.setObjectName("modalDetail")
        layout.addWidget(self.count_label)

        self.remaining_label = QLabel(f"Remaining: {self.total:,} {item_word}")
        self.remaining_label.setObjectName("modalSecondary")
        layout.addWidget(self.remaining_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, self.total)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setObjectName("deleteProgressBar")
        layout.addWidget(self.progress)

        details = QFrame()
        details.setObjectName("modalSection")
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(SPACE_LG, SPACE_MD, SPACE_LG, SPACE_MD)
        details_layout.setSpacing(SPACE_XS)

        self.current_item_label = QLabel("Current item: Preparing deletion...")
        self.current_item_label.setObjectName("modalDetail")
        self.current_item_label.setWordWrap(True)
        details_layout.addWidget(self.current_item_label)

        self.location_label = QLabel("Location: --")
        self.location_label.setObjectName("modalSecondary")
        self.location_label.setWordWrap(True)
        details_layout.addWidget(self.location_label)

        self.elapsed_label = QLabel("Elapsed: 00:00")
        self.elapsed_label.setObjectName("modalSecondary")
        details_layout.addWidget(self.elapsed_label)

        self.working_label = QLabel("Working: preparing current item...")
        self.working_label.setObjectName("modalSecondary")
        details_layout.addWidget(self.working_label)

        layout.addWidget(details)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.cancel_button = QPushButton("Cancel after current item")
        self.cancel_button.setObjectName("modalCancel")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self._cancel)
        btn_row.addWidget(self.cancel_button)
        layout.addLayout(btn_row)

        self.status_timer = QTimer(self)
        self.status_timer.setInterval(1000)
        self.status_timer.timeout.connect(self._refresh_live_status)
        self.status_timer.start()

    def _format_elapsed(self, seconds):
        seconds = max(0, int(seconds or 0))
        minutes, secs = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _refresh_live_status(self):
        elapsed = self._format_elapsed(time.time() - self.started_at)
        current_elapsed = self._format_elapsed(time.time() - self.current_started_at)
        self.elapsed_label.setText(f"Elapsed: {elapsed}")
        if self.done >= self.total and self.total:
            self.working_label.setText("Working: finalizing...")
        elif self.current_path:
            self.working_label.setText(f"Working on current item: {current_elapsed}")
        else:
            self.working_label.setText(f"Working: preparing current item... {elapsed}")

    def _cancel(self):
        self.cancel_requested_flag = True
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Cancel requested")
        self.working_label.setText("Cancel requested - finishing current item...")
        self.cancel_requested.emit()

    def update_progress(self, done, total, path):
        path = path or ""
        if path != self.current_path:
            self.current_path = path
            self.current_started_at = time.time()
        self.done = int(done or 0)
        self.total = int(total or 0)
        self.progress.setMaximum(total)
        self.progress.setValue(done)
        item_word = "item" if self.total == 1 else "items"
        remaining = max(0, self.total - self.done)
        remaining_word = "item" if remaining == 1 else "items"
        self.count_label.setText(f"Moved {self.done:,} of {self.total:,} {item_word}")
        self.remaining_label.setText(f"Remaining: {remaining:,} {remaining_word}")

        normalized = os.path.normpath(path) if path else ""
        item_name = os.path.basename(normalized) if normalized else "Preparing deletion..."
        location = os.path.dirname(normalized) if normalized else "--"
        self.current_item_label.setText(f"Current item: {item_name or normalized}")
        self.current_item_label.setToolTip(path)
        self.location_label.setText(f"Location: {location or '--'}")
        self.location_label.setToolTip(path)
        if self.cancel_requested_flag:
            self.working_label.setText("Cancel requested - finishing current item...")
        else:
            self._refresh_live_status()

    def closeEvent(self, event):
        if hasattr(self, 'status_timer'):
            self.status_timer.stop()
        if self._allow_close:
            event.accept()
            return

        if self.cancel_button.isEnabled():
            self._cancel()
        event.ignore()

class DeletePreviewDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Delete preview")
        self.setModal(True)
        self.setFixedSize(600, 390)
        self.setObjectName("modalDialog")
        self.preview_paths = []
        self.preview_payload = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        layout.setSpacing(SPACE_MD)

        self.title_label = QLabel("Preparing delete preview")
        self.title_label.setObjectName("modalTitle")
        layout.addWidget(self.title_label)

        self.detail_label = QLabel("Calculating selected items and size...")
        self.detail_label.setObjectName("modalDetail")
        layout.addWidget(self.detail_label)

        self.primary_count_label = QLabel("")
        self.primary_count_label.setObjectName("fileTypesMetricValue")
        self.primary_count_label.setVisible(False)
        layout.addWidget(self.primary_count_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setObjectName("deleteProgressBar")
        layout.addWidget(self.progress)

        self.summary_widget = QFrame()
        self.summary_widget.setObjectName("modalSection")
        summary_layout = QGridLayout(self.summary_widget)
        summary_layout.setContentsMargins(SPACE_LG, SPACE_MD, SPACE_LG, SPACE_MD)
        summary_layout.setHorizontalSpacing(SPACE_XL)
        summary_layout.setVerticalSpacing(SPACE_SM)
        summary_layout.setColumnStretch(0, 1)

        matched_label = QLabel("Matched items")
        matched_label.setObjectName("modalSecondary")
        self.matched_value = QLabel("0")
        self.matched_value.setObjectName("modalDetail")
        self.matched_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        summary_layout.addWidget(matched_label, 0, 0)
        summary_layout.addWidget(self.matched_value, 0, 1)

        recycle_count_label = QLabel("Items sent to Recycle Bin")
        recycle_count_label.setObjectName("modalSecondary")
        self.recycle_count_value = QLabel("0")
        self.recycle_count_value.setObjectName("modalTitle")
        self.recycle_count_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        summary_layout.addWidget(recycle_count_label, 1, 0)
        summary_layout.addWidget(self.recycle_count_value, 1, 1)

        folders_label = QLabel("Folders")
        folders_label.setObjectName("modalSecondary")
        self.folders_value = QLabel("0")
        self.folders_value.setObjectName("modalDetail")
        self.folders_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        summary_layout.addWidget(folders_label, 2, 0)
        summary_layout.addWidget(self.folders_value, 2, 1)

        files_label = QLabel("Files")
        files_label.setObjectName("modalSecondary")
        self.files_value = QLabel("0")
        self.files_value.setObjectName("modalDetail")
        self.files_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        summary_layout.addWidget(files_label, 3, 0)
        summary_layout.addWidget(self.files_value, 3, 1)

        size_label = QLabel("Total size")
        size_label.setObjectName("modalSecondary")
        self.size_value = QLabel("0 B")
        self.size_value.setObjectName("modalDetail")
        self.size_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        summary_layout.addWidget(size_label, 4, 0)
        summary_layout.addWidget(self.size_value, 4, 1)

        self.summary_widget.setVisible(False)
        layout.addWidget(self.summary_widget)

        self.preview_note = QLabel("")
        self.preview_note.setObjectName("modalSecondary")
        self.preview_note.setWordWrap(True)
        self.preview_note.setVisible(False)
        layout.addWidget(self.preview_note)

        self.error_label = QLabel("")
        self.error_label.setObjectName("modalSecondary")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        layout.addStretch()

        note_row = QHBoxLayout()
        note_row.setSpacing(SPACE_SM)
        note_row.setContentsMargins(0, 0, 0, 0)
        recycle_icon = QLabel()
        recycle_icon.setPixmap(
            QIcon(
                os.path.join(
                    os.path.join(os.path.dirname(__file__), "..", ".."),
                    "assets",
                    "toolbar_trash.svg",
                )
            ).pixmap(QSize(18, 18))
        )
        recycle_icon.setFixedSize(22, 22)
        recycle_icon.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        note_row.addWidget(recycle_icon)
        self.recycle_bin_note = QLabel(
            "Items will be moved to the Recycle Bin and can be restored."
        )
        self.recycle_bin_note.setObjectName("modalSecondary")
        self.recycle_bin_note.setWordWrap(True)
        note_row.addWidget(self.recycle_bin_note, 1)
        layout.addLayout(note_row)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("modalCancel")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.setDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_button)
        self.delete_button = QPushButton("Delete Selected")
        self.delete_button.setObjectName("destructiveBtn")
        self.delete_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_button.setAutoDefault(False)
        self.delete_button.setDefault(False)
        self.delete_button.setIcon(
            QIcon(
                os.path.join(
                    os.path.join(os.path.dirname(__file__), "..", ".."),
                    "assets",
                    "toolbar_trash_white.svg",
                )
            )
        )
        self.delete_button.setIconSize(QSize(18, 18))
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self.accept)
        btn_row.addWidget(self.delete_button)
        layout.addLayout(btn_row)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
            return
        super().keyPressEvent(event)

    def apply_preview(self, payload):
        data = dict(payload or {})
        self.preview_payload = data
        self.preview_paths = list(data.get('paths', []) or [])
        total = int(data.get('total', len(self.preview_paths)) or 0)
        delete_operations = int(
            data.get('delete_operations', len(self.preview_paths)) or 0
        )
        folders = int(data.get('folders', 0) or 0)
        files = int(data.get('files', 0) or 0)
        size = data.get('size')

        if size is None:
            self.title_label.setText("Preparing delete preview")
            self.detail_label.setText("Calculating selected items and size...")
            self.primary_count_label.setVisible(False)
            self.summary_widget.setVisible(False)
            self.preview_note.setVisible(False)
            self.error_label.setVisible(False)
            self.progress.setRange(0, 0)
            self.progress.setVisible(True)
            self.delete_button.setEnabled(False)
            return

        self.title_label.setText("Review before deleting")
        self.detail_label.setText("Check the deletion summary before continuing.")
        item_word = "item" if delete_operations == 1 else "items"
        self.primary_count_label.setText(f"{delete_operations:,} {item_word} to delete")
        self.primary_count_label.setVisible(True)
        self.matched_value.setText(f"{total:,}")
        self.recycle_count_value.setText(f"{delete_operations:,}")
        self.folders_value.setText(f"{folders:,}")
        self.files_value.setText(f"{files:,}")
        self.size_value.setText("0 B" if size == 0 else format_size(size))
        self.summary_widget.setVisible(True)
        self.error_label.setVisible(False)
        self.progress.setVisible(False)

        show_note = total != delete_operations
        self.preview_note.setText(
            "Non-empty folders outside your filter won't be deleted."
            if show_note
            else ""
        )
        self.preview_note.setVisible(show_note)
        self.delete_button.setEnabled(True)

    def show_error(self, message):
        self.title_label.setText("Could not calculate preview")
        self.detail_label.setText("The delete summary could not be prepared.")
        self.primary_count_label.setVisible(False)
        self.summary_widget.setVisible(False)
        self.preview_note.setVisible(False)
        self.error_label.setText(message)
        self.error_label.setVisible(True)
        self.progress.setVisible(False)
        self.delete_button.setEnabled(False)

class DeleteAuthDialog(QDialog):
    def __init__(self, item_count, total_size=0, parent=None):
        super().__init__(parent)
        self.authorized_username = None
        self.item_count = int(item_count or 0)
        self.total_size = int(total_size or 0)
        self.store = AuthStore()
        self.setWindowTitle("Delete authorization")
        self.setModal(True)
        self.setFixedSize(520, 250)
        self.setObjectName("modalDialog")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        layout.setSpacing(SPACE_MD)

        title = QLabel("Authorization required")
        title.setObjectName("modalTitle")
        layout.addWidget(title)

        size_text = format_size(self.total_size) if self.total_size else "0 B"
        detail = QLabel(
            f"Enter your credentials to move {self.item_count} "
            f"item{'s' if self.item_count != 1 else ''} ({size_text}) "
            "to the Recycle Bin."
        )
        detail.setObjectName("modalDetail")
        detail.setWordWrap(True)
        layout.addWidget(detail)

        self.message_label = QLabel("")
        self.message_label.setObjectName("authMessage")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        self.username_input = QLineEdit()
        self.username_input.setObjectName("filterSearch")
        self.username_input.setPlaceholderText("Username")
        self.username_input.setFixedHeight(34)
        layout.addWidget(self.username_input)

        self.password_input = QLineEdit()
        self.password_input.setObjectName("filterSearch")
        self.password_input.setPlaceholderText("Password")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setFixedHeight(34)
        layout.addWidget(self.password_input)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("modalCancel")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_button)
        self.submit_button = QPushButton("Authorize delete")
        self.submit_button.setObjectName("primaryBtn")
        self.submit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.submit_button.clicked.connect(self._submit)
        btn_row.addWidget(self.submit_button)
        layout.addLayout(btn_row)

        self.password_input.returnPressed.connect(self._submit)
        self.username_input.returnPressed.connect(self._username_return_pressed)
        self.username_input.textChanged.connect(self._update_lockout_state)
        self.cooldown_timer = QTimer(self)
        self.cooldown_timer.setInterval(1000)
        self.cooldown_timer.timeout.connect(self._update_lockout_state)
        self._update_lockout_state()

    def _username_return_pressed(self):
        if self.password_input.text():
            self._submit()
        else:
            self.password_input.setFocus()

    def _update_lockout_state(self):
        remaining = self.store.lockout_remaining(self.username_input.text())
        if remaining > 0:
            self.submit_button.setEnabled(False)
            self.message_label.setText(f"Too many failed attempts - try again in {remaining}s.")
            if not self.cooldown_timer.isActive():
                self.cooldown_timer.start()
            return

        self.cooldown_timer.stop()
        self.submit_button.setEnabled(True)
        if self.message_label.text().startswith("Too many failed attempts"):
            self.message_label.setText("")

    def _submit(self):
        if self.store.is_empty():
            QMessageBox.warning(
                self,
                "Delete authorization",
                "No authorized users are configured for this installation. Contact your administrator.",
            )
            return

        self._update_lockout_state()
        if not self.submit_button.isEnabled():
            return

        attempted_username = self.username_input.text()
        username = attempted_username.strip()
        password = self.password_input.text()
        if self.store.verify(attempted_username, password):
            self.authorized_username = self.store.canonical_username(username)
            append_delete_audit(
                "delete_authorized",
                username=self.authorized_username,
                items=self.item_count,
                size=self.total_size,
            )
            self.accept()
            return

        append_delete_audit(
            "delete_denied",
            username="UNKNOWN",
            items=self.item_count,
            size=self.total_size,
            reason="invalid_credentials",
            attempted_username=username or "UNKNOWN",
        )
        self.password_input.clear()
        self.message_label.setText("Invalid username or password.")
        self._update_lockout_state()
