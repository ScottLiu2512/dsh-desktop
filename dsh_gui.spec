# -*- mode: python ; coding: utf-8 -*-
# 单文件（onefile）+ 无控制台（windowed）打包配置。
# 用法：python -m PyInstaller dsh_gui.spec

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

# 版本号只在 dsh_gui/__init__.py 里维护一份，这里正则提取，避免又一个地方手动同步。
_init_text = (Path(SPECPATH) / 'dsh_gui' / '__init__.py').read_text(encoding='utf-8')
_version = re.search(r'__version__\s*=\s*"([^"]+)"', _init_text).group(1)
_filevers = tuple(int(part) for part in _version.split('.')) + (0,)

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_filevers, prodvers=_filevers),
    kids=[
        StringFileInfo([
            StringTable('080404b0', [
                StringStruct('CompanyName', 'dsh-gui'),
                StringStruct('FileDescription', 'DSH Desktop —— DeepSeek Harness 桌面客户端'),
                StringStruct('FileVersion', _version),
                StringStruct('InternalName', 'DSH-Desktop'),
                StringStruct('LegalCopyright', 'Copyright (c) 2026 梧井流曦'),
                StringStruct('OriginalFilename', 'DSH-Desktop.exe'),
                StringStruct('ProductName', 'DSH Desktop'),
                StringStruct('ProductVersion', _version),
            ]),
        ]),
        # 0x0804 = 简体中文, 0x04b0 = Unicode 码页；需与上面 StringTable 的 key '080404b0' 对应。
        VarFileInfo([VarStruct('Translation', [0x0804, 0x04b0])]),
    ],
)

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DSH-Desktop',
    icon='icon.ico',
    version=version_info,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
