# ADR-001 大模型接入抽象

- 状态：已接受
- 日期：2026-11-19

## 背景

项目定位是利用 AI 大模型辅助个股价值分析。基础骨架已就绪，需要在不动摇现有分层的前提下，
给全项目一个统一、可切换的大模型调用入口，避免以后每个模块各自接一个 SDK。

## 决策

- 大模型作为第 18 个能力放在 `fundamental/providers/llm/`，沿用 Ports & Adapters + registry 模式。
- 供应商与参数放 `config/llm.toml`，密钥放 `.env`（gitignore），默认 `mock` 离线可用。
- 调用入口统一为 `fundamental/services/llm.py` 的 `get_llm()` / `chat()` / `chat_json()` / `chat_optional()`。

## 理由

- 与现有 provider 扩展方式一致，换供应商零改代码。
- LLM 调用收敛到 services 层，`analysis/` 保持纯计算可测，符合分层铁律。
- 自研轻适配走 OpenAI 兼容 `/chat/completions`，覆盖 OpenAI / DeepSeek / Moonshot / Qwen / Ollama / vLLM，零新依赖。
- 真实模型仅做叙述增强，失败回退模板，不阻塞主流程。

## 后果

- 正面：可扩展、可测试、密钥不落库、无新运行时依赖。
- 负面：`analysis/` 不直接调 LLM，AI 文字统一经 services 编排。
- 约束：LLM 不落 SQLite，结果实时生成。

## 备选

- 引入 `openai` / `anthropic` 官方 SDK（依赖变多）。
- 引入 `litellm` 统一层（最重，与项目轻依赖风格冲突）。
- 独立 `fundamental/llm/` 客户端包（调用更直白，但会让 analysis 有机会直接碰外部服务）。
