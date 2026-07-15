"""A non-finite close must skip the tick, never trade (2026-07-15 incident)."""

import numpy as np
import pandas as pd

from qtrade.live import paper
from qtrade.live.paper import PaperTrader
from qtrade.presets import PRESETS


def test_nan_close_skips_tick(tmp_path, monkeypatch):
    p = PRESETS["etf_trend"]
    idx = pd.date_range(end=pd.Timestamp.now("UTC").floor("D"), periods=300, freq="D")

    def fake_bars(preset, adapter=None):
        out = {}
        for i, s in enumerate(preset.symbols):
            c = pd.Series(np.linspace(90, 110, 300), index=idx)
            if i == 0:
                c.iloc[-1] = np.nan  # the yahoo glitch
            out[s] = pd.DataFrame({"open": c, "high": c, "low": c,
                                   "close": c, "volume": 1.0}).rename_axis("ts")
        return out

    monkeypatch.setattr(paper, "fetch_live_bars", fake_bars)
    trader = PaperTrader(p, state_dir=tmp_path)
    res = trader.tick()
    assert res.get("skipped_nonfinite") == [p.symbols[0]]
    assert not (tmp_path / "state.json").exists()   # nothing was written
    assert not (tmp_path / "trades.csv").exists()
