"""E50b: stitch per-contract dailies into back-adjusted continuous series, then audit.

Frozen rules (preregistered in log.md, 2026-07-12):
- daily main = contract with the largest *previous-day* open interest (hold);
  rolls are forward-only (new expiry >= current expiry); no look-ahead.
- back-adjustment: multiplicative, factor = new/old close on the day before
  the roll (settle as fallback); earlier history is scaled by the factor.
- strategy/costs/panel identical to E50 (cn_futures_trend.py), zero changes.

Verdict thresholds: full-period Sharpe >= 0.4 -> approve; 0.2-0.4 -> marginal
archive; < 0.2 -> the domestic CTA line closes on free data.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.schema import normalize_ohlcv  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import MarketRules  # noqa: E402
from qtrade.strategies.cta import CTATrend  # noqa: E402
from qtrade.strategies.overlays import VolTarget  # noqa: E402

CONTRACT_DIR = Path(__file__).resolve().parents[1] / "data_store" / "cn_contracts"
MARKET = "cnfutures_adj"
PRODUCTS = ["RB", "I", "J", "M", "Y", "CF", "SR", "TA", "MA", "CU", "AL", "AU", "AG", "RU"]
CN_FUT_RULES = MarketRules(market=MARKET, fee_rate=0.0002, slippage=0.0004,
                           allow_short=True)


def load_product(product: str) -> dict[str, pd.DataFrame]:
    """{contract_code: daily frame indexed by date} for one product."""
    out = {}
    for f in sorted(CONTRACT_DIR.glob(f"{product}[0-9][0-9][0-9][0-9].parquet")):
        if not re.fullmatch(f"{product}\\d{{4}}", f.stem):
            continue  # e.g. RB glob must not swallow RB-prefixed other products
        df = pd.read_parquet(f)
        df["date"] = pd.to_datetime(df["date"])
        df = df.drop_duplicates("date").set_index("date").sort_index()
        out[f.stem] = df
    return out


def expiry_key(code: str) -> int:
    """RB1905 -> 201905 (grid spans 2014-2027, so 14-27 -> 20xx)."""
    yymm = code[-4:]
    return (2000 + int(yymm[:2])) * 100 + int(yymm[2:])


def pick_main(contracts: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Daily main-contract schedule with roll bookkeeping, no look-ahead."""
    oi = pd.DataFrame({c: df["hold"] for c, df in contracts.items()}).sort_index()
    prev_oi = oi.shift(1)
    prev_oi.iloc[0] = oi.iloc[0]  # bootstrap: first day may use same-day OI
    has_bar = oi.notna()

    rows, main = [], None
    for dt in oi.index:
        live = [c for c in oi.columns if has_bar.at[dt, c]]
        if not live:
            continue
        if main is not None and main in live:
            floor = expiry_key(main)
            cands = [c for c in live if expiry_key(c) >= floor]
            best = max(cands, key=lambda c: (prev_oi.at[dt, c] if pd.notna(prev_oi.at[dt, c]) else -1.0, -expiry_key(c)))
            if best != main and (prev_oi.at[dt, best] if pd.notna(prev_oi.at[dt, best]) else -1.0) > (prev_oi.at[dt, main] if pd.notna(prev_oi.at[dt, main]) else -1.0):
                main = best
        else:  # first day, or current main stopped trading: forced pick
            main = max(live, key=lambda c: (prev_oi.at[dt, c] if pd.notna(prev_oi.at[dt, c]) else -1.0, -expiry_key(c)))
        rows.append((dt, main))
    return pd.DataFrame(rows, columns=["date", "contract"]).set_index("date")


def anchor_price(df: pd.DataFrame, dt: pd.Timestamp) -> float | None:
    """Close (settle fallback) on the last bar at or before dt."""
    sub = df.loc[:dt]
    if sub.empty:
        return None
    row = sub.iloc[-1]
    px = row["close"] if pd.notna(row["close"]) and row["close"] > 0 else row["settle"]
    return float(px) if pd.notna(px) and px > 0 else None


