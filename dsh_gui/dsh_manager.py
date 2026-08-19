"""管理 ``dsh web`` 子进程：启动、停止、日志与访问地址解析。"""

import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

# 匹配 dsh web 启动后打印的访问地址（http/https + 本机地址 + 可选端口）。
_URL_RE = re.compile(r"https?://(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?", re.IGNORECASE)
# 去掉终端 ANSI 颜色码，便于在日志面板里阅读。
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def find_dsh() -> str:
    """在 PATH 与常见 npm 全局目录中查找 dsh 可执行文件。"""
    for name in ("dsh.cmd", "dsh", "dsh.exe"):
        found = shutil.which(name)
        if found:
            return found
    npm_dir = Path(os.environ.get("APPDATA", "")) / "npm"
    for name in ("dsh.cmd", "dsh.ps1", "dsh"):
        candidate = npm_dir / name
        if candidate.exists():
            return str(candidate)
    return "dsh"


class DshManager(QObject):
    """负责启动/停止 dsh web 子进程，并把输出转发成 Qt 信号。"""

    started = Signal(str)   # 解析到服务地址时发出
    log_line = Signal(str)  # 每行子进程输出
    stopped = Signal(int)   # 进程退出时发出（带返回码）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc = None
        self._reader = None
        self._url = None
        self._workspace = str(Path.home())
        self._port = 3080

    # ---- 配置 ----
    def set_workspace(self, path: str) -> None:
        p = Path(path).expanduser()
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
        self._workspace = str(p)

    def set_port(self, port: int) -> None:
        self._port = int(port) if port else 3080

    @property
    def workspace(self) -> str:
        return self._workspace

    @property
    def port(self) -> int:
        return self._port

    # ---- 状态 ----
    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def url(self):
        return self._url

    # ---- 生命周期 ----
    def start(self) -> bool:
        if self.is_running:
            return False
        exe = find_dsh()
        cmd = [exe, "web"]
        if self._port:
            cmd += ["--port", str(self._port)]
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=self._workspace,
                shell=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except Exception as exc:  # noqa: BLE001
            self.log_line.emit(f"[启动失败] {exc}")
            return False
        self._url = None
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        return True

    def stop(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            self._proc = None
            return
        # 用 taskkill 结束整棵进程树（node 可能再拉起子进程）。
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # ---- 输出读取 ----
    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for raw in proc.stdout:
            line = _ANSI_RE.sub("", raw).rstrip()
            if not line:
                continue
            self.log_line.emit(line)
            if self._url is None:
                match = _URL_RE.search(line)
                if match:
                    self._url = match.group(0)
                    self.started.emit(self._url)
        code = proc.wait()
        self._proc = None
        self.stopped.emit(code)
