"""Daily PIT snapshot collector #2: the data you cannot buy later (2026-08-04).

Extends the live-forward principle from collect_cb_events.py to three more
streams whose history is either API-blocked or never archived point-in-time:

  crypto funding  full-universe hourly funding rates. Exchanges cap free
                  history at 1-3 months (E56: binance 451 / bybit 403 / OKX
                  3mo) — the long series only exists if someone records it.
  crypto OI       open interest per perp. No free history at all; OI is a
                  standard positioning input we currently cannot research.
  A股 涨停池      each day's limit-up pool with 封单/开板次数 — the queue
                  reality behind our "锁板不可成交" approximation, and the
                  raw material for any future limit-locked event study.
  A股 龙虎榜      daily block-list (retail/institutional flow). Public but
                  never archived PIT by free sources.

One polite pass per CN day, hard timeouts, append-only day files. A failed
stream never blocks the others; a failed day leaves a gap, not a corruption.

Layout (data_store/market_snapshots/):
  crypto_funding/YYYY-MM-DD.parquet
  crypto_oi/YYYY-MM-DD.parquet
  zt_pool/YYYY-MM-DD.parquet
  lhb/YYYY-MM-DD.parquet
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qtrade.timeconv import CN_TZ, utc_now  # noqa: E402

socket.setdefaulttimeout(60)

ROOT = REPO / "data_store" / "market_snapshots"
# the ten-coin book universe plus BNB (second-source axis); :USDT = OKX perp
PERPS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT",
         "DOGE/USDT:USDT", "ADA/USDT:USDT", "AVAX/USDT:USDT", "DOT/USDT:USDT",
         "LINK/USDT:USDT", "LTC/USDT:USDT", "BNB/USDT:USDT"]


def _day() -> str:
    return utc_now().tz_convert(CN_TZ).strftime("%Y-%m-%d")


def _ashare_session_day() -> str:
    """Most recent COMPLETED CN session: pre-close runs collect yesterday.

    The EM endpoints answer with the latest available table regardless of the
    date you ask for — labeling a pre-market pull with today's date would
    silently store yesterday's data under today (the repo's #1 defect class,
    caught on this collector's first run).
    """
    now_cn = utc_now().tz_convert(CN_TZ)
    day = now_cn.normalize()
    if now_cn.hour < 16:
        day -= pd.Timedelta(days=1)
    return day.strftime("%Y-%m-%d")


def _save(df: pd.DataFrame, stream: str, day: str, append: bool = False) -> str:
    d = ROOT / stream
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{day}.parquet"
    if append and p.exists():
        df = pd.concat([pd.read_parquet(p), df], ignore_index=True)
    df.to_parquet(p)
    return f"{stream}: {len(df)} rows"


def collect_crypto(day: str) -> list[str]:
    import ccxt

    ex = ccxt.okx({"enableRateLimit": True, "timeout": 20000})
    ex.load_markets()
    fund, oi = [], []
    for s in PERPS:
        if s not in ex.markets:
            continue
        try:
            fr = ex.fetch_funding_rate(s)
            fund.append({"symbol": s, "funding_rate": fr.get("fundingRate"),
                         "next_ts": fr.get("fundingDatetime"),
                         "mark_px": fr.get("markPrice")})
            o = ex.fetch_open_interest(s)
            oi.append({"symbol": s,
                       "oi_contracts": o.get("openInterestAmount"),
                       "oi_value": o.get("openInterestValue")})
        except Exception as e:  # one symbol failing must not kill the pass
            fund.append({"symbol": s, "error": str(e)[:80]})
    out = []
    if fund:
        out.append(_save(pd.DataFrame(fund).assign(ts=str(utc_now())), "crypto_funding", day, append=True))
    if oi:
        out.append(_save(pd.DataFrame(oi).assign(ts=str(utc_now())), "crypto_oi", day, append=True))
    return out


def collect_ashare(_day_unused: str) -> list[str]:
    import akshare as ak

    day = _ashare_session_day()  # label by session, not by wall clock
    out = []
    try:
        zt = ak.stock_zt_pool_em(date=day.replace("-", ""))
        if len(zt):
            out.append(_save(zt, "zt_pool", day))
    except Exception as e:
        out.append(f"zt_pool: skip ({str(e)[:60]})")
    try:
        lhb = ak.stock_lhb_detail_em(start_date=day.replace("-", ""),
                                     end_date=day.replace("-", ""))
        if len(lhb):
            out.append(_save(lhb, "lhb", day))
    except Exception as e:
        out.append(f"lhb: skip ({str(e)[:60]})")
    return out


def main() -> None:
    day = _day()
    done = []
    # crypto funding/OI runs EVERY call (hourly cadence, 2026-08-05 upgrade):
    # intraday OI structure is the moat — exchanges keep only ~30 days of it.
    # Hourly rows append into the day file; the A-share streams stay daily
    # behind their own sentinel.
    try:
        done += collect_crypto(day)
    except Exception as e:
        done.append(f"collect_crypto: FAILED ({str(e)[:80]})")
    sentinel = ROOT / "zt_pool" / f".ashare_{_ashare_session_day()}.done"
    if not sentinel.exists():
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.touch()
        try:
            done += collect_ashare(day)
        except Exception as e:
            done.append(f"collect_ashare: FAILED ({str(e)[:80]})")
    print(f"[snapshots {day}] " + " | ".join(done))


if __name__ == "__main__":
    main()
