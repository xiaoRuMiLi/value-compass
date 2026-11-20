# value-compass 项目架构

## 1. 项目定位

本项目同时具备两种身份：

- Codex skill：通过 `SKILL.md` 供 agent 调用，完成个股价值投资基本面分析。
- 本地可视化工具：产出可交互的 HTML 页面，用户可本地打开或通过在线界面查看。

覆盖市场：A股、港股、中概股（美股 ADR）。

定位为综合价值分析框架：财务 + 行业 + 大师模型 + 官网动态 + 可视化，而不是单一财务指标分析。

## 2. 技术栈

- 后端与计算：Python 3.11 标准 CPython。
- 数据源：东方财富公开接口 + 巨潮资讯（免费、无 key），股吧/天天基金为辅，官网数据为补充。
- 存储：SQLite 存结构化数据，本地文件系统存 PDF 等大文件。
- 前端：ECharts（本地内置，离线可用）+ 原生 JavaScript。
- 在线界面：FastAPI + uvicorn，本地运行。
- 大模型：OpenAI 兼容 `/chat/completions` 轻适配，可切换 deepseek / openai / ollama / mock（离线）。
- 可视化页面：标准交互页放 `pages/`，一次性生成页放 `generated/`。

## 3. 目录结构

```text
value-compass/
├── SKILL.md                          # skill 入口：给 agent 的总指令与路由
├── README.md
├── ARCHITECTURE.md                   # 架构与目录（本文档）
├── CONTRACTS.md / SCHEMA.md          # 接口契约 / 表结构
├── CONVENTIONS.md / EXTENDING.md     # 命名规范 / 扩展手册
├── ROADMAP.md / PROGRESS.md          # 路线 / 进度
├── ADR/                              # 架构决策记录
│   └── ADR-001-llm-provider-abstraction.md
├── references/                       # 方法论，agent 按需加载，不常驻上下文
│   ├── checklist.md                  # 财务质量检查清单
│   ├── valuation.md                  # 估值方法说明
│   ├── red-flags.md                  # 风险红旗
│   ├── report-format.md              # 报告规范
│   └── batch-analysis.md             # 批量/行业分析工作流
│
├── prompts/                          # 提示词模板（.md，可手工修改）
│   ├── overview_narrative.md         # 个股概览叙述
│   ├── score_narrative.md            # 财务五维评分解读
│   ├── valuation_narrative.md        # 估值模型批判性解读
│   ├── valuation_params.md           # 估值参数校准（LLM 结合财报/研报给保守 g/r）
│   └── masters/                      # 大师观点叙述（基座 + 个人档案，可人工编辑）
│       ├── _base.md                  # 共用约束 + 输入字段说明（行业/同业/最新财报/市场估值）
│       ├── buffett.md                # 巴菲特档案（致股东的信主题提炼：护城河/股东盈余/资本配置）
│       └── duan_yongping.md          # 段永平档案（公开访谈提炼：生意模式/本分/能力圈）
│
├── scripts/                          # 稳定入口命令（面向人和 agent）
│   ├── fetch_data.py                 # 只拉数并入库
│   └── serve.py                      # 启动本地 web 界面
│
├── fundamental/                      # 核心 Python 包
│   ├── __init__.py
│   ├── models.py                     # 归一化数据模型，所有 provider 的唯一出口
│   ├── providers/                    # 数据提供者，按能力分域（Ports & Adapters）
│   │   ├── registry.py               # 能力 → 默认源/备用源/优先级
│   │   ├── _shared/                  # 公共传输：HTTP、重试、限流、缓存
│   │   ├── quote/                    # 实时行情 + 复权 K 线（含港股估值快照）
│   │   │   ├── port.py
│   │   │   └── eastmoney.py          # 港股走 RPT_HKF10_FN_MAININDICATOR 取 PE_TTM/PB_TTM
│   │   ├── financial/                # 财务指标、三大报表、主营构成（港股走 HKF10 主要指标）
│   │   ├── announcement/             # 公告 + 财报 PDF + 招股书
│   │   ├── sentiment/                # 公众情绪（股吧）
│   │   ├── holding/                  # 股东 / 机构 / 基金持仓
│   │   ├── research/                 # 个股研报（评级/目标价/EPS 预测）
│   │   ├── industry_research/        # 行业研报（东财 qType=1，按行业代码）
│   │   ├── survey/                   # 机构调研纪要（东财 RPT_ORG_SURVEY，带正文）
│   │   ├── industry/                 # 行业动态
│   │   ├── litigation/               # 诉讼
│   │   ├── dividend/                 # 历史分红送配
│   │   ├── capitalflow/              # 资金流向
│   │   ├── hotspot/                  # 市场热点
│   │   ├── official/                 # 官网数据
│   │   ├── commodity/                # 商品/产品价格
│   │   ├── forecast/                 # 业绩预告/一致预期/财报日历
│   │   ├── governance/               # 公司行动/增减持/户数/质押/解禁
│   │   ├── macro/                    # 宏观指标
│   │   ├── integrity/                # 诚信记录
│   │   └── llm/                      # 大模型（Ports & Adapters，可切换供应商）
│   ├── services/                     # 编排层：取数 → 归一化 → 入库 → 计算
│   │   ├── sync.py                   # 增量拉取与水位管理
│   │   ├── archive.py                # 文档下载与本地存档
│   │   ├── analyze.py                # 分析编排
│   │   ├── llm.py                    # 大模型调用薄封装（get_llm/chat/chat_json）
│   │   └── narrative/                # 结构化结果 → 分段叙述（LLM 增强 + 模板回退 + 本地缓存）
│   │       ├── __init__.py           # 门面：导出六个 generate_* 入口
│   │       ├── _common.py            # 格式化 / 提示词输入 / 本地缓存
│   │       ├── _data.py              # 补充数据加载（逐年财报摘要、行业行情/同业对比、主营构成、最近一期财报、市场估值）
│   │       ├── _overview.py          # 个股概览叙述
│   │       ├── _score.py             # 五维评分解读
│   │       ├── _valuation.py         # 估值模型解读
│   │       ├── _masters.py           # 大师观点解读（基本面 + 行业/同业 + 财报 + 估值 + 档案 + 情报资料 → LLM）
│   │       ├── _valuation_params.py  # 估值参数校准（LLM 保守 g/r → 钳制 → 重算 + 复投 + 合理价区间 + 引用清单/卖方分歧度）
│   │       └── _forensics.py         # 财报深度体检（确定性取证扫描 → LLM 逐条剖析 → 结构化报告，含趋势型风险点）
│   ├── analysis/                     # 纯计算，不 import 任何具体数据源
│   │   ├── financial.py              # 财务质量评分
│   │   ├── comparison.py             # 横向对比（市占率、指标趋势、排名）
│   │   ├── cycle.py                  # 市场周期判断
│   │   ├── returns.py                # 分红复利 / 总回报
│   │   ├── valuation/
│   │   │   ├── base.py               # ValuationMethod 接口
│   │   │   ├── dcf.py                # 现金流折现
│   │   │   ├── ddm.py                # 股利折现
│   │   │   ├── relative.py           # PE/PB/PS 分位、PEG、EV/EBITDA
│   │   │   └── graham.py             # 格雷厄姆
│   │   ├── industry.py               # 行业地位、护城河、分布计算
│   │   ├── moat.py                   # 护城河评估
│   │   ├── price_correlation.py      # 产品价格与股价相关性
│   │   ├── holder_tracking.py        # 牛散/机构持仓变化追踪
│   │   ├── delisting.py              # 退市风险
│   │   ├── fraud_score.py            # 造假预警（M-score 等）
│   │   ├── financial_review.py       # 财报体检/大白话汇总（原轻量版，保留）
│   │   ├── forensics.py              # 财报取证扫描：多期风险点/异常波动/证据/覆盖度（纯确定性，不下结论）
│   │   ├── integrity.py              # 诚信记录挖掘
│   │   ├── masters/                  # 大师模型（可插拔）
│   │   │   ├── base.py               # MasterModel 接口
│   │   │   ├── buffett.py            # 巴菲特
│   │   │   └── duan_yongping.py      # 段永平
│   │   └── redflags.py               # 风险红旗
│   ├── storage/                      # SQLite 与文件存档层
│   │   ├── schema.sql                # 表结构
│   │   ├── db.py                     # 连接与迁移
│   │   └── repository.py             # 数据访问
│   ├── web/                          # 在线界面（FastAPI）
│   │   ├── app.py
│   │   └── routes/
│   │       └── api.py                # /api/* 数据接口
│   └── utils/                        # 横切工具
│       ├── symbols.py                # 代码/市场归一化
│       ├── prompts.py                # 提示词模板加载（prompts/*.md、prompts/masters/*.md）
│       ├── logging.py
│       ├── pdf.py                    # PDF 文本提取
│       ├── research_text.py          # 东财研报正文与元数据抓取（schema 版本/TTL/按篇复用/失败降级）
│       ├── intel.py                  # 统一情报门面：个股研报 + 行业研报 + 机构调研（分类型 TTL 缓存/失败降级）
│       └── paths.py
│
├── pages/                            # 标准交互页面，可共用、参数化
│   ├── index.html                    # 首页（搜索入口）
│   ├── report.html                   # 单股报告
│   ├── compare.html                  # 多股横向对比
│   ├── screener.html                 # 筛选
│   ├── calculator.html               # 分红复利 + 估值计算器
│   ├── financials.html               # 历年财务全景
│   ├── industry.html                 # 行业地位分布
│   ├── products.html                 # 产品价格 vs 股价对比
│   ├── holders.html                  # 个股十大流通股东
│   ├── holders-track.html            # 牛散/机构持仓追踪
│   ├── calendar.html                 # 事件日历
│   ├── review.html                   # 财报体检报告
│   ├── integrity.html                # 诚信档案
│   └── static/
│       ├── js/
│       │   ├── vendor/echarts.min.js # 本地内置，离线可用
│       │   ├── nav.js                # 全局导航（顶栏 + 侧栏 + 个股标签）
│       │   ├── home.js
│       │   ├── report.js
│       │   ├── compare.js
│       │   ├── screener.js
│       │   ├── calculator.js
│       │   ├── financials.js
│       │   ├── industry.js
│       │   ├── products.js
│       │   ├── holders.js
│       │   ├── holders-track.js
│       │   ├── calendar.js
│       │   ├── review.js
│       │   └── integrity.js
│       └── css/
│           ├── base.css
│           ├── home.css
│           └── report.css
│
├── generated/                        # 非标准、按临时要求生成的一次性展示 HTML
│
├── data/
│   ├── cache/                        # 原始响应缓存 + 叙述缓存 + 研报/情报资料缓存（TTL/版本号）
│   ├── db/
│   │   └── fundamental.db            # SQLite 主库
│   └── archive/                      # 永久本地存档，不清除
│       └── documents/                # 年报/公告/招股书 PDF（{code}/{year}/{doc_id}.pdf）
│
├── config/                           # 配置文件夹，按序合并
│   ├── default.toml                  # 默认配置
│   ├── dev.toml                      # 本地开发覆盖
│   ├── providers.toml                # 能力 → 源/备用/TTL/限流
│   ├── products.toml                 # 股票↔商品映射（种子数据）
│   ├── official.toml                 # 官网站点模板与选择器
│   ├── llm.toml                      # 大模型供应商与参数
│   └── masters/                      # 大师模型配置
│       ├── buffett.toml              # 巴菲特行为逻辑、阈值、权重
│       └── duan_yongping.toml        # 段永平行为逻辑、阈值、权重
│
├── scratch/                          # 临时脚本（gitignore）
├── tests/                            # 单元/契约测试
│   ├── test_analysis.py
│   ├── test_registry.py
│   ├── test_llm.py
│   └── test_narrative.py
├── .env                              # 密钥，gitignore
├── .env.example                      # 密钥模板，提交
├── pyproject.toml
└── .gitignore
```

