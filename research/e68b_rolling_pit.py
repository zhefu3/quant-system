"""E68-B: run the universe the way you would have had to run it.

E68-A asked a counterfactual (what if we had blindly taken the 2019 top six
and never touched them). This asks an operational question: keep a quarterly
point-in-time top-six, rebalanced on rankings knowable at the time, and see
what survives after the cost of maintaining it.

Two rules carry the honesty here, both frozen in the log before any run:

  - rankings come from dated CoinMarketCap snapshots, cached to disk with
    their source URLs, and take effect on the bar AFTER the quarter ends;
  - turnover is split at the source. At each rebalance we first ask what the
    strategy would have wanted under the OLD universe (q_signal), then apply
    the new membership (q_final). Trading costs are charged in full, but
    reported separately, so "the strategy churned" and "the universe churned"
    can never be confused for one another.

Both arms run through the same light engine below rather than run_portfolio,
because the split needs the weight matrix mid-flight. Read differences, not
levels — the control here is the incumbent six under THIS engine.

Usage: .venv/bin/python research/e68b_rolling_pit.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import CRYPTO_PERP  # noqa: E402
from qtrade.presets import CRYPTO_CORE  # noqa: E402

from e68_pit_universe import INCUMBENT6, START  # noqa: E402

RANKINGS = Path(__file__).resolve().parents[1] / "data_store" / "pit_rankings.json"
UNIVERSE_N = 6  # frozen: same size as the flagship book, not searched
BARS_PER_YEAR = 8760.0
# stablecoins only — an exchange token like LEO stays eligible rather than
# being quietly excluded on a judgment call (it never reaches the top six)
STABLE = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDE", "USD1", "FDUSD",
          "PYUSD", "USDD"}


def load_rankings() -> dict[str, list[str]]:
    """Quarter-end date -> ranked non-stablecoin symbols (archival)."""
    raw = json.loads(RANKINGS.read_text())
    out = {}
    for date, snap in sorted(raw.items()):
        if not snap:
            continue
        syms = [c["symbol"].upper() for c in snap["top"]
                if not c.get("stable") and c["symbol"].upper() not in STABLE]
        out[date] = syms
    return out


def build_panel(store: BarStore, symbols: set[str]) -> dict[str, pd.DataFrame]:
    out = {}
    for s in sorted(symbols):
        pair = f"{s}/USDT"
        try:
            b = store.load("crypto", pair, "1h")
        except Exception:
            continue
        b = b[b.index >= START]
        if len(b) > 720:
            out[pair] = b
    return out


def rolling_universe(index: pd.DatetimeIndex, rankings: dict[str, list[str]],
                     available: set[str]) -> tuple[pd.DataFrame, dict]:
    """Membership matrix (1 = in universe), effective the bar AFTER quarter end.

    A ranked coin we cannot price is NOT silently replaced by the next name
    down — that would rebuild the very survivorship bias this measures. The
    slot is held empty (cash) and counted, so the data gap shows up in the
    result instead of hiding inside it.
    """
    members = pd.DataFrame(0.0, index=index, columns=sorted(available))
    gaps, chosen_log = [], {}
    for date, ranked in rankings.items():
        eff = pd.Timestamp(date, tz="UTC") + pd.Timedelta(hours=1)
        picked = ranked[:UNIVERSE_N]
        have = [f"{s}/USDT" for s in picked if f"{s}/USDT" in available]
        missing = [s for s in picked if f"{s}/USDT" not in available]
        chosen_log[date] = {"picked": picked, "priced": len(have),
                            "missing": missing}
        if missing:
            gaps.append((date, missing))
        rows = index >= eff
        if not rows.any():
            continue
        members.loc[rows, :] = 0.0
        for c in have:
            members.loc[rows, c] = 1.0
    return members, {"gaps": gaps, "log": chosen_log}


def simulate(raw: pd.DataFrame, closes: pd.DataFrame, members: pd.DataFrame,
             eps: float, fee: float, slip: float) -> dict:
    """Light engine with the frozen turnover split.

    Weights are 1/N over the CURRENT members; the split compares, at each bar,
    what membership would have produced under yesterday's roster against
    today's, so a quarter-end trade is attributed to the universe and not to
    the signal.
    """
    n_live = members.sum(axis=1).replace(0, np.nan)
    alloc = members.div(n_live, axis=0).fillna(0.0)
    q_final = (raw * alloc).clip(-1.0, 1.0).fillna(0.0)

    prev_members = members.shift(1).fillna(members.iloc[0])
    n_prev = prev_members.sum(axis=1).replace(0, np.nan)
    q_signal = (raw * prev_members.div(n_prev, axis=0)).clip(-1.0, 1.0).fillna(0.0)

    held = pd.DataFrame(0.0, index=q_final.index, columns=q_final.columns)
    cost_sig = pd.Series(0.0, index=q_final.index)
    cost_uni = pd.Series(0.0, index=q_final.index)
    prev = np.zeros(q_final.shape[1])
    qf, qs = q_final.to_numpy(), q_signal.to_numpy()
    rate = fee + slip
    for i in range(len(q_final)):
        target = qf[i]
        move = np.abs(target - prev)
        act = move > eps                       # frozen rebalance throttle
        new = np.where(act, target, prev)
        d_total = np.abs(new - prev)
        # attribute the executed move: signal first, universe takes the residual
        d_sig = np.minimum(d_total, np.abs(qs[i] - prev))
        cost_sig.iloc[i] = d_sig.sum() * rate
        cost_uni.iloc[i] = (d_total.sum() - d_sig.sum()) * rate
        held.iloc[i] = new
        prev = new

    ret = (held.shift(1) * closes.pct_change()).sum(axis=1)
    net = ret - cost_sig - cost_uni
    sharpe = float(net.mean() / net.std() * np.sqrt(BARS_PER_YEAR)) if net.std() else np.nan
    eq = (1 + net).cumprod()
    return {"sharpe": sharpe,
            "ann_return": float(eq.iloc[-1] ** (BARS_PER_YEAR / len(net)) - 1),
            "max_dd": float((eq / eq.cummax() - 1).min()),
            "cost_signal": float(cost_sig.sum()),
            "cost_universe": float(cost_uni.sum()),
            "turnover_universe_share": float(
                cost_uni.sum() / max(cost_sig.sum() + cost_uni.sum(), 1e-12))}


def main() -> None:
    if not RANKINGS.exists():
        print(f"missing {RANKINGS} — collect archival snapshots first")
        return
    store = BarStore()
    rankings = load_rankings()
    ever = {s for r in rankings.values() for s in r[:UNIVERSE_N]}
    print(f"E68-B · {len(rankings)} quarterly snapshots, "
          f"{len(ever)} distinct coins ever in the PIT top-{UNIVERSE_N}")

    bars = build_panel(store, ever | {s.split('/')[0] for s in INCUMBENT6})
    common = None
    for df in bars.values():
        common = df.index if common is None else common.union(df.index)
    closes = pd.DataFrame({s: df["close"].reindex(common) for s, df in bars.items()}).ffill()
    strat = CRYPTO_CORE.strategy()
    raw = pd.DataFrame({s: strat.target_position(df).reindex(common).ffill().fillna(0.0)
                        for s, df in bars.items()})

    avail = set(closes.columns)
    members, meta = rolling_universe(common, rankings, avail)
    eps, fee, slip = CRYPTO_CORE.rebalance_eps, CRYPTO_PERP.fee_rate, CRYPTO_PERP.slippage

    ctrl_members = pd.DataFrame(0.0, index=common, columns=closes.columns)
    for s in INCUMBENT6:
        if s in ctrl_members:
            ctrl_members[s] = 1.0
    ctrl = simulate(raw, closes, ctrl_members, eps, fee, slip)
    roll = simulate(raw, closes, members, eps, fee, slip)

    print(f"\n  {'arm':28s} {'Sharpe':>8s} {'annRet':>9s} {'maxDD':>8s}")
    for name, r in (("control: incumbent six", ctrl), ("E68-B: quarterly PIT", roll)):
        print(f"  {name:28s} {r['sharpe']:8.3f} {r['ann_return']*100:8.1f}% "
              f"{r['max_dd']*100:7.1f}%")
    print(f"\n  ΔSharpe vs control: {roll['sharpe'] - ctrl['sharpe']:+.3f}")
    print(f"  cost split — signal {roll['cost_signal']*100:.1f}% of equity, "
          f"universe {roll['cost_universe']*100:.1f}% "
          f"({roll['turnover_universe_share']*100:.1f}% of all trading cost)")

    if meta["gaps"]:
        print(f"\n  ⚠ data gaps ({len(meta['gaps'])} quarters had an unpriceable "
              f"top-{UNIVERSE_N} member; slot held in cash, never back-filled "
              f"by the next name down):")
        for date, miss in meta["gaps"]:
            print(f"    {date}: {miss}")


if __name__ == "__main__":
    main()
