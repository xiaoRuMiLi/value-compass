"""数据提供者层：按能力分域的 Ports & Adapters。"""


class ProviderError(RuntimeError):
    """数据提供者统一异常，所有 provider 调用失败都应抛它或其子类。"""


class ProviderConfigError(ProviderError, KeyError, ValueError):
    """配置或注册错误（能力未注册、缺少 primary 数据源等）。

    同时继承 KeyError/ValueError 以兼容旧调用方的 catch 写法。
    """


class ProviderRequestError(ProviderError):
    """上游请求失败（网络错误、超时、HTTP 错误状态）。"""


class ProviderDataError(ProviderError, ValueError):
    """上游数据缺失或无法解析。"""


class ProviderNotImplementedError(ProviderError, NotImplementedError):
    """provider 能力已声明但尚未实现。"""
