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

from PySide6.QtWidgets import QApplication

from dsh_gui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("DSH Desktop")
    app.setOrganizationName("dsh-gui")

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
