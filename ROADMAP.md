# value-compass（价值罗盘）实施路线图

## 当前状态

- 阶段：架构设计完成，尚未开始实现。
- 三份基线文档已封版并交叉核对：`ARCHITECTURE.md`、`SCHEMA.md`、`CONTRACTS.md`。
- 数据源：东财、巨潮、新浪期货已实测可用；股吧/天天基金需 HTML 解析；官网需模板适配；其余新增源待实现时统一验证。

## 里程碑

1. 脚手架与建库
   - 按 `ARCHITECTURE.md` 建立目录骨架与 `pyproject.toml`。
   - 按 `SCHEMA.md` 初始化 SQLite（`data/db/fundamental.db`）与迁移机制。

2. 数据源统一验证
   - 逐个验证 17 个 provider 能力的接口与字段，能通的照设计，不通的当场改设计。

3. Provider 层实现
   - `_shared` 传输（HTTP、重试、限流、缓存）与 `registry.py`。
   - 按能力实现 Port + 东财/巨潮/新浪等数据源，先覆盖核心财务与行情。

4. Analysis 层实现
   - 财务评分、估值（DCF/DDM/相对/格雷厄姆）、回报、护城河、周期、横向对比。
   - 大师模型、财报体检、诚信挖掘、造假预警、退市风险。

5. Web 与页面
   - FastAPI + `pages/` 十二张标准页面 + `/api/*` 接口。
   - 静态资源本地化（ECharts），在线界面 `serve.py` 启动。

6. Skill 化
   - 编写 `SKILL.md` 与 `references/` 方法论。
   - 把技能壳安装或软链接到 `~/.codex/skills/value-compass`。

7. 测试与验收
   - 契约测试、单元测试、fixtures；跑通茅台单股、白酒行业批量两个真实案例。

## 工作约定

- 源码根目录：`C:\Users\Administrator\Documents\Codex\value-compass`。
- 技能部署目录：`C:\Users\Administrator\.codex\skills\value-compass`（软链接或安装，待 skill 化阶段创建）。
- 每完成一个里程碑，更新本文件"当前状态"。

## 文档索引

- [ARCHITECTURE.md](ARCHITECTURE.md)：目录结构、分层与扩展规则。
- [SCHEMA.md](SCHEMA.md)：SQLite 表结构与字段定义。
- [CONTRACTS.md](CONTRACTS.md)：数据模型、Port 接口与 Web API 契约。
