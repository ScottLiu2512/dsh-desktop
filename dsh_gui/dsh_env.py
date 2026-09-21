"""DSH 环境探测：定位安装位置、读取版本与插件状态、扫描升级残留。

**本模块不引入任何 Qt 依赖**——所有函数都是纯逻辑，可以脱离 GUI 单独测试
（这也是为什么运行外部命令用的隐藏窗口参数在这里独立实现了一份，而不是
从 ``dsh_manager`` 导入：那个模块会连带拉起 PySide6）。两处都要改时记得同步。

所有状态都从 ``~/.dsh`` 与 npm 全局目录派生，不依赖任何私有路径，
因此「自己用」和「发出去给别人用」共用同一套逻辑。
"""

import json
import os
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config_store import dsh_home

# profile bundles 里以此开头的算 dsh 自带，不算「用户自己装的插件」。
OFFICIAL_PREFIX = "@deepseek-ai/"
# npm 升级失败时留下的暂存目录前缀（形如 .dsh-a1b2c3）。
STASH_PREFIX = ".dsh-"
# profile 补丁层文件名（dsh 官方约定）。
PATCH_FILE_NAME = "cordis.patch.yml"
DEFAULT_PORT = 3080


def _hidden_kwargs() -> dict:
    """避免弹出控制台窗口的 Popen 参数（与 dsh_manager 同源，改动需同步）。"""
    if not hasattr(subprocess, "DETACHED_PROCESS"):
        return {}
    return {
        "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | subprocess.DETACHED_PROCESS
    }


def _run_shell(cmdline: str, timeout: int = 20) -> str:
    """经 shell 跑一条命令行并返回 stdout 文本；失败或超时返回空串。

    ``npm`` 在 Windows 上是 ``.cmd``，只能由 cmd.exe 解释，所以必须走 shell。
    """
    try:
        proc = subprocess.run(
            cmdline,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=True,
            **_hidden_kwargs(),
        )
    except Exception:  # noqa: BLE001
        return ""
    return (proc.stdout or "").strip()


def _run_exe(args: list, timeout: int = 15) -> str:
    """直接调用可执行文件（不经 shell）并返回 stdout 文本。"""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **_hidden_kwargs(),
        )
    except Exception:  # noqa: BLE001
        return ""
    return (proc.stdout or "").strip()


# ---- npm 全局目录 ----

def _looks_like_npm_root(path: Path) -> bool:
    return (path / "@deepseek-ai" / "dsh").is_dir()


def npm_global_root() -> "Path | None":
    """npm 全局 node_modules 目录；找不到返回 None。

    优先查已知位置，最后才回退到 ``npm root -g``——后者要完整启动一次 npm，
    在配置复杂或网络慢的机器上可能卡好几秒，而打开维护面板时会同步调用它。
    """
    candidates = []
    override = os.environ.get("DSH_NPM_ROOT")
    if override:
        candidates.append(Path(override))
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "npm" / "node_modules")
    candidates.append(Path.home() / ".npm-global" / "lib" / "node_modules")
    candidates.append(Path("/usr/local/lib/node_modules"))
    candidates.append(Path("/usr/lib/node_modules"))

    for candidate in candidates:
        if _looks_like_npm_root(candidate):
            return candidate

    reported = _run_shell("npm root -g")
    if reported:
        path = Path(reported)
        if path.is_dir():
            return path
    return None


def dsh_install_dir() -> "Path | None":
    """dsh 本体的安装目录（含它自己 package.json 的那一层）。"""
    root = npm_global_root()
    if root is not None:
        candidate = root / "@deepseek-ai" / "dsh"
        if (candidate / "package.json").is_file():
            return candidate
    # 退路：profile 树里也有一份 dsh（通常与全局是硬链接）。
    candidate = dsh_home() / "profiles" / "node_modules" / "@deepseek-ai" / "dsh"
    if (candidate / "package.json").is_file():
        return candidate
    return None


