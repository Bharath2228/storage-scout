from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
)

from ...theme import SPACE_MD, SPACE_XL


class LoadingDialog(QDialog):
    def __init__(self, title="Loading", detail="Working...", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedSize(380, 140)
        self.setObjectName("modalDialog")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        layout.setSpacing(SPACE_MD)

        title = QLabel(title)
        title.setObjectName("modalTitle")
        layout.addWidget(title)

        self.detail_label = QLabel(detail)
        self.detail_label.setObjectName("modalSecondary")
        self.detail_label.setWordWrap(True)
        self.detail_label.setMinimumHeight(24)
        self.detail_label.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Minimum,
        )
        layout.addWidget(self.detail_label)

        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        bar.setObjectName("loadingBar")
        layout.addWidget(bar)
