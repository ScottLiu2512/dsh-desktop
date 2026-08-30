"""两份打包 spec 共用的配置。

本项目出两种产物，共用同一份源码与版本资源：

- ``dsh_gui.spec``（onefile）：免安装单文件版，双击即运行，发布到 Releases。
- ``dsh_gui_onedir.spec``（onedir）：安装包内部用的目录版。

onefile 的代价是每次启动都要把全部依赖（约 550 MB、近 3000 个文件、其中
300 多个是 exe/dll/pyd）解压到 ``%TEMP%\\_MEIxxxxx``：启动要多花好几秒，
安全软件每次都得把这几百个「刚落盘的新可执行文件」重扫一遍（实测会导致
终端窗口反复闪现），而且程序非正常退出时解压目录不会清理、越堆越多。
装到本机长期使用的用户走安装包，用 onedir 就没有这些问题——文件永久躺在
安装目录里，只在安装那一次被扫描。

版本号仍然只在 ``dsh_gui/__init__.py`` 里维护一份，这里负责读出来并组装成
Windows 版本资源，避免两份 spec 各写一遍、迟早写岔。
"""

import re
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

APP_NAME = "DSH-Desktop"
ICON = "icon.ico"
ENTRY_SCRIPT = "main.py"
EXCLUDES = ["tkinter"]


def read_version(spec_dir) -> str:
    """从 ``dsh_gui/__init__.py`` 里读出 ``__version__``。"""
    init_text = (Path(spec_dir) / "dsh_gui" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', init_text).group(1)


def make_version_info(version: str) -> VSVersionInfo:
    """按版本号组装 Windows 版本资源（右键属性里的「详细信息」）。"""
    filevers = tuple(int(part) for part in version.split(".")) + (0,)
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=filevers, prodvers=filevers),
        kids=[
            StringFileInfo([
                StringTable('080404b0', [
                    StringStruct('CompanyName', 'dsh-gui'),
                    StringStruct('FileDescription', 'DSH Desktop —— DeepSeek Harness 桌面客户端'),
                    StringStruct('FileVersion', version),
                    StringStruct('InternalName', APP_NAME),
                    StringStruct('LegalCopyright', 'Copyright (c) 2026 梧井流曦'),
                    StringStruct('OriginalFilename', f'{APP_NAME}.exe'),
                    StringStruct('ProductName', 'DSH Desktop'),
                    StringStruct('ProductVersion', version),
                ]),
            ]),
            # 0x0804 = 简体中文, 0x04b0 = Unicode 码页；需与上面 StringTable 的 key '080404b0' 对应。
            VarFileInfo([VarStruct('Translation', [0x0804, 0x04b0])]),
        ],
    )