def stitch(product: str) -> tuple[pd.DataFrame | None, dict]:
    contracts = load_product(product)
    if not contracts:
        return None, {}
    sched = pick_main(contracts)

    # per-day raw OHLCV of the scheduled main + roll factors
    rolls, factors = [], []  # factors[i] applies to everything before roll date i
    prev_c = None
    for dt, c in sched["contract"].items():
        if prev_c is not None and c != prev_c:
            prior = dt - pd.Timedelta(days=1)
            new_px, old_px = anchor_price(contracts[c], prior), anchor_price(contracts[prev_c], prior)
            f = (new_px / old_px) if (new_px and old_px) else 1.0
            rolls.append(dt)
            factors.append(f)
        prev_c = c

    # cumulative factor per segment, walking backward (latest segment = raw)
    cum, seg_factor = 1.0, {}
    for dt, f in zip(reversed(rolls), reversed(factors)):
        cum *= f
        seg_factor[dt] = cum  # applies to all dates strictly before dt

    bounds = sorted(seg_factor)
    frames = []
    for dt, c in sched["contract"].items():
        row = contracts[c].loc[dt]
        idx = next((b for b in bounds if dt < b), None)
        k = seg_factor[idx] if idx is not None else 1.0
        frames.append((dt, row["open"] * k, row["high"] * k, row["low"] * k,
                       row["close"] * k, row["volume"]))
    out = pd.DataFrame(frames, columns=["date", "open", "high", "low", "close", "volume"])
    out = out.set_index("date").sort_index()
    out.index = (out.index + pd.Timedelta(hours=15)).tz_localize("Asia/Shanghai").tz_convert("UTC")

    gaps = [abs(f - 1.0) for f in factors]
    stats = {"contracts": len(contracts), "rolls": len(rolls),
             "mean_gap_pct": 100 * (sum(gaps) / len(gaps)) if gaps else 0.0,
             "cum_factor": pd.Series(factors).prod() if factors else 1.0}
    return out, stats


def book():
    return VolTarget(CTATrend(h1=21, h2=63, h3=252), target_vol=0.30,
                     vol_window=63, bars_per_year=252)


def main():
    store = BarStore()
    print("=== stitching ===")
    for p in PRODUCTS:
        out, st = stitch(p)
        if out is None or len(out) < 500:
            print(f"{p}: insufficient data, skipped")
            continue
        store.save(normalize_ohlcv(out), MARKET, p, "1d")
        print(f"{p}: {st['contracts']} contracts, {len(out)} days "
              f"({out.index[0].date()} -> {out.index[-1].date()}), "
              f"{st['rolls']} rolls, mean |gap| {st['mean_gap_pct']:.2f}%")

    cov = store.coverage()
    syms = sorted(cov[cov["market"] == MARKET]["symbol"])
    bars = {s: store.load(MARKET, s, "1d") for s in syms}
    start = max(b.index[0] for b in bars.values())
    bars = {s: b[b.index >= start] for s, b in bars.items()}
    print(f"\npanel: {len(bars)} products from {start.date()}")

    res = run_portfolio(book(), bars, CN_FUT_RULES, "1d", allocation="equal",
                        rebalance_eps=0.02, align="ffill")
    print(res[["return_pct", "bench_ew_bh_pct", "edge_pp", "sharpe",
               "max_dd_pct", "trades", "fees"]].to_string())

    print("\n=== year by year ===")
    for year in range(2015, 2027):
        y0 = pd.Timestamp(f"{year}-01-01", tz="UTC")
        y1 = pd.Timestamp(f"{year + 1}-01-01", tz="UTC")
        yb = {s: b[(b.index >= y0 - pd.Timedelta(days=420)) & (b.index < y1)]
              for s, b in bars.items()}
        if min(len(b) for b in yb.values()) < 350:
            continue
        r = run_portfolio(book(), yb, CN_FUT_RULES, "1d", allocation="equal",
                          rebalance_eps=0.02, align="ffill",
                          oos_fraction=0.0001).loc["full"]
        print(f"{year}: ret {r['return_pct']:+7.2f}%  dd {r['max_dd_pct']:5.1f}%  "
              f"sharpe {r['sharpe']:5.2f}")


if __name__ == "__main__":
    main()
