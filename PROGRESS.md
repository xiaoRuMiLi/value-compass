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
| W0.5 | _shared 传输 | 无 | HTTP、重试、限流、缓存 | W0.3 | 待办 |
| W0.6 | registry | providers.toml | 能力 → 源选择 | W0.5 | 待办 |

## L2 providers（17 个，可并行）

统一输入：`symbol`（如 `600519.SH`）+ 时间区间等；输出为对应 Port 定义的 models。

| 编号 | 能力 | Port | 输出 models | 状态 |
|---|---|---|---|---|
| P1 | quote | QuotePort | Snapshot / KlineBar / ValuationPoint | 待办 |
| P2 | financial | FinancialPort | FinancialReport / StatementItem / RevenueComposition | 待办 |
| P3 | announcement | AnnouncementPort | Announcement(Page) / Document | 待办 |
| P4 | sentiment | SentimentPort | Sentiment | 待办 |
| P5 | holding | HoldingPort | CirculatingHolder / FundHolding | 待办 |
| P6 | research | ResearchPort | ResearchReport | 待办 |
| P7 | industry | IndustryPort | IndustryBoard | 待办 |
| P8 | litigation | LitigationPort | Litigation | 待办 |
| P9 | dividend | DividendPort | DividendRecord | 待办 |
| P10 | capitalflow | CapitalFlowPort | CapitalFlow / NorthboundHolding | 待办 |
| P11 | hotspot | HotspotPort | Hotspot | 待办 |
| P12 | official | OfficialPort | OfficialUpdate | 待办 |
| P13 | commodity | CommodityPort | ProductPrice / StockProduct | 待办 |
| P14 | forecast | ForecastPort | EarningsForecast / ConsensusEstimate / ReportCalendarItem | 待办 |
| P15 | governance | GovernancePort | CorporateAction / InsiderTrade / ShareholderCount / Pledge / Unlock | 待办 |
| P16 | macro | MacroPort | MacroIndicator | 待办 |
| P17 | integrity | IntegrityPort | IntegrityRecord | 待办 |

## L3 analysis（11 个，可并行）

统一输入：models 数据对象；输出为结果模型。

| 编号 | 模块 | 输出 | 状态 |
|---|---|---|---|
| A1 | financial 评分 | DimensionScore | 待办 |
| A2 | valuation（DCF/DDM/相对/格雷厄姆） | ValuationResult | 待办 |
| A3 | return（分红复利/回报） | ReturnResult | 待办 |
| A4 | moat（护城河） | MoatAssessment | 待办 |
| A5 | cycle（市场周期） | 周期位置 | 待办 |
| A6 | comparison + industry（横向对比/行业地位） | CompanyComparison / PeerPosition | 待办 |
| A7 | holder_tracking（持仓追踪） | HolderPosition | 待办 |
| A8 | price_correlation（价格相关性） | PriceCorrelation | 待办 |
| A9 | redflags + fraud_score + delisting（风险红旗/造假/退市） | RedFlag / FraudScore / DelistingRisk | 待办 |
| A10 | masters（大师模型） | MasterVerdict | 待办 |
| A11 | financial_review + integrity（财报体检/诚信挖掘） | FinancialReviewReport / IntegrityRecord | 待办 |

## L4 web API 与 pages（可并行）

| 编号 | 模块 | 输入 | 输出 | 依赖 | 状态 |
|---|---|---|---|---|---|
| W4.1 | web 骨架 + 路由 | FastAPI | app.py、routes/、静态托管 | L1 | 待办 |
| W4.2 | API 接口组 | L2/L3 | /api/* 全部端点（见 CONTRACTS 第 5 节） | L2、L3 | 待办 |
| W4.3 | 页面组 A（report/financials/industry） | API | 三张页面 | W4.2 | 待办 |
| W4.4 | 页面组 B（compare/screener/calculator） | API | 三张页面 | W4.2 | 待办 |
| W4.5 | 页面组 C（products/holders/calendar） | API | 三张页面 | W4.2 | 待办 |
| W4.6 | 页面组 D（review/integrity/charts） | API | 三张页面 | W4.2 | 待办 |

## L5 skill 化与验收（串行收尾）

| 编号 | 模块 | 输出 | 依赖 | 状态 |
|---|---|---|---|---|
| W5.1 | SKILL.md + references 方法论 | 技能壳 | L4 | 待办 |
| W5.2 | 技能壳链接进 ~/.codex/skills | 可被 Codex 发现 | W5.1 | 待办 |
| W5.3 | 测试补全 + 真实案例验收 | 茅台单股 + 白酒行业批量跑通 | L4 | 待办 |

## 备注

- 数据源验证 spike 已完成（2026-11-11），完整清单见 ARCHITECTURE.md 第 13 节。
- 三项待定，实现对应 provider 时再定：股权质押、公司行动（增发/回购/可转债/股权激励）、无风险利率。
- 本环境 git 推送需先 `Remove-Item Env:GIT_SSH_COMMAND`，再设 `GIT_SSH=C:\Windows\System32\OpenSSH\ssh.exe`。
