"""管理 ``dsh web`` 子进程：启动、停止、日志与访问地址解析。"""

import ctypes
import os
import re
import shutil
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QObject, Signal

# 匹配 dsh web 启动后打印的访问地址（http/https + 本机地址 + 可选端口）。
# ⚠️ 路径和查询串必须一起取：dsh 0.1.6 起打印的地址带一次性访问令牌，
#    形如 http://127.0.0.1:3080/?token=xxx。只取到主机端口的话，内嵌浏览器
#    会落到「authentication required; reopen the URL printed by dsh web」页。
#    用 [^\s\"'()<>] 排除空白与括号，避免把后面 "(LAN: …)" 之类的括注吃进来。
_URL_RE = re.compile(
    r"https?://(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?(?:/[^\s\"'()<>]*)?",
    re.IGNORECASE,
)
# 去掉终端 ANSI 颜色码，便于在日志面板里阅读。
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# npm 在 Windows 上生成的 .cmd shim 里，真正的入口永远是形如
# "%dp0%\node_modules\...\xxx.js" 的一段（cmd-shim 模板多年未变）。
_NPM_SHIM_ENTRY_RE = re.compile(r'"%dp0%\\([^"]+\.js)"')

DEFAULT_PORT = 3080
INSTALL_HINT = "请先安装 Node.js 22 或更高版本，然后执行：npm install -g @deepseek-ai/dsh"

# dsh 写凭据时用的跨进程互斥锁，就在凭据文件旁边（<凭据文件>.lock）。
# 这个模块要清理它的残骸，见 _clear_stale_credential_lock()。
_CREDENTIAL_LOCK_NAME = ".credentials.yaml.lock"


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


# ---- 定位「占着端口的那个进程」----
# 用来在启动前回收被孤儿 dsh 占住的端口。用 iphlpapi 的 GetExtendedTcpTable
# 而不是解析 netstat：后者要起子进程、输出还受系统语言影响，而这里只要一张表。
_AF_INET = 2
_TCP_TABLE_OWNER_PID_LISTENER = 3
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _MIB_TCPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [
        ("dwState", wintypes.DWORD),
        ("dwLocalAddr", wintypes.DWORD),
        ("dwLocalPort", wintypes.DWORD),
        ("dwRemoteAddr", wintypes.DWORD),
        ("dwRemotePort", wintypes.DWORD),
        ("dwOwningPid", wintypes.DWORD),
    ]


def _listener_pids(port: int) -> set:
    """返回所有在 IPv4 上监听该端口的进程 PID；查询失败返回空集合。"""
    try:
        iphlpapi = ctypes.WinDLL("iphlpapi")
    except (OSError, AttributeError):
        # WinDLL 在非 Windows 平台上是 AttributeError，不是 OSError——这个
        # 模块整体是 Windows 专用，但保底不炸比区分异常类型重要。
        return set()
    iphlpapi.GetExtendedTcpTable.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
        wintypes.ULONG,
        ctypes.c_int,
        wintypes.ULONG,
    ]
    iphlpapi.GetExtendedTcpTable.restype = wintypes.DWORD

    size = wintypes.DWORD(0)
    # 第一次只问需要多大缓冲；返回 ERROR_INSUFFICIENT_BUFFER 是预期行为。
    iphlpapi.GetExtendedTcpTable(
        None, ctypes.byref(size), False, _AF_INET, _TCP_TABLE_OWNER_PID_LISTENER, 0
    )
    if size.value == 0:
        return set()
    buf = ctypes.create_string_buffer(size.value)
    if iphlpapi.GetExtendedTcpTable(
        buf, ctypes.byref(size), False, _AF_INET, _TCP_TABLE_OWNER_PID_LISTENER, 0
    ) != 0:
        return set()

    count = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD)).contents.value
    rows = ctypes.cast(
        ctypes.addressof(buf) + ctypes.sizeof(wintypes.DWORD),
        ctypes.POINTER(_MIB_TCPROW_OWNER_PID),
    )
    pids = set()
    for i in range(count):
        raw = rows[i].dwLocalPort
        # 结构里的端口是网络字节序，低 16 位才是真正的端口号
        if (((raw & 0xFF) << 8) | ((raw >> 8) & 0xFF)) == port:
            pids.add(int(rows[i].dwOwningPid))
    return pids


def _pid_image_name(pid: int):
    """返回该 PID 的可执行文件名（小写 basename）；取不到返回 None。"""
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (OSError, AttributeError):
        return None
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return Path(buf.value).name.lower()
    except OSError:
        return None
    finally:
        k32.CloseHandle(handle)


