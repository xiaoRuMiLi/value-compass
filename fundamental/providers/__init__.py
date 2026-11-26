"""数据提供者层：按能力分域的 Ports & Adapters。"""


class ProviderError(RuntimeError):
    """数据提供者统一异常，所有 provider 调用失败都应抛它或其子类。"""


class ProviderConfigError(ProviderError):
    """配置或注册错误（能力未注册、缺少 primary 数据源等）。"""


class ProviderRequestError(ProviderError):
    """上游请求失败（网络错误、超时、HTTP 错误状态）。"""


class ProviderRateLimitError(ProviderRequestError):
    """上游接口频率限制；调用方可用其它数据源降级。"""


class ProviderDataError(ProviderError):
    """上游数据缺失或无法解析。"""


class ProviderNotImplementedError(ProviderError):
    """provider 能力已声明但尚未实现。"""
