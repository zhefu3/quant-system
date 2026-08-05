"""E70 universe freeze: top-50 US-listed stocks by market cap.

Prereg (research/log.md 2026-08-04): universe = 美股市值前 50, frozen before
the book's first tick; quarterly re-evaluation by RULE, never chasing ranks.

Rule (frozen with this script — the appendix in log.md quotes it):
  - Eligible: NYSE/NASDAQ-listed common stocks and ADRs. ETFs/funds excluded.
    "美股" reads literally as US-*listed*, so foreign ADRs (TSM, ASML, ...)
    are eligible — they trade in USD on US exchanges and are covered by the
    same US news flow the committee reads.
  - One class per company (the more liquid class: GOOGL over GOOG).
  - Ranking metric: yfinance fast_info market_cap on the freeze date, from a
    fixed ~120-name candidate superset (top-100-by-cap coverage with margin;
    a name outside this superset cannot plausibly be top-50).
  - Quarterly re-eval (first committee day of Jan/Apr/Jul/Oct): recompute the
    same ranking; incumbents stay unless they fall below rank 60, entrants
    join by rank until the book is back at 50 (50/60 hysteresis = no churn).
    Every change is logged in research/log.md and a new dated artifact.

Output: research/artifacts/llm_us_universe_<date>.json (full cap table +
the frozen 50) — the artifact referenced by the log appendix.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

# Candidate superset: US mega/large caps + major US-listed ADRs, drawn wide
# (~120 names) so the true top-50 is a strict subset with a wide margin.
CANDIDATES = [
    # US-domiciled (and Ireland/UK-domiciled ordinaries like ACN/LIN/MDT)
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "TSLA", "BRK-B",
    "LLY", "WMT", "JPM", "V", "XOM", "UNH", "ORCL", "MA", "HD", "PG", "COST",
    "JNJ", "NFLX", "BAC", "ABBV", "CRM", "CVX", "KO", "AMD", "TMUS", "PEP",
    "TMO", "WFC", "CSCO", "ADBE", "MCD", "IBM", "PM", "ABT", "GE", "ISRG",
    "QCOM", "CAT", "INTU", "AXP", "MS", "DIS", "NOW", "T", "VZ", "GS",
    "TXN", "BX", "PLTR", "RTX", "BKNG", "AMGN", "HON", "SPGI", "UBER", "PFE",
    "UNP", "LOW", "SYK", "NEE", "COP", "SCHW", "C", "BLK", "ETN", "AMAT",
    "TJX", "BSX", "PGR", "DE", "LMT", "MU", "ADP", "GILD", "ANET", "PANW",
    "MDT", "CB", "MMC", "LRCX", "KLAC", "SBUX", "INTC", "CRWD", "CEG", "VRTX",
    "ARM", "APP", "SO", "PLD", "MO", "DHR", "ACN", "LIN", "MRK", "COIN",
    "MSTR", "DASH", "ABNB", "SNOW", "WDAY", "MAR", "FI", "PH", "GEV", "WELL",
    # US-listed ADRs / foreign listings
    "TSM", "ASML", "NVO", "SAP", "TM", "AZN", "SHEL", "BHP", "TTE", "SONY",
    "BABA", "PDD", "HSBC", "UL", "RIO", "NVS", "GSK", "BUD", "MUFG", "IBN",
]

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"


def fetch_caps() -> tuple[dict[str, float], list[str]]:
    import yfinance as yf

    caps, failed = {}, []
    for t in CANDIDATES:
        try:
            mc = yf.Ticker(t).fast_info["market_cap"]
            if mc and mc > 0:
                caps[t] = float(mc)
            else:
                failed.append(t)
        except Exception as e:  # noqa: BLE001 — record and keep ranking
            print(f"  {t}: FAILED ({str(e)[:60]})")
            failed.append(t)
        time.sleep(0.15)  # polite pacing on the free API
    return caps, failed


def main():
    print(f"fetching market caps for {len(CANDIDATES)} candidates...")
    caps, failed = fetch_caps()
    ranked = sorted(caps.items(), key=lambda kv: -kv[1])
    top50 = [t for t, _ in ranked[:50]]

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = {
        "experiment": "E70 llm_us",
        "frozen_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": "yfinance fast_info market_cap, fixed candidate superset, "
                  "one class per company, ETFs excluded, ADRs eligible; "
                  "quarterly re-eval with 50/60 hysteresis (see this script)",
        "n_candidates": len(CANDIDATES),
        "failed_fetch": failed,
        "market_caps_usd": {t: caps[t] for t, _ in ranked},
        "top50": top50,
    }
    ARTIFACTS.mkdir(exist_ok=True)
    path = ARTIFACTS / f"llm_us_universe_{today.replace('-', '')}.json"
    path.write_text(json.dumps(out, indent=2))

    print(f"\nfrozen top-50 (caps in $B), artifact: {path.name}")
    for i, (t, mc) in enumerate(ranked[:50], 1):
        print(f"{i:3d}. {t:6s} {mc / 1e9:10.1f}")
    if failed:
        print(f"\nWARNING — failed fetches (excluded from ranking): {failed}")
    print("\nPython list for presets.py:")
    print(json.dumps(sorted(top50)))


if __name__ == "__main__":
    main()
