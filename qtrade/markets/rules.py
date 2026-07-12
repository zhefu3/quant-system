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
    allow_short: bool = False
    t_plus_one: bool = False  # position opened today cannot close until next trading day
    tz: str = "UTC"           # exchange timezone, used for trading-date boundaries

    def __post_init__(self):
        if self.fee_rate <= 0 or self.slippage <= 0:
            raise ValueError("costs must be positive: a free-lunch backtest is disallowed")


CRYPTO = MarketRules(market="crypto", fee_rate=0.001, slippage=0.0005)

# 永续合约: taker ~0.05%, 可做空。资金费率暂未建模 —— 长期持仓的结果会偏乐观,
# 等策略进入候选阶段再把 funding 数据接进来修正。
CRYPTO_PERP = MarketRules(market="crypto_perp", fee_rate=0.0005, slippage=0.0005, allow_short=True)

# A股: 佣金~万2.5 双边 + 卖出印花税 0.05% → 对称化近似为单边 0.0008;
# T+1 由引擎在执行层强制(当日开仓禁止当日平仓); 涨跌停暂未建模。
ASHARE = MarketRules(
    market="ashare", fee_rate=0.0008, slippage=0.001, t_plus_one=True, tz="Asia/Shanghai"
)

US = MarketRules(market="us", fee_rate=0.0005, slippage=0.0005, allow_short=True)

# 国内商品期货: 手续费 ~0.02% + 滑点 0.04%/边(保守), 可双向。E50/E50b 同款。
CNFUTURES = MarketRules(
    market="cnfutures", fee_rate=0.0002, slippage=0.0004, allow_short=True, tz="Asia/Shanghai"
)

BY_NAME = {r.market: r for r in (CRYPTO, CRYPTO_PERP, ASHARE, US, CNFUTURES)}
