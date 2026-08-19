"""关于对话框。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from . import __version__

APP_NAME = "DSH Desktop"
AUTHOR = "梧井流曦"
EMAIL = "744508955@qq.com"


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("关于")
        self.setFixedWidth(380)

        title = QLabel(APP_NAME)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 20px; font-weight: bold;")

        version = QLabel(f"版本 {__version__}（Windows）")
        version.setAlignment(Qt.AlignCenter)
        version.setStyleSheet("color: gray;")

        author = QLabel(f"作者：{AUTHOR}")
        author.setAlignment(Qt.AlignCenter)

        email = QLabel(f'<a href="mailto:{EMAIL}" style="color:#2a6fd4">{EMAIL}</a>')
        email.setAlignment(Qt.AlignCenter)
        email.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        email.setOpenExternalLinks(True)

        note = QLabel("欢迎交流，共同研究。")
        note.setAlignment(Qt.AlignCenter)
        note.setStyleSheet("color: gray;")

        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(ok_btn)
        btn_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addSpacing(8)
        layout.addWidget(title)
        layout.addWidget(version)
        layout.addSpacing(12)
        layout.addWidget(author)
        layout.addWidget(email)
        layout.addSpacing(8)
        layout.addWidget(note)
        layout.addSpacing(8)
        layout.addLayout(btn_row)
