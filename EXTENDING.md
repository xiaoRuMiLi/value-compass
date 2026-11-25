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
- 估值参数校准：`analysis/valuation/ddm.py`、`dcf.py` 的默认 g/r 只是**回退基准**；正常路径由
  `narrative/_valuation_params.py` 用 LLM 读财报/分红/行业/研报后给保守参数，经边界钳制（g −0.05~0.12、
  r 0.07~0.14、r−g≥0.02）写入 `valuation_params_{symbol}.json`。`services/analyze.py` 通过
  `load_calibration_params()` 惰性读取同一份参数注入 `evaluate_ddm/evaluate_dcf`，因此 `/analysis`、
  估值解读和报告页上方估值卡片与下方「参数校准」始终是同一套结果，不再出现两套矛盾的合理价。
  校准未生成或缓存过期时才回退到 analysis 默认值。提示词在 `prompts/valuation_params.md`，
  改参数口径或输出结构后递增 `narrative/_common.py` 的 `_VALUATION_PARAMS_VERSION` 使缓存失效（当前 v4）。
  研报正文由 `utils/research_text.py` 抓取（东财详情页 `#ctx-content`，缓存于 `data/cache/research/`，
  失败降级为空列表，绝不打断主流程）；无 LLM 时回退到由历史净利增速推导的保守参数。
  端点 `/api/stock/{symbol}/valuation-params?refresh=` 重新校准。校准同时输出**悲观 / 基准 / 乐观**三档
  参数与区间：LLM 按公司实际不确定来源定参（不是机械对称），缺失时由 `_derive_scenarios()` 从基准派生；
  三档共用同一份 DPS/FCF 输入，只变 g/r/永续。报告页上方估值区只显示相对估值与格雷厄姆，
  DDM/DCF 统一在下方「估值参数校准」展示一套，避免上下重复。
  改情景口径后递增 `_VALUATION_PARAMS_VERSION`（当前 v5）使缓存失效。

## 5. 调整财报体检的触发阈值

改 `config/forensics.toml`，不要改代码。`analysis/forensics.py` 里没有任何硬编码判定值，全部经
`ForensicsConfig` 注入；字段含义与调参方向写在配置文件每个分组的注释里，原则是**数值调大＝触发更少**。

- `[forensics]`：结论窗口期数、事件证据年限、送入 LLM 的信号/波动上限、原文守卫下限与补档份数。
- `[forensics.thresholds]`：离群倍数、各率类百分点阈值、预告容差、质押/减持门槛、聚合门限、风险等级门槛与线索关键词（主扫描 `analysis/forensics.py`）；末尾两组 `十一·B / 十一·C` 分别控制「单季还原 + 全年-累计现金含量对照」与「财报原文事实的趋势/组合信号」，同样遵循「调大＝触发更少」。
- `[forensics.thresholds.fraud_patterns]`：财务戏法检查中 `quantitative` 类 11 项的全部判定值，独立子表，对应 `FraudPatternConfig`。同样遵循「调大＝触发更少」，并随 `ForensicsConfig.fingerprint()` 一起让缓存自动重算。`documentary` 类 4 项**没有数值阈值**——它们靠「引文可逐字定位 + 模型建议 + 风险语义标记」确认，标记在 `analysis/factor_registry.py` 的 `trigger_markers`，不下发到模型。
- 嫌风险点太多：调大 `net_margin_pp` / `roe_pp` / `expense_ratio_pp` / `revenue_yoy_swing_pp`；
  或把 `rate_require_outlier` 改成四项全开（率类异动还必须偏离自身同口径历史波动）。
- 嫌戏法线索太敏感：调大子表里的 `ar_divergence_pp` / `big_bath_rebound_pct` / `top5_customer_pct` 等；
  调小 `cash_conversion_low` 可让「净现比持续偏低」更少触发。
- 嫌条目太碎：调小 `consolidate_min_occurrences`（同类信号 2 期即合并成趋势型风险点），
  或调小 `expense_cluster_min_items`（同报告期多个科目异动合并成一条）；两者都保留逐期/逐科目明细，不丢信息。
- 关闭降噪：`consolidate_min_occurrences = 0`、`expense_cluster_min_items = 0`。
- 生效方式：报告缓存记录 `thresholds_fingerprint`，改完即自动重算，无需清缓存；也可点页面「重新分析」或用 `?refresh=true`。
  体检区底部的「本次生效的触发阈值」折叠面板可用来核对配置文件是否已被读取。
- 新增主扫描阈值字段：`analysis/forensics_config.py` 的 `ForensicsConfig` 加字段与 `from_mapping()` 的 `pick(...)`（带非法值兜底），
  `config/forensics.toml` 的 `[forensics.thresholds]` 加默认值，`analysis/forensics.py` 用 `cfg.xxx` 判定并写进 `trigger` 文案。
