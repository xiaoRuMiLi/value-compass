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
| W5.3 | 测试补全（单元 + 契约 + web API，134 例） | 茅台/五粮液/腾讯/小米/招行多股已验收；白酒行业批量待 L4 多股同步 | L4 | 已完成 |

## 备注

- 数据源验证 spike 已完成（2026-11-11），完整清单见 ARCHITECTURE.md 第 13 节。
- 三项待定，实现对应 provider 时再定：股权质押、公司行动（增发/回购/可转债/股权激励）、无风险利率。
- `analysis/return.py` 因与 Python 关键字冲突已改名 `returns.py`。
- DCF 的 FCF 由 `services/analyze._compute_fcf` 计算：当现金流量表含 `CONSTRUCT_LONG_ASSET`（购建固定资产等）时用 `OCF - capex`；该字段在多期请求中可能缺失，此时回退为 OCF 代理。若要精确 FCF 仍待补单期请求。
- 本环境 git 推送需先 `Remove-Item Env:GIT_SSH_COMMAND`，再设 `GIT_SSH=C:\Windows\System32\OpenSSH\ssh.exe`。

### 进度核对（2026-11-20）

对照代码与 CONTRACTS.md 逐项复核，修正 L4/L5 标记：

- L4 六个单元中 W4.1、W4.3–W4.6 均已落地（13 个页面路由 + 静态托管 + 13 张页面及配套 JS/CSS），原"待办"标记过时，改为"已完成"。
- W4.2 保持"进行中"：CONTRACTS 第 5 节定义 49 个 API 端点，当前实现 26 个（其中 21 个在契约内、5 个为契约外扩展：`/stock/{symbol}/analysis`、`/stock/{symbol}/calendar`、`/stocks`、`/stock/{symbol}/valuation-params`、`/stock/{symbol}/financial-forensics`）。缺口集中在单数据源明细端点（sentiment/research/litigations/dividends/capital-flows 等）与部分分析端点（score/valuation/masters 等），其分析能力多数已由聚合端点 `/stock/{symbol}/analysis` 覆盖，但独立端点尚未暴露。
- W5.3 测试数由 17 例增至 144 例：`test_analysis` 15 + `test_llm` 15（含输入预算/截断兜底）+ `test_narrative` 20 + `test_registry` 4 + `test_storage` 3 + `test_web_api` 13 + `test_exceptions` 6 + `test_financial_provider` 6（港股财务/估值映射）+ `test_valuation_params` 24（参数校准/研报缓存/合理价区间/分歧度/引用清单/缓存指纹）+ `test_intel` 4（情报门面/分级缓存/字段映射）+ `test_forensics` 24（同比口径/误报防护/趋势聚合/LLM 输出降级/缓存/路由）+ `test_forensics_config` 10（配置加载/非法值兜底/指纹/阈值驱动扫描/同源聚类/缓存按阈值失效）。`python -m unittest discover -s tests` 全部通过。测试数只在本表维护，ROADMAP 引用此处，避免多处漂移。
- 前端公共模块已抽取：`pages/static/js/api.js`（统一 fetch 封装 `VC.getJSON`）、`common.js`（代码符号归一化 `VC.normalize`），13 个页面脚本删除本地重复实现；`el` 因各页签名/默认值不同保留本地。CONVENTIONS 第 4 节已补充"数据形态边界"表，明确 dict 允许的边界（聚合出口/存储行）。
- charts 页在 CONTRACTS 第 6 节标注为"可选"，实际未实现，不影响 W4.6 完成判定。
- P2 内部治理（同日完成）：`services/narrative.py`（794 行）拆为 `narrative/` 子包（门面仅导出三个 generate 入口，内部按职责分为共享工具/数据加载/三类叙述；后续大师叙述扩为四个入口）；provider 异常统一到 `ProviderError` 层次（Config/Request/Data/NotImplemented 四类，LlmConfigError 归入 Config 分支，不继承 builtin 异常类型）；SQLite 迁移加显式事务，失败时回滚并关闭连接，避免半迁移库。
- P2 收尾（同日）：收窄 narrative 门面（不再 re-export 私有函数）、测试改为直接 import 子模块；移除异常的 KeyError/ValueError/NotImplementedError 兼容继承，避免宽泛 except 误吞；HttpClient 的 JSON 解析失败包装为 ProviderDataError。
- 大师观点叙述（同日完成）：新增 `prompts/masters/_base.md` + `buffett.md` + `duan_yongping.md`（可人工修改）；`narrative/_data.py` 按年聚合年报与同年季报/中报摘要，`narrative/_masters.py` 逐位大师调用 LLM 并保留模板回退/TTL 缓存；新增 `/api/stock/{symbol}/masters-narrative?refresh=` 与 report 页大师卡片文字描述。narrative 门面现导出四个 generate 入口。
- 大师档案扩充与输入增强（同日完成）：`buffett.md` 按《巴菲特致股东的信》主题提炼为八节框架（所有者视角、护城河、股东盈余、资本配置、会计质量、杠杆与回购纪律、治理、反面清单），`duan_yongping.md` 按其公开访谈与经营实践扩充为六节；`narrative/_data.py` 新增 `_load_master_context()`，提供行业板块行情、同行业最近年报中位数对比、主营构成、最近一期财报摘要与市场估值，`_master_facts()` 一并注入 user JSON，模板回退同步引用；新增 `_MASTERS_PROMPT_VERSION` 使大师缓存独立失效（不影响概览/评分/估值缓存）。无年报年份摘要改为“年报尚未披露，已披露……”，避免误报数据缺失。
- 港股支持（同日完成）：`financial/eastmoney.py` 的 `fetch_financials` 按市场分支，港股走东财 `RPT_HKF10_FN_MAININDICATOR`，按 `DATE_TYPE_CODE` 映射 Q1/H1/Q3/FY，字段对齐 FinancialReport（归母净利用 HOLDER_PROFIT、ROE 用 ROE_AVG、负债率用 DEBT_ASSET_RATIO 等）；`quote/eastmoney.py` 的 `fetch_valuations` 港股分支从同一数据集取报告期 PE_TTM/PB_TTM 作估值快照（港股无独立估值时序接口）。A 股路径完全不变。腾讯控股 00700.HK 已入库（financials 97 期、kline 5529、valuations 97），大师观点与 report 页实测通过。港股主营构成/同业对比/诚信/公告等接口仍缺失，解读层已标注[数据缺失]降级。
- 大师解读字数约束收紧：`prompts/masters/_base.md` 改为“严格 350~550 字、同类缺失只标注一次”，`_MASTERS_PROMPT_VERSION` 升到 3 使旧解读失效。实测腾讯解读由 770/1008 字降到 594/581 字。
- 小米集团测试（同日）：01810.HK 入库（financials 40 期、valuations 40、kline 2062）。大师量化结论两位均「回避」（硬件低毛利/ROE 不达标），LLM 解读引用 2026 中报现金流/净利骤降至 0.14。招商银行测试：600036.SH 入库（financials 103、valuations 2155、主营构成 200、诚信 24、研报 100）。发现通用价值投资规则对银行股失真（无毛利率/ROIC/OCF、负债率天然约 90%），LLM 正确指出“高负债是行业属性、低负债硬约束不适用”并维持量化结论——金融业需单设阈值模型，属已知适用边界。均为纯数据入库，数据库已 gitignore，无需提交。
- 估值参数校准（同日完成）：破解 DDM/DCF 默认 g/r 写死导致的失真（analyze 里 g=5%/r=8%、DCF g=10%/r=9%，零基本面输入）。新增 `prompts/valuation_params.md`（可人工修改）让 LLM 结合财报/分红/行业/同业/市场估值 + 联网研报正文给保守参数；`utils/research_text.py` 抓东财研报详情页 `#ctx-content` 正文（带缓存、失败降级）；`narrative/_valuation_params.py` 对参数做边界钳制（g −0.05~0.12、r 0.07~0.14）与 r−g≥0.02 校验后复用 `evaluate_ddm/evaluate_dcf` 重算，并给出 10 年股息复投预期；模板回退由历史净利增速推导；独立缓存 `valuation_params_{symbol}.json` + 版本号。新端点 `/api/stock/{symbol}/valuation-params?refresh=`，report 页估值区新增「参数校准」对照区块（保留原默认估值不动）。实测招行研报 4 篇，DDM 由默认 28~42（安全边际 −20%）校准为 13.77~20.66（−61%），10 年复投年化约 5.35%；茅台 DCF 也正常重算。narrative 门面现导出五个 generate 入口。
- 研报资料缓存结构化与合理股价区间（同日完成）：`ResearchReport` 扩展 `info_code`/`analyst`/`industry_name`/`eps_forecast`，东财 provider 映射评级、目标价与今/明/后三年 EPS 预测，不再只留标题链接；`utils/research_text.py` 缓存从裸数组升级为 `{schema_version, fetched_at, reports}`（schema=2），TTL 由 `config/default.toml` 的 `[research]` 控制（正文默认 7 天，列表仍由 provider 的 1 小时响应缓存负责），按 infoCode 逐篇复用正文、联网失败降级旧缓存、旧版裸数组首次自动迁移。`_valuation_params.py` 新增综合 `overall` 区间：下沿取各模型最低、上沿取最高、中枢取各模型中点均值，标注参与模型（银行缺自由现金流时仅 DDM，明示参考性有限）；`_VALUATION_PARAMS_VERSION` 升到 2 使旧缓存失效。report 页估值区顶部新增醒目「综合合理股价区间」卡片，静态资源版本升至 20261120c。实测招行研报 4 篇均带评级/EPS 预测，合理价 13.77~20.66 元、中枢 17.21 元（现价 44 元，相对 −60.89%）；茅台双模型合并逻辑由测试覆盖。
- 情报资料统一层与消费端接入（同日完成）：新增 `providers/industry_research/`（东财行业研报 `qType=1` 按 `industryCode`）与 `providers/survey/`（东财 `RPT_ORG_SURVEY` 机构调研，带 `CONTENT` 正文）两个能力及各自 Port，注册表能力数 18→20，`config/providers.toml` 增对应段；`utils/intel.py` 提供 `load_intel_digest(symbol, kinds, refresh, research_limit)`，把个股研报 + 行业研报（由个股研报的 `industry_code` 推导）+ 机构调研整合成统一结构，按类型分级 TTL 缓存于 `data/cache/intel/{symbol}_{kind}.json`（schema 版本号/`fetched_at`、联网失败降级、旧缓存迁移）。消费端三处：① `narrative/_masters.py` 用 `_intel_facts()` 把情报摘录注入 user JSON，`_MASTERS_PROMPT_VERSION` 升到 5（接入资料后解读变长至 640 字，故在基座补「只挑 1~2 条、不得扩充篇幅」约束）使旧解读失效，模板回退同步引用研报/调研并输出 `intel_count`；② `_valuation_params.py` 改走 intel 门面（替换原 `load_research_digest` 调用），`_VALUATION_PARAMS_VERSION` 升到 3，新增 `_sellside_sentiment()`（评级分布、目标价区间、近 30 天评级变动）与 `_citations()`（日期/机构/标题/原文链接，最多 10 条）；③ report 页估值区新增「卖方观点与分歧度」卡片与「参考资料（可点击溯源）」清单，静态资源版本升至 `20261120d`。合规边界：只存公开研报与投资者关系活动记录，不碰非公开渠道材料；LLM 仅解读、不改量化结论，卖方观点明确标注为第三方判断。实测招行拿到 4 篇个股研报 + 3 篇行业研报 + 3 条调研纪要。全量 104 例测试通过，前端 `node --check` 通过。
- 上下文预算放宽与配置化（同日完成）：把散落在代码里的资料截断硬编码全部收敛到配置，原则是质量优先、默认可调大。`[research]` 研报 4→6 篇、单篇 1200→3000 字；`[intel]` 行业研报/调研 3→5 条、800/1000→2000/2500 字；新增 `[masters]`（每类 3 条、研报/行业各 1500 字、调研 2000 字）与 `[valuation]`（每类 3 条、行业 1200/调研 1500 字、引用每类 5 条、总数 20 条）；新增 `[narrative]` 五个输出 token 上限（overview 2000 / score 1200 / valuation 1600 / masters 1800 / valuation_params 2000），替代原 1600/800/1200/700/1400 硬编码。`llm.max_input_tokens`（默认 32000）作为输入软预算：`services/llm.py` 新增 `estimate_tokens()`（CJK 约 1 字 1 token）与 `_guard_input()`，超预算仅告警不截断（截断 JSON 会诱发幻觉），超 2 倍才中段截断兜底。提示词同步放宽：大师解读 350~550→800~1200 字、删除「只挑 1~2 条、不得扩充篇幅」改为要求标注机构与时间并指出分歧；评分解读 300→500~700 字。`_NARRATIVE_VERSION` 3→4、`_MASTERS_PROMPT_VERSION` 5→6 使旧缓存失效。新增缓存指纹：研报/情报缓存 payload 记录写入时 `max_chars`，读取时小于当前配置即视为不新鲜并重抓（`cache_max_chars()`），避免「调大预算但仍复用旧短摘要」。实测招行：研报 6 篇（正文 705~1926 字）、行业研报 5 篇、调研 5 条，引用清单 15 条，大师解读 1088/1103 字且引用具体研报与数据。全量 110 例测试通过（新增 `test_llm` 输入预算 5 例、`test_valuation_params` 缓存指纹 1 例）。
- 财报深度体检（同日完成）：把原「财报体检」从「最新一期数字 + 4 条规则红旗」升级为**法证式风险扫描 + LLM 逐条剖析**。原则是「数字由代码算、判断由 LLM 做」：新增 `analysis/forensics.py`（纯确定性）从多期财报、利润表科目明细、K 线、诚信/监管记录、公告、业绩预告、内部人交易与质押、研报/调研中归纳风险点与异常波动，每条都带精确数值、报告期、触发阈值与证据引用。关键口径防护：① Q1/H1/Q3 累计值一律按**同报告期同比**对齐，不与 FY 混比；② 率类阈值以**自身历史 MAD 离群**为主，银行 90% 负债率、无毛利率不再被误判（负债率必须同时满足离群才报）；③ 全历史仅作波动基线，结论窗口默认近 12 期、事件类证据近 6 年，避免 20 年老噪音淹没结论；④ 连续 ≥3 期同类信号聚合为「趋势型风险点」并保留 `occurrences` 逐期明细；⑤ 东财预告金额字段混杂营收/净利/扣非/EPS，按公告正文过滤并对「约/左右」留 5% 容差，消除 −50% 假偏离；⑥ 数据缺失的检查统一写入 `coverage.skipped`，明确「未检出 ≠ 没问题」。
  新增 `prompts/financial_forensics.md`（可人工编辑）与 `narrative/_forensics.py`：LLM 对每个 signal 输出「实质 / 合理解释 / 需警惕（条件句）/ 结论分级（confirmed|suspicious|normal|unverifiable）/ 该核查什么」，再给综合判断、交叉印证与免责；硬约束包括不得下「造假/粉饰」定性结论、不得引入未提供事实、第三方研报数据必须标注来源、机器归类的诚信记录不得写成处罚。`_normalize()` 对非法枚举降级、缺失项补齐；模板回退只陈述客观证据；独立缓存 `forensics_{symbol}.json` + `_FORENSICS_VERSION`。输出 token 按风险点数动态放大，避免多信号时 JSON 被截断。配置新增 `[forensics]`（lookback_periods/recent_years/max_signals/max_anomalies）与 `forensics_max_tokens`。
  新端点 `/api/stock/{symbol}/financial-forensics?refresh=`；新增共享渲染组件 `pages/static/js/forensics.js`，report 页「财报深度体检」区块与 `review.html` 独立页同时接入（结论卡 / 逐条风险卡片 / 异常波动表 / 交叉印证 / 覆盖度与免责 / 可溯源引用 / 重新分析按钮），静态资源版本升至 `20261120e`。实测五股：招行 2 条（LLM 识破 5 条「高严重度诚信记录」实为年报例行资金占用专项说明）、茅台 5 条、腾讯 5 条、五粮液 13 条（13/13 条均完成剖析，指向会计差错更正后的基数效应）、小米 9 条。全量 134 例测试通过（新增 `test_forensics` 24 例），`node --check` 三支脚本通过。narrative 门面现导出六个 generate 入口。
