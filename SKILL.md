---
name: value-compass
description: A股/港股/中概股价值投资基本面深度分析。当用户要求分析个股基本面、评估公司质地、估值与安全边际、行业横向对比、筛选优质标的、财报解读、诚信排查、大师视角研判时触发。工作流：先 python scripts/fetch_data.py <symbol> 拉数据入库，再 python scripts/serve.py 起服务查看报告页；分析时结合结构化结果与本地财报原文。只做基本面研究，不构成投资建议，不做技术面/买卖时点，不覆盖期货/外汇/加密货币。
---

# value-compass（价值罗盘）

综合价值分析框架：财务 + 行业 + 大师模型 + 财报原文 + 诚信记录 + 可视化。

## 何时使用

- 个股基本面/价值投资分析、估值、安全边际、护城河、行业地位。
- 财报体检（面向不懂财报的用户）、诚信排查、大师视角研判。
- 不适用：技术面/择时、期货/外汇/加密货币、纯宏观择时。

## 快速上手

1. 拉数据入库：`python scripts/fetch_data.py 600519.SH`（加 `--docs` 同时下载财报 PDF）。
2. 看报告页：`python scripts/serve.py`，浏览器打开 `http://127.0.0.1:8000/report?symbol=600519.SH`。
3. 结构化结果：`from fundamental.services.analyze import analyze_stock` 或 `/api/stock/{symbol}/analysis`。

## 分析工作流（agent）

1. 确定股票标识 `symbol`（`600519.SH` / `00700.HK` / `BABA.US`）。
2. 若库中无数据，先 `fetch_data.py <symbol> --docs`。
3. 读结构化结果：评分、估值、回报、护城河、周期、大师、风险红旗、造假、退市、财报体检。
4. 读本地财报原文：`data/archive/documents/{code}/{年份}/*.txt`，重点看"管理层讨论与分析"、"重要事项"、"财务报表附注"。
5. 用结构化数字做骨架、用原文与公开信息补定性，按 `references/report-format.md` 输出。

## 关键约定

- 数据源：东财/巨潮/新浪（免费），Ports & Adapters 可换源，见 `config/providers.toml`。
- 所有数字来自库中数据或标注来源，缺失必须标注 `[数据缺失]`，禁止编造。
- 不构成投资建议；不提供买卖时点；结论是研究参考。
- 改代码前先读 `EXTENDING.md`、`ARCHITECTURE.md`、`CONVENTIONS.md`。

## 文档索引

- `ARCHITECTURE.md` 架构与数据源、`SCHEMA.md` 表结构、`CONTRACTS.md` 接口契约。
- `CONVENTIONS.md` 命名规范、`EXTENDING.md` 扩展手册、`PROGRESS.md` 进度。
- `references/` 下为方法论，按需读取。
