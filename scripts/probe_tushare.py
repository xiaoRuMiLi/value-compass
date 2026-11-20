"""tushare 接口验证脚本（只读 .env 中的 TUSHARE_TOKEN，不打印 token）。

用途：在改造 provider 之前，先用真实 token 探测各关键接口的可用性与权限，
确认账号积分档位，据此决定 tushare 能替代哪些数据源。

用法：
    .venv/Scripts/python.exe scripts/probe_tushare.py
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

API_URL = "https://api.tushare.pro"
ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
DEMO_SYMBOL = "000858.SZ"  # 五粮液，已有本地数据便于对照
DEMO_DATE = "20260930"     # 报告期：2026 三季报

# (接口名, params, fields) —— 覆盖体检核心数据与几个辅助数据
PROBES = [
    ("daily", {"ts_code": DEMO_SYMBOL, "start_date": "20261101", "end_date": "20261120"},
     "ts_code,trade_date,open,high,low,close,vol,amount"),
    ("stock_basic", {"ts_code": DEMO_SYMBOL}, "ts_code,name,industry,market,list_date"),
    ("income", {"ts_code": DEMO_SYMBOL, "period": DEMO_DATE},
     "ts_code,end_date,report_type,revenue,operate_profit,n_income_attr_p"),
    ("balancesheet", {"ts_code": DEMO_SYMBOL, "period": DEMO_DATE},
     "ts_code,end_date,total_assets,total_liab"),
    ("cashflow", {"ts_code": DEMO_SYMBOL, "period": DEMO_DATE},
     "ts_code,end_date,net_cash_flows_oper_act"),
    ("fina_indicator", {"ts_code": DEMO_SYMBOL, "period": DEMO_DATE},
     "ts_code,end_date,roe,netprofit_margin,grossprofit_margin,debt_to_assets"),
    ("forecast", {"ts_code": DEMO_SYMBOL}, "ts_code,ann_date,end_date,type,p_change_min,p_change_max"),
    ("announcements", {"ts_code": DEMO_SYMBOL, "start_date": "20260101", "end_date": "20261120"},
     "ann_date,ts_code,name,title,url"),
]


def load_token() -> str | None:
    """从环境变量或 .env 读取 token（不打印值）。"""
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if token:
        return token
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("TUSHARE_TOKEN") and "=" in line:
                value = line.split("=", 1)[1].strip().strip("'\"")
                if value and not value.startswith("你的"):
                    return value
    return None


def call(api_name: str, token: str, params: dict, fields: str) -> dict:
    payload = {
        "api_name": api_name,
        "token": token,
        "params": params,
        "fields": fields,
    }
    resp = requests.post(API_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    token = load_token()
    if not token:
        print("未找到 TUSHARE_TOKEN：请在 .env 写入 TUSHARE_TOKEN=你的token，或设置同名环境变量。")
        return

    print(f"token 已加载（长度 {len(token)}），开始探测 {len(PROBES)} 个接口...\n")
    summary: dict[str, str] = {}
    for api_name, params, fields in PROBES:
        try:
            result = call(api_name, token, params, fields)
            code = result.get("code")
            msg = result.get("msg")
            data = result.get("data") or {}
            items = data.get("items") or []
            fields_list = data.get("fields") or []
            if code == 0:
                status = "OK"
                detail = f"{len(items)} 行, 字段={','.join(fields_list[:6])}"
                if items:
                    detail += f", 样本={dict(zip(fields_list, items[0]))}"
            elif code == 2002:
                status = "权限不足"
                detail = str(msg)
            else:
                status = f"code={code}"
                detail = str(msg)
            summary[api_name] = status
            print(f"[{api_name}] {status}: {detail}")
        except Exception as exc:  # noqa: BLE001 - 探测脚本需捕获所有异常
            summary[api_name] = "异常"
            print(f"[{api_name}] 异常: {type(exc).__name__}: {exc}")
        time.sleep(0.4)  # tushare 有频率限制

    print("\n=== 汇总 ===")
    for name, status in summary.items():
        print(f"  {name}: {status}")


if __name__ == "__main__":
    main()
