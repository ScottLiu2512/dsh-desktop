"""汉化 dsh 里仍为英文的斜杠命令描述（Python 版）。

dsh 0.1.x 的官方界面已经自带完整中文（命令菜单框架、工具卡片、轨迹界面、
权限预设、会话日志等），唯一还是英文的是各命令包在 host 侧注册的
``description`` 字段——就是敲 ``/`` 后菜单里那一行说明。

原先靠 ``D:\\deepseek\\DSH\\apply_cn_patches.mjs`` 手工跑。那是 Node 脚本，
Desktop 打包发出去之后用户机器上既没有这个文件、也不该要求「为了汉化先装
Node」；逻辑本身很轻（6 条精确整串替换），所以这里用 Python 重写一遍。

原脚本的三条保命机制在这里一条都没省：

1. **精确整串替换**——只改命中的那个字符串字面量，不重写整个文件
2. **大小写漂移兜底**——新版会把描述首字母改成大写（``record`` → ``Record``、
   ``set`` → ``Set``），整串匹配失败时用不区分大小写的正则再试一次
3. **备份 + 语法校验 + 失败回滚**——改前备份、改后 ``node --check``、不过就还原
"""

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import dsh_env

# package → [(英文原串, 中文替换), ...]
PATCHES: "dict[str, list[tuple[str, str]]]" = {
    "dsh-command-compact": [
        ("Compact older conversation history", "压缩较早的对话历史"),
    ],
    "dsh-session-log-export": [
        ("Download this Session log as a ZIP archive", "将会话日志下载为 ZIP 压缩包"),
    ],
    "dsh-command-feedback": [
        ("record feedback about this session", "记录对本会话的反馈"),
    ],
    "dsh-command-goal": [
        ("set or view the goal for a long-running task", "设置或查看长任务目标"),
    ],
    "dsh-permission-presets": [
        (
            "Switch the permission preset (sandbox mode + approval policy)",
            "切换权限预设（沙箱模式 + 审批策略）",
        ),
    ],
    "dsh-plan-mode": [
        ("Enter or leave plan mode", "进入或退出计划模式"),
    ],
}

# 单条补丁的状态
STATE_APPLIED = "applied"      # 已是中文
STATE_PENDING = "pending"      # 仍是英文，可改
STATE_NOT_FOUND = "not_found"  # 英文串和中文串都没找到（官方可能改了措辞）


@dataclass
class PatchItem:
    """一条替换规则在某文件里的状态。"""

    english: str
    chinese: str
    state: str


@dataclass
class PatchEntry:
    """一个包（一个文件）的检测结果。"""

    package: str
    file: "Path | None"
    items: "list[PatchItem]" = field(default_factory=list)
    error: str = ""

    @property
    def applied(self) -> int:
        return sum(1 for item in self.items if item.state == STATE_APPLIED)

    @property
    def pending(self) -> int:
        return sum(1 for item in self.items if item.state == STATE_PENDING)

    @property
    def not_found(self) -> int:
        return sum(1 for item in self.items if item.state == STATE_NOT_FOUND)


@dataclass
class PatchOutcome:
    """一次写操作（应用/还原）对某个包的结果。"""

    package: str
    action: str  # applied / skipped / reverted / failed / no-file
    detail: str = ""


def _needle(text: str) -> str:
    """把原串转成文件里等价的 JSON 字符串字面量。

    ``ensure_ascii=False`` 是关键：JS 的 ``JSON.stringify`` 不会把中文转成
    ``\\uXXXX``，两边必须一致，否则替换出来的中文形式对不上。
    """
    return json.dumps(text, ensure_ascii=False)


def patch_file(package: str) -> "Path | None":
    """定位某个包的 ``lib/index.js``。"""
    for base in dsh_env.package_dirs():
        candidate = base / package / "lib" / "index.js"
        if candidate.is_file():
            return candidate
    return None


def _replace_once(text: str, english: str, chinese: str) -> str:
    """把 ``english`` 的字面量换成 ``chinese``；没命中就原样返回。"""
    needle = _needle(english)
    replacement = _needle(chinese)

    if needle in text:
        return text.replace(needle, replacement)
    # 已经是中文了，不用再动（幂等）。
    if replacement in text:
        return text
    # 大小写漂移兜底：新版把首字母改成了大写。
    pattern = re.compile(re.escape(needle), re.IGNORECASE)
    if pattern.search(text):
        return pattern.sub(lambda _match: replacement, text, count=1)
    return text


def _state_of(text: str, english: str, chinese: str) -> str:
    if _needle(chinese) in text:
        return STATE_APPLIED
    if _needle(english) in text:
        return STATE_PENDING
    if re.search(re.escape(_needle(english)), text, re.IGNORECASE):
        return STATE_PENDING
    return STATE_NOT_FOUND


