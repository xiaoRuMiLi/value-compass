"""汇率服务：港币 → 人民币（供港股分红 DDM 换算）。

设计约束：
- 在线源优先：欧洲央行口径（frankfurter.app，免费无密钥），当日汇率；
- 有 TTL 缓存，失败回退到 config/default.toml 的 hkd_to_cny_default；
- 汇率值与其来源/日期一起返回，供前端标注口径，绝不静默用旧值当实时值。
"""

from __future__ import annotations

import time
from typing import Any

from fundamental.config import load_settings
from fundamental.providers._shared import HttpClient, RateLimiter, ResponseCache
from fundamental.providers._shared.convert import to_float
from fundamental.utils.logging import get_logger

_logger = get_logger(__name__)

_URL = "https://api.frankfurter.app/latest"
_client = HttpClient(cache=ResponseCache(), rate_limiter=RateLimiter(min_interval=0.1))
_cached: tuple[float, str, float] | None = None  # (rate, date, fetched_ts)


def hkd_to_cny() -> dict[str, Any]:
    """返回港币兑人民币汇率，含来源与日期。

    {"rate": float, "source": str, "date": str, "is_fallback": bool}
    """
    global _cached
    settings = load_settings()
    default_rate = float(settings.get("fx.hkd_to_cny_default", 0.85))
    ttl = float(settings.get("fx.fx_cache_ttl_seconds", 86400))

    now = time.time()
    if _cached and now - _cached[2] < ttl:
        return {"rate": _cached[0], "source": "frankfurter.app (ECB)", "date": _cached[1], "is_fallback": False}

    try:
        payload = _client.get_json(
            _URL, params={"from": "HKD", "to": "CNY"}, ttl=int(ttl)
        )
        rate = to_float((payload.get("rates") or {}).get("CNY"))
        if rate and rate > 0:
            date = str(payload.get("date") or "")
            _cached = (rate, date, now)
            return {"rate": rate, "source": "frankfurter.app (ECB)", "date": date, "is_fallback": False}
    except Exception as exc:  # noqa: BLE001
        _logger.warning("汇率在线获取失败，回退默认值：%s", exc)

    return {
        "rate": default_rate,
        "source": "config/default.toml 默认值",
        "date": "",
        "is_fallback": True,
    }
