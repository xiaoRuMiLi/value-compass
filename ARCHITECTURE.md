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
- 可视化页面：标准交互页放 `pages/`，一次性生成页放 `generated/`。

## 3. 目录结构

```text
value-compass/
├── SKILL.md                          # skill 入口：给 agent 的总指令与路由
├── agents/
│   └── openai.yaml                   # skill 的 UI 元数据
├── references/                       # 方法论，agent 按需加载，不常驻上下文
│   ├── checklist.md                  # 财务质量检查清单
│   ├── valuation.md                  # 估值方法说明
│   ├── red-flags.md                  # 风险红旗
│   ├── report-format.md              # 报告规范
│   └── batch-analysis.md             # 批量/行业分析工作流
│
├── scripts/                          # 稳定入口命令（面向人和 agent）
│   ├── fetch_data.py                 # 只拉数并入库
│   ├── analyze.py                    # 拉数 + 计算 + 出报告
│   ├── batch_analyze.py              # 批量/行业横向对比 + 出报告
│   └── serve.py                      # 启动本地 web 界面
│
├── fundamental/                      # 核心 Python 包
│   ├── __init__.py
│   ├── models.py                     # 归一化数据模型，所有 provider 的唯一出口
│   ├── providers/                    # 数据提供者，按能力分域（Ports & Adapters）
│   │   ├── registry.py               # 能力 → 默认源/备用源/优先级
│   │   ├── _shared/                  # 公共传输：HTTP、重试、限流、缓存
│   │   ├── quote/                    # 实时行情 + 复权 K 线
│   │   │   ├── port.py
│   │   │   ├── eastmoney.py
│   │   │   └── _test/
│   │   ├── financial/                # 财务指标、三大报表、主营构成
│   │   ├── announcement/             # 公告 + 财报 PDF + 招股书
│   │   ├── sentiment/                # 公众情绪（股吧）
│   │   ├── holding/                  # 股东 / 机构 / 基金持仓
│   │   ├── research/                 # 研报
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
│   │   └── integrity/                # 诚信记录
│   ├── services/                     # 编排层：取数 → 归一化 → 入库 → 计算
│   │   ├── sync.py                   # 增量拉取与水位管理
│   │   ├── archive.py                # 文档下载与本地存档
│   │   ├── analyze.py                # 分析编排
│   │   └── batch.py                  # 批量/行业分析编排
│   ├── analysis/                     # 纯计算，不 import 任何具体数据源
│   │   ├── financial.py              # 财务质量评分
│   │   ├── comparison.py             # 横向对比（市占率、指标趋势、排名）
│   │   ├── cycle.py                  # 市场周期判断
│   │   ├── return.py                 # 分红复利 / 总回报
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
│   │   ├── financial_review.py       # 财报体检/大白话汇总
│   │   ├── integrity.py              # 诚信记录挖掘
│   │   ├── masters/                  # 大师模型（可插拔）
│   │   │   ├── base.py               # MasterModel 接口
│   │   │   ├── buffett.py            # 巴菲特
│   │   │   └── duan_yongping.py      # 段永平
│   │   ├── redflags.py               # 风险红旗
│   │   └── score.py                  # 综合评分
│   ├── storage/                      # SQLite 与文件存档层
│   │   ├── schema.sql                # 表结构
│   │   ├── db.py                     # 连接与迁移
│   │   ├── repository.py             # 数据访问
│   │   └── archive.py                # 文件存档访问
│   ├── web/                          # 在线界面（FastAPI）
│   │   ├── app.py
│   │   └── routes/
│   │       ├── api.py                # /api/* 数据接口
│   │       └── pages.py              # 页面路由
│   └── utils/                        # 横切工具
│       ├── symbols.py                # 代码/市场归一化
│       ├── validation.py             # 数据质量校验、跨源交叉验证
│       ├── logging.py
│       └── paths.py
│
├── pages/                            # 标准交互页面，可共用、参数化
│   ├── report.html                   # 单股报告
│   ├── compare.html                  # 多股横向对比
│   ├── screener.html                 # 筛选
│   ├── calculator.html               # 分红复利 + 估值计算器
│   ├── financials.html               # 历年财务全景
│   ├── industry.html                 # 行业地位分布
│   ├── products.html                 # 产品价格 vs 股价对比
│   ├── holders.html                  # 牛散/机构持仓追踪
│   ├── calendar.html                 # 事件日历
│   ├── review.html                   # 财报体检报告
│   ├── integrity.html                # 诚信档案
│   ├── charts.html                   # 蜡烛图大图（可选，后期加）
│   ├── shared/                       # 共用片段：布局、组件
│   └── static/
│       ├── js/
│       │   ├── vendor/echarts.min.js # 本地内置，离线可用
│       │   ├── api.js                # 统一 API 客户端
│       │   ├── common.js
│       │   ├── report.js
│       │   ├── compare.js
│       │   ├── screener.js
│       │   ├── calculator.js
│       │   ├── financials.js
│       │   ├── industry.js
│       │   ├── products.js
│       │   ├── holders.js
│       │   ├── calendar.js
│       │   ├── review.js
│       │   └── integrity.js
│       └── css/
│           ├── base.css
│           └── report.css
│
├── generated/                        # 非标准、按临时要求生成的一次性展示 HTML
│
├── data/
│   ├── cache/                        # 下载的原始响应缓存（TTL，可审计）
│   ├── db/
│   │   └── fundamental.db            # SQLite 主库
│   └── archive/                      # 永久本地存档，不清除
│       ├── documents/                # 年报/公告/招股书 PDF
│       │   └── {code}/{year}/{doc_id}.pdf
│       └── kline/                    # 蜡烛图离线快照（可选，默认走 SQLite）
│
├── config/                           # 配置文件夹，按序合并
│   ├── default.toml                  # 默认配置
│   ├── dev.toml                      # 本地开发覆盖
│   ├── providers.toml                # 能力 → 源/备用/TTL/限流
│   ├── products.toml                 # 股票↔商品映射（种子数据）
│   ├── official.toml                 # 官网站点模板与选择器
│   ├── famous_traders.toml           # 牛散名单（识别用）
│   └── masters/                      # 大师模型配置
│       ├── buffett.toml              # 巴菲特行为逻辑、阈值、权重
│       └── duan_yongping.toml        # 段永平行为逻辑、阈值、权重
│
├── scratch/                          # 临时脚本（gitignore）
├── tests/                            # 契约测试 + 单元测试 + fixtures
├── .env                              # 密钥，gitignore
├── .env.example                      # 密钥模板，提交
├── pyproject.toml
├── .gitignore
└── README.md
```

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
| 研报 | `research` | 东财 | 研报、评级 |
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

