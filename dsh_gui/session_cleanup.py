"""列出/删除某个工作区下 dsh 的历史会话（``$DSH_HOME/sessions/*/session-*/``）。

会话目录名是 dsh 自己对工作区路径的编码（未公开、别处也没用到过这个规则），
这里不去猜测它——直接解压每个会话文件、读第一行 JSON 里自带的 ``cwd``
字段，按真实路径精确匹配，不管编码规则长什么样都不会出错。
"""

import io
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import zstandard

from .config_store import dsh_home

SESSION_FILE_NAME = "session.jsonl.zstd"

# 每个会话文件里，不管有没有真实对话都会写这几种生命周期/配置记录
# （建会话、权限预设、沙箱模式、审批策略、结束标记）；用户是否真正发过消息，
# 只有 "user/message" 这个事件类型能确认——行数本身不是可靠信号。
_USER_MESSAGE_TYPE = "user/message"


@dataclass
class SessionInfo:
    id: str
    dir: Path
    size: int
    mtime: datetime
    line_count: int
    has_user_message: bool
    cwd: str  # 会话文件里记录的原始工作区路径（未归一化，仅用于展示/分组）

    @property
    def is_empty(self) -> bool:
        """没有任何一条 user/message，说明用户从没在这个会话里真正发过消息。"""
        return not self.has_user_message


def _read_session_meta(path: Path):
    """解压会话文件，返回 (cwd, 行数, 是否含 user/message)；

    读取/解压/解析失败一律返回 (None, 0, False)。

    会话文件是随对话增量追加写入的：每次写入各自压缩成一个 zstd 帧，
    直接拼在文件末尾，整份文件是多个 zstd 帧首尾相连而成。一次性的
    ``ZstdDecompressor().decompress()`` 只解出第一帧就停——对这些文件
    而言，那正好只是开头的 session 头信息，会把有大量真实对话的会话
    误判成"只有 1 行、是空的"。用 stream_reader 顺序遍历全部帧才对。
    """
    try:
        raw = path.read_bytes()
        text = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw)).read()
    except Exception:
        return None, 0, False
    lines = [line for line in text.split(b"\n") if line.strip()]
    if not lines:
        return None, 0, False
    cwd = None
    try:
        cwd = json.loads(lines[0]).get("cwd")
    except Exception:
        pass
    has_user_message = False
    for line in lines:
        try:
            if json.loads(line).get("type") == _USER_MESSAGE_TYPE:
                has_user_message = True
                break
        except Exception:
            continue
    return cwd, len(lines), has_user_message


def _resolve(path_str: str) -> str:
    try:
        return str(Path(path_str).resolve())
    except Exception:
        return path_str


def _normalize(path_str: str) -> str:
    """大小写不敏感的比较键（Windows 路径不区分大小写）。"""
    return _resolve(path_str).lower()


def list_all_sessions() -> list[SessionInfo]:
    """扫描 ``$DSH_HOME/sessions`` 下所有工作区的全部会话，不按工作区过滤。

    按修改时间从新到旧排列；:func:`list_sessions_for_workspace` 和
    :func:`list_workspaces` 都基于这一次扫描的结果做进一步筛选/分组，
    避免为每个工作区重复解压一遍全部会话文件。
    """
    sessions_root = dsh_home() / "sessions"
    if not sessions_root.is_dir():
        return []
    results = []
    for workspace_dir in sessions_root.iterdir():
        if not workspace_dir.is_dir():
            continue
        for session_dir in workspace_dir.iterdir():
            data_file = session_dir / SESSION_FILE_NAME
            if not data_file.is_file():
                continue
            cwd, line_count, has_user_message = _read_session_meta(data_file)
            if cwd is None:
                continue
            stat = data_file.stat()
            results.append(
                SessionInfo(
                    id=session_dir.name,
                    dir=session_dir,
                    size=stat.st_size,
                    mtime=datetime.fromtimestamp(stat.st_mtime),
                    line_count=line_count,
                    has_user_message=has_user_message,
                    cwd=cwd,
                )
            )
    results.sort(key=lambda s: s.mtime, reverse=True)
    return results


def list_workspaces(sessions: "list[SessionInfo] | None" = None) -> list[str]:
    """列出所有会话里出现过的工作区路径，按最近活动排在前面。

    ``sessions`` 已经按时间从新到旧排好序，顺序遍历时只在第一次见到某个
    （大小写无关）路径时记录，就自然保留了该工作区最近一次使用的原始写法
    作为展示文本，也保证了排序。
    """
    if sessions is None:
        sessions = list_all_sessions()
    seen: dict[str, str] = {}
    for info in sessions:
        key = _normalize(info.cwd)
        if key not in seen:
            seen[key] = _resolve(info.cwd)
    return list(seen.values())


def list_sessions_for_workspace(
    workspace: str, sessions: "list[SessionInfo] | None" = None
) -> list[SessionInfo]:
    """从（可选的预扫描）会话列表里筛出 cwd 与给定工作区一致的那些。"""
    if sessions is None:
        sessions = list_all_sessions()
    if not workspace:
        return list(sessions)
    target = _normalize(workspace)
    return [info for info in sessions if _normalize(info.cwd) == target]


def delete_session(session: SessionInfo) -> None:
    """彻底删除一个会话目录，不可恢复。"""
    shutil.rmtree(session.dir)
