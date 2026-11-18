"""大师模型接口与通用研判构造。"""

from __future__ import annotations

from typing import Protocol

from fundamental.models import DividendRecord, FinancialReport, MasterVerdict


class MasterModel(Protocol):
    name: str

    def evaluate(
        self,
        symbol: str,
        financials: list[FinancialReport],
        dividends: list[DividendRecord] | None = None,
    ) -> MasterVerdict: ...


def build_verdict(symbol: str, master: str, signals: list[dict]) -> MasterVerdict:
    passed = sum(1 for signal in signals if signal.get("passed"))
    score = round(passed / len(signals) * 100, 1) if signals else 0.0
    verdict = "买入" if score >= 80 else ("持有" if score >= 60 else "回避")
    narrative = f"{master} 视角：{verdict}（{passed}/{len(signals)} 项达标）"
    return MasterVerdict(symbol=symbol, master=master, score=score, verdict=verdict, signals=signals, narrative=narrative)