def _dsh_home() -> Path:
    """dsh 的家目录，凭据与那把写锁都在这儿。

    规则和 config_store.dsh_home() 一致，这里再写一遍是为了不让这个底层模块
    为了两行路径推导去依赖 yaml（config_store 要 import yaml）。dsh_env.py
    里也是同样的推导，本项目对此一直是这个做法。
    """
    return Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))


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

        # 端口上如果还留着上一次启动的 dsh（应用重开过一次、或上次退出时没被
        # 带走），必须先把它清掉，不能「接上使用」。
        #
        # 原因：dsh 的访问令牌只打印在启动它的那个进程的 stdout 里，是一次性的。
        # 外部实例拿不到那张令牌，把 web 视图指过去只会落到
        # 「dsh web authentication required; reopen the URL printed by dsh web」
        # 这个页面上——看着像坏了，其实只是没令牌。之前这里图省事直接 adopt，
        # 就是这个报错的来源。
        #
        # 位置放在确认 dsh 可用、工作区可写之后：只有真要启动时才该动别人的
        # 进程，不能出现「回收完才发现 dsh 没装」这种平白误伤。
        reclaimed, remaining = self._reclaim_orphan_server()
        if reclaimed:
            pids = "、".join(str(pid) for pid in reclaimed)
            self.log_line.emit(
                f"[提示] 端口 {self._port} 上有上次残留的 dsh 进程（PID {pids}），"
                "已结束它以释放端口。访问令牌只在启动它的那个进程的输出里打印一次，"
                "接上去只会看到一个要求重新打开地址的认证页，所以这里改为重启一个干净的实例。"
            )
        if remaining:
            names = "、".join(
                sorted({_pid_image_name(pid) or f"PID {pid}" for pid in remaining})
            )
            self.log_line.emit(
                f"[提示] 端口 {self._port} 仍被占用（{names}）。本程序只回收能确认属于 "
                "dsh 的 node.exe 进程，不会去结束别的程序；dsh 很可能因端口冲突起不来，"
                "可在「设置」里换一个端口再试。"
            )

        # 紧接着回收之后就清一次凭据锁残骸：上面刚刚强杀过 node，而强杀恰好
        # 就是残骸的成因（见该方法自己的说明）。放在这里而不是启动之前，是因为
        # 残骸是回收动作产生的，必须先回收、再清理。
        cleared = self._clear_stale_credential_lock()
        if cleared:
            self.log_line.emit(
                "[提示] dsh 上次被强制结束时，在凭据写锁上留下了残骸（"
                + "、".join(cleared)
                + "）。这种状态 dsh 自己接管不了，会导致它每次启动都报"
                "「1 required plugin did not activate」；已代为清理，凭据文件本身未改动。"
            )

        # --no-open：dsh 默认会在启动后把带令牌的地址交给系统默认浏览器打开
        # （Config 里 openBrowser 默认就是 true）。本程序自己有内嵌视图，工具栏
        # 上还专门放了一个「打开浏览器」按钮，再自动弹一个外部浏览器既多余，
        # 又会多起一个 node 启动器进程。这个开关只关掉浏览器交接，不影响地址
        # 打印（printUrl 同为默认 true），所以下面从 stdout 解析地址照旧成立。
        args = ["web", "--no-open"]
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

    def _reclaim_orphan_server(self):
        """回收占用配置端口的残留 dsh，返回 ``(已结束的 PID 列表, 仍占用端口的 PID 列表)``。

        只动镜像名是 node.exe 的监听者：端口被别的程序占着时，宁可让后面的
        启动流程照常去撞 EADDRINUSE 由 dsh 自己报错，也不要误杀别人的进程。
        GetExtendedTcpTable 只是读一张表，比先发 HTTP 探测再回头查进程更省，
        也不会因为安全软件丢包而白等一个超时。

        「有没有回收成功」不看 taskkill 的返回码，只看**端口是否真的让开了**。
        原因：taskkill /T 要遍历整棵进程树，只要树里恰好有子进程正在退出，
        它就会报错并把返回码置成 128（实测 8 次里中 1 次），而目标进程其实
        已经被结束。拿返回码当判据会漏报，把「其实已经清干净了」误判成失败；
        反过来，若进程还活着，端口会继续报有人监听，也骗不过去。
        """
        present = sorted(_listener_pids(self._port))
        candidates = [pid for pid in present if _pid_image_name(pid) == "node.exe"]
        if not candidates:
            # 端口本来就空着（最常见）或占用者是别的程序：立刻返回，不做任何等待。
            return [], present

        for pid in candidates:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=5,
                    **_hidden_window_kwargs(),
                )
            except (subprocess.TimeoutExpired, OSError):
                # 杀不掉也不能就此认定失败：下面统一以端口状态为准。
                pass

        # taskkill 返回时监听套接字不一定已经释放（内核里的关闭是异步的），
        # 立刻重启会撞上 EADDRINUSE。轮询到端口真正让开为止，比死等固定时长
        # 既更快（通常不到半秒）又更可靠（不会在慢机器上等不够）。
        deadline = time.time() + 2.0
        while time.time() < deadline and _listener_pids(self._port):
            time.sleep(0.1)
        remaining = sorted(_listener_pids(self._port))
        reclaimed = [pid for pid in candidates if pid not in remaining]
        return reclaimed, remaining

    def _clear_stale_credential_lock(self) -> list:
        """清掉 dsh 凭据写锁的残骸，返回被删掉的文件名列表。

        为什么要做这件事（全部是实测复现的结果，不是推测）：

        dsh 写凭据时用 ``<凭据文件>.lock`` 做跨进程互斥，锁文件里记着持有者的
        进程号。持有者被强杀后锁会留在盘上。dsh 本来有能力接管这种「持有者已
        死」的锁——但接管前它要先创建一个以**那把锁的内容**（死进程号）命名的
        标记文件 ``.takeover-<hash>``，且一旦发现同名标记就直接放弃本次接管。

        对照实验（临时目录里直接调 dsh 自带的 dsh-atomic-write）：

        - 只有锁、没有标记：dsh 自己 161ms 接管成功，残骸也清干净了；
        - 锁和标记同时在盘上：**永远超时**。这把锁再也清不掉，dsh 之后每次
          启动都以「1 required plugin did not activate」告终。

        而那个标记，恰好会在「正在执行接管的进程也被强杀」时残留。本程序的
        端口回收用的就是强杀（访问令牌拿不到，只能重启一个干净实例），所以
        必须自己把残骸收拾掉——否则就是亲手把 dsh 弄成起不来的状态。

        只删「能确认持有者已经不在」的锁：
        - 锁里记的进程查不到 → 已退出；
        - 锁里记的进程存在、但镜像名不是 node.exe → 进程号被别的程序复用了
          （dsh 在 Windows 上一定是 node.exe），原来那个 dsh 早已不在。
        真有一个 node 在跑就一律不动它——那可能正是正在写凭据的 dsh。

        凭据本体 ``.credentials.yaml`` 永远不碰。
        """
        lock = _dsh_home() / _CREDENTIAL_LOCK_NAME
        try:
            raw = lock.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return []
        if not raw.isdigit():
            # 拿不到内容、或内容不是进程号：不在能确认的范围内，交给 dsh 自己处理。
            return []
        image = _pid_image_name(int(raw))
        if image is not None and image == "node.exe":
            return []
        victims = [lock]
        try:
            victims += sorted(lock.parent.glob(_CREDENTIAL_LOCK_NAME + ".takeover-*"))
        except OSError:
            pass
        removed = []
        for path in victims:
            try:
                path.unlink()
                removed.append(path.name)
            except OSError:
                # 删不掉就算了：宁可留着（dsh 顶多还是起不来，与原来一样），
                # 也不要因为清理失败把启动流程整个打断。
                pass
        return removed

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
                # 防止 taskkill 卡住时主线程无限等待导致窗口「未响应」；
                # 超时后走下面的兜底直接 kill 主进程。
                timeout=5,
                **_hidden_window_kwargs(),
            )
        except subprocess.TimeoutExpired:
            self.log_line.emit("[停止] taskkill 超时，改为直接结束主进程。")
        except Exception as exc:  # noqa: BLE001
            self.log_line.emit(f"[停止] taskkill 无法执行：{exc}")
        # 判据是「进程是否真的没了」，不是 taskkill 的返回码：/T 遍历进程树时
        # 返回码不可靠（可能报错而进程其实已结束，实测会复现），拿它判断会打出
        # 一句多余的「未能结束进程树」。轮询能让正常退出被立刻识别，
        # 只有进程确实还活着才走兜底，避免它已退出时误报。
        deadline = time.time() + 2.0
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.1)
        if proc.poll() is None:
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