> 规划项落地核对（2026-11-20）：上述目录结构中除以下各项外均已落地。
>
> - **未实现**：`scripts/analyze.py`、`scripts/batch_analyze.py`、`services/batch.py`（批量分析专用入口，批量能力暂由 `/api/compare`、`/api/screener` 提供）、`utils/validation.py`。
> - **可选未实现**：`pages/charts.html`（CONTRACTS 第 6 节标注为可选；K 线已在 report 页内呈现）。
> - **未落地但已由别处覆盖**：`analysis/score.py`（评分逻辑在 `analysis/financial.py`）、`config/famous_traders.toml`（牛散识别改用 `config/providers.toml` + `HolderCategory` 枚举）。
> - **已知技术债**：`static/js/api.js`（统一 fetch 封装）、`static/js/common.js`（公共工具）未抽取，当前各页面脚本各自实现 `getJSON` 等工具函数；`pages/shared/` 未建立。详见 `PROGRESS.md` 备注。

## 4. 分层与依赖方向

依赖只允许单向：

```text
providers ─┐
storage ───┼──→ models ←── analysis
           │
web ───────┴──→ services ──→ providers / storage / analysis
```

三条硬规则：

1. provider 只依赖 `models` 和 `_shared`。
2. analysis 只依赖 `models`，不 import 任何具体数据源。
3. 所有横向调用只经过 `services` 或 `registry`。

