"""Bollinger z-score mean reversion.

Enter when price stretches `entry_z` std devs from its rolling mean, exit on
reversion to the mean (z crosses 0). A trend veto (price vs long MA) keeps it
from catching falling knives in a strong trend. Weight scales with |z| up to 1.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class BollingerRevert(Strategy):
    name = "boll_revert"

    def __init__(
        self,
        window: int = 96,
        entry_z: float = 2.0,
        long_only: bool = True,
        side: str = "",              # "both" | "long" | "short"; overrides long_only if set
        trend_veto_window: int = 0,  # 0 = off; else skip longs below this MA
        regime_window: int = 0,      # 0 = off; longs only above this MA, shorts only below
        short_regime_window: int = 0,  # 0 = off; shorts ALSO need close < this slower MA
        stop_z: float = 0.0,         # 0 = off; exit if z stretches this far AGAINST us
        max_hold: int = 0,           # 0 = off; time stop in bars
    ):
        self.window = window
        self.entry_z = entry_z
        self.side = side or ("long" if long_only else "both")
        self.regime_window = regime_window
        self.short_regime_window = short_regime_window
        self.trend_veto_window = trend_veto_window
        self.stop_z = stop_z
        self.max_hold = max_hold

    def target_position(self, bars: pd.DataFrame) -> pd.Series:
        close = bars["close"]
        mean = close.rolling(self.window).mean()
        std = close.rolling(self.window).std()
        z = (close - mean) / std

        n = len(close)
        zv = z.to_numpy()
        veto = None
        if self.trend_veto_window:
            veto = (close < close.rolling(self.trend_veto_window).mean()).to_numpy()
        # Regime alignment: buy dips only in an uptrend, fade pumps only in a
        # downtrend. NaN regime (warm-up) blocks both entries.
        long_ok = short_ok = None
        if self.regime_window:
            regime_ma = close.rolling(self.regime_window).mean()
            long_ok = (close >= regime_ma).to_numpy()
            short_series = close < regime_ma
            if self.short_regime_window:
                # Deep-bear confirmation: fading pumps below a fast MA gets
                # run over in V-recoveries; require the slow MA agrees.
                short_series &= close < close.rolling(self.short_regime_window).mean()
            short_ok = short_series.to_numpy()

        w = np.zeros(n)
        state = 0.0    # current side: 0 flat, +1 long-revert, -1 short-revert
        held = 0       # bars in current position
        locked = False  # after a stop-out, no re-entry until z returns inside the band
        for i in range(n):
            if np.isnan(zv[i]):
                continue
            if locked and abs(zv[i]) < self.entry_z:
                locked = False
            if state == 0.0:
                if locked:
                    continue
                if (
                    self.side in ("both", "long")
                    and zv[i] <= -self.entry_z
                    and not (veto is not None and veto[i])
                    and (long_ok is None or long_ok[i])
                ):
                    state, held = 1.0, 0
                elif (
                    self.side in ("both", "short")
                    and zv[i] >= self.entry_z
                    and (short_ok is None or short_ok[i])
                ):
                    state, held = -1.0, 0
            else:
                held += 1
                reverted = (state == 1.0 and zv[i] >= 0) or (state == -1.0 and zv[i] <= 0)
                stopped = self.stop_z and (
                    (state == 1.0 and zv[i] <= -self.stop_z)
                    or (state == -1.0 and zv[i] >= self.stop_z)
                )
                timed_out = self.max_hold and held >= self.max_hold
                if reverted:
                    state = 0.0
                elif stopped or timed_out:
                    state = 0.0
                    locked = True  # thesis broken: wait for z to normalize
            w[i] = state
        return pd.Series(w, index=bars.index)

    def explain(self, bars: pd.DataFrame) -> dict:
        close = bars["close"]
        mean = close.rolling(self.window).mean().iloc[-1]
        std = close.rolling(self.window).std().iloc[-1]
        z = float((close.iloc[-1] - mean) / std) if std else float("nan")
        out = {"name": self.name, "z": round(z, 2), "entry_z": self.entry_z,
               "target": round(float(self.target_position(bars).iloc[-1]), 4)}
        if self.regime_window:
            regime_ma = close.rolling(self.regime_window).mean().iloc[-1]
            out["regime"] = "long_ok" if close.iloc[-1] >= regime_ma else "short_ok"
        return out
