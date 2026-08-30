"""版本检测与升级下载。

通过 GitHub Releases API 检测最新版本，并下载最新安装包触发升级。
所有网络操作都是异步的（QNetworkAccessManager），不会阻塞 GUI 线程。
"""

import json
import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import __version__

REPO = "ScottLiu2512/dsh-desktop"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD_BASE = f"https://github.com/{REPO}/releases/latest/download"
RELEASE_URL = f"https://github.com/{REPO}/releases/latest"
SETUP_ASSET_NAME = "DSH-Desktop-Setup.exe"
USER_AGENT = f"DSH-Desktop-Updater/{__version__}"


def parse_version(text) -> tuple:
    """把版本号文本解析为可比较的整数元组；解析不到返回空元组。

    "v1.0.1" → (1, 0, 1)；"1.2.3-beta.2" → (1, 2, 3)。
    """
    match = re.search(r"(\d+(?:\.\d+)*)", str(text or ""))
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def version_gt(a, b) -> bool:
    """版本号比较：a 是否严格大于 b。"""
    return parse_version(a) > parse_version(b)


class VersionCheck(QObject):
    """异步查询 GitHub 最新 release 信息。"""

    finished = Signal(object)  # dict: {"tag":…, "url":…, "body":…}；解析失败不发
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        self._reply = None

    def check(self) -> None:
        if self._reply is not None:
            return
        req = QNetworkRequest(QUrl(API_URL))
        req.setRawHeader(b"User-Agent", USER_AGENT.encode("utf-8"))
        req.setRawHeader(b"Accept", b"application/vnd.github+json")
        req.setTransferTimeout(15000)
        self._reply = self._nam.get(req)
        self._reply.finished.connect(self._on_finished)

    def _on_finished(self) -> None:
        reply = self._reply
        self._reply = None
        try:
            if reply.error() != QNetworkReply.NoError:
                self.failed.emit(reply.errorString())
                return
            data = bytes(reply.readAll())
            info = json.loads(data.decode("utf-8", "replace"))
            tag = str(info.get("tag_name") or "")
            if not tag:
                self.failed.emit("响应中未找到版本号")
                return
            self.finished.emit({
                "tag": tag,
                "url": str(info.get("html_url") or RELEASE_URL),
                "body": str(info.get("body") or ""),
            })
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            reply.deleteLater()


class Downloader(QObject):
    """异步下载安装包，带进度信号。"""

    progress = Signal(int, int)  # (已下载字节, 总字节；总字节可能为 -1 表示未知)
    finished = Signal(str)       # 保存路径
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        self._reply = None
        self._file = None
        self._dest: Optional[Path] = None

    @property
    def destination(self) -> Optional[Path]:
        return self._dest

    def start(self, dest_dir) -> None:
        if self._reply is not None:
            return
        self._dest = Path(dest_dir) / SETUP_ASSET_NAME
        url = f"{DOWNLOAD_BASE}/{SETUP_ASSET_NAME}"
        req = QNetworkRequest(QUrl(url))
        req.setRawHeader(b"User-Agent", USER_AGENT.encode("utf-8"))
        req.setTransferTimeout(600_000)  # 10 分钟无数据即放弃
        self._reply = self._nam.get(req)
        self._reply.downloadProgress.connect(self._on_progress)
        self._reply.readyRead.connect(self._on_ready_read)
        self._reply.finished.connect(self._on_finished)

    def cancel(self) -> None:
        if self._reply is not None:
            self._reply.abort()

    def _on_ready_read(self) -> None:
        if self._file is None:
            self._dest.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(self._dest, "wb")
        self._file.write(bytes(self._reply.readAll()))

    def _on_progress(self, received: int, total: int) -> None:
        self.progress.emit(received, total)

    def _on_finished(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
        reply = self._reply
        self._reply = None
        try:
            if reply.error() != QNetworkReply.NoError:
                if self._dest is not None and self._dest.exists():
                    try:
                        self._dest.unlink()
                    except OSError:
                        pass
                self.failed.emit(reply.errorString())
                return
            self.finished.emit(str(self._dest))
        finally:
            reply.deleteLater()
