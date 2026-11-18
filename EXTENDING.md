# value-compass 扩展指南

本文档是修改/扩展本项目的操作手册，供后续 agent 或协作者参考。动手前先读
`ARCHITECTURE.md`、`CONTRACTS.md`、`CONVENTIONS.md`。

## 1. 结构总览

```text
fundamental/
├── models.py              全部枚举 + dataclass，数据唯一出口
├── config.py              配置加载
├── providers/             17 个能力，每个 = port.py + 实现
│   ├── registry.py        能力 → 数据源 注册表
│   └── _shared/           http / cache / rate_limit / convert
├── services/
│   ├── sync.py            取数 → 入库（全能力）
│   └── analyze.py         读库 → 分析 → 汇总结果
├── analysis/              纯计算（valuation/ 估值、masters/ 大师）
├── storage/               schema.sql / db.py / repository.py
├── web/                   app.py / routes/api.py
└── utils/                 symbols / paths / logging

pages/                     前端页面 + static/js/css + vendor/echarts
config/                    default/dev/providers/official + masters/
scripts/                   fetch_data.py / serve.py
```

## 2. 依赖方向铁律

```text
providers ─┐
storage ───┼──→ models ←── analysis
           │
web ───────┴──→ services ──→ providers / storage / analysis
```

1. provider 只依赖 `models` 和 `_shared`，返回 `models` 里的 dataclass。
2. analysis 只依赖 `models`，不 import 具体数据源。
3. 横向调用只经过 `services` 或 `registry`。

## 3. 通用约定

- 命名：目录/模块 `snake_case`，类 `PascalCase`，Port 名 `<Capability>Port`，实现名 `<Source><Capability>Provider`。
- 每完成一个工作单元：更新 `PROGRESS.md` 状态 + git 提交（`feat:`/`fix:`/`docs:` 等）。
- 本环境 git 推送需先 `Remove-Item Env:GIT_SSH_COMMAND`，再设 `GIT_SSH=C:\Windows\System32\OpenSSH\ssh.exe`。

## 4. 扩展操作

### 4.1 换数据源 / 加备用源

在能力目录加一个实现文件，注册，再改配置即可，上层零改动。

```python
# fundamental/providers/quote/sina.py
from fundamental.providers.registry import register

@register("quote", "sina")
class SinaQuoteProvider:
    def fetch_snapshot(self, symbol): ...
    def fetch_kline(self, symbol, start, end): ...
    def fetch_valuations(self, symbol, start=None, end=None): ...
```

```toml
# config/providers.toml
[providers.quote]
primary = "sina"        # 或 primary = "eastmoney" + fallback = "sina"
```

### 4.2 加一个全新的数据能力

1. `fundamental/providers/<capability>/` 建 `port.py`（Protocol）+ `<source>.py`（实现，`@register`）。
2. `fundamental/models.py` 加对应 dataclass。
3. `SCHEMA.md` + `fundamental/storage/schema.sql` 加表。
4. `fundamental/services/sync.py` 加写入段。
5. `config/providers.toml` 加 `[providers.<capability>] primary = ...`。

### 4.3 加一个分析模块

1. `fundamental/analysis/<module>.py` 加纯函数（输入 models，输出结果 dataclass）。
2. `fundamental/models.py` 加结果 dataclass（若还没有）。
3. `fundamental/services/analyze.py` 的 `analyze_stock` 里挂进返回字典。
4. 页面需要时在 `fundamental/web/routes/api.py` 加端点。

### 4.4 加估值方法 / 加大师

估值：

1. `fundamental/analysis/valuation/<method>.py` 加函数，返回 `ValuationResult`。
2. `fundamental/analysis/valuation/__init__.py` 导出。
3. `fundamental/services/analyze.py` 的 `valuations_result` 列表里追加调用。

大师：

1. `fundamental/analysis/masters/<master>.py` 加模型类（`name` + `evaluate`，返回 `MasterVerdict`）。
2. `config/masters/<master>.toml` 加阈值。
3. `fundamental/analysis/masters/__init__.py` 导出。
4. `fundamental/services/analyze.py` 的 `masters` 列表里追加。

### 4.5 加一个页面

1. `pages/<name>.html` + `pages/static/js/<name>.js`。
2. `fundamental/web/app.py` 加路由（返回 FileResponse）。
3. 数据不足时 `fundamental/web/routes/api.py` 加端点。

### 4.6 加一张表

同步改四处：`SCHEMA.md`、`fundamental/storage/schema.sql`、`fundamental/models.py`、`fundamental/services/sync.py`（写入）。
注意 `db.py` 用 `SCHEMA_VERSION` 做迁移，加表后要 bump 版本号并处理旧库迁移。

## 5. 快速清单

- 改数据源：只动 provider 文件 + `providers.toml`。
- 改分析：只动 `analysis/` + `models.py` + `analyze.py`。
- 改展示：只动 `pages/` + `web/`。
- 改存储：四处同步（SCHEMA/schema.sql/models/sync）。
- 每步后：验证 + 更新 `PROGRESS.md` + 提交推送。
