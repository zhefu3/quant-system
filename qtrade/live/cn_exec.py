"""A-share execution realism for paper books (QMT-shaped, 2026-07-21).

The E61 prereg recorded the simplification honestly: suspensions and price
limits were not simulated. This layer closes that gap BEFORE the first real
monthly rebalance (Aug), so the forward record never contains a fill that
the real market would have refused:

- suspended (no bar today)            -> cannot trade, position frozen
- limit-up 一字板 (high==low at +limit) -> buys refused
- limit-down 一字板 (low==high at -limit)-> sells refused

Refused orders go to a pending queue retried on each daily tick until they
fill or the next monthly decision replaces them. Every attempt is logged to
exec_log.csv (the paper twin of the live TCA stream: intended price, fill
price, status, retry count).

Detection runs on 后复权 series, so limits are checked as ratios with a
tolerance for raw-scale price rounding (round(prev*1.1, 2) happens on raw
prices; on hfq data the exact tick is unrecoverable, ±0.6% tolerance
captures it). 一字板 comparison is high==low, adjustment-invariant.
The gross shadow stays frictionless by design — it measures signal decay;
execution friction belongs to the cost side of the ledger.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

LIMIT_TOL = 0.006  # raw-scale rounding blur when checking on adjusted data


def limit_pct(code: str) -> float:
    """Daily price band by board: 20% for 创业板(300/301)/科创板(688/689),
    10% for main boards. HS300/CB universes hold no ST names."""
    head = code.split(".")[0][:3]
    return 0.20 if head in ("300", "301", "688", "689") else 0.10


def fill_verdict(side: str, code: str, bar_today: pd.Series | None,
                 prev_close: float | None) -> str:
    """'fill' | 'suspended' | 'limit_locked' for one intended order today."""
    if bar_today is None:
        return "suspended"
    if prev_close is None or prev_close <= 0:
        return "fill"  # no basis to judge a lock; do not over-refuse
    hi, lo = float(bar_today["high"]), float(bar_today["low"])
    if abs(hi / lo - 1) > 1e-6:
        return "fill"  # traded through a range — not a one-way board
    lim = limit_pct(code)
    chg = hi / float(prev_close) - 1
    if side == "buy" and chg >= lim - LIMIT_TOL:
        return "limit_locked"
    if side == "sell" and chg <= -(lim - LIMIT_TOL):
        return "limit_locked"
    return "fill"


def pick_asof(df, ref_day: str):
    """(bar at ref_day, prev_close) from a daily frame, or (None, last close)
    when the series stops short of ref_day — the suspension signal.

    ref_day is the market's latest COMPLETED trade day (the benchmark's last
    bar date), NOT the wall clock: daily bars publish hours after the close,
    and decision ticks can fire on weekends (2026-08-01 is a Saturday). The
    2026-07-21 rehearsal caught the wall-clock version marking 50/50 CSI300
    names "suspended" at 16:10 on an ordinary Tuesday."""
    if df is None or not len(df):
        return None, None
    days = df.index.tz_convert("Asia/Shanghai").strftime("%Y-%m-%d")
    last_day = days[-1]
    if last_day < ref_day:
        return None, float(df["close"].iloc[-1])  # behind the market: suspended
    if last_day == ref_day:
        prev = float(df["close"].iloc[-2]) if len(df) >= 2 else None
        return df.iloc[-1], prev
    # series runs past ref_day (shouldn't happen live): locate ref_day's row
    hits = [i for i, d in enumerate(days) if d == ref_day]
    if not hits:
        older = df[[d < ref_day for d in days]]
        return None, (float(older["close"].iloc[-1]) if len(older) else None)
    i = hits[-1]
    prev = float(df["close"].iloc[i - 1]) if i >= 1 else None
    return df.iloc[i], prev


def bar_asof(store, market: str, code: str, ref_day: str):
    try:
        df = store.load(market, code, "1d")
    except FileNotFoundError:
        return None, None
    return pick_asof(df, ref_day)


def pool_ref_day(store, market: str, codes) -> str | None:
    """The latest bar date across the codes in play — the book's own
    self-consistent 'as of' day.

    NOT the benchmark index and NOT the wall clock: the second rehearsal
    (2026-07-21) showed the index publishing before adj_factor lands, so a
    bench reference marks the entire stock universe suspended for a few
    hours every afternoon. Judged against the pool's own max date, verdicts
    and fill prices (_latest closes) always describe the same market state;
    a single name lagging the pool is a genuine suspension."""
    days = []
    for c in codes:
        try:
            df = store.load(market, c, "1d")
        except FileNotFoundError:
            continue
        if len(df):
            days.append(df.index[-1].tz_convert("Asia/Shanghai").strftime("%Y-%m-%d"))
    return max(days) if days else None


def log_attempt(book_dir: Path, ts: str, code: str, side: str, status: str,
                ref_px: float | None, fill_px: float | None, retry: int) -> None:
    f = Path(book_dir) / "exec_log.csv"
    pd.DataFrame([{"ts": ts, "symbol": code, "side": side, "status": status,
                   "ref_px": ref_px, "fill_px": fill_px, "retry": retry}]).to_csv(
        f, mode="a", header=not f.exists(), index=False)


def split_executable(desired: dict[str, float], current_pos: dict[str, float],
                     verdicts: dict[str, str]) -> tuple[set[str], dict[str, float]]:
    """(codes to freeze this tick, pending net targets to retry later).
    Callers pass the FULL desired weights to rebalance() along with the
    frozen set: the net leg skips frozen codes, the gross shadow still
    trades them (see ashare_ml.rebalance)."""
    frozen: set[str] = set()
    pending: dict[str, float] = {}
    for code in set(desired) | set(current_pos):
        v = verdicts.get(code, "fill")
        if v == "fill":
            continue
        frozen.add(code)
        tgt = desired.get(code, 0.0)
        cur = current_pos.get(code, 0.0)
        if abs(tgt) > 1e-12 or abs(cur) > 1e-12:
            pending[code] = tgt
    return frozen, pending