- 财报体检阈值配置化与降噪（同日完成）：把 `analysis/forensics.py` 里全部硬编码判定值抽成独立配置 `config/forensics.toml`（`[forensics]` 窗口/预算 + `[forensics.thresholds]` 约 45 项阈值与线索关键词），接入 `config.py` 的合并顺序，`default.toml` 不再保留 `[forensics]`。新增 `analysis/forensics_config.py`（纯 dataclass `ForensicsConfig`，字段级非法值兜底 + `fingerprint()`）与 `services/forensics_config.py`（读配置注入扫描器），保持 analysis 层不读配置文件。`narrative/_forensics.py` 改用同一配置对象，`_FORENSICS_VERSION` 升到 3，报告缓存新增 `thresholds_fingerprint`：**改阈值后缓存自动失效重算，无需清缓存或点重新分析**；响应新增 `thresholds` 快照，前端体检区底部新增可折叠的「本次生效的触发阈值」面板。
  降噪与口径修正：① 默认阈值整体收紧（净利率 5→8pp、ROE 3→5pp、费用率 3→5pp、营收骤变 30→40pp、扣非缺口 20→30%、OCF 连续 2→3 期且比值 0.8→0.65、聚合门限 3→2 期等）；② MAD 离群基线由「全历史混合」改为**按报告期类型分组**，避免 Q1 累计与 FY 混比把 MAD 撑歪；③ 同一报告期 ≥2 个科目费用率异动合并成一条「费用与成本结构同步异动」，异常波动明细仍逐条保留；④ 触发规则文案随配置动态生成，前端 `触发规则：` 显示的就是当前生效值。实测五粮液由 **13 条信号 / 21 项异常波动** 降到 **8 条 / 14 项**；把 `rate_require_outlier` 四项全开并再调大几个阈值可降到 **7 条 / 6 项**，两种预设都保留 ROE、监管记录、业绩预告等实质线索。全量 144 例测试通过（新增 `test_forensics_config` 10 例），`node --check forensics.js` 通过，静态资源版本升至 `20261120f`。
