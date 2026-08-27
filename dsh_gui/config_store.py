"""读取与写入 DeepSeek Harness（dsh）的配置文件。

dsh 默认把配置放在 ``$DSH_HOME``（未设置时为 ``~/.dsh``）：

- ``.credentials.yaml``：存放 API Key 等凭据（例如 ``DEEPSEEK_API_KEY``）。
- ``settings.yaml``：存放界面与默认模型等设置（例如 ``agent-default-model``）。

本模块只提供最小读写封装，供设置对话框使用。
"""

import os
from pathlib import Path

import yaml


def dsh_home() -> Path:
    """返回 DSH_HOME 目录。"""
    return Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))


# 下面两个路径都用函数而非模块级常量：常量会在 import 时就把 DSH_HOME 的值
# 固化下来，之后再改环境变量就不生效了。
def credentials_file() -> Path:
    """凭据文件路径（API Key 等）。"""
    return dsh_home() / ".credentials.yaml"


def settings_file() -> Path:
    """设置文件路径（默认模型等）。"""
    return dsh_home() / "settings.yaml"


DEFAULT_PROVIDER = "deepseek-official"
DEFAULT_MODELS = ["deepseek-v4-flash", "deepseek-v4-pro"]
REASONING_EFFORTS = ["high", "max", "off"]


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(
            data,
            fh,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )


def get_api_key() -> str:
    """读取 DeepSeek API Key（可能为空字符串）。"""
    return str(_read_yaml(credentials_file()).get("DEEPSEEK_API_KEY", "") or "")


def set_api_key(key: str) -> None:
    """写入 DeepSeek API Key；传入空字符串表示删除。"""
    path = credentials_file()
    data = _read_yaml(path)
    key = (key or "").strip()
    if key:
        data["DEEPSEEK_API_KEY"] = key
    else:
        data.pop("DEEPSEEK_API_KEY", None)
    _write_yaml(path, data)


def get_default_model_config() -> dict:
    """读取默认模型配置（provider / model / reasoningEffort）。"""
    cfg = _read_yaml(settings_file()).get("agent-default-model", {})
    return cfg if isinstance(cfg, dict) else {}


def set_default_model_config(model: str, reasoning_effort: str) -> None:
    """写入默认模型配置，保留现有 provider（默认 deepseek-official）。"""
    path = settings_file()
    data = _read_yaml(path)
    existing = data.get("agent-default-model", {})
    provider = (
        existing.get("provider", DEFAULT_PROVIDER)
        if isinstance(existing, dict)
        else DEFAULT_PROVIDER
    )
    data["agent-default-model"] = {
        "provider": provider,
        "model": model,
        "reasoningEffort": reasoning_effort,
    }
    _write_yaml(path, data)
