"""DeepSeek Harness 桌面客户端入口。

用法：
    pip install -r requirements.txt
    python main.py
"""

import os
import sys

# QtWebEngine 在部分 Windows 环境（安全软件拦截、沙箱、远程桌面）下，其
# 子进程会因沙箱/GPU 问题启动失败，导致主窗口长时间无响应。在创建
# QApplication 之前关闭 WebEngine 沙箱以换取稳定性（PyInstaller 打包
# QtWebEngine 的官方推荐做法）；如仍卡顿可自行追加 --disable-gpu。
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from pathlib import Path

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox

from dsh_gui.main_window import MainWindow


# 单实例守卫用的锁文件路径（放在用户临时目录，避免工作区/安装目录不可写）。
_SINGLE_INSTANCE_LOCK = Path.home() / "AppData" / "Local" / "Temp" / "DSH-Desktop-instance.lock"


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("DSH Desktop")
    app.setOrganizationName("dsh-gui")

    # 单实例守卫:防止重复启动导致两个 dsh 服务争抢 3080 端口、闪窗翻倍。
    # 用 QLockFile 而非 QSharedMemory:锁文件带 PID+启动号做陈旧检测，崩溃残留
    # 能自动清理；QSharedMemory 可能被 QtWebEngine 子进程的句柄拖住而留下残留锁。
    lock = QLockFile(str(_SINGLE_INSTANCE_LOCK))
    if not lock.tryLock(0):
        QMessageBox.information(
            None,
            "DSH Desktop",
            "DSH Desktop 已经在运行。\n请切换到已打开的窗口，或先退出它再运行。",
        )
        return 1

    window = MainWindow()
    window.show()
    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())
