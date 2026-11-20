"""数据提供者层：按能力分域的 Ports & Adapters。"""


class ProviderError(RuntimeError):
    """数据提供者统一异常，所有 provider 调用失败都应抛它或其子类。"""