## 5. 数据流

```text
providers 取数 → models 归一化 → storage 入库/存档 → analysis 计算
                                                        ↓
                      web API (/api/*) → pages 页面 → ECharts 渲染
```

评分与结论实时计算，不落库；原始数据落 SQLite，文档 PDF 落本地存档。

## 6. Provider 能力清单

| 能力 | 目录 | 主要来源 | 数据 |
|---|---|---|---|
| 实时行情 | `quote` | 东财 | 现价、复权 K 线、历史估值（PE/PB/PS） |
| 财务指标 | `financial` | 东财 | 核心指标 + 利润表/资产负债表/现金流量表三大报表明细 + 主营构成 |
| 公告与报告 | `announcement` | 巨潮、东财 | 公告、财报 PDF、招股书 |
| 公众情绪 | `sentiment` | 股吧 | 帖子、阅读/回复数 |
| 持仓 | `holding` | 东财、天天基金 | 十大流通股东（牛散/机构）、基金持仓 |
| 研报 | `research` | 东财 | 研报、评级、目标价、今/明/后三年 EPS 预测 |
| 行业研报 | `industry_research` | 东财 | 按行业代码的行业策略/跟踪研报（qType=1） |
| 机构调研 | `survey` | 东财 | 投资者关系活动记录、调研问答正文（RPT_ORG_SURVEY） |
| 行业动态 | `industry` | 东财 | 行业板块、资金流 |
| 诉讼 | `litigation` | 巨潮 | 诉讼/仲裁公告 |
| 分红送配 | `dividend` | 东财 | 每股分红、送转、除权除息日 |
| 资金流向 | `capitalflow` | 东财 | 主力/大单净流入、沪深港通持股 |
| 市场热点 | `hotspot` | 东财 | 概念板块、领涨股 |
| 官网数据 | `official` | 公司官网 | 最新动态、产品价格、新闻 |
| 商品价格 | `commodity` | 新浪期货等 | 商品主力实时价与历史 K 线 |
| 预期差 | `forecast` | 东财 | 业绩预告/快报、一致预期、财报日历 |
| 治理 | `governance` | 东财 | 公司行动、增减持、股东户数、质押、解禁 |
| 宏观 | `macro` | 东财/新浪 | 无风险利率、CPI/PPI/PMI |
| 诚信记录 | `integrity` | 东财/巨潮 | 道德行为文字描述：兑现/失信、违规、处罚、问询、财务重述 |
| 大模型 | `llm` | OpenAI 兼容 `/chat/completions` | 文本对话、JSON 结构化输出；可切换 deepseek/openai/ollama/mock |

