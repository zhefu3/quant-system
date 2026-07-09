"""OKX perpetual-swap executor with hard safety rails.

Credentials come ONLY from environment variables — never from code, configs,
or command lines:

    OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE
    QTRADE_OKX_DEMO=1   -> OKX demo-trading environment (fake money, real API)

Rails (hard-coded, not options):
  - the executor manages at most `capital` USDT, never the whole account
  - per-symbol |weight| capped at MAX_SINGLE_W, gross capped at MAX_GROSS
  - drawdown kill switch: equity below (1 - MAX_DD_STOP) x high-water mark
    flattens the book and writes a HALTED flag; runs refuse to start while
    the flag exists (delete it manually after investigating)
  - dry-run by default; orders are only sent with send=True
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..presets import BookPreset
from .signals import compute_targets

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "outputs" / "live"

MAX_SINGLE_W = 0.15   # no symbol may exceed 15% of managed capital
MAX_GROSS = 1.0       # no leverage on the managed sleeve
MAX_DD_STOP = 0.20    # kill switch: flatten + halt at -20% from high water


def _swap_symbol(sym: str) -> str:
    return f"{sym}:USDT"  # BTC/USDT -> BTC/USDT:USDT (USDT-margined swap)


def plan_orders(
    targets: dict[str, float],
    closes: dict[str, float],
    current_notional: dict[str, float],
    capital: float,
    markets: dict[str, dict],
    eps: float,
) -> list[dict]:
    """Pure sizing logic (unit-testable, no network).

    Returns a list of {symbol, contracts, side, notional, note} order plans.
    Contract granularity is respected; deltas below eps*capital or below one
    contract are skipped with a note.
    """
    plans = []
    for sym, tgt_w in targets.items():
        tgt_w = max(-MAX_SINGLE_W, min(MAX_SINGLE_W, tgt_w))
        price = closes[sym]
        cur = current_notional.get(sym, 0.0)
        desired = tgt_w * capital
        delta = desired - cur
        if abs(delta) < eps * capital and not (desired == 0.0 and cur != 0.0):
            continue
        m = markets[_swap_symbol(sym)]
        contract_size = float(m.get("contractSize") or 1.0)
        contract_notional = contract_size * price
        contracts = math.floor(abs(delta) / contract_notional)
        if contracts == 0:
            plans.append({"symbol": sym, "contracts": 0, "side": "-",
                          "notional": 0.0,
                          "note": f"skip: |delta| {abs(delta):.2f} < 1 contract "
                                  f"({contract_notional:.2f} USDT)"})
            continue
        plans.append({
            "symbol": sym,
            "contracts": contracts,
            "side": "buy" if delta > 0 else "sell",
            "notional": round(math.copysign(contracts * contract_notional, delta), 2),
            "note": f"w {cur / capital:+.3f} -> {tgt_w:+.3f}",
        })
    gross = sum(abs(p["notional"]) for p in plans if p["contracts"])
    if gross > MAX_GROSS * capital * 1.5:  # sanity: order flow itself absurd
        raise RuntimeError(f"planned order gross {gross:.0f} exceeds sanity bound; refusing")
    return plans


class OKXExecutor:
    def __init__(self, preset: BookPreset, capital: float,
                 state_dir: Path | str | None = None):
        self.preset = preset
        self.capital = capital
        self.dir = Path(state_dir) if state_dir else DEFAULT_ROOT / preset.name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.halt_flag = self.dir / "HALTED"
        self.hwm_file = self.dir / "hwm.json"
        self.orders_file = self.dir / "orders.csv"
        self.equity_file = self.dir / "equity.csv"
        self._ex = None

    # -- exchange ------------------------------------------------------------
    @property
    def ex(self):
        if self._ex is None:
            import ccxt

            key = os.environ.get("OKX_API_KEY")
            secret = os.environ.get("OKX_API_SECRET")
            passphrase = os.environ.get("OKX_API_PASSPHRASE")
            if not (key and secret and passphrase):
                raise EnvironmentError(
                    "set OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE in the "
                    "environment (never pass keys on the command line)"
                )
            ex = ccxt.okx({
                "apiKey": key, "secret": secret, "password": passphrase,
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            })
            if os.environ.get("QTRADE_OKX_DEMO") == "1":
                ex.set_sandbox_mode(True)
            ex.load_markets()
            self._ex = ex
        return self._ex

    # -- account state -------------------------------------------------------
    def account_state(self) -> tuple[float, dict[str, float]]:
        """Returns (usdt_equity, {spot_symbol: signed_position_notional})."""
        bal = self.ex.fetch_balance()
        usdt = float(bal.get("USDT", {}).get("total") or 0.0)
        notionals: dict[str, float] = {}
        positions = self.ex.fetch_positions([_swap_symbol(s) for s in self.preset.symbols])
        for pos in positions:
            contracts = float(pos.get("contracts") or 0.0)
            if not contracts:
                continue
            sym = pos["symbol"].split(":")[0]
            sign = 1 if pos.get("side") == "long" else -1
            mark = float(pos.get("markPrice") or pos.get("entryPrice") or 0.0)
            csize = float(self.ex.market(pos["symbol"]).get("contractSize") or 1.0)
            notionals[sym] = sign * contracts * csize * mark
        return usdt, notionals

    # -- one run -------------------------------------------------------------
    def run(self, send: bool = False, flatten: bool = False) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if self.halt_flag.exists() and not flatten:
            print(f"HALTED flag present ({self.halt_flag}) — investigate, remove it, rerun.")
            return

        usdt_equity, current = self.account_state()
        managed = min(self.capital, usdt_equity)
        print(f"[{now}] account USDT {usdt_equity:.2f} | managed sleeve {managed:.2f} "
              f"| mode {'DEMO' if os.environ.get('QTRADE_OKX_DEMO') == '1' else 'REAL'}"
              f" | {'SEND' if send else 'DRY-RUN'}")

        if flatten:
            targets = {s: 0.0 for s in self.preset.symbols}
            closes = {s: abs(current.get(s, 0.0)) or 1.0 for s in self.preset.symbols}
            # use live tickers for pricing the exit
            for s in self.preset.symbols:
                closes[s] = float(self.ex.fetch_ticker(_swap_symbol(s))["last"])
        else:
            targets, closes = compute_targets(self.preset)

        # Kill switch bookkeeping on the managed sleeve.
        hwm = managed
        if self.hwm_file.exists():
            hwm = max(json.loads(self.hwm_file.read_text())["hwm"], managed)
        self.hwm_file.write_text(json.dumps({"hwm": hwm, "ts": now}))
        if managed < hwm * (1 - MAX_DD_STOP) and not flatten:
            print(f"KILL SWITCH: equity {managed:.2f} < {1 - MAX_DD_STOP:.0%} of HWM {hwm:.2f}")
            self.halt_flag.write_text(f"drawdown stop at {now}\n")
            self.run(send=send, flatten=True)
            return

        plans = plan_orders(targets, closes, current, managed,
                            {k: self.ex.market(k) for k in
                             (_swap_symbol(s) for s in self.preset.symbols)},
                            self.preset.rebalance_eps)
        if not plans:
            print("  book already at target — no orders")
        for p in plans:
            line = (f"  {p['symbol']:10s} {p['side']:4s} {p['contracts']:>6d} contracts "
                    f"(~{p['notional']:+.2f} USDT)  {p['note']}")
            print(line)
            if not send or p["contracts"] == 0:
                continue
            order = self.ex.create_order(
                _swap_symbol(p["symbol"]), "market", p["side"], p["contracts"],
                params={"tdMode": "cross"},
            )
            pd.DataFrame([{**p, "ts": now, "order_id": order.get("id"),
                           "status": order.get("status")}]).to_csv(
                self.orders_file, mode="a",
                header=not self.orders_file.exists(), index=False)

        pd.DataFrame([{"ts": now, "usdt_equity": usdt_equity, "managed": managed,
                       "sent": send, "n_orders": sum(1 for p in plans if p["contracts"])}]
                     ).to_csv(self.equity_file, mode="a",
                              header=not self.equity_file.exists(), index=False)
