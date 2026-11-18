"""行业地位：把公司映射到二维坐标（市值对数 × ROE）。"""

from __future__ import annotations

import math

from fundamental.models import PeerPosition


def compute_peer_positions(peers: list[PeerPosition]) -> list[PeerPosition]:
    market_caps = [peer.market_cap for peer in peers if peer.market_cap and peer.market_cap > 0]
    max_log = max(math.log10(cap) for cap in market_caps) if market_caps else None

    results: list[PeerPosition] = []
    for peer in peers:
        x = (
            round(math.log10(peer.market_cap) / max_log, 4)
            if peer.market_cap and peer.market_cap > 0 and max_log
            else None
        )
        results.append(
            PeerPosition(
                symbol=peer.symbol,
                name=peer.name,
                industry=peer.industry,
                market_cap=peer.market_cap,
                revenue=peer.revenue,
                roe=peer.roe,
                gross_margin=peer.gross_margin,
                moat_rating=peer.moat_rating,
                x=x,
                y=peer.roe,
            )
        )
    return results