- 新增财务戏法因子（注册表驱动，`analysis/factor_registry.py` 是单一事实来源）：
  - `documentary`（原文取证型）：**只需在 `FACTOR_REGISTRY` 追加一条 `FactorSpec`**，填 `source_scope / probe_criteria / trigger_markers / red_flag_refs`。
    `documentary_briefs()`、`checks` 列表、`coverage` 模板、前端因子清单全部自动生成，不用改扫描器、不用改前端标签表、不用碰配置阈值。
  - `quantitative`（数值型）：除注册表一条 `FactorSpec` 外，在 `analysis/forensic_patterns.py` 加一个 `_check_*(...) -> tuple[FraudPattern | None, dict | None]`
    并注册进 `_HANDLERS`（模块级包装函数，**不要再往扫描循环里插 lambda**）；新阈值加到 `FraudPatternConfig` 字段与
    `[forensics.thresholds.fraud_patterns]` 子表（`from_mapping()` 按字段类型自动逐项兜底）；若 facts 用了新 key，再补 `forensics.js` 的 `FACT_LABELS`。
  - 组合条件、同报告期同比、结论窗口与 `coverage` 三分沿用现有约定，不要退回单点异动报警。
  - **缓存失效**：新增阈值会自动进 `ForensicsConfig.fingerprint()`；但只加因子、不新增阈值时指纹不变，
    **必须递增 `_FORENSICS_VERSION`（涉及通读结构时同时递增 `_MINING_VERSION`）**，否则旧报告缓存不会重算。
  - `red_flag_refs` 只能指向 `references/red-flags.md` 里已存在的 `RF-*` 编号；新增红旗先追加编号，不要复用或重排。
- 新增「依赖财报原文文字」的信号，分层是固定的：services 层（`_report_facts.py` / `_audit_evidence.py`）只做章节定位 + 数字抽取 + 原文摘录，
  抽不到写 `missing`/`missing_facts`；把结果当**纯 dict** 通过 `scan_forensics(report_facts=..., audit_evidence=...)` 传入，
  `analysis/forensics.py` 的 `_scan_evidence_facts()` 只做数值比较并写 `coverage`；判定阈值仍放 `ForensicsConfig`。
  `analysis` 层不读文件、不查库、不 import provider，依赖方向不反转。
- 单季信号只能用 `_derive_quarters()` 的**同一自然年内累计差分**结果，禁止拿 FY 与 Q3 跨年相减，
  也禁止把缺期造成的「多期合并增量」当成单季；`is_single=False` 的条目只进事实序列。
- 两个扫描器都**不要**留第二份硬编码，也**不要**让 `analysis` 层反过来读配置文件；配置对象由 services 层注入。
  `analysis` 出参走 `models.py` 的 dataclass，services 层在报告出口 `dataclasses.asdict` 转 dict 后再交给 Web 层。
- 模型返回非法 JSON 时，provider 的 `_extract_json()` 只做**不改变内容**的清洗（去代码围栏、截取首尾、去尾逗号），
  不给截断的 JSON 补右括号或补字符串结尾（那会捏造数字与摘录）；分段挖掘 `_chat_segment_json()` 会带严格格式指令重试一次，
  仍失败则把该段写进 `report_mining.failed_segments` 并在 `caveats` 声明，绝不显示成「通读无发现」。

## 6. 快速清单

- 调财报体检阈值（主扫描 + 财务戏法）：只动 `config/forensics.toml`，含 `[forensics.thresholds.fraud_patterns]` 子表。
- 调审计视角/资金动向提示阈值与例行合规披露识别：只动 `config/forensics.toml` 的 `[forensics.audit_evidence]` 与 `regulatory_routine_patterns`；新增事实字段改 `services/narrative/_audit_evidence.py`（只抽取不判断，抽不到写 `missing`）。
- 改数据源：只动 provider 文件 + `providers.toml`。
- 改 PDF 文本提取：只动 `fundamental/utils/pdf.py`；新增 PDF 源若出现「㛮暈⛚㕾」式乱码，先查字体有没有 `/ToUnicode`，偏移由 `infer_cid_offset()` 自动推断、不要硬编码，且必须重跑归档 `.txt`（不要手改已抽出的文本）。
- 改分析：只动 `analysis/` + `models.py` + `analyze.py`。
- 改展示：只动 `pages/` + `web/`。
- 改存储：四处同步（SCHEMA/schema.sql/models/sync）。
- 改大模型：只动 `providers/llm/` + `config/llm.toml`。
- 改提示词：只动 `prompts/*.md`。
- 每步后：验证 + 更新 `PROGRESS.md` + 提交推送。
