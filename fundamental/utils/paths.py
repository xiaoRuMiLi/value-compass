"""项目路径解析：基于项目根目录的相对路径，允许环境变量覆盖。"""

from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    """返回项目根目录（fundamental/utils/paths.py 上两级）。"""
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    return project_root() / "data"


def cache_dir() -> Path:
    return data_dir() / "cache"


def db_dir() -> Path:
    return data_dir() / "db"


def db_path() -> Path:
    override = os.environ.get("VALUE_COMPASS_DB")
    if override:
        return Path(override)
    return db_dir() / "fundamental.db"


def archive_dir() -> Path:
    return data_dir() / "archive"


def documents_dir() -> Path:
    return archive_dir() / "documents"


def generated_dir() -> Path:
    return project_root() / "generated"


def config_dir() -> Path:
    return project_root() / "config"


def ensure_dirs() -> None:
    """创建运行时所需的目录（data/cache、db、archive、documents、generated）。"""
    for directory in (cache_dir(), db_dir(), archive_dir(), documents_dir(), generated_dir()):
        directory.mkdir(parents=True, exist_ok=True)
