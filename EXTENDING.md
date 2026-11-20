# value-compass 扩展指南

本文档是修改/扩展本项目的操作手册，供后续 agent 或协作者参考。动手前先读
`ARCHITECTURE.md`、`CONTRACTS.md`、`CONVENTIONS.md`。

## 1. 结构总览

```text
fundamental/
├── models.py              全部枚举 + dataclass，数据唯一出口
├── config.py              配置加载
├── providers/             17 个能力，每个 = port.py + 实现
│   ├── registry.py        能力 → 数据源 注册表
│   └── _shared/           http / cache / rate_limit / convert
├── services/
│   ├── sync.py            取数 → 入库（全能力）
│   └── analyze.py         读库 → 分析 → 汇总结果
├── analysis/              纯计算（valuation/ 估值、masters/ 大师）
├── storage/               schema.sql / db.py / repository.py
├── web/                   app.py / routes/api.py
└── utils/                 symbols / paths / logging

pages/                     前端页面 + static/js/css + vendor/echarts
config/                    default/dev/providers/official + masters/
scripts/                   fetch_data.py / serve.py
```

## 2. 依赖方向铁律

```text
providers ─┐
storage ───┼──→ models ←── analysis
           │
web ───────┴──→ services ──→ providers / storage / analysis
```

1. provider 只依赖 `models` 和 `_shared`，返回 `models` 里的 dataclass。
2. analysis 只依赖 `models`，不 import 具体数据源。
3. 横向调用只经过 `services` 或 `registry`。

## 3. 通用约定

- 命名：目录/模块 `snake_case`，类 `PascalCase`，Port 名 `<Capability>Port`，实现名 `<Source><Capability>Provider`。
- 每完成一个工作单元：更新 `PROGRESS.md` 状态 + git 提交（`feat:`/`fix:`/`docs:` 等）。
- 本环境 git 推送需先 `Remove-Item Env:GIT_SSH_COMMAND`，再设 `GIT_SSH=C:\Windows\System32\OpenSSH\ssh.exe`。

## 4. 扩展操作

### 4.1 换数据源 / 加备用源

在能力目录加一个实现文件，注册，再改配置即可，上层零改动。

```python
# fundamental/providers/quote/sina.py
from fundamental.providers.registry import register

@register("quote", "sina")
class SinaQuoteProvider:
    def fetch_snapshot(self, symbol): ...
    def fetch_kline(self, symbol, start, end): ...
    def fetch_valuations(self, symbol, start=None, end=None): ...
```

```toml
# config/providers.toml
[providers.quote]
primary = "sina"        # 或 primary = "eastmoney" + fallback = "sina"
```

### 4.2 加一个全新的数据能力

1. `fundamental/providers/<capability>/` 建 `port.py`（Protocol）+ `<source>.py`（实现，`@register`）。
2. `fundamental/models.py` 加对应 dataclass。
3. `SCHEMA.md` + `fundamental/storage/schema.sql` 加表。
4. `fundamental/services/sync.py` 加写入段。
5. `config/providers.toml` 加 `[providers.<capability>] primary = ...`。

### 4.3 加一个分析模块

1. `fundamental/analysis/<module>.py` 加纯函数（输入 models，输出结果 dataclass）。
2. `fundamental/models.py` 加结果 dataclass（若还没有）。
3. `fundamental/services/analyze.py` 的 `analyze_stock` 里挂进返回字典。
4. 页面需要时在 `fundamental/web/routes/api.py` 加端点。

### 4.4 加估值方法 / 加大师

估值：

1. `fundamental/analysis/valuation/<method>.py` 加函数，返回 `ValuationResult`。
2. `fundamental/analysis/valuation/__init__.py` 导出。
3. `fundamental/services/analyze.py` 的 `valuations_result` 列表里追加调用。

大师：

1. `fundamental/analysis/masters/<master>.py` 加模型类（`name` + `evaluate`，返回 `MasterVerdict`）。
2. `config/masters/<master>.toml` 加阈值。
3. `fundamental/analysis/masters/__init__.py` 导出。
4. `fundamental/services/analyze.py` 的 `masters` 列表里追加。

### 4.5 加一个页面

1. `pages/<name>.html` + `pages/static/js/<name>.js`。
2. `fundamental/web/app.py` 加路由（返回 FileResponse）。
3. 数据不足时 `fundamental/web/routes/api.py` 加端点。

### 4.6 加一张表

同步改四处：`SCHEMA.md`、`fundamental/storage/schema.sql`、`fundamental/models.py`、`fundamental/services/sync.py`（写入）。
注意 `db.py` 用 `SCHEMA_VERSION` 做迁移，加表后要 bump 版本号并处理旧库迁移。

### 4.7 换大模型供应商 / 加大模型适配器

换供应商：改 `config/llm.toml` 的 `[llm] provider`（`deepseek` / `openai` / `ollama` / `mock`），
密钥放 `.env`（`DEEPSEEK_API_KEY` / `OPENAI_API_KEY`），或用环境变量 `LLM_PROVIDER` 覆盖。无需改代码。

加新适配器：

1. `fundamental/providers/llm/<adapter>.py` 加实现类，实现 `LlmPort`，用 `@register("llm", "<adapter>")` 注册。
2. `fundamental/providers/llm/__init__.py` 加导入，保证注册到 registry。
3. `config/llm.toml` 加 `[llm.providers.<name>]`（`adapter` / `base_url` / `api_key_env` / `model`）。

调用入口统一 `from fundamental.services.llm import chat, chat_json, chat_optional`，上层不感知具体供应商。