- Python 模块/包：小写 `snake_case`，如 `eastmoney.py`、`red_flags.py`。
- 数据 JSON：`{code}_{category}.json`，如 `600519_financial.json`。
- 输出报告：`{code}_{report}_{YYYYMMDD}.html`。
- 文档存档：`{code}/{year}/{doc_id}.pdf`。
- 测试：`test_*.py`，与源码目录一一对应。
- 常量集中 `config/`，枚举/类型集中 `models.py`。

## 12. 扩展规则

- 加新数据源：在对应能力目录加一个文件，实现该能力 Port，在 `registry.py` 注册，在 `config/providers.toml` 写优先级；上层零改动。
- 加新能力：新建 `providers/<capability>/`，写 `port.py` + 至少一个实现 + config 一段。
- 加新估值方法：在 `analysis/valuation/` 加一个文件，实现 `ValuationMethod`。
- 加新大师：在 `analysis/masters/` 加一个文件 + `config/masters/` 加一个 toml。
- 加新页面：在 `pages/` 加 HTML + `static/js/` 加对应脚本 + `web/routes/pages.py` 加路由。

## 13. 数据源可用性（已实测）

| 数据源 | 可用性 | 说明 |
|---|---|---|
| 东方财富公开接口 | 可用 | 行情、K 线、F10 财务指标、公告、板块、资金流、分红送配、机构持仓 |
| 巨潮资讯 | 可用 | 公告、财报 PDF、招股书（需正确 orgId，如茅台 `gssh0600519`） |
| 东财股吧 | 可用但需绕 | JSON 接口失效，需解析 HTML |
| 天天基金 | 可用但需绕 | 基金持仓 JSON 接口失效，需解析 HTML |
| 公司官网 | 可用但需绕 | 无统一接口，需按站点模板 + 选择器抓取 |
| 新浪期货 | 可用 | 商品主力实时价与全历史日 K 线，免费无 key |
| 通达信 MCP / iFinD MCP / Wind | 不可用 | 本环境未配置 |
| 东财人气榜接口 | 不可用 | 返回服务异常，市场热点改用概念板块实现 |

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
| 持仓追踪 | `holders.html` | 按牛散/机构名称追踪跨股持仓及增减方向 |
| 事件日历 | `calendar.html` | 聚合财报、解禁、分红、股东大会事件（股东大会来自公告） |
| 财报体检 | `review.html` | 大白话财务汇总、疑点与关键点排查 |
| 诚信档案 | `integrity.html` | 道德行为正负面记录（兑现/失信、违规、处罚、问询） |
| 蜡烛图 | `charts.html` | 可选，后期加 |

