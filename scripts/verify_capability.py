#!/usr/bin/env python3
"""财报深度体检「能力基线」：录一次基线，之后每次改动后对比，防止现有能力降级。

用法：
    python scripts/verify_capability.py --record     # 把当前线上报告录为基线
    python scripts/verify_capability.py              # 与基线对比，任一能力项下降则退出码 1
    python scripts/verify_capability.py --base-url http://127.0.0.1:8000

设计原则：只允许「变多 / 变好」，不允许「变少」。新增检查项会让指标上升，
不算降级；原有信号 id 消失、剖析条数减少、原文引用丢失、事件点减少、LLM
退化成模板、关键原文证据短语消失，都判为降级。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "baselines" / "capability_baseline.json"
SYMBOLS = ("300308.SZ", "00700.HK")

# 两只在人工核对中确认过的「财报原文证据」：一旦总纲不再引用，说明原文取证退化。
# 用正则而非精确字符串：LLM 会写「1066亿元 / 1,066 亿」「视同处置 / 視同處置」
# 「汇兑 / 匯兌」，这些只是简繁与千分位差异，不是能力退化。
KEY_PATTERNS = {
    "300308.SZ": re.compile(r"13[.,]?87\s*亿|[汇匯]兑|H\s*股募资|公允价值变动损失"),
    "00700.HK": re.compile(
        r"1,?066\s*亿|1066\s*亿|[視视]同[處处]置\s*美[團团]|溢利其他项目|营业利润以下"
    ),
}

# 这些指标只能 >= 基线（ratio 为允许的最低比例）
NON_DECREASING = {
    "essence_chars": 0.8,
    "signal_count": 1.0,
    "analysis_count": 1.0,
    "analysis_complete": 1.0,
    "mining_segments": 1.0,
    "mining_findings": 1.0,
    # LLM 通读「额外」给几个信号配上片段，受单次输出波动影响；
    # 真正的原文取证能力看两路并集（下面 evidence_covered_signals，要求 100%）。
    "mining_evidence_signals": 0.8,
    "evidence_covered_signals": 1.0,
    "official_explanations": 1.0,
    "coverage_performed": 1.0,
    "marker_points": 1.0,
}


def _get_json(base: str, path: str, timeout: int = 180):
    with urllib.request.urlopen(base + path, timeout=timeout) as resp:
        return json.load(resp)


def _marker_points(fx: dict, kl: list) -> int:
    """优先用真实前端脚本算事件点数，失败则退回同口径的 Python 估算。"""
    js = (
        "const fs=require('fs'),vm=require('vm');"
        "const ctx={window:{VC:{}},console:console};vm.createContext(ctx);"
        "vm.runInContext(fs.readFileSync('pages/static/js/forensics.js','utf8'),ctx);"
        "const f=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));"
        "const k=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));"
        "const r=ctx.window.VC.buildForensicMarkers(f,k);"
        "console.log(JSON.stringify({points:r.points.length,counts:r.counts}));"
    )
    try:
        with tempfile.TemporaryDirectory() as td:
            fx_path = Path(td) / "fx.json"
            kl_path = Path(td) / "kline.json"
            fx_path.write_text(json.dumps(fx, ensure_ascii=False), encoding="utf-8")
            kl_path.write_text(json.dumps(kl, ensure_ascii=False), encoding="utf-8")
            out = subprocess.check_output(
                ["node", "-e", js, str(fx_path), str(kl_path)],
                cwd=str(ROOT), text=True, timeout=90,
            )
            return int(json.loads(out.strip().splitlines()[-1])["points"])
    except Exception:  # noqa: BLE001 - 基线校验本身不能因边缘依赖而中断
        import re
        dated = sum(
            1 for s in (fx.get("scan") or {}).get("signals", [])
            if re.search(r"(19|20)\d{2}", str(s.get("period") or ""))
        )
        return dated + len((fx.get("report_mining") or {}).get("new_findings") or [])


def collect(base: str, symbol: str) -> dict:
    fx = _get_json(base, f"/api/stock/{symbol}/financial-forensics")
    kl = _get_json(base, f"/api/stock/{symbol}/kline")
    scan = fx.get("scan") or {}
    signals = scan.get("signals") or []
    analyses = fx.get("analyses") or []
    mining = fx.get("report_mining") or {}
    coverage = fx.get("coverage") or {}
    verdict = fx.get("verdict") or {}
    essence = str(verdict.get("essence") or "")
    return {
        "source": fx.get("source"),
        "forensics_version": fx.get("forensics_version"),
        "verdict_level": verdict.get("level"),
        "essence_chars": len(essence),
        "key_phrase_hit": bool(KEY_PATTERNS[symbol].search(essence)),
        "key_phrase_sample": (
            KEY_PATTERNS[symbol].search(essence).group(0)
            if KEY_PATTERNS[symbol].search(essence) else ""
        ),
        "essence_cites_source": bool(re.search(r"原文|据公司|财报", essence)),
        "signal_count": len(signals),
        "signal_ids": [s.get("id") for s in signals],
        # 信号 id 是生成顺序的产物：新增一条检查会让后续全部重新编号。
        # 判定「原有风险点是否消失」必须按标题，否则会把编号变化误报成能力降级。
        "signal_titles": [s.get("title") for s in signals],
        "analysis_count": len(analyses),
        "analysis_complete": sum(
            1 for a in analyses
            if all(a.get(k) for k in ("substance", "benign", "judgement"))
        ),
        "mining_segments": int(mining.get("segments") or 0),
        "mining_findings": len(mining.get("new_findings") or []),
        "mining_evidence_signals": len(mining.get("signal_evidence") or {}),
        # 每个风险点是否都能从财报原文找到印证：LLM 通读 + 确定性关键词检索两路并集。
        # 单独看 signal_evidence 会把「模型这次恰好少配一条」误报成取证链路退化。
        "evidence_covered_signals": len(
            set(mining.get("signal_evidence") or {})
            | set(fx.get("official_explanations") or {})
        ),
        "official_explanations": len(fx.get("official_explanations") or {}),
        "source_guard": (fx.get("source_guard") or {}).get("status"),
        "coverage_performed": len(coverage.get("performed") or []),
        "marker_points": _marker_points(fx, kl),
    }


def compare(symbol: str, base_data: dict, now: dict) -> list[str]:
    problems: list[str] = []
    for key, ratio in NON_DECREASING.items():
        old, new = base_data.get(key, 0), now.get(key, 0)
        if new < old * ratio:
            problems.append(f"{key}: {old} -> {new} (降幅超过允许范围)")
    if now.get("source") != "llm":
        problems.append(f"source: llm -> {now.get('source')} (LLM 解读退化)")
    if not now.get("key_phrase_hit"):
        problems.append("总纲不再引用关键财报原文证据 (原文取证链路退化)")
    if not now.get("essence_cites_source"):
        problems.append("总纲未标注财报原文出处")
    if now.get("source_guard") not in ("ok", "repaired"):
        problems.append(f"source_guard: {now.get('source_guard')}")
    # 优先按标题判定；旧基线若只有 id，退回 id 判定并提示口径较脆
    base_titles = base_data.get("signal_titles")
    if base_titles:
        missing = set(base_titles) - set(now.get("signal_titles") or [])
        label = "原有风险点（按标题）消失"
    else:
        missing = set(base_data.get("signal_ids") or []) - set(now.get("signal_ids") or [])
        label = "原有风险信号 id 消失"
    if missing:
        problems.append(f"{label}: " + ", ".join(sorted(str(x) for x in missing)))
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--record", action="store_true", help="把当前状态录为基线")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    current = {}
    for symbol in SYMBOLS:
        try:
            current[symbol] = collect(base, symbol)
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {symbol} 采集失败: {exc}", file=sys.stderr)
            return 2

    if args.record:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(
            json.dumps({"symbols": current}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[OK] 基线已写入 {BASELINE}")
        for symbol, data in current.items():
            print(f"  {symbol}: signals={data['signal_count']} "
                  f"analyses={data['analysis_count']} findings={data['mining_findings']} "
                  f"points={data['marker_points']} essence={data['essence_chars']}字")
        return 0

    if not BASELINE.exists():
        print(f"[FAIL] 缺少基线文件 {BASELINE}，请先 --record", file=sys.stderr)
        return 2
    saved = json.loads(BASELINE.read_text(encoding="utf-8"))["symbols"]

    failed = False
    for symbol in SYMBOLS:
        problems = compare(symbol, saved[symbol], current[symbol])
        status = "FAIL" if problems else "PASS"
        print(f"[{status}] {symbol}")
        for key in NON_DECREASING:
            print(f"    {key:26s} {saved[symbol][key]:>5} -> {current[symbol][key]:>5}")
        print(f"    source / guard / phrase     "
              f"{current[symbol]['source']} / {current[symbol]['source_guard']} / "
              f"{current[symbol]['key_phrase_hit']}")
        for p in problems:
            print(f"    !! {p}")
        failed = failed or bool(problems)
    print("\n结论：" + ("存在能力降级，请勿合入" if failed else "无能力降级"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