## 7. 估值方法清单

- DCF（现金流折现）
- DDM（股利折现）
- 相对估值（PE/PB/PS 分位、PEG、EV/EBITDA）
- 格雷厄姆估值

每个方法实现 `ValuationMethod` 接口，输出统一的估值区间、合理价格区间、关键假设与安全边际。

## 8. 大师模型（可插拔）

- 每位大师一个模型文件，放在 `analysis/masters/`，实现 `MasterModel` 接口。
- 大师的行为逻辑、筛选阈值、权重全部放在 `config/masters/*.toml`，模型代码读配置，不硬编码。
- 输出统一的 `MasterVerdict`：研判结论、通过/未通过信号、评分，以及一段"以大师视角审视个股"的文字。
- 文字由结构化信号 + 模板生成，agent 可在 `SKILL.md` 指引下润色，但不得改变结论。
- 加新大师 = 在 `masters/` 加一个文件 + `config/masters/` 加一个 toml，上层零改动。

## 9. 行业分析

- 单独页面 `industry.html`，以气泡/散点图展示行业内各公司的行业地位。
- X/Y 轴可用营收规模、ROE、毛利率、市值等可配置维度，气泡大小表示市值，颜色表示护城河评级。
- `analysis/industry.py` 计算行业内的相对地位，`analysis/moat.py` 给出护城河评级。
- 数据来自 `stocks.industry` 分组 + `financials` + `snapshots`，不新增网络源。

