# value-compass 工作单元与进度

每个工作单元是一张可独立派发的小需求单。输入/输出契约统一定义在 `CONTRACTS.md`：
Port 方法定义输入参数，`models.py` 定义返回类型，`SCHEMA.md` 定义存储结构。

状态取值：`待办` / `进行中` / `已完成` / `受阻`。
协作约定：完成一个单元 = 实现 + 单元测试 + 更新本表状态 + git 提交。
`并行 = 是` 表示该单元可与其他"是"的单元同时交给不同人/agent 完成。

## 依赖分层

```text
L1 地基（串行）：目录/包 → models → 配置/工具 → storage → _shared → registry
L2 providers（17 个，可并行）：依赖 L1，输出 models
L3 analysis（11 个，可并行）：依赖 models，输出结果模型
L4 web API + pages（可并行）：依赖 L2/L3
L5 skill 化 + 验收（串行收尾）
```

## L1 地基（串行）

| 编号 | 模块 | 输入 | 输出 | 依赖 | 状态 |
|---|---|---|---|---|---|
| W0.1 | 目录骨架 + pyproject.toml | 无 | 可安装的包结构 | - | 已完成 |
| W0.2 | models.py | CONTRACTS 第 2 节 | 全部数据类（34 类） | W0.1 | 已完成 |
| W0.3 | 配置/路径/日志/代码归一化 | 无 | config 加载、paths、logging、symbols | W0.1 | 已完成 |
| W0.4 | storage 层 | SCHEMA.md | schema.sql、db.py 迁移、repository 基础读写 | W0.2 | 已完成 |
| W0.5 | _shared 传输 | 无 | HTTP、重试、限流、缓存 | W0.3 | 已完成 |
| W0.6 | registry | providers.toml | 能力 → 源选择 | W0.5 | 已完成 |

## L2 providers（17 个，可并行）

统一输入：`symbol`（如 `600519.SH`）+ 时间区间等；输出为对应 Port 定义的 models。

| 编号 | 能力 | Port | 输出 models | 状态 |
|---|---|---|---|---|
| P1 | quote | QuotePort | Snapshot / KlineBar / ValuationPoint | 已完成 |
| P2 | financial | FinancialPort | FinancialReport / StatementItem / RevenueComposition | 已完成 |
| P3 | announcement | AnnouncementPort | Announcement(Page) / Document | 已完成 |
| P4 | sentiment | SentimentPort | Sentiment | 已完成 |
| P5 | holding | HoldingPort | CirculatingHolder / FundHolding | 已完成 |
| P6 | research | ResearchPort | ResearchReport | 已完成 |
| P7 | industry | IndustryPort | IndustryBoard | 已完成 |
| P8 | litigation | LitigationPort | Litigation | 已完成 |
| P9 | dividend | DividendPort | DividendRecord | 已完成 |
| P10 | capitalflow | CapitalFlowPort | CapitalFlow / NorthboundHolding | 已完成 |
| P11 | hotspot | HotspotPort | Hotspot | 已完成 |
| P12 | official | OfficialPort | OfficialUpdate | 已完成 |
| P13 | commodity | CommodityPort | ProductPrice / StockProduct | 已完成 |
| P14 | forecast | ForecastPort | EarningsForecast / ConsensusEstimate / ReportCalendarItem | 已完成 |
| P15 | governance | GovernancePort | CorporateAction / InsiderTrade / ShareholderCount / Pledge / Unlock | 已完成 |
| P16 | macro | MacroPort | MacroIndicator | 已完成 |
| P17 | integrity | IntegrityPort | IntegrityRecord | 已完成 |

## L3 analysis（11 个，可并行）

统一输入：models 数据对象；输出为结果模型。