def dsh_version() -> "str | None":
    """已安装的 dsh 本体版本号（读 package.json，不启动进程）。"""
    install = dsh_install_dir()
    if install is None:
        return None
    try:
        data = json.loads((install / "package.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    version = data.get("version")
    return str(version) if version else None


def package_dirs() -> "list[Path]":
    """按优先级返回可能存放 dsh 各子包的目录。

    全局树：``<npm_root>/@deepseek-ai/dsh/node_modules/@deepseek-ai``
    profile 树：``<DSH_HOME>/profiles/node_modules/@deepseek-ai``

    两者通常是**硬链接**（同一批物理文件，实测同 inode + 同 dev），改一棵
    两棵同时生效，所以取第一个存在的就够，不必两棵都处理。
    """
    result: list[Path] = []
    install = dsh_install_dir()
    if install is not None:
        nested = install / "node_modules" / "@deepseek-ai"
        if nested.is_dir():
            result.append(nested)
    profile_nested = dsh_home() / "profiles" / "node_modules" / "@deepseek-ai"
    if profile_nested.is_dir():
        result.append(profile_nested)
    return result


def node_version() -> "str | None":
    """Node.js 版本号（形如 ``v22.22.2``）；找不到返回 None。"""
    exe = shutil.which("node.exe") or shutil.which("node")
    if not exe:
        return None
    return _run_exe([exe, "--version"]) or None


# ---- profile ----

def profile_dir() -> Path:
    """web profile 目录。"""
    return dsh_home() / "profiles" / "web"


def _profile_config() -> dict:
    path = profile_dir() / "package.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def profile_bundles() -> "list[str]":
    """profile 里登记要加载的 bundle 列表。"""
    data = _profile_config()
    profile = data.get("dsh") or {}
    if not isinstance(profile, dict):
        return []
    bundle_cfg = profile.get("profile") or {}
    if not isinstance(bundle_cfg, dict):
        return []
    bundles = bundle_cfg.get("bundles")
    if isinstance(bundles, list):
        return [str(item) for item in bundles]
    return []


def profile_dependencies() -> "dict[str, str]":
    """profile 的 dependencies（含插件的来源，如 ``file:D:/...``）。"""
    data = _profile_config()
    deps = data.get("dependencies")
    if not isinstance(deps, dict):
        return {}
    return {str(key): str(value) for key, value in deps.items()}


def source_path(source: str) -> "Path | None":
    """把 ``file:`` 形式的依赖来源解析成本地路径；其它形式返回 None。"""
    text = (source or "").strip()
    if not text.lower().startswith("file:"):
        return None
    raw = text[5:].strip()
    if not raw:
        return None
    try:
        return Path(raw)
    except (OSError, ValueError):
        return None


def plugin_resolved(name: str) -> bool:
    """插件能否真的被解析到（node_modules 里有它，或它本来就是 dsh 子包）。"""
    bases = [
        profile_dir() / "node_modules",
        dsh_home() / "profiles" / "node_modules",
    ]
    for base in bases:
        if (base / name / "package.json").is_file():
            return True
    for pkg_dir in package_dirs():
        if (pkg_dir / name / "package.json").is_file():
            return True
    return False


def patch_layer_entries() -> "list[str]":
    """读 profile 补丁层里 ``insert`` 进来的插件名。

    ``cordis.patch.yml`` 的顶层是一个 YAML 数组，每项形如::

        - insert:
            - id: tts-button
              name: '@deepseek-ai/dsh-tts-button'
    """
    path = profile_dir() / PATCH_FILE_NAME
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, list):
        return []
    names: list[str] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        for item in entry.get("insert") or []:
            if isinstance(item, dict) and item.get("name"):
                names.append(str(item["name"]))
    return names


# ---- 升级残留 ----

def stash_dirs() -> "list[dict]":
    """npm 升级时留下的暂存残留目录（``.dsh-XXXXXX``），带各自占用体积。

    这些目录是**升级时 harness 还在跑**才产生的——旧版原生模块（``sharp``
    的 ``libvips*.dll``、``koffi`` 的 ``koffi.node``）被运行中的进程占用，
    npm 删不掉就留在这里。所以清理有个硬前提：dsh 必须已经停止。
    """
    root = npm_global_root()
    if root is None:
        return []
    scope = root / "@deepseek-ai"
    if not scope.is_dir():
        return []

    results: list[dict] = []
    for child in scope.iterdir():
        if not child.is_dir() or not child.name.startswith(STASH_PREFIX):
            continue
        size = 0
        for item in child.rglob("*"):
            try:
                if item.is_file():
                    size += item.stat().st_size
            except OSError:
                continue
        results.append({"path": child, "size": size})
    results.sort(key=lambda entry: str(entry["path"]))
    return results


def port_listening(port: int = DEFAULT_PORT) -> bool:
    """本机某端口上是否有进程在监听。

    用裸 socket 而不是 urllib 探测：这里只关心「端口占没占」，
    连上就说明有服务，不必等它响应完一个 HTTP 请求。
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            return sock.connect_ex(("127.0.0.1", int(port))) == 0
    except Exception:  # noqa: BLE001
        return False


# ---- 汇总 ----

@dataclass
class PluginInfo:
    """一个用户自己装的插件。"""

    name: str
    source: str
    path: "Path | None"
    source_exists: bool
    installed: bool


@dataclass
class EnvSnapshot:
    """一次环境快照。"""

    dsh_version: "str | None"
    dsh_install: "Path | None"
    node_version: "str | None"
    npm_root: "Path | None"
    profile_dir: Path
    bundles: "list[str]" = field(default_factory=list)
    dependencies: "dict[str, str]" = field(default_factory=dict)
    plugins: "list[PluginInfo]" = field(default_factory=list)
    patch_entries: "list[str]" = field(default_factory=list)
    missing_bundles: "list[str]" = field(default_factory=list)
    stash: "list[dict]" = field(default_factory=list)


def snapshot() -> EnvSnapshot:
    """采集一次当前 DSH 环境状态。只读，不改动任何文件。"""
    bundles = profile_bundles()
    deps = profile_dependencies()

    plugins: list[PluginInfo] = []
    for name in bundles:
        if name.startswith(OFFICIAL_PREFIX):
            continue
        source = deps.get(name, "")
        path = source_path(source)
        plugins.append(
            PluginInfo(
                name=name,
                source=source,
                path=path,
                source_exists=bool(path is not None and path.is_dir()),
                installed=plugin_resolved(name),
            )
        )

    missing = [name for name in bundles if not plugin_resolved(name)]

    return EnvSnapshot(
        dsh_version=dsh_version(),
        dsh_install=dsh_install_dir(),
        node_version=node_version(),
        npm_root=npm_global_root(),
        profile_dir=profile_dir(),
        bundles=bundles,
        dependencies=deps,
        plugins=plugins,
        patch_entries=patch_layer_entries(),
        missing_bundles=missing,
        stash=stash_dirs(),
    )


def format_size(size: int) -> str:
    """人类可读的体积文本。"""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def format_report(snap: EnvSnapshot, desktop_version: str, cn_summary: str = "") -> str:
    """把快照拼成可一键复制的纯文本报告，方便用户反馈问题时附上。"""
    def dash(value) -> str:
        return str(value) if value else "（未找到）"

    lines = [
        f"DSH Desktop      {desktop_version}",
        f"dsh 本体          {dash(snap.dsh_version)}",
        f"Node.js          {dash(snap.node_version)}",
        f"dsh 安装目录      {dash(snap.dsh_install)}",
        f"npm 全局目录      {dash(snap.npm_root)}",
        f"profile 目录      {snap.profile_dir}",
    ]
    if cn_summary:
        lines.append(f"汉化补丁          {cn_summary}")

    lines.append("")
    lines.append(f"bundle（{len(snap.bundles)}）")
    for name in snap.bundles:
        flag = "  " if plugin_resolved(name) else "! "
        lines.append(f"  {flag}{name}")

    lines.append("")
    if snap.plugins:
        lines.append(f"用户插件（{len(snap.plugins)}）")
        for plugin in snap.plugins:
            state = "已装" if plugin.installed else "未解析"
            origin = plugin.source or "（无来源记录）"
            exists = "" if plugin.source_exists else "  [来源路径不存在]"
            lines.append(f"  {plugin.name}  [{state}]  {origin}{exists}")
    else:
        lines.append("用户插件（0）")

    if snap.patch_entries:
        lines.append("")
        lines.append(f"补丁层（{len(snap.patch_entries)}）")
        for name in snap.patch_entries:
            lines.append(f"  {name}")

    if snap.missing_bundles:
        lines.append("")
        lines.append(f"⚠ 已登记但解析不到的 bundle（{len(snap.missing_bundles)}）")
        for name in snap.missing_bundles:
            lines.append(f"  {name}")

    lines.append("")
    if snap.stash:
        total = sum(entry["size"] for entry in snap.stash)
        lines.append(f"升级残留（{len(snap.stash)} 个，共 {format_size(total)}）")
        for entry in snap.stash:
            lines.append(f"  {entry['path'].name}  {format_size(entry['size'])}")
    else:
        lines.append("升级残留（无）")

    return "\n".join(lines)