## 10. 官网数据

- `company_profile` 新增 `official_website` 字段，维护每家公司的官网地址。
- `official` 能力从官网抓取最新动态、产品价格、新闻，存 `official_updates` 表。
- 官网结构不统一，采用"站点模板 + 选择器配置"的适配方式，按公司或按模板配置，可扩展。

## 11. 命名约定

完整开发规范见 [CONVENTIONS.md](CONVENTIONS.md)，这里只列要点：

- Python 模块/包：小写 `snake_case`，如 `eastmoney.py`、`red_flags.py`。
- 数据 JSON：`{code}_{category}.json`，如 `600519_financial.json`。
- 输出报告：`{code}_{report}_{YYYYMMDD}.html`。
- 文档存档：`{code}/{year}/{doc_id}.pdf`。
- 测试：`test_*.py`，与源码目录一一对应。
- 常量集中 `config/`，枚举/类型集中 `models.py`。
- 类用 `PascalCase`，函数/变量用 `snake_case`，常量用 `UPPER_SNAKE_CASE`。

## 12. 扩展规则

- 加新数据源：在对应能力目录加一个文件，实现该能力 Port，在 `registry.py` 注册，在 `config/providers.toml` 写优先级；上层零改动。
- 加新能力：新建 `providers/<capability>/`，写 `port.py` + 至少一个实现 + config 一段。
- 加新估值方法：在 `analysis/valuation/` 加一个文件，实现 `ValuationMethod`。
- 加新大师：在 `analysis/masters/` 加一个文件 + `config/masters/` 加一个 toml。
- 换大模型供应商：改 `config/llm.toml` 的 `[llm] provider`（或设 `LLM_PROVIDER` 环境变量），无需改代码；加新适配器在 `providers/llm/` 加文件并 `@register("llm", ...)`。
- 加新页面：在 `pages/` 加 HTML + `static/js/` 加对应脚本 + `web/routes/pages.py` 加路由。

## 13. 数据源可用性（spike 实测，2026-11-11）

