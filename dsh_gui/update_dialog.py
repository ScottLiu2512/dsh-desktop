"""版本升级对话框：展示新版本信息、下载安装包、触发安装。"""

import subprocess
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from . import __version__
from .updater import RELEASE_URL, Downloader

TEMP_DIR = Path.home() / "AppData" / "Local" / "Temp" / "dsh-desktop-update"


class UpdateDialog(QDialog):
    """发现新版本时的升级对话框。"""

    def __init__(self, parent=None, info=None):
        super().__init__(parent)
        self._info = info or {}
        self.setWindowTitle("发现新版本")
        self.setMinimumWidth(460)

        latest_tag = self._info.get("tag", "")

        title = QLabel(f"发现新版本 {latest_tag}")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")

        ver = QLabel(f"当前版本 {__version__}　→　最新版本 {latest_tag}")
        ver.setStyleSheet("color: gray;")

        self._notes = QPlainTextEdit(self._info.get("body") or "（该版本没有更新说明）")
        self._notes.setReadOnly(True)
        self._notes.setMaximumHeight(160)

        # 下载区（默认隐藏）
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setTextVisible(True)

        self._size_label = QLabel("")
        self._size_label.setStyleSheet("color: gray;")
        self._size_label.setVisible(False)

        # 按钮
        self._download_btn = QPushButton("下载更新")
        self._download_btn.setDefault(True)
        self._download_btn.clicked.connect(self._start_download)

        self._goto_btn = QPushButton("前往 Releases 页")
        self._goto_btn.clicked.connect(self._open_releases)

        self._later_btn = QPushButton("稍后")
        self._later_btn.clicked.connect(self.reject)

        self._install_btn = QPushButton("立即安装")
        self._install_btn.setVisible(False)
        self._install_btn.clicked.connect(self._install)

        self._open_btn = QPushButton("打开文件位置")
        self._open_btn.setVisible(False)
        self._open_btn.clicked.connect(self._open_folder)

        self._cancel_btn = QPushButton("取消下载")
        self._cancel_btn.setVisible(False)
        self._cancel_btn.clicked.connect(self._cancel_download)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self._install_btn)
        btn_row.addWidget(self._open_btn)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addWidget(self._goto_btn)
        btn_row.addWidget(self._later_btn)
        btn_row.addWidget(self._download_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(ver)
        layout.addWidget(self._notes)
        layout.addWidget(self._progress)
        layout.addWidget(self._size_label)
        layout.addLayout(btn_row)

        self._downloader = None

    # ---- 下载 ----
    def _start_download(self) -> None:
        self._download_btn.setEnabled(False)
        self._goto_btn.setEnabled(False)
        self._later_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._size_label.setVisible(True)
        self._cancel_btn.setVisible(True)
        self._progress.setRange(0, 0)  # 总量未知时先显示忙动画
        self._size_label.setText("正在连接…")

        self._downloader = Downloader(self)
        self._downloader.progress.connect(self._on_progress)
        self._downloader.finished.connect(self._on_download_done)
        self._downloader.failed.connect(self._on_download_failed)
        self._downloader.start(TEMP_DIR)

    def _on_progress(self, received: int, total: int) -> None:
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(received)
            self._size_label.setText(f"{received / 1048576:.1f} / {total / 1048576:.1f} MB")
        else:
            self._size_label.setText(f"已下载 {received / 1048576:.1f} MB")

    def _cancel_download(self) -> None:
        if self._downloader is not None:
            self._downloader.cancel()

    def _on_download_done(self, path: str) -> None:
        self._progress.setVisible(False)
        self._size_label.setText(f"下载完成：{path}")
        self._cancel_btn.setVisible(False)
        self._download_btn.setVisible(False)
        self._install_btn.setVisible(True)
        self._open_btn.setVisible(True)
        self._install_btn.setDefault(True)

    def _on_download_failed(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._size_label.setText(f"下载失败：{msg}")
        self._cancel_btn.setVisible(False)
        self._download_btn.setEnabled(True)
        self._goto_btn.setEnabled(True)
        self._later_btn.setEnabled(True)

    # ---- 安装 / 跳转 ----
    def _install(self) -> None:
        path = self._downloader.destination if self._downloader else None
        if not path or not Path(path).exists():
            return
        # 启动安装包（Inno Setup，独立进程）；随后退出当前应用，避免文件占用。
        try:
            subprocess.Popen([str(path)], cwd=str(path.parent))
        except Exception as exc:  # noqa: BLE001
            self._size_label.setText(f"无法启动安装程序：{exc}")
            return
        QApplication.instance().quit()

    def _open_releases(self) -> None:
        QDesktopServices.openUrl(QUrl(self._info.get("url") or RELEASE_URL))

    def _open_folder(self) -> None:
        path = self._downloader.destination if self._downloader else None
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))