def _syntax_ok(path: Path) -> "tuple[bool, str]":
    """用 ``node --check`` 校验语法。

    找不到 node 时**降级为跳过校验并返回通过**——替换的是 JSON 字符串字面量，
    语法风险极低，为了一个校验又去要求用户装 Node 并不划算。但会把「跳过了」
    写在消息里，让人知道这次没校验。
    """
    exe = shutil.which("node.exe") or shutil.which("node")
    if not exe:
        return True, "（未找到 node，本次跳过语法校验）"
    try:
        proc = subprocess.run(
            [exe, "--check", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            **dsh_env._hidden_kwargs(),
        )
    except Exception as exc:  # noqa: BLE001
        return True, f"（语法校验无法执行：{exc}）"
    if proc.returncode == 0:
        return True, ""
    lines = [line for line in (proc.stderr or proc.stdout or "").splitlines() if line.strip()]
    return False, lines[-1] if lines else "未知错误"


def check() -> "list[PatchEntry]":
    """只检测不修改。"""
    entries: "list[PatchEntry]" = []
    for package, pairs in PATCHES.items():
        path = patch_file(package)
        if path is None:
            entries.append(
                PatchEntry(package, None, [], "包不存在（该版本可能已移除）")
            )
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            entries.append(PatchEntry(package, path, [], f"读取失败：{exc}"))
            continue
        items = [
            PatchItem(english, chinese, _state_of(text, english, chinese))
            for english, chinese in pairs
        ]
        entries.append(PatchEntry(package, path, items))
    return entries


def apply() -> "list[PatchOutcome]":
    """应用汉化。逐包处理，单个包失败不影响其它包。"""
    outcomes: "list[PatchOutcome]" = []
    for package, pairs in PATCHES.items():
        path = patch_file(package)
        if path is None:
            outcomes.append(
                PatchOutcome(package, "no-file", "包不存在（该版本可能已移除）")
            )
            continue
        try:
            before = path.read_text(encoding="utf-8")
        except OSError as exc:
            outcomes.append(PatchOutcome(package, "failed", f"读取失败：{exc}"))
            continue

        after = before
        for english, chinese in pairs:
            after = _replace_once(after, english, chinese)

        if after == before:
            outcomes.append(
                PatchOutcome(package, "skipped", "无需修改（已是中文，或该版本未找到原串）")
            )
            continue

        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup = path.with_name(f"{path.name}.bak_{stamp}")
        try:
            shutil.copy2(path, backup)
            path.write_text(after, encoding="utf-8")
        except OSError as exc:
            outcomes.append(PatchOutcome(package, "failed", f"写入失败：{exc}"))
            continue

        ok, message = _syntax_ok(path)
        if ok:
            outcomes.append(
                PatchOutcome(package, "applied", f"已汉化（备份 {backup.name}）{message}")
            )
        else:
            rolled_back = True
            try:
                shutil.copy2(backup, path)
            except OSError:
                rolled_back = False
            tail = "已自动回滚" if rolled_back else "回滚也失败了，请手工从备份恢复"
            outcomes.append(
                PatchOutcome(package, "failed", f"语法校验未通过，{tail}：{message}")
            )
    return outcomes


def revert() -> "list[PatchOutcome]":
    """从最近一次备份还原。"""
    outcomes: "list[PatchOutcome]" = []
    for package in PATCHES:
        path = patch_file(package)
        if path is None:
            outcomes.append(PatchOutcome(package, "no-file", "包不存在"))
            continue
        backups = sorted(path.parent.glob(f"{path.name}.bak_*"))
        if not backups:
            outcomes.append(PatchOutcome(package, "skipped", "没有备份，无法还原"))
            continue
        latest = backups[-1]
        try:
            shutil.copy2(latest, path)
        except OSError as exc:
            outcomes.append(PatchOutcome(package, "failed", f"还原失败：{exc}"))
            continue
        outcomes.append(PatchOutcome(package, "reverted", f"已还原 ← {latest.name}"))
    return outcomes


def summarize(entries: "list[PatchEntry]") -> str:
    """一行摘要，给「概览」页和状态栏用。"""
    total = sum(len(entry.items) for entry in entries)
    if total == 0:
        return "不适用（未找到任何命令包）"
    applied = sum(entry.applied for entry in entries)
    pending = sum(entry.pending for entry in entries)
    if pending == 0 and applied == total:
        return f"已应用（{applied}/{total}）"
    if pending and applied:
        return f"部分应用（{applied}/{total}，{pending} 条待应用）"
    if pending:
        return f"未应用（{pending}/{total} 条待应用）"
    missing = sum(entry.not_found for entry in entries)
    return f"未找到可替换项（{missing}/{total}，可能已官方汉化）"


def needs_apply(entries: "list[PatchEntry]") -> bool:
    """是否有待应用的条目。"""
    return any(entry.pending for entry in entries)