| 能力 | 结论 | 接口 / 说明 |
|---|---|---|
| 实时行情 + 历史估值 | 可用 | 东财 push2 + RPT_VALUEANALYSIS_DET |
| 财务指标 + 三大报表 | 可用 | 东财 RPT_F10_FINANCE_MAINFINADATA + lrb/zcfzb/xjllb |
| 主营构成（产品/地区） | 可用 | 东财 F10 BusinessAnalysis（zygcfx） |
| 公告 / 财报 PDF / 招股书 | 可用 | 巨潮 hisAnnouncement + static.cninfo.com.cn PDF + 东财公告 |
| 分红送配 | 可用 | 东财 RPT_SHAREBONUS_DET |
| 研报 | 可用 | 东财 reportapi |
| 行业板块 | 可用 | 东财 push2 板块列表 |
| 资金流向 | 可用 | 东财个股资金流 kline |
| 沪深港通持股 | 可用 | 东财 RPT_MUTUAL_HOLDSTOCKNORTH_STA |
| 市场热点 | 可用 | 东财概念板块（人气榜接口失效） |
| 商品价格 | 可用 | 新浪期货实时价 + 历史 K 线 |
| 业绩预告 | 可用 | 东财 RPT_PUBLIC_OP_NEWPREDICT |
| 一致预期 | 可用 | 东财 F10 ProfitForecast（评级统计/机构预测/盈利预测） |
| 财报披露日历 | 可用 | 东财 RPT_PUBLIC_BS_APPOIN |
| 十大流通股东 / 十大股东 | 可用 | 东财 F10 ShareholderResearch（sdltgd/sdgd）或 RPT_F10_EH_FREEHOLDERS |
| 股东户数 | 可用 | 东财 F10 ShareholderResearch（gdrs） |
| 解禁 | 可用 | 东财 RPT_LIFT_STOCK / F10 xsjj（无解禁时为空） |
| 宏观 CPI / PMI | 可用 | 东财 RPT_ECONOMY_CPI / RPT_ECONOMY_PMI |
| 股吧情绪 | 需绕 | JSON 接口失效，解析股吧 HTML |
| 基金持仓 | 需绕 | 天天基金 HTML，或 F10 ShareholderResearch 的 jjcg |
| 诉讼 | 需绕 | 东财公告列表 + 本地标题关键词过滤（巨潮全文搜索不限定单股） |
| 诚信（处罚/问询/违规） | 需绕 | 东财公告列表 + 关键词/规则挖掘 |
| 官网动态 | 需绕 | 站点模板 + 选择器，无统一接口 |
| 股权质押 | 待定 | 东财 report 名未找到；候选：公告挖掘 / 中登质押接口 |
| 公司行动（增发/回购/可转债/股权激励） | 待定 | 候选：公告挖掘 / 东财 F10 股本变动 |
| 无风险利率（国债收益率） | 待定 | 候选：中债 / 新浪 / 东财宏观（report 名待确认） |
| 通达信 / iFinD / Wind MCP | 不可用 | 本环境未配置 |

## 14. SQLite 表清单

完整字段定义见 [SCHEMA.md](SCHEMA.md)，下表只列用途：

| 表 | 用途 |
|---|---|
| `stocks` | 股票主表 |
| `company_profile` | 公司概况（含官网地址） |
| `kline` | 日 K 蜡烛图数据（OHLCV + 复权） |
| `snapshots` | 实时快照（现价、市值、换手率） |
| `valuations` | 历史估值（PE/PB/PS/PEG/市现率） |
| `financials` | 财务指标 |
| `statement_items` | 三大报表明细（利润表/资产负债表/现金流量表） |
| `revenue_composition` | 主营构成（产品/地区，国内国际占比） |
| `announcements` | 公告元数据 |
| `documents` | 文档存档（PDF 本地路径、状态） |
| `litigations` | 诉讼/仲裁 |
| `circulating_holders` | 十大流通股东（牛散/机构持仓变化） |
| `fund_holdings` | 基金持仓 |
| `sentiment` | 股吧情绪 |
| `research_reports` | 研报 |
| `industry` | 行业/板块 |
| `dividends` | 分红送配 |
| `capital_flows` | 资金流向 |
| `hotspots` | 市场热点 |
| `stock_products` | 股票与商品/产品价格的映射 |
| `product_prices` | 商品/产品价格序列 |
| `official_updates` | 官网动态 |
| `earnings_forecasts` | 业绩预告/快报 |
| `consensus_estimates` | 机构一致预期 |
| `report_calendar` | 财报披露日历 |
| `unlocks` | 限售解禁 |
| `pledges` | 股权质押 |
| `corporate_actions` | 公司行动（增发/回购等） |
| `insider_trades` | 高管增减持 |
| `shareholder_count` | 股东户数 |
| `macro_indicators` | 宏观指标 |
| `northbound` | 沪深港通持股 |
| `integrity_records` | 诚信记录（道德行为文字描述，正负面） |
| `sync_state` | 增量拉取水位线 |

