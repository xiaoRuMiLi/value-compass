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
- `models.py` 用 dataclass 定义数据模型，禁止用裸字典跨模块传数据。
- 导入顺序：标准库 → 第三方 → 本地包，分组空行。
- provider 只依赖 `models` 和 `_shared`；analysis 只依赖 `models`。
- 统一抛 `ProviderError`，日志统一走 `utils/logging.py`。

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

- 禁止 provider 返回第三方类型或私有字典。
- 禁止 analysis 反向 import 具体数据源。
- 禁止把评分、估值、结论落库（均为实时计算）。
