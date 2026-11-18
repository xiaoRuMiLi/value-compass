"""HTTP 客户端：重试、超时、限流、可选缓存。"""

from __future__ import annotations

import time
from typing import Any, Optional

import requests

from fundamental.providers._shared.cache import ResponseCache
from fundamental.providers._shared.rate_limit import RateLimiter

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


class HttpClient:
    def __init__(
        self,
        retries: int = 3,
        timeout: float = 15.0,
        backoff: float = 1.0,
        rate_limiter: Optional[RateLimiter] = None,
        cache: Optional[ResponseCache] = None,
    ) -> None:
        self.retries = retries
        self.timeout = timeout
        self.backoff = backoff
        self.rate_limiter = rate_limiter
        self.cache = cache
        self._session = requests.Session()
        # 直连数据源，不读取环境代理（本环境有干扰性 HTTP_PROXY 变量）。
        self._session.trust_env = False
        self._session.headers.update(_DEFAULT_HEADERS)

    def get(self, url: str, params: Optional[dict] = None, headers: Optional[dict] = None) -> requests.Response:
        return self._request("GET", url, params=params, headers=headers)

    def get_json(
        self,
        url: str,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        ttl: Optional[int] = None,
    ) -> Any:
        if self.cache is not None:
            cache_key = self.cache.key(url, params)
            cached = self.cache.get(cache_key, ttl)
            if cached is not None:
                return cached
            data = self.get(url, params=params, headers=headers).json()
            self.cache.set(cache_key, data, ttl)
            return data
        return self.get(url, params=params, headers=headers).json()

    def get_text(
        self,
        url: str,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        ttl: Optional[int] = None,
    ) -> str:
        if self.cache is not None:
            cache_key = self.cache.key(url, params)
            cached = self.cache.get(cache_key, ttl)
            if cached is not None:
                return str(cached)
            text = self.get(url, params=params, headers=headers).text
            self.cache.set(cache_key, text, ttl)
            return text
        return self.get(url, params=params, headers=headers).text

    def post_form(
        self,
        url: str,
        data: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> requests.Response:
        return self._request("POST", url, data=data, headers=headers)

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        last_exc: Optional[requests.RequestException] = None
        for attempt in range(self.retries + 1):
            if self.rate_limiter is not None:
                self.rate_limiter.wait(url)
            try:
                response = self._session.request(method, url, timeout=self.timeout, **kwargs)
                if response.status_code in _RETRYABLE_STATUS and attempt < self.retries:
                    time.sleep(self.backoff * (2**attempt))
                    continue
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(self.backoff * (2**attempt))
                    continue
        assert last_exc is not None
        raise last_exc