- 财报体检因果解释增强（2026-11-23）：新增 `financials.operating_profit` / `pretax_profit`（schema v4，港股由东财 `RPT_HKF10_FN_MAININDICATOR` 映射，A 股从三大报表明细回填）；`analysis/forensics.py` 输出 `profit_structure`（营业利润 / 税前利润 / 营业利润以下合计逐期同比）并注入率类信号 facts，移除「基数效应 / 会计口径」等硬编码候选文案。`prompts/financial_forensics.md` 改为要求解释落到具体科目、金额与因果链，禁止用核查提醒代替解释；`_FORENSICS_VERSION` 升至 11 使旧缓存失效。起因：腾讯 00700.HK 的 ROE 变化被误归为经营端基数效应，实际是营业利润以下项目（美团视同处置高基数、投资/联营/公允价值/减值）摆动。
- 港股利润结构证据再下钻（同日）：上一版只把「营业利润以下合计」作为黑箱交给 LLM，仍无法点名「溢利其他项目 / 联营损益」等具体来源。新增港股逐科目适配：`eastmoney.py` 走 `RPT_HKF10_FN_INCOME` / `RPT_HKF10_FN_BALANCE` 长表写入既有 `statement_items`（港股 6611 行，不改接口契约与表结构；港股现金流量表长表不存在，coverage 明确缺口）。`analysis/forensics.py` 把营业利润以下拆解为「溢利其他项目（投资/处置/公允价值/减值等）/ 应占联营公司溢利 / 利息收入 / 融资成本 / 税项」逐期金额与同比，写入 `scan.profit_structure[].below_breakdown` 并注入率类信号 facts；提示词同步要求必须点名具体科目，不能只说「非经营项目影响」。`_FORENSICS_VERSION` 升至 12。腾讯实测 ROE 信号现直接给出：2023 年「溢利其他项目」由 1,162.87 亿转为 −60.90 亿、2024 年联营溢利 58→251.76 亿，主因是非经营收益基数反转而非经营恶化；「美团视同处置收益 1,066 亿」仍需公告/财报原文证据通道，当前框架未接。全量 195 例测试通过。
- 港股财报原文取证链路打通（同日）：回归框架本意——偏离原因由财报原文印证，而非靠接口数字反推。根因是 `archive_documents()` 写死巨潮数据源，港股归档永远为 0。新增 `providers/announcement/hkex.py` 实现同一 `AnnouncementPort`：披露易 prefix.do 查 stockId + titleSearchServlet.do 检索，按中文年份解析报告期，业绩公告与完整年报分别归档（`_results` / `_report` 后缀，杜绝同名覆盖），排除 ESG 与英文版；归档服务按市场选源，A 股仍走巨潮。`utils/pdf.py` 改用 PyMuPDF 首选提取（pypdf 无法解码港交所嵌入字体导致中文乱码，保留兜底），`pyproject.toml` 增 `pymupdf`。`_evidence.py` 补繁体/港股口径同义词（視同處置、美團、權益持有人應佔盈利、非國際財務報告準則等）、新增跨行「该下降主要由於…」解释句切片并加权，证据展示 max_chars 420→900；`_merge_evidence` 改为挖掘/关键词两路交错保留名额，避免当期 2026 摘录把 2023 关键原文挤出上下文。`_FORENSICS_VERSION` 12→16。腾讯实测（24 份原文归档）最终 ROE 解读为「据公司2023年报原文，2022年第四季确认视同处置美团的收益1,066亿元，导致2023年溢利其他项目同比减少1,223.77亿元，这是ROE从24.64%降至15.06%的主因」——与人工核对结论一致。全量 197 例测试通过。
- 财报原文前置守卫（同日）：把「先用财报原文印证、再分析」做成流程硬约束，避免缺原文时 LLM 只能按结构化数字推断。新增 `narrative/_source_guard.py`：统计本地可读原文（PDF 必须已抽出非空文本）的份数与覆盖年份，不足则复用归档服务按市场自动下载多期（A 股巨潮 / 港股披露易），补齐后才进入扫描、原文检索与剖析；下载失败不阻断分析，但明确写出 status 与 reason，不静默降级。`config/forensics.toml` 增加 `min_source_documents=3` / `min_source_years=2` / `source_archive_per_type=6`，纳入 `ForensicsConfig` 指纹；报告响应新增 `source_guard`（status/required/before/after/downloaded/reason），`_FORENSICS_VERSION` 16→17。顺带修正归档服务按 `(stock_id, doc_type, period)` 判重（此前同报告期的业绩公告与年报被唯一约束忽略后仍反复下载），并支持本地文本丢失时重下。验证：隔离临时库从空库自动下载 8 份、补齐 7 份可读文本/2 个年份→`repaired`；腾讯已有 21 份原文→`ok` 不重下，刷新后报告仍引用「視同處置美團 1,066 億元」。`test_source_guard.py` 新增 5 例 + 集成断言，全量 **203 例通过**。
- 财报关键点渲染到日 K 线（同日）：report 页日 K 线图新增底部「财报关键点事件带」。`forensics.js` 新增纯函数 `VC.buildForensicMarkers(data, kline)`（每条风险点按 `period` 末次报告期锚定到当天或之后最近交易日，第二条隐藏 y 轴固定在图表底部成带；色调判定：LLM 判「更可能是正常解释」→ 红色正向，已披露事实/需核查线索/高严重度 → 绿色警示，其余 → 灰色中性；同日同色调轻微错位防重叠）与 `VC.focusForensicSignal(id)`（展开、滚动、短暂高亮对应风险卡片，卡片新增 `fx-signal-<id>` 锚点）。`report.js` 的 K 线改造为双 y 轴 + scatter 事件系列，axis tooltip 自定义 formatter 同时显示 OHLC 与事件详情（标题/报告期/等级/判定/结论摘要/点击提示，全部经 escapeHtml 转义）；体检数据与 K 线两条异步链路谁先到都不丢，合流后重绘；体检数据到达后自动把 dataZoom 默认视窗拉近到事件区间（否则 20 年 K 线会把近年点挤成一条线）；图例按三色统计渲染于图表下方。`report.html` K 线区加副标题与 `#klineMarkerLegend`，静态资源版本 20261120f→20261123a（report/review 两页一致）。新增 `tests/test_forensic_markers.py`：5 例静态接线断言 + Node vm 加载真实脚本验证构建逻辑（色调、锚定、区间取末次、高严重度兜底、同日错位、空数据降级），全量 **209 例通过**。无头 Edge 渲染验证：图例显示「正向 4 / 风险 1 / 中性 0 · 共 5 个财报关键点」，5 张风险卡片锚点齐全。
- 原文通读发现接入 K 线事件带（同日）：把原先只在报告里展示的 `report_mining.new_findings` 也做成 K 线事件点，让「规则风险点」与「通读线索」在同一时间轴上对照。`forensics.js` 的 `MARKER_TONES` 新增 `clue`（琥珀 `#d97706`、菱形 `diamond`、独立 y 层 0.195），刻意不占用「红=正向 / 绿=风险」语义；`buildForensicMarkers()` 同时遍历 `scan.signals` 与 `report_mining.new_findings`，用 `findingReportDate()` 把归档只存年份的 `period`（如 "2026"）结合 `report_label`（一季报/中报/三季报/年报）还原成报告期末日期再锚定最近交易日，与风险信号口径一致；悬停详情含线索描述 + 财报原文摘录。每条通读发现卡片新增 `fx-finding-N` 锚点，新增通用 `VC.focusForensicAnchor(anchorId)`（展开折叠祖先 details + 滚动 + 短暂高亮），`focusForensicSignal` 委托给它保持兼容，所有事件点统一带 `anchorId`。`report.js` 的 tooltip 按事件类型显示不同点击提示、图例遍历全部色调并把线索画成菱形、点击优先用 `anchorId` 定位；`report.html` 副标题与静态资源版本 `20261123a→20261123b`（review 同步），CSS 增 `.kl-legend-dot.is-diamond` 与 `scroll-margin-top`。`tests/test_forensic_markers.py` 扩展 Node vm 断言（clue 色调/菱形/锚点、季度日期还原、完整日期优先、无年份跳过、earliest 更新、反向定位含祖先展开、旧接口委托）与静态接线断言，全量 **209 例通过**，`node --check` 通过。线上真实数据验证：腾讯 5 条通读发现 + 5 条规则信号 = 10 个点（clue 5），线索日期分别落到 2026-09-30 / 2024-12-31 / 2026-06-30 / 2026-03-31；无头 Edge dump-dom 图例显示「正向 4 / 风险 1 / 中性 0 / 原文通读线索 5 · 共 10 个财报关键点」，5 个 `fx-finding-N` 锚点齐全。
- A 股财报原文「披露提示公告壳」修复 + 中际旭创 300308.SZ 分析（同日）：新拉中际旭创（kline 3384 / financials 66 / statement_items 2016 / 研报 100 / 业绩预告 47）后发现原文通读只挖到 5 段、线索 0 条。根因：巨潮归档把「XX年度报告披露的提示性公告」（100~370KB 的"报告已披露"声明）当成财报正文，2024 年后各期全是壳，而通读优先最新季报，读到的全是空壳；守卫只判「文本非空」也误判覆盖充足。按分层修复：`utils/pdf.py` 新增 `is_source_title_usable` / `is_source_text_usable`（排除壳标题 + 文本 <2000 字）；`cninfo.py::_match_doc_type` 排除提示性公告/英文版/摘要；`archive.py` 判重时识别存量壳并删记录重下、只把正文写入 documents 表（壳不入库不计数，继续找下一份候选）；`_evidence.py::_load_documents` 在入口过滤壳文档；`_source_guard._is_usable` 改为读文本做长度判定；`_FORENSICS_VERSION` 17→18 使旧缓存失效。重下后 300308 归档 **22 份全是正文**（年报 21~24 万字、季报 1.3~2.5 万字，覆盖 2021–2026，壳 0），体检 `source_guard=ok`；重跑后通读段落 5→110、原文证据 0→12 个风险点全覆盖、通读发现 0→5 条，结论改为直接引用 2026 三季报原文的汇兑损失 13.87 亿 / H 股募资净损失 2.54 亿 / 公允价值变动损失 4.4 亿，解释净利与 ROE 背离。`test_source_guard.py` 新增 3 例（壳标题/短文本不算可用、cninfo 只匹配正文标题、pdf 质量判定），全量 **212 例通过**。K 线事件带在 A 股同样生效：12 信号中 8 个有完整报告期 + 5 条通读线索 = 13 个点，图例「正向 5 / 风险 3 / 中性 0 / 原文通读线索 5」，5 个锚点齐全。
- 财报深度体检覆盖度诊断（同日，只读评估，**未改代码**）：以 300308.SZ / 00700.HK 已归档原文逐条对照，确认框架方向正确（原文印证偏离、事实与解释分离），但存在**三层收窄**，不是再加检查项能解决。① 事实层：`_load_inputs()` / `scan_forensics()` 只消费 21 个主指标 + 少量 statement_items，25 条规则全是比率/同比异动；原文里的结构性事实不在输入中，尽管 `prompts/financial_forensics.md` 与 `_forensics.py:275-277` 已要求 LLM 点名「前五名客户关联方占比 / 研发投入资本化比重」——提示词承诺的能力大于 facts 能提供的输入。原文证据：300308 2025 年报 L1386-1428 前五名客户 75.98%（第一大 24.06%、关联方销售 8.10%）、供应商前五 51.50%；L449-509 非经常性损益明细（政府补助 3,940.68 万、股权投资公允价值变动及分红 6,712.19 万、合计 8,720.11 万，三年可比）；L1585-1606 资本化研发支出 6,064.93 万 / 占研发投入 3.62%（8.65%→6.64%→3.62%，公司自述大幅变动原因）；L4954-4959 会计政策/估计变更「不适用」但新纳入 9 家合并主体（含泰国、TeraHop、Vincrest、Lunaris 等海外实体）；L1121/L1663 经营活动现金流量净额 108.96 亿、同比 +244.31% 且公司解释「本期业务增长带来的销售回款」——而报告 caveats 仍写「因字段缺失未执行」、skipped「经营现金流与利润背离」、reason 文案「银行/港股常见」，**同一数字原文层读到、事实层判缺失**。00700 2025 年报 L5589-5591 五大客户仅占总收入 6.5%、最大 3.4%（供应商前五 18.8%），与 300308 的 75.98% 形成关键对照，两边原文都有该章节却都不进 facts。② 原文通读层：`_report_mining._pick_documents()` 年报组 count=2 被风险年份占满，腾讯 source_docs 只有 2026 三份季报 + 2024/2023 年报，**最新最完整的 2025 年报未读**（该份含 196 亿元回购、153,415,000 股已注销、五大客户占比、獨立核數師報告與關鍵審計事項、重要會計估計及判斷、資本承擔、利率掉期對沖、自由現金流 340 億/經營現金流 665 億）；`_MAX_FINDINGS=5` + 每份最多 2 条 + 排序偏时效/解释句，导致腾讯 5 条线索里 4 条同为「AI 推高费用」，主题重复、客户/审计/回购零覆盖。③ 呈现层：`coverage.skipped` 混装「真数据缺失 / 规则未触发或样本不足 / 港股制度不适用」三类，前端一律渲染「因数据缺失未执行的检查」；港股费用率扫描因 item_code 不匹配逐期 continue 仍计入 `performed`（假阳性）。改进方向（保留分层，按优先级）：**P0** 补事实层一等输入——A 股 `ocf` 从 `NETCASH_OPERATE` 回填、接已在库的 `revenue_composition`（分部）/ `corporate_actions`（回购，现只陈列不判定）/ `circulating_holders` / `shareholder_count`；新增「财报结构化事实卡」适配器，从归档原文按表头定位抽 6 组确定性字段（客户与供应商集中度、非经常性损益明细、研发资本化率、会计政策/估计变更与合并范围、审计意见与关键审计事项/审计机构、承诺与或有/资本承担/回购），作为 `scan` 并列输入，让 LLM 的解释有可复现凭据而非靠 5 条线索碰运气；`coverage` 拆三类并分色渲染。同时修通读采样：年报组强制保留最新完整年报、new_findings 按主题分桶去重（客户/审计/资本/会计估计/经营各保底 1 条）。**P1** 把客户集中度、分部毛利率、资本化率做成同款 `anomalies` 序列 + MAD 判定并接入 K 线事件带；事件点改用实际披露日；前端展示 `source_guard` 与事实卡。**P2** 章节优先级 + 按文档 sha 增量缓存（现 110 段全量送 LLM）、港股制度差异标注 not_applicable、港股审计意见从核数师报告抽取。明确不做：单纯调大 new_findings 上限、让 LLM 代替确定性扫描发现数字、把制度差异包装成风险提示。全量 212 例测试未变，本轮仅诊断。
- 改动前存档与能力回归基线（同日）：按用户要求先保证可回滚。落三个本地检查点——`75a019f`（v18 基线代码）、`1f102b9`（基线校验脚本 + 报告快照）、`df771df`（财务戏法后端）；另在项目外 `D:\Projects\value-compass-archive\baseline-20261123-2027\` 做物理快照（`code.zip` 724KB / `runtime.zip` 141MB / `manifest.json` 含 commit、SHA256 与回滚说明）。新增 `scripts/verify_capability.py` + `tests/baselines/capability_baseline.json`：`--record` 录基线、默认对比，任一能力项下降即退出码 1；只允许变多不允许变少，检查 source=llm、原有风险点是否消失、剖析完整率、原文证据覆盖、通读段落、K 线事件点、source_guard、总纲是否仍引用财报原文。两处口径修正：原有风险点按**标题**判定（新增检查会让 signal id 整体重新编号，按 id 会误报降级）；关键原文证据用**正则**判定（LLM 会写「1066亿元/1,066 亿」「视同处置/視同處置」，精确字符串会误报取证退化）。基线缺失的 `signal_titles` 从物理快照里的 v18 缓存补录，不是把改动后状态当成基线。
- 财务戏法检查（同日）：新增 `analysis/forensic_patterns.py`（11 项检查 / 4 类：虚增利润与收入质量、财务洗澡、关联交易、会计估计与资本运作）与 `services/narrative/_report_facts.py`（财报原文结构化事实卡）。**刻意不并入 `scan.signals`**：作为报告并列字段 `fraud_patterns` / `report_facts` 输出，不占 `max_signals` 名额、不进 analyses 逐条剖析数组，因此既有 25 条规则与 LLM 剖析完整性零改动；失败时 try/except 写 `error` 字段而非静默。准确度设计：组合条件（应收增速背离需叠加应收/营收比抬升且连续 ≥2 期）、同报告期同比（去年同月同日）、自身历史 MAD、结论窗口 `lookback_periods`（全历史只作同比基数，禁止拿 2008/2016 年低谷解释 2026 年）、`coverage` 三分（已执行/未触发、制度不适用 not_applicable、数据不足 data_missing）。原文事实卡从归档年报抽前五名客户/供应商占比、关联方销售占比、非经常性损益合计、研发资本化率、会计政策变更、合并范围新增主体、审计意见、股份回购；按文本体积选同报告期最完整的年报，绕过 `documents` 表登记不全的问题。
- 顺带修掉三个真实缺陷：① `services/narrative/_forensics.py::_fill_ocf_from_items()` 从 `statement_items` 的 `NETCASH_OPERATE` 回填 A 股 `financials.ocf`（300308 从 0/66 → 42/66），消掉「原文里写着经营现金流 108.96 亿、事实层却判字段缺失」的割裂，让原本被 skip 的「经营现金流与利润背离」成为 high 信号；② 发现**港股 2025 完整年报（488KB）未进 `documents` 表**，表内只登记了 85KB 的业绩公告（`archive.py` 按 `(stock_id, doc_type, period)` 判重，同期两类文档只留一条），这才是上一条诊断里「腾讯 2025 年报未被通读」的真正根因；本轮让事实卡直接扫磁盘把完整年报用起来（抽到五大客户 6.5%、供应商 18.8%、回购 196 亿），归档链路本身的修复留作独立改动，避免一次动太多；③ 港股核数师报告里「假若披露不足，則我們應當發表非無保留意見」是条件句，关键词正则会误报非标意见，改为只认「我們認為…真實而公平」结论句，否则标 `unknown` 不下判断。
- 准确度问题修正过程（真实数据驱动）：首版 11 项检查跑出 7/3 条线索，但含 2008-03-31 净现比、2012 收现比、2016 财务洗澡、港股「连续 19 期存货异常」等误报。逐条修：加结论窗口裁剪、客户集中度按年份取最新（原取到 2023 的 73.71%/0%，实际 2025 是 75.98%/8.10%）、净现比连续期数按原序列统计（原遍历已筛列表会算成 len(low)）、财务洗澡要求反弹紧邻低谷（一年内）、港股无营业成本科目时存货检查标 `not_applicable` 而非拿营收当分母。修后：300308 3 条（净现比 0.36、客户集中 75.98% + 关联方销售 8.10%、2025 合并范围新增 9 家），00700 0 条（现金流强、客户分散，本就不该报警）。
- 提示词与前端（同日）：`prompts/financial_forensics.md` 新增铁律 13「戏法线索只作解释对象，不得给 analyses 编造 id、不得下定性结论」；首轮重跑发现新增内容把 `essence` 里「汇兑损失 13.87 亿」原文取证挤出总纲（761→638 字），补强为**原文取证硬要求**（essence 必须引用至少一处 `official_explanations` 里的具体科目与金额并注明报告期，新增线索不得替换），`_MAX_FRAUD_PATTERNS_IN_FACTS` 5→3，`_FORENSICS_VERSION` 19→20。前端 `forensics.js` 新增 `VC.renderFraudPatterns()`（线索卡片含类别/等级/报告期/解释/原文摘录/判定依据折叠/下一步/免责，附原文事实卡网格与覆盖度三分清单）、`report.css` 增 `.fx-fraud-*` 样式、静态版本 `20261123b→20261123c`（report/review 同步）、`tests/test_forensic_markers.py` 加静态接线断言。
- 验证：新增 `tests/test_fraud_patterns.py` 17 例（结论窗口、组合条件防误报、最新年份、制度不适用、核数师条件句、ocf 回填边界、无定性词），全量 **230 例通过**，`node --check` 通过；`verify_capability.py` 对比基线 **PASS 且多项上升**：300308 总纲 761→846 字、信号 12→13、剖析 12→13、原文证据 12→13、K 线事件点 13→14；00700 总纲 594→667 字、其余持平；两份报告 source=llm、source_guard=ok。无头 Edge dump-dom：300308 渲染 3 张线索卡片 + 原文事实卡（75.98%/8.1%），00700 显示「未触发」提示 + 事实卡（6.5%），覆盖度清单齐全。
- 遗留（未做，避免一次改动过大）：港股归档同期「业绩公告 + 完整年报」只留一条的根因未修；原文通读仍按风险年份选年报（腾讯 2025 年报仍未进入 `source_docs`），本轮只让事实卡读到它；`new_findings` 5 条上限与主题去重；事件点仍按报告期末而非实际披露日定位；`coverage.skipped` 旧链路仍把制度差异/样本不足混写为「因数据缺失未执行」（新戏法检查已用三分口径，旧 coverage 未动）。