| 编号 | 模块 | 输出 | 状态 |
|---|---|---|---|
| A1 | financial 评分 | DimensionScore | 已完成 |
| A2 | valuation（DCF/DDM/相对/格雷厄姆） | ValuationResult | 已完成 |
| A3 | return（分红复利/回报） | ReturnResult | 已完成 |
| A4 | moat（护城河） | MoatAssessment | 已完成 |
| A5 | cycle（市场周期） | CycleAssessment | 已完成 |
| A6 | comparison + industry（横向对比/行业地位） | CompanyComparison / PeerPosition | 已完成 |
| A7 | holder_tracking（持仓追踪） | HolderPosition | 已完成 |
| A8 | price_correlation（价格相关性） | PriceCorrelation | 已完成 |
| A9 | redflags + fraud_score + delisting（风险红旗/造假/退市） | RedFlag / FraudScore / DelistingRisk | 已完成 |
| A10 | masters（大师模型） | MasterVerdict | 已完成 |
| A11 | financial_review + integrity（财报体检/诚信挖掘） | FinancialReviewReport / IntegrityRecord | 已完成 |

## L4 web API 与 pages（可并行）

| 编号 | 模块 | 输入 | 输出 | 依赖 | 状态 |
|---|---|---|---|---|---|
| W4.1 | web 骨架 + 路由 | FastAPI | app.py、routes/、静态托管 | L1 | 已完成 |
| W4.2 | API 接口组 | L2/L3 | /api/* 全部端点（见 CONTRACTS 第 5 节） | L2、L3 | 进行中 |
| W4.3 | 页面组 A（report/financials/industry） | API | 三张页面 | W4.2 | 已完成 |
| W4.4 | 页面组 B（compare/screener/calculator） | API | 三张页面 | W4.2 | 已完成 |
| W4.5 | 页面组 C（products/holders/calendar） | API | 三张页面 + holders-track（反向持仓追踪） | W4.2 | 已完成 |
| W4.6 | 页面组 D（review/integrity/charts） | API | review、integrity 两页（charts 为契约可选页，未实现） | W4.2 | 已完成 |

## L5 skill 化与验收（串行收尾）

| 编号 | 模块 | 输出 | 依赖 | 状态 |
|---|---|---|---|---|
| W5.1 | SKILL.md + references 方法论 | 技能壳 | L4 | 已完成 |
| W5.2 | 技能壳链接进 ~/.codex/skills | 可被 Codex 发现 | W5.1 | 已完成 |
| W5.3 | 测试补全（单元 + 契约 + web API，73 例） | 茅台/五粮液/腾讯三股已验收；白酒行业批量待 L4 多股同步 | L4 | 已完成 |

## 备注

- 数据源验证 spike 已完成（2026-11-11），完整清单见 ARCHITECTURE.md 第 13 节。
- 三项待定，实现对应 provider 时再定：股权质押、公司行动（增发/回购/可转债/股权激励）、无风险利率。
- `analysis/return.py` 因与 Python 关键字冲突已改名 `returns.py`。
- DCF 的 FCF 由 `services/analyze._compute_fcf` 计算：当现金流量表含 `CONSTRUCT_LONG_ASSET`（购建固定资产等）时用 `OCF - capex`；该字段在多期请求中可能缺失，此时回退为 OCF 代理。若要精确 FCF 仍待补单期请求。
- 本环境 git 推送需先 `Remove-Item Env:GIT_SSH_COMMAND`，再设 `GIT_SSH=C:\Windows\System32\OpenSSH\ssh.exe`。

### 进度核对（2026-11-20）

对照代码与 CONTRACTS.md 逐项复核，修正 L4/L5 标记：

- L4 六个单元中 W4.1、W4.3–W4.6 均已落地（13 个页面路由 + 静态托管 + 13 张页面及配套 JS/CSS），原"待办"标记过时，改为"已完成"。
- W4.2 保持"进行中"：CONTRACTS 第 5 节定义 49 个 API 端点，当前实现 24 个（其中 21 个在契约内、3 个为契约外扩展：`/stock/{symbol}/analysis`、`/stock/{symbol}/calendar`、`/stocks`）。缺口集中在单数据源明细端点（sentiment/research/litigations/dividends/capital-flows 等）与部分分析端点（score/valuation/masters 等），其分析能力多数已由聚合端点 `/stock/{symbol}/analysis` 覆盖，但独立端点尚未暴露。
- W5.3 测试数由 17 例增至 73 例：`test_analysis` 15 + `test_llm` 10 + `test_narrative` 18 + `test_registry` 4 + `test_storage` 3 + `test_web_api` 11 + `test_exceptions` 6 + `test_financial_provider` 6（港股财务/估值映射）。`python -m unittest discover -s tests` 全部通过。测试数只在本表维护，ROADMAP 引用此处，避免多处漂移。
- 前端公共模块已抽取：`pages/static/js/api.js`（统一 fetch 封装 `VC.getJSON`）、`common.js`（代码符号归一化 `VC.normalize`），13 个页面脚本删除本地重复实现；`el` 因各页签名/默认值不同保留本地。CONVENTIONS 第 4 节已补充"数据形态边界"表，明确 dict 允许的边界（聚合出口/存储行）。
- charts 页在 CONTRACTS 第 6 节标注为"可选"，实际未实现，不影响 W4.6 完成判定。
- P2 内部治理（同日完成）：`services/narrative.py`（794 行）拆为 `narrative/` 子包（门面仅导出三个 generate 入口，内部按职责分为共享工具/数据加载/三类叙述；后续大师叙述扩为四个入口）；provider 异常统一到 `ProviderError` 层次（Config/Request/Data/NotImplemented 四类，LlmConfigError 归入 Config 分支，不继承 builtin 异常类型）；SQLite 迁移加显式事务，失败时回滚并关闭连接，避免半迁移库。
- P2 收尾（同日）：收窄 narrative 门面（不再 re-export 私有函数）、测试改为直接 import 子模块；移除异常的 KeyError/ValueError/NotImplementedError 兼容继承，避免宽泛 except 误吞；HttpClient 的 JSON 解析失败包装为 ProviderDataError。
- 大师观点叙述（同日完成）：新增 `prompts/masters/_base.md` + `buffett.md` + `duan_yongping.md`（可人工修改）；`narrative/_data.py` 按年聚合年报与同年季报/中报摘要，`narrative/_masters.py` 逐位大师调用 LLM 并保留模板回退/TTL 缓存；新增 `/api/stock/{symbol}/masters-narrative?refresh=` 与 report 页大师卡片文字描述。narrative 门面现导出四个 generate 入口。
- 大师档案扩充与输入增强（同日完成）：`buffett.md` 按《巴菲特致股东的信》主题提炼为八节框架（所有者视角、护城河、股东盈余、资本配置、会计质量、杠杆与回购纪律、治理、反面清单），`duan_yongping.md` 按其公开访谈与经营实践扩充为六节；`narrative/_data.py` 新增 `_load_master_context()`，提供行业板块行情、同行业最近年报中位数对比、主营构成、最近一期财报摘要与市场估值，`_master_facts()` 一并注入 user JSON，模板回退同步引用；新增 `_MASTERS_PROMPT_VERSION` 使大师缓存独立失效（不影响概览/评分/估值缓存）。无年报年份摘要改为“年报尚未披露，已披露……”，避免误报数据缺失。
- 港股支持（同日完成）：`financial/eastmoney.py` 的 `fetch_financials` 按市场分支，港股走东财 `RPT_HKF10_FN_MAININDICATOR`，按 `DATE_TYPE_CODE` 映射 Q1/H1/Q3/FY，字段对齐 FinancialReport（归母净利用 HOLDER_PROFIT、ROE 用 ROE_AVG、负债率用 DEBT_ASSET_RATIO 等）；`quote/eastmoney.py` 的 `fetch_valuations` 港股分支从同一数据集取报告期 PE_TTM/PB_TTM 作估值快照（港股无独立估值时序接口）。A 股路径完全不变。腾讯控股 00700.HK 已入库（financials 97 期、kline 5529、valuations 97），大师观点与 report 页实测通过。港股主营构成/同业对比/诚信/公告等接口仍缺失，解读层已标注[数据缺失]降级。
- 大师解读字数约束收紧：`prompts/masters/_base.md` 改为“严格 350~550 字、同类缺失只标注一次”，`_MASTERS_PROMPT_VERSION` 升到 3 使旧解读失效。实测腾讯解读由 770/1008 字降到 594/581 字。
