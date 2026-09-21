"""dsh 本体的版本检测与升级。

**这是「环境管家」里最要紧的一块**，因为 dsh 有一个真实的升级陷阱：

    npm view @deepseek-ai/dsh dist-tags
    latest → 0.1.5-rc.2    ← 比已装的 0.1.6-alpha.2 还旧
    alpha  → 0.1.6-alpha.2 ← 真正的最新

裸跑 ``npm install -g @deepseek-ai/dsh`` 会照 ``latest`` 标签装回旧版，也就是
**降级**。所以检测最新版本不能看标签，必须把 registry 返回的 ``versions``
全量取出来按语义化版本规则（含 prerelease 优先级）自己比大小；安装时也
必须显式带上版本号。

网络请求沿用 ``updater.py`` 那套 ``QNetworkAccessManager``（异步，不阻塞
GUI 线程）；``npm install`` 可能跑几分钟，所以放在 ``QThread`` 里并逐行
把输出转发到日志面板——绝不能在 GUI 线程里等它。
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import cn_patches, dsh_env

REGISTRY_URL = "https://registry.npmjs.org/@deepseek-ai%2Fdsh"
USER_AGENT = "DSH-Desktop-Maintenance/1.1"

# 去掉终端 ANSI 颜色码，便于在日志面板里阅读（与 dsh_manager 同源）。
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_SEMVER_RE = re.compile(
    r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.\-]+))?(?:\+[0-9A-Za-z.\-]+)?$"
)


# ---- 语义化版本比较 ----

def parse_semver(text) -> "tuple | None":
    """解析语义化版本号；解析不了返回 None。

    返回 ``(major, minor, patch, prerelease)``，其中 prerelease 为 ``None``
    表示这是正式版。
    """
    match = _SEMVER_RE.match(str(text or "").strip())
    if not match:
        return None
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        match.group(4),
    )


def _compare_prerelease(left: str, right: str) -> int:
    """比较两段 prerelease 标识（semver 2.0 第 11 条）。"""
    left_parts = left.split(".")
    right_parts = right.split(".")
    for a, b in zip(left_parts, right_parts):
        a_is_num, b_is_num = a.isdigit(), b.isdigit()
        if a_is_num and b_is_num:
            if int(a) != int(b):
                return -1 if int(a) < int(b) else 1
        elif a_is_num != b_is_num:
            # 纯数字标识的优先级**低于**字母标识
            return -1 if a_is_num else 1
        elif a != b:
            return -1 if a < b else 1
    if len(left_parts) != len(right_parts):
        return -1 if len(left_parts) < len(right_parts) else 1
    return 0


def compare_semver(left, right) -> int:
    """比较版本号：left<right 返回 -1，相等返回 0，left>right 返回 1。

    特意**不复用** ``updater.py`` 的 ``parse_version``——那个函数只提取数字段，
    把 ``1.2.3-beta.2`` 和 ``1.2.3`` 当成同一个版本，拿来判断 Desktop 自己
    （纯数字版本号）没问题，但 dsh 全是 ``0.1.6-alpha.2`` 这种带 prerelease
    的版本号，用它比大小会得出错误结论。
    """
    left_parsed, right_parsed = parse_semver(left), parse_semver(right)
    if left_parsed is None or right_parsed is None:
        left_text, right_text = str(left or ""), str(right or "")
        return (left_text > right_text) - (left_text < right_text)

    for index in range(3):
        if left_parsed[index] != right_parsed[index]:
            return -1 if left_parsed[index] < right_parsed[index] else 1

    left_pre, right_pre = left_parsed[3], right_parsed[3]
    if left_pre is None and right_pre is None:
        return 0
    # 正式版优先于任何 prerelease
    if left_pre is None:
        return 1
    if right_pre is None:
        return -1
    return _compare_prerelease(left_pre, right_pre)


def is_newer(candidate, current) -> bool:
    """candidate 是否比 current 新。"""
    if parse_semver(candidate) is None:
        return False
    if parse_semver(current) is None:
        return True
    return compare_semver(candidate, current) > 0


def pick_latest(versions) -> "str | None":
    """从版本号列表里挑出 semver 最大的那个（纯函数，便于脱网测试）。"""
    best = None
    for version in versions or []:
        if parse_semver(version) is None:
            continue
        if best is None or compare_semver(version, best) > 0:
            best = str(version)
    return best


# ---- 版本检测 ----

class DshVersionCheck(QObject):
    """异步查询 npm registry，找出真正的最新版本。"""

    finished = Signal(object)  # dict: current / latest / latest_tag / has_update / total
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        self._reply = None

    def check(self) -> None:
        if self._reply is not None:
            return
        request = QNetworkRequest(QUrl(REGISTRY_URL))
        request.setRawHeader(b"User-Agent", USER_AGENT.encode("utf-8"))
        request.setRawHeader(b"Accept", b"application/json")
        # registry 文档可能很大（几 MB），给足超时。
        request.setTransferTimeout(30000)
        self._reply = self._nam.get(request)
        self._reply.finished.connect(self._on_finished)

    def _on_finished(self) -> None:
        reply = self._reply
        self._reply = None
        try:
            if reply.error() != QNetworkReply.NoError:
                self.failed.emit(reply.errorString())
                return
            payload = bytes(reply.readAll()).decode("utf-8", "replace")
            data = json.loads(payload)
            versions = list((data.get("versions") or {}).keys())
            latest = pick_latest(versions)
            if not latest:
                self.failed.emit("registry 响应里没有可用的版本号")
                return
            current = dsh_env.dsh_version()
            dist_tags = data.get("dist-tags") or {}
            self.finished.emit({
                "current": current,
                "latest": latest,
                "latest_tag": str(dist_tags.get("latest") or ""),
                "has_update": bool(current) and is_newer(latest, current),
                "total": len(versions),
            })
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            reply.deleteLater()


# ---- 通用后台命令 ----

class ShellTask(QThread):
    """在后台跑一条命令，逐行把输出转发成信号。

    凡是可能跑上几十秒的操作（``dsh plugin add`` 之类的 pnpm 安装）都该走这里，
    在 GUI 线程里 ``subprocess.run`` 会让窗口直接「未响应」。
    """

    log_line = Signal(str)
    done = Signal(bool, str)  # (是否成功, 摘要)

    def __init__(self, cmdline: str, cwd: "Path | None" = None, parent=None):
        super().__init__(parent)
        self._cmdline = cmdline
        self._cwd = cwd or Path.home()
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:  # noqa: D102
        self.log_line.emit(f"> {self._cmdline}")
        try:
            proc = subprocess.Popen(
                self._cmdline,
                shell=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=str(self._cwd),
                **dsh_env._hidden_kwargs(),
            )
        except Exception as exc:  # noqa: BLE001
            self.done.emit(False, f"无法启动命令：{exc}")
            return

        assert proc.stdout is not None
        for raw in proc.stdout:
            line = _ANSI_RE.sub("", raw).rstrip()
            if line:
                self.log_line.emit(line)
            if self._cancelled:
                proc.kill()
                self.done.emit(False, "已取消")
                return

        code = proc.wait()
        self.done.emit(code == 0, f"退出码 {code}")


# ---- 升级执行 ----

class UpgradeWorker(QThread):
    """后台执行 ``npm install -g``，流式回传日志。

    调用方（维护面板）需要**先停止 dsh**再启动本线程——harness 运行中会占用
    旧版原生模块，npm 删不掉，升级会留下残留甚至半失败。
    """

    log_line = Signal(str)
    done = Signal(bool, str)  # (是否成功, 摘要文本)

    def __init__(self, version: str, reapply_patches: bool = True, parent=None):
        super().__init__(parent)
        self._version = version
        self._reapply_patches = reapply_patches
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    # ---- 主流程 ----
    def run(self) -> None:  # noqa: D102
        target = self._version.strip()
        if not target:
            self.done.emit(False, "没有指定要安装的版本号")
            return

        self._emit(f"> 开始升级 dsh → {target}")
        self._emit(f"> 命令：npm install -g @deepseek-ai/dsh@{target}")

        ok, detail = self._run_npm(target)
        if not ok:
            self.done.emit(False, detail)
            return

        # 升级会覆盖汉化补丁（新文件取代旧文件），所以升级完顺手重打一遍。
        if self._reapply_patches and not self._cancelled:
            self._emit("")
            self._emit("> 重新应用汉化补丁…")
            for outcome in cn_patches.apply():
                self._emit(f"    {outcome.package}: {outcome.action} — {outcome.detail}")

        # 残留清理放在最后：升级过程本身就可能产生新的残留。
        if not self._cancelled:
            removed = self._clean_stash()
            if removed:
                self._emit(f"> 已清理 {removed} 个升级残留目录")

        self.done.emit(True, f"已升级到 {target}")

    # ---- 子步骤 ----
    def _run_npm(self, version: str) -> "tuple[bool, str]":
        cmdline = f"npm install -g @deepseek-ai/dsh@{version}"
        try:
            proc = subprocess.Popen(
                cmdline,
                shell=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=str(Path.home()),
                **dsh_env._hidden_kwargs(),
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"无法启动 npm：{exc}"

        assert proc.stdout is not None
        for raw in proc.stdout:
            line = _ANSI_RE.sub("", raw).rstrip()
            if line:
                self._emit(line)
            if self._cancelled:
                proc.kill()
                return False, "已取消"

        code = proc.wait()
        if code != 0:
            return False, f"npm 退出码 {code}，升级未完成（详见日志）"

        after = dsh_env.dsh_version()
        if after and compare_semver(after, version) != 0:
            # npm 报成功但版本对不上——可能是权限问题装到了别处。
            return False, f"npm 返回成功，但当前版本仍是 {after}，请检查 npm 全局目录权限"
        return True, ""

    def _clean_stash(self) -> int:
        """删掉 npm 升级残留的 ``.dsh-*`` 暂存目录。

        只删 ``npm_global_root()/@deepseek-ai`` 下、名字以 ``.dsh-`` 开头的目录，
        并且逐个复核父目录——避免因为环境变量异常把别处的目录删了。
        """
        root = dsh_env.npm_global_root()
        if root is None:
            return 0
        scope = (root / "@deepseek-ai").resolve()
        removed = 0
        for entry in dsh_env.stash_dirs():
            path = Path(entry["path"])
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved.parent != scope or not resolved.name.startswith(dsh_env.STASH_PREFIX):
                self._emit(f"    跳过（路径校验不通过）：{resolved}")
                continue
            shutil.rmtree(resolved, ignore_errors=True)
            if not resolved.exists():
                removed += 1
                self._emit(f"    已删除 {resolved.name}")
            else:
                self._emit(f"    未能删除 {resolved.name}（可能仍被占用）")
        return removed

    def _emit(self, line: str) -> None:
        self.log_line.emit(line)
