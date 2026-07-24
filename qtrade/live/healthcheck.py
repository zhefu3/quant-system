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
from ..timeconv import cn_date, utc_date, utc_now
from .paper import DEFAULT_ROOT
from .timeouts import call_with_timeout

# freshness tolerance per bar timeframe (calendar quirks: weekends, holidays)
STALE_AFTER = {"1h": pd.Timedelta(hours=12), "4h": pd.Timedelta(hours=24),
               "1d": pd.Timedelta(days=6)}
RECENT = 200  # bars inspected for NaN/gap checks
XSOURCE_TOL = 0.01  # two venues disagreeing >1% on a daily close is a finding

# Ordered by reachability from THIS network: binance is geo-blocked here and
# sat at the front of the old pair, so the crypto cross-check never once
# completed between deploy (07-19) and 07-24 — while reporting only INFO.
# A monitor that can fail silently forever is not a monitor; hence the list
# (first two reachable venues win) plus the darkness escalation below.
XSOURCE_VENUES = ("okx", "kraken", "coinbase", "binance")
XSOURCE_DARK_DAYS = 3  # no completed comparison for this long => WARN
XSOURCE_STATE = Path(__file__).resolve().parents[2] / "outputs" / "health_xsource.json"

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


def _apply_xsource_darkness(completed: dict[str, bool]) -> list[str]:
    """Escalate a cross-check that has not COMPLETED a comparison in
    XSOURCE_DARK_DAYS. Lesson of 07-24: the binance leg was unreachable from
    day one, the stored fallback compared against a research archive that by
    design stops updating, and both failures were INFO — the monitor was dark
    for its whole life and nothing said so. Completion is tracked per key in
    a state file; disagreement WARNs elsewhere, this only watches darkness."""
    import json

    state: dict[str, dict] = {}
    if XSOURCE_STATE.exists():
        try:
            state = json.loads(XSOURCE_STATE.read_text())
        except (ValueError, OSError):
            state = {}
    today = str(utc_date(utc_now()))
    findings = []
    for key, ok in completed.items():
        ent = state.setdefault(key, {"first_try": today, "last_ok": None})
        if ok:
            ent["last_ok"] = today
        anchor = ent["last_ok"] or ent["first_try"]
        dark = (pd.Timestamp(today) - pd.Timestamp(anchor)).days
        if dark >= XSOURCE_DARK_DAYS:
            findings.append(f"xsource {key}: no completed comparison in {dark}d "
                            "— the cross-check itself is dark (sources/network?)")
    XSOURCE_STATE.parent.mkdir(parents=True, exist_ok=True)
    XSOURCE_STATE.write_text(json.dumps(state, indent=1))
    return findings


def _cross_source_checks() -> tuple[list[str], list[str]]:
    """(findings, info lines). A venue being unreachable is INFO — only an
    actual disagreement between two live sources is a WARN. Every call is
    soft-timeout wrapped; this section may never hang the health run.
    Persistent failure-to-compare escalates via _apply_xsource_darkness."""
    findings, infos = [], []
    completed: dict[str, bool] = {}

    # crypto: yesterday's completed daily close, first two reachable venues
    # from XSOURCE_VENUES. Days must match — venues roll their daily candle
    # at the same UTC boundary, but a lagging API must not fake a divergence.
    try:
        import ccxt
        for sym in ("BTC/USDT", "ETH/USDT"):
            completed[sym] = False
            quotes: dict[str, tuple] = {}  # venue -> (completed day, close)
            for venue in XSOURCE_VENUES:
                if len(quotes) == 2:
                    break
                try:
                    ex = getattr(ccxt, venue)({"timeout": 15000})
                    o = call_with_timeout(ex.fetch_ohlcv, 30.0, sym, "1d", limit=3)
                    quotes[venue] = (utc_date(pd.Timestamp(o[-2][0], unit="ms")),
                                     float(o[-2][4]))  # last COMPLETED day, UTC roll
                except Exception as e:  # noqa: BLE001 — venue down is not a finding
                    infos.append(f"{sym} {venue}: unavailable ({type(e).__name__})")
            if len(quotes) == 2:
                (v1, (d1, p1)), (v2, (d2, p2)) = quotes.items()
                if d1 != d2:
                    infos.append(f"{sym}: {v1}/{v2} completed days differ "
                                 f"({d1} vs {d2}) — skipped")
                else:
                    completed[sym] = True
                    div = abs(p1 / p2 - 1)
                    if div > XSOURCE_TOL:
                        findings.append(f"xsource {sym}: {v1} vs {v2} daily close "
                                        f"diverge {div:.2%} ({d1})")
                    else:
                        infos.append(f"{sym}: {v1}/{v2} agree ({div:.3%})")
            else:
                infos.append(f"{sym}: <2 venues reachable — comparison skipped")
    except ImportError:
        infos.append("ccxt not importable — crypto cross-check skipped")

    # A-share: last stored daily return (tushare pipeline, hfq) vs akshare.
    # Dates aligned in Asia/Shanghai (store stamps are CN-midnight-in-UTC —
    # naive .date() shifts a day; this check's OWN first live firing was that
    # exact false positive, 2026-07-22). The store return must agree with
    # EITHER akshare's raw return (exchange fact, convention-free) or its
    # hfq return (covers ex-div days where raw diverges by the dividend);
    # only disagreement with BOTH is a data finding.
    try:
        store = BarStore()
        sym = "000001.SZ"
        completed[sym] = False
        df = store.load("ashare_ts", sym, "1d").tail(3)
        if len(df) >= 2:
            day1 = cn_date(df.index[-2])
            day2 = cn_date(df.index[-1])
            r_store = float(df["close"].iloc[-1] / df["close"].iloc[-2] - 1)

            def _ak_hist(adjust: str):
                import akshare as ak
                end = df.index[-1].tz_convert("Asia/Shanghai")
                return ak.stock_zh_a_hist(symbol=sym.split(".")[0], period="daily",
                                          adjust=adjust,
                                          start_date=(end - pd.Timedelta(days=14)).strftime("%Y%m%d"),
                                          end_date=end.strftime("%Y%m%d"))
            try:
                r_aks = {}
                for adjust, tag in (("", "raw"), ("hfq", "hfq")):
                    h = call_with_timeout(_ak_hist, 45.0, adjust)
                    by_date = {cn_date(d): float(c)
                               for d, c in zip(h["日期"], h["收盘"])}
                    if day1 in by_date and day2 in by_date:
                        r_aks[tag] = by_date[day2] / by_date[day1] - 1
                if r_aks:
                    completed[sym] = True
                    best = min(abs(r_store - r) for r in r_aks.values())
                    if best > 0.005:
                        detail = ", ".join(f"{t} {r:+.2%}" for t, r in r_aks.items())
                        findings.append(f"xsource {sym}: store {r_store:+.2%} vs "
                                        f"akshare ({detail}) — no basis agrees")
                    else:
                        infos.append(f"{sym}: tushare/akshare agree ({best:.3%})")
                else:
                    infos.append(f"{sym}: akshare missing {day1}/{day2} — skipped")
            except Exception as e:  # noqa: BLE001 — source down is not a finding
                infos.append(f"{sym} akshare: unavailable ({type(e).__name__})")
    except Exception as e:  # noqa: BLE001
        infos.append(f"ashare cross-check skipped ({type(e).__name__})")

    findings.extend(_apply_xsource_darkness(completed))
    return findings, infos


