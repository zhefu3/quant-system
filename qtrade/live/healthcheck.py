"""health: one command that says whether the machine is trustworthy right now.

Checks every stored bar series (integrity + freshness), the per-contract
futures store, every paper book (heartbeat freshness + halt markers), live
executor flags (HALTED/RECONCILE), and a cross-source spot check (two venues
must agree on yesterday's close — single-source data is trusted only as far
as a second source confirms it). WARN lines are actionable; a clean run
prints only PASS/INFO. With alert=True, WARNs push a macOS notification
(de-duplicated in qtrade/live/alerts.py).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..data.store import BarStore
from ..presets import PRESETS
from .paper import DEFAULT_ROOT
from .timeouts import call_with_timeout

# freshness tolerance per bar timeframe (calendar quirks: weekends, holidays)
STALE_AFTER = {"1h": pd.Timedelta(hours=12), "4h": pd.Timedelta(hours=24),
               "1d": pd.Timedelta(days=6)}
RECENT = 200  # bars inspected for NaN/gap checks
XSOURCE_TOL = 0.01  # two venues disagreeing >1% on a daily close is a finding

LIVE_ROOT = Path(__file__).resolve().parents[2] / "outputs" / "live"


def _check_series(store: BarStore, market: str, symbol: str, tf: str,
                  now: pd.Timestamp) -> tuple[list[str], bool]:
    """(integrity issues, is_stale). Staleness is informational for research
    archives — the live paths fetch fresh data themselves — so it must never
    drown integrity WARNs in alert fatigue."""
    issues = []
    df = store.load(market, symbol, tf)
    tail = df.tail(RECENT)
    if not df.index.is_monotonic_increasing:
        issues.append("index not monotonic")
    if df.index.has_duplicates:
        issues.append(f"{int(df.index.duplicated().sum())} duplicate timestamps")
    nan_frac = float(tail.isna().any(axis=1).mean())
    if nan_frac > 0:
        issues.append(f"NaN rows in last {len(tail)}: {nan_frac:.1%}")
    if (tail["close"] <= 0).any():
        issues.append("non-positive closes")
    limit = STALE_AFTER.get(tf)
    stale = limit is not None and (now - df.index[-1]) > limit
    return issues, stale


def _cross_source_checks() -> tuple[list[str], list[str]]:
    """(findings, info lines). A venue being unreachable is INFO — only an
    actual disagreement between two live sources is a WARN. Every call is
    soft-timeout wrapped; this section may never hang the health run."""
    findings, infos = [], []

    # crypto: yesterday's completed daily close, binance vs okx. When only one
    # venue is reachable (geo-blocks), fall back to venue vs our stored series
    # — the check degrades, it does not disappear.
    try:
        import ccxt
        store = BarStore()
        for sym in ("BTC/USDT", "ETH/USDT"):
            closes, day = {}, None
            for venue in ("binance", "okx"):
                try:
                    ex = getattr(ccxt, venue)({"timeout": 15000})
                    o = call_with_timeout(ex.fetch_ohlcv, 30.0, sym, "1d", limit=3)
                    closes[venue] = float(o[-2][4])  # last COMPLETED day
                    day = pd.Timestamp(o[-2][0], unit="ms", tz="UTC").date()
                except Exception as e:  # noqa: BLE001 — venue down is not a finding
                    infos.append(f"{sym} {venue}: unavailable ({type(e).__name__})")
            if len(closes) == 2:
                div = abs(closes["binance"] / closes["okx"] - 1)
                if div > XSOURCE_TOL:
                    findings.append(f"xsource {sym}: binance vs okx daily close "
                                    f"diverge {div:.2%}")
                else:
                    infos.append(f"{sym}: binance/okx agree ({div:.3%})")
            elif len(closes) == 1 and day is not None:
                venue, px = next(iter(closes.items()))
                try:
                    bars = store.load("crypto", sym, "1h")
                    day_bars = bars[bars.index.date == day]
                    if len(day_bars):
                        stored = float(day_bars["close"].iloc[-1])
                        div = abs(px / stored - 1)
                        if div > XSOURCE_TOL:
                            findings.append(f"xsource {sym}: {venue} vs stored "
                                            f"close diverge {div:.2%} ({day})")
                        else:
                            infos.append(f"{sym}: {venue}/stored agree ({div:.3%})")
                    else:
                        infos.append(f"{sym}: no stored bars for {day} — skipped")
                except Exception as e:  # noqa: BLE001
                    infos.append(f"{sym} stored fallback failed ({type(e).__name__})")
    except ImportError:
        infos.append("ccxt not importable — crypto cross-check skipped")

    # A-share: last stored daily return (tushare pipeline, hfq) vs akshare hfq
    # return. Aligned by calendar DATE (stored stamps carry an intraday tz
    # offset, akshare uses midnight); compared as returns, not levels —
    # adjustment-base invariant.
    try:
        store = BarStore()
        sym = "000001.SZ"
        df = store.load("ashare_ts", sym, "1d").tail(3)
        if len(df) >= 2:
            day1, day2 = df.index[-2].date(), df.index[-1].date()
            r_store = float(df["close"].iloc[-1] / df["close"].iloc[-2] - 1)

            def _ak_hist():
                import akshare as ak
                return ak.stock_zh_a_hist(symbol=sym.split(".")[0], period="daily",
                                          adjust="hfq",
                                          start_date=(df.index[-1] - pd.Timedelta(days=14)).strftime("%Y%m%d"),
                                          end_date=df.index[-1].strftime("%Y%m%d"))
            try:
                h = call_with_timeout(_ak_hist, 45.0)
                by_date = {pd.Timestamp(d).date(): float(c)
                           for d, c in zip(h["日期"], h["收盘"])}
                if day1 in by_date and day2 in by_date:
                    r_ak = by_date[day2] / by_date[day1] - 1
                    div = abs(r_store - r_ak)
                    if div > 0.005:
                        findings.append(f"xsource {sym}: tushare vs akshare daily "
                                        f"return diverge {div:.2%} "
                                        f"({r_store:+.2%} vs {r_ak:+.2%})")
                    else:
                        infos.append(f"{sym}: tushare/akshare returns agree "
                                     f"({div:.3%})")
                else:
                    infos.append(f"{sym}: akshare missing {day2} — skipped")
            except Exception as e:  # noqa: BLE001 — source down is not a finding
                infos.append(f"{sym} akshare: unavailable ({type(e).__name__})")
    except Exception as e:  # noqa: BLE001
        infos.append(f"ashare cross-check skipped ({type(e).__name__})")

    return findings, infos


def run_health(alert: bool = False) -> int:
    now = pd.Timestamp.now("UTC")
    store = BarStore()
    findings: list[str] = []
    print(f"══════ qtrade health · {now:%Y-%m-%d %H:%M} UTC ══════")

    print("\n--- bar store ---")
    cov = store.coverage()
    stale_count = 0
    n_integrity = 0
    for _, row in cov.iterrows():
        m, s, tf = row["market"], row["symbol"], row["timeframe"]
        try:
            issues, stale = _check_series(store, m, s, tf, now)
        except Exception as e:  # noqa: BLE001 — a broken file IS the finding
            issues, stale = [f"unreadable: {e}"], False
        for msg in issues:
            print(f"WARN  {m}/{s}/{tf}: {msg}")
            findings.append(f"{m}/{s}/{tf}: {msg}")
        n_integrity += len(issues)
        stale_count += stale
    print(f"{'PASS' if n_integrity == 0 else '    '}  {len(cov)} series checked, "
          f"integrity issues above; {stale_count} research archives older than "
          "tolerance (INFO — live paths fetch fresh data)")

    print("\n--- contract store ---")
    from ..data.cn_futures import CONTRACT_DIR
    files = list(CONTRACT_DIR.glob("*.parquet"))
    marker = CONTRACT_DIR / ".last_refresh"
    mark = marker.read_text().strip() if marker.exists() else "never"
    print(f"INFO  {len(files)} contract files, last refresh {mark}")

    print("\n--- paper books ---")
    for name, preset in PRESETS.items():
        d = DEFAULT_ROOT / name
        eq_file = d / "equity.csv"
        if not eq_file.exists():
            print(f"INFO  {name}: no paper record")
            continue
        eq = pd.read_csv(eq_file, parse_dates=["ts"])
        last = pd.Timestamp(eq["ts"].max())
        last = last.tz_localize("UTC") if last.tzinfo is None else last.tz_convert("UTC")
        age_h = (now - last).total_seconds() / 3600
        tol_h = {"1h": 3, "4h": 6, "1d": 30}.get(preset.timeframe, 24)
        if age_h > tol_h:
            print(f"WARN  {name}: heartbeat stale, last mark {age_h:.1f}h ago "
                  f"(is the hourly loop running?)")
            findings.append(f"{name}: heartbeat stale {age_h:.1f}h")
        else:
            print(f"PASS  {name}: last mark {age_h:.1f}h ago, equity {eq['equity'].iloc[-1]:.2f}")
        if (d / "HALTED").exists():
            print(f"WARN  {name}: HALTED marker present — book is flattened, "
                  "needs human review")
            findings.append(f"{name}: HALTED")

    if LIVE_ROOT.exists():
        flagged = [(p.parent.name, p.name) for flag in ("HALTED", "RECONCILE")
                   for p in LIVE_ROOT.glob(f"*/{flag}")]
        if flagged:
            print("\n--- live executor flags ---")
            for book, flag in flagged:
                print(f"WARN  live/{book}: {flag} flag present — sending disabled, "
                      "needs human review")
                findings.append(f"live/{book}: {flag}")

    print("\n--- cross-source spot check ---")
    x_findings, x_infos = _cross_source_checks()
    for msg in x_findings:
        print(f"WARN  {msg}")
        findings.append(msg)
    for msg in x_infos:
        print(f"INFO  {msg}")

    warns = len(findings)
    print(f"\n{'ALL CLEAR' if warns == 0 else f'{warns} WARNING(S)'}")

    if alert:
        from .alerts import push_health_alerts
        outcome = push_health_alerts(findings)
        print(f"INFO  alerting: {outcome}")
    return warns