### 4.8 改提示词模板 / 概览缓存

- 提示词模板在 `prompts/*.md`，大师观点另有 `prompts/masters/_base.md` 与同名档案（如 `buffett.md`），
  由 `fundamental/utils/prompts.py` 的 `load_prompt(name)` 按名加载。
  改文案直接编辑对应 `.md`，无需改代码；新增场景就加一个 `.md` 并在调用处 `load_prompt("<name>")`。
- 新增大师时：`analysis/masters/` 加规则模型，`prompts/masters/` 加同名档案，
  并在 `narrative/_masters.py` 的 `_MASTER_LABELS` 补中文标签。
- 大师档案是“思想框架提炼”而非原文摘录：`buffett.md` 基于《巴菲特致股东的信》主题重编，
  `duan_yongping.md` 基于其公开访谈与经营实践。改档案时保留分节结构，量化阈值仍以
  `config/masters/*.toml` 为准，LLM 不得改写量化 verdict。
- 大师叙述的 user 消息除量化信号外还包含：`company.profile_summary`/`revenue_mix`、
  `industry`（板块行情）、`peer_comparison`（同行业最近年报中位数）、`latest_report`（最近一期
  财报摘要）、`market_valuation`（市场估值）。新增字段要同步更新 `prompts/masters/_base.md`
  的输入说明，否则模型可能不知道怎么用。
- 大师缓存有独立版本 `narrative/_common.py` 的 `_MASTERS_PROMPT_VERSION`：改档案或 facts 结构后
  递增，旧解读自动失效，其他三类叙述缓存不受影响；也可用 `?refresh=1` 强制刷新。
- 叙述类结果均带本地缓存：概览 `data/cache/narrative/{symbol}.json`、大师
  `data/cache/narrative/masters_{symbol}.json`、估值参数 `data/cache/narrative/valuation_params_{symbol}.json`，
  TTL 在 `config/default.toml` 的 `[narrative] cache_ttl_days`（默认 10 天）。接口加 `?refresh=1` 可强制刷新。
- 估值参数校准：默认 DDM/DCF 的 g/r 在 `analysis/valuation/ddm.py`、`dcf.py` 里写死，仅作基准；
  `narrative/_valuation_params.py` 用 LLM 读财报/分红/行业/研报后给保守参数，经边界钳制（g −0.05~0.12、
  r 0.07~0.14、r−g≥0.02）后复用同一公式重算，另给 10 年股息复投预期。提示词在 `prompts/valuation_params.md`，
  改参数口径或输出结构后递增 `narrative/_common.py` 的 `_VALUATION_PARAMS_VERSION` 使缓存失效。
  研报正文由 `utils/research_text.py` 抓取（东财详情页 `#ctx-content`，缓存于 `data/cache/research/`，
  失败降级为空列表，绝不打断主流程）；无 LLM 时回退到由历史净利增速推导的保守参数。
  新端点 `/api/stock/{symbol}/valuation-params?refresh=`；报告页在原默认估值下方新增「参数校准」对照区块，
  两者并存、默认估值不动。

## 5. 调整财报体检的触发阈值

改 `config/forensics.toml`，不要改代码。`analysis/forensics.py` 里没有任何硬编码判定值，全部经
`ForensicsConfig` 注入；字段含义与调参方向写在配置文件每个分组的注释里，原则是**数值调大＝触发更少**。

- `[forensics]`：结论窗口期数、事件证据年限、送入 LLM 的信号/波动上限。
- `[forensics.thresholds]`：离群倍数、各率类百分点阈值、预告容差、质押/减持门槛、聚合门限、风险等级门槛与线索关键词。
- 嫌风险点太多：调大 `net_margin_pp` / `roe_pp` / `expense_ratio_pp` / `revenue_yoy_swing_pp`；
  或把 `rate_require_outlier` 改成四项全开（率类异动还必须偏离自身同口径历史波动）。
- 嫌条目太碎：调小 `consolidate_min_occurrences`（同类信号 2 期即合并成趋势型风险点），
  或调小 `expense_cluster_min_items`（同报告期多个科目异动合并成一条）；两者都保留逐期/逐科目明细，不丢信息。
- 关闭降噪：`consolidate_min_occurrences = 0`、`expense_cluster_min_items = 0`。
- 生效方式：报告缓存记录 `thresholds_fingerprint`，改完即自动重算，无需清缓存；也可点页面「重新分析」或用 `?refresh=true`。
  体检区底部的「本次生效的触发阈值」折叠面板可用来核对配置文件是否已被读取。
- 新增阈值字段：`analysis/forensics_config.py` 加字段与 `from_mapping()` 的 `pick(...)`（带非法值兜底），
  `config/forensics.toml` 加默认值，`analysis/forensics.py` 用 `cfg.xxx` 判定并写进 `trigger` 文案；
  不要在扫描器里留第二份硬编码，也不要让 `analysis` 层反过来读配置文件。

## 6. 快速清单

- 调财报体检阈值：只动 `config/forensics.toml`。
- 改数据源：只动 provider 文件 + `providers.toml`。
- 改分析：只动 `analysis/` + `models.py` + `analyze.py`。
- 改展示：只动 `pages/` + `web/`。
- 改存储：四处同步（SCHEMA/schema.sql/models/sync）。
- 改大模型：只动 `providers/llm/` + `config/llm.toml`。
- 改提示词：只动 `prompts/*.md`。
- 每步后：验证 + 更新 `PROGRESS.md` + 提交推送。
