"""提示词模板加载：prompts/*.md，供各分析场景复用与手工修改。"""

from __future__ import annotations

from pathlib import Path

from fundamental.utils import paths


def prompts_dir() -> Path:
    return paths.project_root() / "prompts"


def load_prompt(name: str) -> str:
    """按名称读取 prompts/<name>.md，返回去掉首尾空白的全文。"""

    path = prompts_dir() / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"缺少提示词模板: {path}")
    return path.read_text(encoding="utf-8").strip()