## 16. 增量与离线策略

- `services/sync.py` 统一"先看本地、只补缺口"：查 `sync_state` → 只拉新数据 → 入库/存档 → 更新水位。
- 文档类：`documents.local_path` 存在且文件真实存在则不下载。
- 行情类：K 线增量追加，实时快照按 TTL 刷新。
- 离线模式：本地已有则直接从 SQLite + archive 读，不联网。

## 17. 批量与行业分析

- 两种模式：指定一批股票（`--symbols`）或整个行业（`--industry`），由 `services/batch.py` 解析为股票列表后循环拉取。
- 数据复用现有 provider + SQLite，不新增网络源：历年财报、市值、分红、估值、行业营收汇总。
- `analysis/comparison.py` 计算横向对比指标：营收增速、市占率及变化、毛利率/净利率、ROE、分红比例、股息率、市值变化、估值、护城河分、安全边际、周期位置。
- 报告五个维度，定性定量结合：

| 维度 | 数据/模块 | 输出 |
|---|---|---|
| 生意模式 | 公司概况 + 主营构成 + 毛利率/现金流 | 商业模式描述 + 定价权判断 |
| 护城河 | `analysis/moat.py` | 护城河评级 |
| 安全边际 | `analysis/valuation.py` | 安全边际、合理价格区间 |
| 行业发展空间 | 行业营收汇总 + 增速 | 行业天花板与成长性判断 |
| 市场周期 | `analysis/cycle.py` + 产品价格 | 周期位置判断 |

- agent 职责：触发 `scripts/batch_analyze.py`，读取结构化对比结果，按 `references/batch-analysis.md` 补写定性结论，输出到 `generated/`。
- 结果不落库，实时计算；股票列表解析和行业归组来自 `stocks.industry`。

## 18. 财报体检与诚信档案

- `analysis/financial_review.py` 面向不会读财报的用户，把三大报表和核心指标翻译成大白话汇总，并输出疑点与关键点清单。
- 汇总整合 `redflags.py`、`fraud_score.py`、`delisting.py` 的结论，做多期交叉检查：营收 vs 现金流、利润 vs 应收账款、毛利率异常、审计意见、财务重述。
- `analysis/integrity.py` 从公告中挖掘言行不一与坑害投资者行为：承诺未兑现、违规担保、资金占用、处罚、问询函、财务重述、误导性披露。
- `integrity` 能力拉取监管处罚、问询、承诺履行等数据，存 `integrity_records` 表，用文字描述正面与负面的道德行为；结果在 `integrity.html` 展示，并汇总进 `review.html`。

## 19. 文档索引

- [ARCHITECTURE.md](ARCHITECTURE.md)：目录结构、分层与扩展规则。
- [SCHEMA.md](SCHEMA.md)：SQLite 表结构与字段定义。
- [CONTRACTS.md](CONTRACTS.md)：数据模型、Port 接口与 Web API 契约。
