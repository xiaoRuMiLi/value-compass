# value-compass 项目进度表

本文档是项目推进的唯一进度表。每完成一个任务，更新其状态并提交。
状态取值：`待办` / `进行中` / `已完成` / `受阻`。

## M0 工程底座

| 编号 | 任务 | 状态 | 依赖 | 完成标准 |
|---|---|---|---|---|
| 0.1 | 目录骨架 + pyproject.toml | 待办 | - | 按 ARCHITECTURE.md 建齐目录，`pip install -e .` 可运行 |
| 0.2 | 配置系统 | 待办 | 0.1 | config 按序合并、paths/logging/symbols 可用 |
| 0.3 | SQLite 建库与迁移 | 待办 | 0.2 | 按 SCHEMA.md 生成 34 张表，迁移可重复执行 |
| 0.4 | 数据源验证 spike | 待办 | 0.1 | 输出 17 个能力的可用性清单，更新 ARCHITECTURE 第 13 节 |

## M1 纵向切片（茅台端到端）

| 编号 | 任务 | 状态 | 依赖 | 完成标准 |
|---|---|---|---|---|
| 1.1 | _shared 传输 + registry | 待办 | 0.3 | HTTP/重试/限流/缓存可用，registry 按配置选源 |
| 1.2 | quote + financial provider | 待办 | 1.1 | 东财快照/K线/估值/财务/三大报表可拉取 |
| 1.3 | models + 数据写入 | 待办 | 1.2 | stocks/kline/snapshots/valuations/financials/statement_items 落库 |
| 1.4 | 最小 API | 待办 | 1.3 | overview/financials/kline 端点返回 JSON |
| 1.5 | report.html 最小版 + serve.py | 待办 | 1.4 | `serve.py` 启动，浏览器看到 600519 图表 |

## M2 数据能力横向补齐

| 编号 | 任务 | 状态 | 依赖 | 完成标准 |
|---|---|---|---|---|
| 2.1 | 公告/财报 PDF/招股书 + 存档 | 待办 | 1.2 | announcements/documents 落库，PDF 存 archive |
| 2.2 | 分红 + 股东/基金持仓 + 增减持/户数 | 待办 | 1.2 | dividends/circulating_holders/fund_holdings/insider_trades/shareholder_count 落库 |
| 2.3 | 资金流/沪港通 + 情绪 + 研报 | 待办 | 1.2 | capital_flows/northbound/sentiment/research_reports 落库 |
| 2.4 | 行业 + 商品价格 + 官网 | 待办 | 1.2 | industry/stock_products/product_prices/official_updates 落库 |
| 2.5 | 业绩预告/一致预期/财报日历 + 解禁/质押/公司行动 | 待办 | 1.2 | earnings_forecasts/consensus_estimates/report_calendar/unlocks/pledges/corporate_actions 落库 |
| 2.6 | 宏观 + 诚信记录 | 待办 | 1.2 | macro_indicators/integrity_records 落库 |

## M3 分析层

| 编号 | 任务 | 状态 | 依赖 | 完成标准 |
|---|---|---|---|---|
| 3.1 | 财务评分 + 红旗 + 造假预警 + 退市风险 | 待办 | M2 | financial/redflags/fraud_score/delisting 输出结构化结果 |
| 3.2 | 估值四件套 | 待办 | M2 | DCF/DDM/相对/格雷厄姆输出统一 ValuationResult |
| 3.3 | 回报/分红复利 + 护城河 + 周期 | 待办 | M2 | return/moat/cycle 输出 ReturnResult/MoatAssessment |
| 3.4 | 横向对比 + 行业地位 | 待办 | M2 | comparison/industry 输出 CompanyComparison/PeerPosition |
| 3.5 | 大师模型 + 财报体检 + 诚信挖掘 | 待办 | 3.1 | masters/financial_review/integrity 输出 MasterVerdict/FinancialReviewReport |

## M4 页面补全

| 编号 | 任务 | 状态 | 依赖 | 完成标准 |
|---|---|---|---|---|
| 4.1 | compare + screener | 待办 | M3 | 多股对比、筛选可用 |
| 4.2 | financials + industry | 待办 | M3 | 三大报表、行业地位图可用 |
| 4.3 | calculator + products + holders | 待办 | M3 | 分红复利、价格对比、持仓追踪可用 |
| 4.4 | calendar + review + integrity | 待办 | 3.5 | 事件日历、财报体检、诚信档案可用 |
| 4.5 | charts（可选） | 待办 | 1.2 | 蜡烛图大图 |

## M5 skill 化与验收

| 编号 | 任务 | 状态 | 依赖 | 完成标准 |
|---|---|---|---|---|
| 5.1 | SKILL.md + references 方法论 | 待办 | M4 | 技能壳完整，agent 可按指引工作 |
| 5.2 | 技能壳链接进 ~/.codex/skills | 待办 | 5.1 | `~/.codex/skills/value-compass` 可被 Codex 发现 |
| 5.3 | 测试补全 + 真实案例验收 | 待办 | M4 | 契约/单元测试通过，茅台单股 + 白酒行业批量跑通 |

## 备注

- 每个任务完成即更新本表并 git 提交，保持进度表与代码同步。
- 数据源验证（0.4）若发现接口不可用，先改 `SCHEMA.md`/`CONTRACTS.md` 再继续。
- 本环境 git 推送需先设 `GIT_SSH=C:\Windows\System32\OpenSSH\ssh.exe`。
