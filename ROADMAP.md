# value-compass（价值罗盘）实施路线图

## 当前状态（2026-11-20 更新）

- 阶段：主体功能已实现并跑通，处于**收尾与一致性治理**阶段。
- 落地情况：L1 地基（6 单元）、L2 providers（17 能力）、L3 analysis（11 模块）、L5 skill 化（3 单元）均已完成；L4 web 与页面中 W4.1、W4.3–W4.6 已完成，W4.2 API 接口组进行中（契约 48 端点已实现 23，详见 `PROGRESS.md`）。
- 测试：`tests/` 下 53 例（analysis / llm / narrative / registry / storage / web_api / exceptions），`python -m unittest discover -s tests` 全部通过。茅台单股已验收；白酒行业批量待批量分析入口（`services/batch.py`）落地。
- 新增能力：大模型叙述增强（LLM 接入 + 模板回退 + 本地缓存），见 `ADR/ADR-001` 与 `ARCHITECTURE.md`。
- 三份基线文档已封版并交叉核对：`ARCHITECTURE.md`、`SCHEMA.md`、`CONTRACTS.md`；实现与契约的偏差以 `PROGRESS.md` 及各文档内的"实现状态"小节为准。
- 数据源：东财、巨潮、新浪期货已实测可用；股吧/天天基金需 HTML 解析；官网需模板适配；其余新增源待实现时统一验证。

## 里程碑（✓ 已完成 / ◐ 部分完成）

1. ✓ 脚手架与建库
   - 按 `ARCHITECTURE.md` 建立目录骨架与 `pyproject.toml`。
   - 按 `SCHEMA.md` 初始化 SQLite（`data/db/fundamental.db`）与迁移机制（`SCHEMA_VERSION=3`）。

2. ✓ 数据源统一验证
   - 逐个验证 17 个 provider 能力的接口与字段，实测清单见 `ARCHITECTURE.md` 第 13 节。

3. ✓ Provider 层实现
   - `_shared` 传输（HTTP、重试、限流、缓存）与 `registry.py`。
   - 17 个能力的 Port + 东财/巨潮/新浪等数据源均已实现。

4. ✓ Analysis 层实现
   - 财务评分、估值（DCF/DDM/相对/格雷厄姆）、回报、护城河、周期、横向对比。
   - 大师模型（巴菲特/段永平）、财报体检、诚信挖掘、造假预警、退市风险。

5. ◐ Web 与页面
   - FastAPI + `pages/` 十三张页面（含 holders-track）已完成；`/api/*` 接口实现 23/48（见 `CONTRACTS.md` 第 5 节实现状态）。
   - 静态资源本地化（ECharts），在线界面 `serve.py` 启动。charts 为可选页，未实现。

6. ✓ Skill 化
   - `SKILL.md` + 5 篇 `references/` 方法论 + 3 个提示词模板。
   - 技能壳已软链接到 `~/.codex/skills/value-compass`。

7. ◐ 测试与验收
   - 单元/契约测试 53 例全部通过（analysis / llm / narrative / registry / storage / web_api / exceptions）。
   - 茅台单股已验收；白酒行业批量的专用入口（`services/batch.py` / `scripts/batch_analyze.py`）尚未落地，批量能力目前通过 `/api/compare`、`/api/screener` 提供。

## 工作约定

- 源码根目录：`C:\Users\Administrator\Documents\Codex\value-compass`。
- 技能部署目录：`C:\Users\Administrator\.codex\skills\value-compass`（软链接或安装，待 skill 化阶段创建）。
- 细粒度任务与实时状态见 [PROGRESS.md](PROGRESS.md)，每完成一个任务更新该表并提交。
- 每完成一个里程碑，更新本文件"当前状态"。

## 文档索引

- [ARCHITECTURE.md](ARCHITECTURE.md)：目录结构、分层与扩展规则。
- [SCHEMA.md](SCHEMA.md)：SQLite 表结构与字段定义。
- [CONTRACTS.md](CONTRACTS.md)：数据模型、Port 接口与 Web API 契约。
- [PROGRESS.md](PROGRESS.md)：细粒度任务清单与进度状态。
