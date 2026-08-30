# -*- mode: python ; coding: utf-8 -*-
# 目录（onedir）+ 无控制台（windowed）打包配置 —— 安装包用。
# 用法：python -m PyInstaller dsh_gui_onedir.spec
# 产物：dist\DSH-Desktop\（DSH-Desktop.exe + _internal\）
#
# 相对 onefile 的好处：依赖文件永久留在安装目录，不再每次启动都往 %TEMP%
# 解压近 3000 个文件（省下好几秒启动时间、不再被安全软件反复全量扫描、
# 也不会因异常退出而堆积残留目录）。取舍详见 build_common.py。

import sys

sys.path.insert(0, SPECPATH)
import build_common

version = build_common.read_version(SPECPATH)
version_info = build_common.make_version_info(version)

a = Analysis(
    [build_common.ENTRY_SCRIPT],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=build_common.EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

# exclude_binaries=True：二进制依赖不塞进 exe，交给下面的 COLLECT 摊到目录里。
exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name=build_common.APP_NAME,
    icon=build_common.ICON,
    version=version_info,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=build_common.APP_NAME,
)
