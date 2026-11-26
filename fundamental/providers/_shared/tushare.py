"""Tushare 公共客户端：token 读取、JSON 请求、错误映射与响应归一化。

所有 Tushare provider 共用本客户端，不直接依赖 tushare SDK（避免 pandas 等
重依赖）；接口契约仍是项目统一的 ProviderError 异常体系。
"""

from __future__ import annotations

import os
from typing import Any, Optional

from fundamental.providers import (
    ProviderConfigError,
    ProviderDataError,
    ProviderRateLimitError,
)
from fundamental.providers._shared.http import HttpClient
from fundamental.utils import paths

API_URL = "https://api.tushare.pro"
TOKEN_ENV = "TUSHARE_TOKEN"


def load_token() -> Optional[str]:
    """按 环境变量 → 项目根目录 .env 的顺序读取 token，不输出明文。"""
    token = os.environ.get(TOKEN_ENV, "").strip()
    if token:
        return token
    env_path = paths.project_root() / ".env"
    if not env_path.exists():
        return None
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith(TOKEN_ENV + "="):
            continue
        value = stripped.split("=", 1)[1].strip().strip("'\"")
        if value and not value.startswith("你的"):
            return value
    return None


class TushareClient:
    """Tushare Pro HTTP API 的轻量封装。"""

    def __init__(self, token: Optional[str] = None, client: Optional[HttpClient] = None) -> None:
        self.token = token if token is not None else load_token()
        self.client = client or HttpClient()

    def call(
        self,
        api_name: str,
        params: Optional[dict[str, Any]] = None,
        fields: str = "",
        ttl: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """调用接口，返回 dict 行列表；统一映射 Tushare 业务错误码。"""
        if not self.token:
            raise ProviderConfigError(f"缺少 {TOKEN_ENV}，请在 .env 或环境变量中配置")
        payload = {
            "api_name": api_name,
            "token": self.token,
            "params": params or {},
            "fields": fields,
        }
        response = self.client.post_json(API_URL, payload, ttl=ttl)
        if not isinstance(response, dict):
            raise ProviderDataError(f"Tushare {api_name} 响应格式异常")
        code = response.get("code")
        message = str(response.get("msg") or "")
        if code not in (0, None):
            if "频率超限" in message or "频率" in message:
                raise ProviderRateLimitError(f"Tushare {api_name} 频率受限：{message}")
            if "没有接口" in message or "权限" in message:
                raise ProviderConfigError(f"Tushare {api_name} 无权限：{message}")
            raise ProviderDataError(f"Tushare {api_name} 返回错误 {code}：{message}")
        data = response.get("data") or {}
        names = data.get("fields") or []
        rows = data.get("items") or []
        return [dict(zip(names, row)) for row in rows if isinstance(row, (list, tuple))]