## 15. 标准页面清单

| 页面 | 文件 | 交互 |
|---|---|---|
| 单股报告 | `report.html` | 概览、财务趋势、估值分位、五维雷达、持仓、公告、蜡烛图、大师观点、风险、结论 |
| 多股对比 | `compare.html` | 并排指标、雷达叠加、归一化走势、持仓对比 |
| 筛选 | `screener.html` | 组合条件、排序、导出 |
| 计算器 | `calculator.html` | 分红复利、估值参数调节 |
| 历年财务 | `financials.html` | 三大报表历年对比、净利润、经营/投资/筹资现金流、股权结构、主营构成 |
| 行业地位 | `industry.html` | 行业内公司地位气泡图、护城河分布 |
| 产品价格 | `products.html` | 商品/产品价格与股价归一化对比、相关性 |
| 股东名册 | `holders.html` | 个股十大流通股东及报告期变化 |
| 持仓追踪 | `holders-track.html` | 按牛散/机构名称追踪跨股持仓及增减方向 |
| 事件日历 | `calendar.html` | 聚合财报、解禁、分红、股东大会事件（股东大会来自公告） |
| 财报体检 | `review.html` | 大白话财务汇总、疑点与关键点排查 |
| 诚信档案 | `integrity.html` | 道德行为正负面记录（兑现/失信、违规、处罚、问询） |
| 蜡烛图 | `charts.html` | 可选，未实现（K 线已在 report 页呈现） |

## 16. 增量与离线策略

- `services/sync.py` 统一"先看本地、只补缺口"：查 `sync_state` → 只拉新数据 → 入库/存档 → 更新水位。
- 文档类：`documents.local_path` 存在且文件真实存在则不下载。
- 行情类：K 线增量追加，实时快照按 TTL 刷新。
- 离线模式：本地已有则直接从 SQLite + archive 读，不联网。

### 上下文预算与配置指纹

- 叙述层的资料注入预算全部配置化，原则是**质量优先、默认可调大**：`[research]`（研报篇数/单篇字数）、`[intel]`（行业研报/调研的条数与字数）、`[masters]`（大师叙述每类摘录条数与字数）、`[valuation]`（估值校准摘录与引用清单上限）、`[forensics]`（财报体检结论窗口期数、事件证据年限、送入 LLM 的信号/波动上限）、`[narrative]`（各叙述模块输出 token 上限，财报体检另按风险点数动态放大，避免 JSON 被截断）。
- 单次请求的输入软预算由 `llm.max_input_tokens`（默认 32000）控制：超出仅在日志告警、**不截断**（截断 JSON 会破坏结构、诱发幻觉），超 2 倍才做中段截断兜底。估算见 `services/llm.estimate_tokens()`（CJK 约 1 字 1 token，其余约 4 字符 1 token）。
- 缓存指纹：研报/情报缓存 payload 记录写入时的 `max_chars`，读取时若小于当前配置，视为不新鲜并重抓（`utils/research_text.cache_max_chars()`），避免"调大了预算、页面却仍是旧短摘要"。

## 17. 批量与行业分析

> 状态：本节描述的是目标设计。`services/batch.py` / `scripts/batch_analyze.py` 尚未实现；
> 当前批量能力由 `/api/compare`、`/api/screener` 提供（指定股票列表），按行业整批分析的入口待补。

