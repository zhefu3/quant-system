"""Domestic futures: per-contract store + back-adjusted continuous series.

Stitching rules are the E50b-frozen ones (research/log.md 2026-07-12):
- daily main = contract with the largest *previous-day* open interest (hold);
  rolls are forward-only (new expiry >= current expiry); no look-ahead.
- back-adjustment: multiplicative, factor = new/old close on the day before
  the roll (settle as fallback); earlier history is scaled by the factor.

The same code serves research (full-history audit) and the live paper path
(incremental refresh + stitch) — one definition, two consumers.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pandas as pd

CONTRACT_DIR = Path(__file__).resolve().parents[2] / "data_store" / "cn_contracts"
PRODUCTS = ["RB", "I", "J", "M", "Y", "CF", "SR", "TA", "MA", "CU", "AL", "AU", "AG", "RU"]
SESSION_CLOSE_SH = "15:05"  # day session close + settle-publication slack
LISTING_HORIZON_MONTHS = 16  # how far ahead new contract codes are probed


def expiry_key(code: str) -> int:
    """RB1905 -> 201905 (store spans 2014+, so yy maps to 20yy)."""
    yymm = code[-4:]
    return (2000 + int(yymm[:2])) * 100 + int(yymm[2:])


def load_product(product: str) -> dict[str, pd.DataFrame]:
    """{contract_code: daily frame indexed by date} for one product."""
    out = {}
    for f in sorted(CONTRACT_DIR.glob(f"{product}[0-9][0-9][0-9][0-9].parquet")):
        if not re.fullmatch(f"{product}\\d{{4}}", f.stem):
            continue  # e.g. product "M" must not swallow "MA" files
        df = pd.read_parquet(f)
        df["date"] = pd.to_datetime(df["date"])
        df = df.drop_duplicates("date").set_index("date").sort_index()
        out[f.stem] = df
    return out


def _oi_or_neg(prev_oi: pd.DataFrame, dt: pd.Timestamp, c: str) -> float:
    v = prev_oi.at[dt, c]
    return float(v) if pd.notna(v) else -1.0


def pick_main(contracts: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Daily main-contract schedule, decided on previous-day OI (no look-ahead)."""
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
            best = max(cands, key=lambda c: (_oi_or_neg(prev_oi, dt, c), -expiry_key(c)))
            if best != main and _oi_or_neg(prev_oi, dt, best) > _oi_or_neg(prev_oi, dt, main):
                main = best
        else:  # first day, or current main stopped trading: forced pick
            main = max(live, key=lambda c: (_oi_or_neg(prev_oi, dt, c), -expiry_key(c)))
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
    """Back-adjusted continuous OHLCV (close-stamped 15:00 Asia/Shanghai, UTC index)."""
    contracts = load_product(product)
    if not contracts:
        return None, {}
    sched = pick_main(contracts)

    rolls, factors = [], []  # factors[i] applies to everything before roll date i
    prev_c = None
    for dt, c in sched["contract"].items():
        if prev_c is not None and c != prev_c:
            prior = dt - pd.Timedelta(days=1)
            new_px = anchor_price(contracts[c], prior)
            old_px = anchor_price(contracts[prev_c], prior)
            rolls.append(dt)
            factors.append((new_px / old_px) if (new_px and old_px) else 1.0)
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
             "cum_factor": float(pd.Series(factors).prod()) if factors else 1.0}
    return out, stats


# -- incremental refresh (live path) ------------------------------------------

def _last_session_date(now_utc: pd.Timestamp) -> pd.Timestamp:
    """Date of the most recent completed day session (naive Shanghai date)."""
    now_sh = now_utc.tz_convert("Asia/Shanghai")
    cutoff = pd.Timestamp(f"{now_sh.date()} {SESSION_CLOSE_SH}", tz="Asia/Shanghai")  # tz-ok: now_sh converted above
    d = now_sh.normalize().tz_localize(None)
    return d if now_sh >= cutoff else d - pd.Timedelta(days=1)


def _codes_to_refresh(product: str, now_utc: pd.Timestamp) -> list[str]:
    """Unexpired contracts on disk + not-yet-seen codes within listing horizon."""
    cur = now_utc.year * 100 + now_utc.month
    codes = set()
    for f in CONTRACT_DIR.glob(f"{product}[0-9][0-9][0-9][0-9].parquet"):
        if re.fullmatch(f"{product}\\d{{4}}", f.stem) and expiry_key(f.stem) >= cur:
            codes.add(f.stem)
    y, m = now_utc.year, now_utc.month
    for _ in range(LISTING_HORIZON_MONTHS):
        codes.add(f"{product}{y % 100:02d}{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return sorted(codes)


def update_contracts(products: list[str] | None = None, sleep_s: float = 0.4) -> int:
    """Refresh per-contract files once per completed session; returns fetch count.

    A marker file gates network access so hourly paper ticks stay cheap: only
    the first tick after a session close hits sina (polite, ~150 calls).
    """
    import akshare as ak

    products = products or PRODUCTS
    CONTRACT_DIR.mkdir(parents=True, exist_ok=True)
    marker = CONTRACT_DIR / ".last_refresh"
    session = _last_session_date(pd.Timestamp.now("UTC"))
    if marker.exists() and marker.read_text().strip() >= str(session.date()):  # tz-ok: naive CN wall by construction
        return 0

    fetched = 0
    for p in products:
        for code in _codes_to_refresh(p, pd.Timestamp.now("UTC")):
            try:
                df = ak.futures_zh_daily_sina(symbol=code)
                if df is not None and len(df) > 0:
                    df.to_parquet(CONTRACT_DIR / f"{code}.parquet")
                    fetched += 1
            except Exception:  # noqa: BLE001 — not yet listed: normal
                pass
            time.sleep(sleep_s)
    marker.write_text(str(session.date()))  # tz-ok: naive CN wall by construction
    return fetched
