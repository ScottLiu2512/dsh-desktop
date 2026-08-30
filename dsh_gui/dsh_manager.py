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
# npm 在 Windows 上生成的 .cmd shim 里，真正的入口永远是形如
# "%dp0%\node_modules\...\xxx.js" 的一段（cmd-shim 模板多年未变）。
_NPM_SHIM_ENTRY_RE = re.compile(r'"%dp0%\\([^"]+\.js)"')

DEFAULT_PORT = 3080
INSTALL_HINT = "请先安装 Node.js 22 或更高版本，然后执行：npm install -g @deepseek-ai/dsh"


def coerce_port(value, default: int = DEFAULT_PORT) -> int:
    """把任意来源（QSettings 可能返回字符串）的端口值收敛成合法端口号。"""
    try:
        port = int(value)
    except (TypeError, ValueError):
        return default
    return port if 1024 <= port <= 65535 else default


def _hidden_window_kwargs() -> dict:
    """构造避免弹出控制台窗口所需的 Popen 参数。

    CREATE_NO_WINDOW 只是「分配控制台、但隐藏窗口」——实测这个隐藏的控制台
    会话本身仍可能被 Windows 的默认终端转接机制接管，重新变成一个可见窗口
    （偶发，不是每次都触发，怀疑是转接逻辑本身的时序竞争）。DETACHED_PROCESS
    彻底不分配控制台，没有会话可供转接。子进程的 stdin/stdout/stderr 都已经
    显式走管道重定向，不依赖真实控制台，所以没有副作用。
    """
    if not hasattr(subprocess, "DETACHED_PROCESS"):
        return {}
    creationflags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | subprocess.DETACHED_PROCESS
    )
    return {"creationflags": creationflags}


_find_dsh_cache = None


def find_dsh():
    """在 PATH 与常见 npm 全局目录中查找 dsh 可执行文件；找不到返回 None。

    找到的结果会被缓存，避免每次启动都遍历 PATH（PATH 较长时可能卡顿
    主线程）；找不到则每次重查，便于用户装好 dsh 后无需重启应用即可生效。
    """
    global _find_dsh_cache
    if _find_dsh_cache:
        return _find_dsh_cache
    for name in ("dsh.cmd", "dsh", "dsh.exe"):
        found = shutil.which(name)
        if found:
            _find_dsh_cache = found
            return found
    appdata = os.environ.get("APPDATA")
    if appdata:
        npm_dir = Path(appdata) / "npm"
        for name in ("dsh.cmd", "dsh.ps1", "dsh"):
            candidate = npm_dir / name
            if candidate.exists():
                _find_dsh_cache = str(candidate)
                return str(candidate)
    return None


def _resolve_npm_shim(shim_path: str):
    """把 npm 的 .cmd shim 拆解成「真正执行的 node.exe + 入口脚本」。

    根源问题：shim 是批处理脚本，只能靠 cmd.exe 解释执行；而 cmd.exe 在
    Windows 11 且系统默认终端是「Windows 终端」时，会触发终端转接
    （terminal handoff）弹出一个可见窗口——这个转接绑定在 cmd.exe 自己的
    manifest 上，CREATE_NO_WINDOW / STARTUPINFO 都压不住（实测确认）。
    直接调用 node.exe 完全不会触发这套机制，所以能拆解就不经过 cmd.exe。

    拆解失败（shim 格式不是预期的 npm cmd-shim 模板、或 node.exe 找不到）
    时返回 None，调用方应回退到 shell=True 的方式。
    """
    try:
        text = Path(shim_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _NPM_SHIM_ENTRY_RE.search(text)
    if not match:
        return None
    entry = Path(shim_path).parent / match.group(1)
    if not entry.exists():
        return None
    shim_dir = Path(shim_path).parent
    node_exe = shim_dir / "node.exe"
    if not node_exe.exists():
        found = shutil.which("node.exe") or shutil.which("node")
        if not found:
            return None
        node_exe = Path(found)
    return [str(node_exe), str(entry)]


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
        self._port = DEFAULT_PORT

    # ---- 配置 ----
    def set_workspace(self, path) -> None:
        """记录工作区目录。

        这里只做路径解析、不创建目录：本方法在窗口构造期间就会被调用，
        若此时因为盘符失效或权限不足抛异常，整个应用会起不来。目录的
        实际创建与校验放在 start() 里，失败可以走日志提示。
        """
        text = str(path or "").strip()
        if not text:
            self._workspace = str(Path.home())
            return
        try:
            self._workspace = str(Path(text).expanduser())
        except (OSError, ValueError, RuntimeError):
            self._workspace = text

    def set_port(self, port) -> None:
        self._port = coerce_port(port)

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
        # 注意：找不到 dsh 时，走 shell=True 分支的 Popen 也能创建成功（真正
        # 失败的是里面的 cmd.exe），异常分支捕获不到，所以必须先自己查一遍，
        # 否则用户只会看到一句莫名其妙的「退出码 1」。
        exe = find_dsh()
        if exe is None:
            self.log_line.emit(f"[启动失败] 未找到 dsh 可执行文件。{INSTALL_HINT}")
            return False
        try:
            Path(self._workspace).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.log_line.emit(
                f"[启动失败] 无法使用工作区目录 {self._workspace}：{exc}"
                "。请在「设置」里换一个可写的目录。"
            )
            return False

        args = ["web"]
        if self._port:
            args += ["--port", str(self._port)]

        # dsh 在 Windows 上通常是 npm 生成的 .cmd shim，只能靠 cmd.exe 解释
        # 执行；但 cmd.exe 在启用了「Windows 终端」默认转接的系统上会弹出
        # 一个可见窗口，CREATE_NO_WINDOW/STARTUPINFO 都压不住（实测确认）。
        # 能拆解出真正的 node.exe + 入口脚本就直接调用，彻底绕开 cmd.exe；
        # 拆不出来（非 npm shim、node 找不到等）就退回 shell=True。
        resolved = _resolve_npm_shim(exe) if exe.lower().endswith((".cmd", ".bat")) else None
        if resolved:
            cmd = resolved + args
            shell = False
        else:
            cmd = [exe] + args
            shell = True
        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=self._workspace,
                shell=shell,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **_hidden_window_kwargs(),
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
        # taskkill 失败时不会抛异常，只是返回非零码，所以要显式判断返回值，
        # 否则杀不掉进程时界面会一直卡在「运行中」。
        killed = False
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                # 防止 taskkill 卡住时主线程无限等待导致窗口「未响应」；
                # 超时后走下面的兜底直接 kill 主进程。
                timeout=5,
                **_hidden_window_kwargs(),
            )
            killed = result.returncode == 0
        except subprocess.TimeoutExpired:
            self.log_line.emit("[停止] taskkill 超时，改为直接结束主进程。")
        except Exception as exc:  # noqa: BLE001
            self.log_line.emit(f"[停止] taskkill 无法执行：{exc}")
        # 进程仍活着才需要兜底，避免它已自行退出时打出多余的提示。
        if not killed and proc.poll() is None:
            self.log_line.emit("[停止] taskkill 未能结束进程树，改为直接结束主进程。")
            try:
                proc.kill()
            except Exception as exc:  # noqa: BLE001
                self.log_line.emit(
                    f"[停止] 结束进程失败：{exc}。可能需要在任务管理器里手动结束 node 进程。"
                )

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
