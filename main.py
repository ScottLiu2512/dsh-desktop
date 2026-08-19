"""DeepSeek Harness 桌面客户端入口。

用法：
    pip install -r requirements.txt
    python main.py
"""

import sys

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
