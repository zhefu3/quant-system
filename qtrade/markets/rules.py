"""Per-market trading rules: costs and constraints the backtest must respect.

The engine never accepts zero costs — an optimistic backtest is worse than none.
A-share/US packs are placeholders until those adapters land; the crypto pack is
calibrated to taker fees on major venues plus a conservative slippage guess.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MarketRules:
    market: str
    fee_rate: float        # per-side, as fraction of notional
    slippage: float        # per-side, as fraction of price
    min_hold_bars: int = 0  # e.g. A-share T+1 expressed in bars at the strategy timeframe
    allow_short: bool = False

    def __post_init__(self):
        if self.fee_rate <= 0 or self.slippage <= 0:
            raise ValueError("costs must be positive: a free-lunch backtest is disallowed")


CRYPTO = MarketRules(market="crypto", fee_rate=0.001, slippage=0.0005)

# 永续合约: taker ~0.05%, 可做空。资金费率暂未建模 —— 长期持仓的结果会偏乐观,
# 等策略进入候选阶段再把 funding 数据接进来修正。
CRYPTO_PERP = MarketRules(market="crypto_perp", fee_rate=0.0005, slippage=0.0005, allow_short=True)

# A股: 佣金~万2.5 + 卖出印花税 0.05% (2023 调降后) → 单边近似 0.0005;
# 涨跌停/T+1 由 min_hold_bars 与信号后处理近似, 待 A股适配器落地时细化。
ASHARE = MarketRules(market="ashare", fee_rate=0.0008, slippage=0.001, min_hold_bars=1)

US = MarketRules(market="us", fee_rate=0.0005, slippage=0.0005, allow_short=True)

BY_NAME = {r.market: r for r in (CRYPTO, CRYPTO_PERP, ASHARE, US)}
