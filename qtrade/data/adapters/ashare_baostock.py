"""A-share adapter via baostock (free, no key; 5m+ bars for stocks).

- Symbols: accepts "600519.SH" / "000858.SZ" or baostock's "sh.600519".
- Prices are 后复权 (adjustflag=1) by default so long-history returns are real.
- Bar timestamps from baostock are bar END times in Asia/Shanghai; we convert
  to UTC and keep the canonical schema.
"""

from __future__ import annotations

import pandas as pd

from ..schema import normalize_ohlcv

FREQ_MAP = {"5m": "5", "15m": "15", "30m": "30", "1h": "60", "1d": "d"}
MINUTE_FREQS = {"5", "15", "30", "60"}


def to_bs_code(symbol: str) -> str:
    s = symbol.lower()
    if s.startswith(("sh.", "sz.")):
        return s
    code, _, suffix = symbol.partition(".")
    if suffix.upper() in ("SH", "SZ"):
        return f"{suffix.lower()}.{code}"
    # bare 6-digit code: 6xxxxx -> Shanghai, else Shenzhen
    return f"{'sh' if code.startswith('6') else 'sz'}.{code}"


class AShareAdapter:
    market = "ashare"

    def __init__(self, adjustflag: str = "1"):  # 1=后复权 2=前复权 3=不复权
        self.adjustflag = adjustflag
        self._session = None

    def _ensure_login(self):
        if self._session is None:
            import baostock as bs

            lg = bs.login()
            if lg.error_code != "0":
                raise ConnectionError(f"baostock login failed: {lg.error_msg}")
            self._session = bs

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: pd.Timestamp,
        end: pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        if timeframe not in FREQ_MAP:
            raise ValueError(f"baostock supports {list(FREQ_MAP)}, got {timeframe}")
        self._ensure_login()
        bs = self._session
        freq = FREQ_MAP[timeframe]
        is_minute = freq in MINUTE_FREQS

        time_field = "time" if is_minute else "date"
        fields = f"{time_field},open,high,low,close,volume"
        end = end if end is not None else pd.Timestamp.now("Asia/Shanghai")
        rs = bs.query_history_k_data_plus(
            to_bs_code(symbol),
            fields,
            start_date=str(pd.Timestamp(start).date()),
            end_date=str(pd.Timestamp(end).date()),
            frequency=freq,
            adjustflag=self.adjustflag,
        )
        if rs.error_code != "0":
            raise RuntimeError(f"baostock query failed: {rs.error_msg}")
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            raise RuntimeError(f"baostock returned no data for {symbol} {timeframe}")

        df = pd.DataFrame(rows, columns=[time_field, "open", "high", "low", "close", "volume"])
        df = df[(df["close"] != "") & (df["volume"] != "")]  # suspended sessions
        if is_minute:
            ts = pd.to_datetime(df[time_field].str[:14], format="%Y%m%d%H%M%S")
        else:
            ts = pd.to_datetime(df[time_field])
        df.index = ts.dt.tz_localize("Asia/Shanghai").dt.tz_convert("UTC")
        return normalize_ohlcv(df.drop(columns=[time_field]))
