"""大模型能力：Ports & Adapters，按配置切换供应商。"""

# 导入即注册到 registry，供 services/llm.py 按配置查找适配器。
from fundamental.providers.llm import mock, openai_compat  # noqa: F401
