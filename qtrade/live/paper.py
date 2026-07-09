"""Paper trading: run a validated book preset against live market data.

Each tick():
  1. fetch fresh bars (enough for indicator warm-up), drop the in-progress bar
  2. compute every symbol's target weight from the SAME strategy object the
     backtest validated (via presets)
  3. simulate fills against the latest close with the preset's fees+slippage
  4. persist state (cash/positions), a trade log, and an equity curve

Run it once per bar (e.g. hourly via cron/launchd, or --loop). Execution
semantics differ slightly from the backtest (fills at latest close vs next-bar
close) — conservative slippage is the buffer; judge results over weeks, not ticks.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..data.adapters.crypto_ccxt import CryptoAdapter
from ..presets import BookPreset
from .signals import compute_targets

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "outputs" / "paper"


class PaperTrader:
    def __init__(self, preset: BookPreset, state_dir: Path | str | None = None,
                 init_cash: float = 10_000.0):
        self.preset = preset
        self.dir = Path(state_dir) if state_dir else DEFAULT_ROOT / preset.name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.dir / "state.json"
        self.trades_file = self.dir / "trades.csv"
        self.equity_file = self.dir / "equity.csv"
        self.init_cash = init_cash
        self.adapter = CryptoAdapter()

    # -- state ---------------------------------------------------------------
    def _load_state(self) -> dict:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text())
        return {"cash": self.init_cash, "positions": {}, "last_ts": None}

    def _save_state(self, state: dict):
        self.state_file.write_text(json.dumps(state, indent=2))

    def _append_csv(self, path: Path, row: dict):
        df = pd.DataFrame([row])
        df.to_csv(path, mode="a", header=not path.exists(), index=False)

    # -- one tick ------------------------------------------------------------
    def tick(self) -> dict:
        p = self.preset
        now = pd.Timestamp.now("UTC")
        targets, closes = compute_targets(p, self.adapter)

        state = self._load_state()
        cash, positions = state["cash"], state["positions"]
        equity = cash + sum(q * closes[s] for s, q in positions.items())

        fills = []
        for sym in p.symbols:
            price = closes[sym]
            cur_qty = positions.get(sym, 0.0)
            cur_w = cur_qty * price / equity
            tgt_w = targets[sym]
            if abs(tgt_w - cur_w) < p.rebalance_eps and not (tgt_w == 0.0 and cur_w != 0.0):
                continue
            delta_notional = tgt_w * equity - cur_qty * price
            side = 1 if delta_notional > 0 else -1
            fill_price = price * (1 + side * p.rules.slippage)
            qty = delta_notional / fill_price
            fee = abs(delta_notional) * p.rules.fee_rate
            cash -= qty * fill_price + fee
            positions[sym] = cur_qty + qty
            if abs(positions[sym] * price) < 1e-6:
                positions.pop(sym, None)
            fill = {
                "ts": str(now), "symbol": sym, "qty": round(qty, 8),
                "price": round(fill_price, 6), "fee": round(fee, 4),
                "target_w": round(tgt_w, 4), "prev_w": round(cur_w, 4),
            }
            fills.append(fill)
            self._append_csv(self.trades_file, fill)

        equity_now = cash + sum(q * closes[s] for s, q in positions.items())
        gross = sum(abs(q * closes[s]) for s, q in positions.items())
        self._append_csv(self.equity_file, {
            "ts": str(now), "equity": round(equity_now, 2),
            "cash": round(cash, 2), "gross_exposure": round(gross / equity_now, 3),
            "n_positions": len(positions), "n_fills": len(fills),
        })
        self._save_state({"cash": cash, "positions": positions, "last_ts": str(now)})

        return {
            "ts": str(now), "equity": round(equity_now, 2),
            "pnl_total": round(equity_now - self.init_cash, 2),
            "gross_exposure": round(gross / equity_now, 3),
            "positions": {s: round(q, 6) for s, q in positions.items()},
            "fills": fills,
        }


def run_tick(preset_name: str, state_dir: str | None = None) -> dict:
    from ..presets import PRESETS

    trader = PaperTrader(PRESETS[preset_name], state_dir=state_dir)
    summary = trader.tick()
    ts = datetime.now(timezone.utc).strftime("%H:%M")
    print(f"[{ts} UTC] equity {summary['equity']}  pnl {summary['pnl_total']:+}  "
          f"gross {summary['gross_exposure']:.0%}  fills {len(summary['fills'])}")
    for f in summary["fills"]:
        print(f"  {f['symbol']:10s} {f['prev_w']:+.3f} -> {f['target_w']:+.3f} "
              f"qty {f['qty']:+.6f} @ {f['price']}")
    if not summary["fills"]:
        print("  no rebalancing needed")
    return summary
