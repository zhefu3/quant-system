"""Tushare Pro adapter — institutional-grade A-share data, token from env.

Token comes ONLY from TUSHARE_TOKEN in the environment. Rate-limited to be
polite (Tushare caps calls/minute per API by points tier; we self-limit and
back off on their rate errors). Every method returns tidy DataFrames ready
for the PIT factor pipeline:

  index_members_monthly : point-in-time index membership + weights
  daily_basic           : daily PE/PB/market-cap/turnover panel per stock
  fina_indicator        : quarterly indicators WITH ann_date (publication)
  daily_bars            : hfq daily bars (delisted included)
"""

from __future__ import annotations

import os
import time

import pandas as pd


class TushareData:
    def __init__(self, calls_per_min: int = 90):
        token = os.environ.get("TUSHARE_TOKEN")
        if not token:
            raise EnvironmentError("set TUSHARE_TOKEN in the environment first")
        import tushare as ts

        ts.set_token(token)
        self.pro = ts.pro_api()
        self._min_interval = 60.0 / calls_per_min
        self._last_call = 0.0

    def _call(self, fn, **kw) -> pd.DataFrame:
        for attempt in range(5):
            wait = self._min_interval - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                self._last_call = time.time()
                df = fn(**kw)
                return df if df is not None else pd.DataFrame()
            except Exception as e:  # noqa: BLE001 — usually rate limit
                if attempt == 4:
                    raise
                time.sleep(15 * (attempt + 1))
        return pd.DataFrame()

    # ---------------------------------------------------------------- panels
    def index_members_monthly(self, index_code: str = "399300.SZ",
                              start: str = "20140101", end: str | None = None) -> pd.DataFrame:
        """Monthly membership+weights. Returns [trade_date, con_code, weight]."""
        end = end or pd.Timestamp.now().strftime("%Y%m%d")
        out = []
        for month_start in pd.date_range(start, end, freq="MS"):
            m0 = month_start.strftime("%Y%m%d")
            m1 = (month_start + pd.offsets.MonthEnd()).strftime("%Y%m%d")
            df = self._call(self.pro.index_weight, index_code=index_code,
                            start_date=m0, end_date=m1)
            if not df.empty:
                latest = df[df["trade_date"] == df["trade_date"].max()]
                out.append(latest)
        return pd.concat(out, ignore_index=True) if out else pd.DataFrame()

    def daily_basic(self, ts_code: str, start: str = "20140101") -> pd.DataFrame:
        """Daily PE/PB/total_mv/turnover for one stock."""
        return self._call(self.pro.daily_basic, ts_code=ts_code, start_date=start,
                          fields="ts_code,trade_date,pe_ttm,pb,total_mv,turnover_rate")

    def fina_indicator(self, ts_code: str, start: str = "20140101") -> pd.DataFrame:
        """Quarterly indicators with ann_date — the PIT-discipline key."""
        return self._call(self.pro.fina_indicator, ts_code=ts_code, start_date=start,
                          fields=("ts_code,ann_date,end_date,roe,roe_dt,netprofit_yoy,"
                                  "or_yoy,grossprofit_margin,debt_to_assets,ocfps,eps"))

    def daily_bars(self, ts_code: str, start: str = "20140101") -> pd.DataFrame:
        """hfq daily bars via pro_bar (covers delisted names)."""
        import tushare as ts

        for attempt in range(3):
            wait = self._min_interval - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()
            df = ts.pro_bar(ts_code=ts_code, adj="hfq", start_date=start)
            if df is not None:
                return df
            time.sleep(10 * (attempt + 1))
        return pd.DataFrame()