- 两种模式：指定一批股票（`--symbols`）或整个行业（`--industry`），由 `services/batch.py` 解析为股票列表后循环拉取。
- 数据复用现有 provider + SQLite，不新增网络源：历年财报、市值、分红、估值、行业营收汇总。
- `analysis/comparison.py` 计算横向对比指标：营收增速、市占率及变化、毛利率/净利率、ROE、分红比例、股息率、市值变化、估值、护城河分、安全边际、周期位置。
- 报告五个维度，定性定量结合：

| 维度 | 数据/模块 | 输出 |
|---|---|---|
| 生意模式 | 公司概况 + 主营构成 + 毛利率/现金流 | 商业模式描述 + 定价权判断 |
| 护城河 | `analysis/moat.py` | 护城河评级 |
| 安全边际 | `analysis/valuation/` | 安全边际、合理价格区间 |
| 行业发展空间 | 行业营收汇总 + 增速 | 行业天花板与成长性判断 |
| 市场周期 | `analysis/cycle.py` + 产品价格 | 周期位置判断 |

- agent 职责：触发 `scripts/batch_analyze.py`，读取结构化对比结果，按 `references/batch-analysis.md` 补写定性结论，输出到 `generated/`。
- 结果不落库，实时计算；股票列表解析和行业归组来自 `stocks.industry`。

## 18. 财报体检与诚信档案

- `analysis/financial_review.py` 面向不会读财报的用户，把三大报表和核心指标翻译成大白话汇总，并输出疑点与关键点清单（原轻量版，report 页仍保留为折叠的「原始量化疑点清单」）。
- **深度体检**为双层：`analysis/forensics.py`（纯确定性）负责「算出风险点」，`services/narrative/_forensics.py` 负责「让 LLM 逐条解释」。前者从多期财报、利润表科目、K 线、诚信/公告、业绩预告、内部人交易与质押、情报资料归纳信号与异常波动，输出精确数值/期次/阈值/证据引用 + `coverage`（含因数据缺失跳过的检查）；后者用 `prompts/financial_forensics.md` 生成逐条剖析、综合判断、交叉印证与免责，并对 LLM 输出做枚举降级与缺失补齐。铁律：**数字由代码算、判断由 LLM 做、不下造假定性结论、第三方观点须标注来源**；"未检出"不等于"没问题"。
- 口径防护：Q1/H1/Q3 累计值按同报告期同比对齐；率类异常以自身历史 MAD 离群为主，避免银行/港股行业属性误判；全历史仅作波动基线，结论窗口默认近 12 期；东财预告金额字段口径混杂，按公告正文过滤并留 5% 容差；连续多期同类信号聚合为趋势型风险点（保留逐期 `occurrences`）。
- 汇总整合 `redflags.py`、`fraud_score.py`、`delisting.py` 的结论，做多期交叉检查：营收 vs 现金流、利润 vs 应收账款、毛利率异常、审计意见、财务重述。
- `analysis/integrity.py` 从公告中挖掘言行不一与坑害投资者行为：承诺未兑现、违规担保、资金占用、处罚、问询函、财务重述、误导性披露。
- `integrity` 能力拉取监管处罚、问询、承诺履行等数据，存 `integrity_records` 表，用文字描述正面与负面的道德行为；结果在 `integrity.html` 展示，并汇总进 `review.html`。

## 19. 文档索引

- [ARCHITECTURE.md](ARCHITECTURE.md)：目录结构、分层与扩展规则。
- [SCHEMA.md](SCHEMA.md)：SQLite 表结构与字段定义。
- [CONTRACTS.md](CONTRACTS.md)：数据模型、Port 接口与 Web API 契约。
- [CONVENTIONS.md](CONVENTIONS.md)：命名与开发规范。
- [ROADMAP.md](ROADMAP.md)：里程碑路线。
- [PROGRESS.md](PROGRESS.md)：工作单元与进度表。
- [EXTENDING.md](EXTENDING.md)：扩展操作手册。
- [ADR/ADR-001-llm-provider-abstraction.md](ADR/ADR-001-llm-provider-abstraction.md)：大模型接入的架构决策记录。
