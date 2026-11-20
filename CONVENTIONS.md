# value-compass 开发规范

本文档是命名与代码风格的唯一标准，实现阶段严格遵循。

## 1. 技术栈（已定）

- 后端：Python 3.11 + FastAPI + uvicorn。
- 前端：无 SPA 框架，原生 JavaScript + ECharts（本地内置，离线可用）。
- 存储：SQLite + 本地文件系统。
- 配置：TOML 多文件，按 `default → dev → 专项` 顺序合并。

## 2. 目录与文件命名

- 目录、Python 包、模块：`snake_case`，如 `fundamental/`、`holder_tracking.py`。
- 数据 JSON：`{code}_{category}.json`，如 `600519_financial.json`。
- 输出报告：`{code}_{report}_{YYYYMMDD}.html`。
- 文档存档：`{code}/{year}/{doc_id}.pdf`。

## 3. Python 命名

- 类、dataclass、异常：`PascalCase`，如 `ValuationResult`、`ProviderError`。
- 函数、方法、变量：`snake_case`。
- 常量：`UPPER_SNAKE_CASE`。
- 私有成员：单下划线前缀 `_`。
- Port 接口：`<Capability>Port`，如 `QuotePort`。
- Provider 实现：`<Source><Capability>Provider`，如 `EastmoneyQuoteProvider`。
- 测试：`test_*.py`，测试函数 `test_*`。

## 4. Python 代码规范

- 全量类型注解。
- `models.py` 用 dataclass 定义数据模型，禁止用裸字典跨模块传数据（边界见下）。
- 导入顺序：标准库 → 第三方 → 本地包，分组空行。
- provider 只依赖 `models` 和 `_shared`；analysis 只依赖 `models`。
- 统一抛 `ProviderError` 及其子类：配置/注册用 `ProviderConfigError`、上游请求失败用 `ProviderRequestError`、数据缺失或解析失败用 `ProviderDataError`、能力未实现用 `ProviderNotImplementedError`。这些子类同时兼容原 `KeyError`/`ValueError`/`NotImplementedError`，便于旧调用方渐进迁移。日志统一走 `utils/logging.py`。

### 数据形态边界（"禁止裸字典"的适用范围）

"禁止裸字典"针对的是**业务实体在模块边界间传递**，按分层区分：

| 边界 | 要求 | 说明 |
|---|---|---|
| provider → services | 必须 `models.py` 的 dataclass | 外部数据在此归一化，禁止第三方响应结构/私有 dict 外泄 |
| analysis 入参/出参 | 必须 dataclass | 纯计算只认模型类型 |
| services → web | 允许 `dict[str, Any]` | 聚合结果（如 `analyze_stock`、叙述层）字段多且异构，用 dict 作为 DTO，由 web 层直接序列化 |
| web API 响应 | 必须 JSON 可序列化 | FastAPI 直接返回 dict/list，不再包一层模型 |
| storage 行 → 上层 | 允许 `dict`（`sqlite3.Row`） | `repository.fetch_*` 返回行字典；进入 analysis/provider 前须转成 dataclass |

要点：dict 只允许出现在**聚合出口与存储行**这两类边界；一旦数据要进入 provider/analysis 的领域逻辑，必须转成 dataclass。新增跨模块函数时，返回类型写 `dict[str, Any]` 需说明它是聚合 DTO 还是存储行，否则按 dataclass 处理。

## 5. 前端规范

- 无框架：原生 JS + ECharts，不引入 React/Vue。
- 页面 `pages/*.html`，脚本 `static/js/*.js`，样式 `static/css/*.css`。
- JS 变量/函数 `camelCase`，常量 `UPPER_SNAKE_CASE`；CSS 类名 `kebab-case`。
- `api.js` 统一封装 `fetch`，集中处理错误与 JSON 解析。
- `common.js` 放公共工具（图表初始化、格式化、状态渲染）。
- `pages/` 由 FastAPI 托管，可用 ES modules；`generated/` 必须自包含（内联 JS + 内嵌数据，双击可开）。
- ECharts 从 `static/js/vendor/echarts.min.js` 本地加载，离线可用。

## 6. 后端与 API 规范

- FastAPI 路由用 `kebab-case`。
- 响应统一 JSON；错误统一 `{ "error": { "code", "message" } }`。
- web 层只做转发和页面托管，不写业务计算；计算在 `analysis`。

## 7. Git 规范

- 分支：`main`。
- 提交信息：`type: 描述`，type 取值 `feat` / `fix` / `docs` / `refactor` / `test` / `chore`。
- 每完成一个工作单元提交一次，提交后更新 `PROGRESS.md` 状态。

## 8. 禁止事项

- 禁止 provider 返回第三方类型或私有字典（必须归一化为 `models.py` 的 dataclass）。
- 禁止 analysis 反向 import 具体数据源。
- 禁止把评分、估值、结论落库（均为实时计算）。
- 禁止在 provider/analysis 的领域逻辑内部用裸 dict 传递业务实体（聚合出口与存储行除外，见第 4 节边界表）。
