"""services 层的配置适配：``config/forensics.toml`` → ``ForensicsConfig``。

``analysis`` 层不读配置文件，由本模块读完后注入扫描器，保持依赖方向
（config → services → analysis → models）。
"""

from __future__ import annotations

from fundamental.analysis.forensics_config import ForensicsConfig
from fundamental.config import load_settings


def load_forensics_config() -> ForensicsConfig:
    """读取当前生效的财报体检阈值；配置文件缺失或字段非法时逐项回退默认值。"""

    return ForensicsConfig.from_mapping(load_settings().get("forensics"))
