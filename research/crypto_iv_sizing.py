"""E66: implied-vol (DVOL) position sizing vs realized-vol sizing
(prereg 2026-07-19, frozen before this ran — research/log.md).

Frozen spec: crypto_core construction verbatim (CTATrend 96/288/720 +
BollingerRevert 96/z2/720, 50/50 composite, VolTarget tv=0.4); the ONLY
change is VolTarget's sigma: BTC uses DVOL_BTC/100, ETH uses DVOL_ETH/100,
the other eight use DVOL_BTC/100 x (realized_s/realized_BTC) with the same
168h realized window — zero new parameters. DVOL day D usable from D+1
00:00 UTC. Window 2021-03-25 -> 2026-07-18, crypto_perp costs, equal
allocation, eps=0.05 (the book's own settings).

Gate: variant net Sharpe >= core + 0.10 AND variant maxDD <= 1.15 x core.
Attribution pre-commitment (non-gate): 2022H1 gross paths — the thesis
pattern is the implied arm de-levering earlier into the crash.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import BY_NAME  # noqa: E402
from qtrade.presets import _crypto_core_strategy, _UNIVERSE  # noqa: E402
from qtrade.strategies.base import Strategy  # noqa: E402
from qtrade.strategies.cta import CTATrend  # noqa: E402
from qtrade.strategies.composite import Composite  # noqa: E402
from qtrade.strategies.meanrev import BollingerRevert  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DERIBIT = REPO / "data_store" / "deribit"
START, END = "2021-03-25", "2026-07-18"
TV, VOL_WIN, BPY, MAXW = 0.4, 168, 8760, 1.0  # crypto_core's frozen values


def _dvol_hourly(ccy: str, index: pd.DatetimeIndex) -> pd.Series:
    d = pd.read_parquet(DERIBIT / f"dvol_{ccy}_1d.parquet")["close"] / 100.0
    d.index = d.index + pd.Timedelta(days=1)  # day D usable from D+1 00:00 UTC
    return d.reindex(index.union(d.index)).ffill().reindex(index)


def _realized(close: pd.Series) -> pd.Series:
    return close.pct_change().rolling(VOL_WIN).std() * np.sqrt(BPY)


class IVolTarget(Strategy):
    """VolTarget with sigma replaced by the implied-anchored estimate.
    Clipping/fill semantics copied verbatim from strategies/overlays.py."""

    name = "ivol_target"

    def __init__(self, base: Strategy, implied: pd.Series):
        self.base = base
        self.implied = implied

    def target_position(self, bars: pd.DataFrame) -> pd.Series:
        raw = self.base.target_position(bars)
        iv = self.implied.reindex(bars.index)
        scale = (TV / iv).clip(upper=MAXW)
        return (raw * scale).clip(-MAXW, MAXW).fillna(0.0)

    def describe(self) -> str:
        return f"ivol_target({self.base.describe()}, tv={TV})"


class SymbolDispatch(Strategy):
    """Route target_position to a per-symbol strategy via bars.attrs tag."""

    name = "symbol_dispatch"

    def __init__(self, by_symbol: dict[str, Strategy]):
        self.by_symbol = by_symbol

    def target_position(self, bars: pd.DataFrame) -> pd.Series:
        sym = bars.attrs.get("symbol")
        if sym not in self.by_symbol:
            raise RuntimeError(f"bars not tagged with a known symbol: {sym!r} "
                               "(attrs lost in alignment?)")
        return self.by_symbol[sym].target_position(bars)

    def describe(self) -> str:
        return f"dispatch({len(self.by_symbol)} symbols)"


def _stats(returns: pd.Series, bars_per_year: float = 8760) -> tuple[float, float, float]:
    r = returns.dropna()
    ann = (1 + r).prod() ** (bars_per_year / len(r)) - 1
    sharpe = r.mean() / r.std() * np.sqrt(bars_per_year) if r.std() > 0 else 0.0
    eq = (1 + r).cumprod()
    dd = float((eq / eq.cummax() - 1).min())
    return float(ann), float(sharpe), dd


def main() -> None:
    store = BarStore()
    bars = {}
    for s in _UNIVERSE:
        df = store.load("crypto", s, "1h").loc[START:END]
        df.attrs["symbol"] = s
        bars[s] = df
    idx = bars["BTC/USDT"].index
    print(f"panel: {len(bars)} symbols, {idx[0]} -> {idx[-1]} ({len(idx)} bars)")

    dvol_btc = _dvol_hourly("BTC", idx)
    dvol_eth = _dvol_hourly("ETH", idx)
    realized_btc = _realized(bars["BTC/USDT"]["close"])

    def build_variant() -> Strategy:
        by_symbol = {}
        for s in _UNIVERSE:
            if s == "BTC/USDT":
                implied = dvol_btc
            elif s == "ETH/USDT":
                implied = _dvol_hourly("ETH", bars[s].index)
            else:
                ratio = (_realized(bars[s]["close"])
                         / realized_btc.reindex(bars[s].index))
                implied = dvol_btc.reindex(bars[s].index) * ratio
            trend = IVolTarget(CTATrend(h1=96, h2=288, h3=720), implied)
            mr = IVolTarget(BollingerRevert(window=96, entry_z=2.0, side="both",
                                            regime_window=720), implied)
            by_symbol[s] = Composite([(trend, 0.5), (mr, 0.5)])
        return SymbolDispatch(by_symbol)

    rules = BY_NAME["crypto_perp"]
    arms = {"core(realized)": _crypto_core_strategy(), "variant(implied)": build_variant()}
    results = {}
    for label, strat in arms.items():
        summary, det = run_portfolio(strat, bars, rules, "1h", allocation="equal",
                                     rebalance_eps=0.05, return_details=True)
        w, closes = det["weights"], det["closes"]
        rets = (w.shift(1) * closes.pct_change()).sum(axis=1)
        turnover = (w - w.shift(1)).abs().sum(axis=1)
        net = rets - turnover * (rules.fee_rate + rules.slippage)
        ann, sharpe, dd = _stats(net)
        gross = w.abs().sum(axis=1)
        results[label] = {"ann": ann, "sharpe": sharpe, "dd": dd,
                          "net": net, "gross": gross}
        print(f"\n{label}: 净年化 {ann:+.1%} | Sharpe {sharpe:.2f} | "
              f"maxDD {dd:.1%} | 平均 gross {gross.mean():.0%}")

    core, var = results["core(realized)"], results["variant(implied)"]
    print("\n=== E66 门槛(冻结) ===")
    d_sharpe = var["sharpe"] - core["sharpe"]
    dd_ok = var["dd"] >= core["dd"] * 1.15  # dd are negative: >= means shallower than 1.15x
    print(f"ΔSharpe = {d_sharpe:+.2f} (需 ≥ +0.10) | "
          f"maxDD {var['dd']:.1%} vs 上限 {core['dd'] * 1.15:.1%}")
    if d_sharpe >= 0.10 and dd_ok:
        print("判决: ✅ 过 → 分支(a) 平行 A/B 纸面账 crypto_core_iv")
    elif d_sharpe <= -0.10 or not dd_ok:
        print("判决: ❌ 差 → 分支(c) 关闭")
    else:
        print("判决: 边缘 → 分支(b) 档存'隐波标定无增量', 不立账")

    print("\n=== 归因预承诺: 2022H1 月均 gross(崩盘段降杠杆形态) ===")
    for label, r in results.items():
        seg = r["gross"].loc["2022-01":"2022-06"].resample("ME").mean()
        print(f"{label}: " + " ".join(f"{ts:%m月}{v:.0%}" for ts, v in seg.items()))
    # yearly net for the record
    print("\n逐年净收益%:")
    for label, r in results.items():
        y = r["net"].groupby(r["net"].index.year).apply(lambda x: ((1 + x).prod() - 1) * 100)
        print(f"{label}: " + str({int(k): round(v, 1) for k, v in y.items()}))


if __name__ == "__main__":
    main()