def _ib_gateway_probe(port: int = 4002) -> str:
    """'ok' | 'down' (port closed) | 'zombie' (port open, API handshake dead).

    The handshake runs in a SUBPROCESS with a hard kill: a wedged gateway
    must never hang the health run itself (which rides the hourly loop —
    the exact incident class of 07-14/16). Client id 93 avoids the books'
    ids; a same-id clash would false-flag zombie."""
    import socket as _socket
    import subprocess
    import sys as _sys

    try:
        with _socket.create_connection(("127.0.0.1", port), timeout=3):
            pass
    except OSError:
        return "down"
    code = (f"from ib_async import IB\n"
            f"ib = IB(); ib.connect('127.0.0.1', {port}, clientId=93, timeout=8)\n"
            f"assert ib.isConnected()\n"
            f"ib.reqCurrentTime(); ib.disconnect()")
    try:
        r = subprocess.run([_sys.executable, "-c", code],
                           capture_output=True, timeout=20)
        return "ok" if r.returncode == 0 else "zombie"
    except subprocess.TimeoutExpired:
        return "zombie"


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

    # IB Gateway health: futures_ibkr's single external dependency. API-level,
    # not port-level — 2026-07-21 the gateway sat ZOMBIE for 8h (local port
    # answering, IB session dead) and the TCP probe stayed green throughout.
    # Port asks "are you there"; the API handshake asks "can you still work".
    if (DEFAULT_ROOT / "futures_ibkr" / "equity.csv").exists():
        verdict = _ib_gateway_probe()
        if verdict == "down":
            print("WARN  ib_gateway: port 4002 unreachable — gateway down "
                  "(IBC KeepAlive should relaunch; see ops-runbook)")
            findings.append("ib_gateway: down")
        elif verdict == "zombie":
            print("WARN  ib_gateway: ZOMBIE — port open but API session dead "
                  "(the 07-21 starvation mode); await IBC restart or kick it")
            findings.append("ib_gateway: zombie session")

    # llm_agents committee freshness: with the graceful fallback (billing
    # outage 2026-07-22), a missing daily decision no longer starves the
    # heartbeat — so it needs its own explicit check to reach the alerts.
    ddir = DEFAULT_ROOT / "llm_agents" / "decisions"
    if ddir.exists():
        latest = max((f.stem for f in ddir.glob("*.json")), default=None)
        if latest:
            age_d = (pd.Timestamp.now("UTC").normalize()
                     - pd.Timestamp(latest, tz="UTC")).days
            if age_d >= 1:
                print(f"WARN  llm_agents: no committee decision since {latest} "
                      f"({age_d}d) — API credits/outage? book is frozen-marking")
                findings.append(f"llm_agents: no decision since {latest}")

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
