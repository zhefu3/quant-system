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
  - structural account guard: sending requires QTRADE_OKX_ACCOUNT_UID and the
    exchange-reported UID must match it (fail-closed). A mode flag can be
    forgotten; the UID pin makes "wrong account" structurally impossible —
    demo and real keys belong to different UIDs.
  - order-level checks (nautilus_trader RiskEngine idea): any single order
    above MAX_ORDER_NOTIONAL x capital, or more real orders than symbols,
    refuses the whole run — catches bad marks/prices before they hit the book
  - reconciliation (nautilus_trader idea): each send records pre-trade and
    target notionals; the next run verifies the venue state lies between them
    (fills may be partial). A violation means manual trading, liquidation, or
    a missed fill — a RECONCILE flag blocks ALL sending (flatten included:
    acting on wrong beliefs is worse than waiting) until a human removes it.
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
# A legitimate rebalance never moves one symbol by more than 2 x MAX_SINGLE_W
# (full flip); anything bigger means a corrupted mark/price or venue state.
MAX_ORDER_NOTIONAL = 0.35  # per-order |notional| cap, fraction of capital
RECON_TOL = 0.02           # reconciliation tolerance, fraction of capital


def _swap_symbol(sym: str) -> str:
    return f"{sym}:USDT"  # BTC/USDT -> BTC/USDT:USDT (USDT-margined swap)


def check_account_uid(expected: str | None, actual: str | None, send: bool) -> str:
    """Structural live-account guard (pure logic, unit-tested).

    Sending with no pinned UID, an unreadable UID, or a mismatch raises —
    fail closed. Dry-runs are allowed unpinned so the guard never blocks
    inspection, only order flow. Returns a note for the audit log."""
    if not actual:
        if send:
            raise RuntimeError("exchange did not report an account UID — refusing to send")
        return "UID unreadable (dry-run allowed)"
    if not expected:
        if send:
            raise RuntimeError(
                "QTRADE_OKX_ACCOUNT_UID is not set — refusing to send orders. Pin the "
                "intended account: export QTRADE_OKX_ACCOUNT_UID=<uid from OKX console>")
        return f"UID {actual} unpinned (dry-run allowed; pin before send)"
    if str(expected) != str(actual):
        raise RuntimeError(
            f"account UID mismatch: pinned {expected}, exchange reports {actual} — "
            "wrong account/key, refusing to send")
    return f"UID verified ({actual})"


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
        # Exposure-INCREASING orders are capped; risk-reducing orders (incl.
        # kill-switch flatten of an oversized book) must never be blocked by
        # their own guard — but beyond 3x capital even a "close" is corrupt data.
        if abs(desired) > abs(cur) and abs(delta) > MAX_ORDER_NOTIONAL * capital:
            raise RuntimeError(
                f"{sym}: exposure-increasing order notional {abs(delta):.0f} exceeds "
                f"{MAX_ORDER_NOTIONAL:.0%} of capital — corrupted mark/price; refusing run")
        if abs(delta) > 3.0 * capital:
            raise RuntimeError(
                f"{sym}: order notional {abs(delta):.0f} exceeds 3x capital — "
                "venue state or marks are corrupt; refusing run")
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
    real = [p for p in plans if p["contracts"]]
    if len(real) > len(targets):  # structurally impossible: 1 order per symbol
        raise RuntimeError(f"{len(real)} real orders for {len(targets)} symbols; refusing")
    gross = sum(abs(p["notional"]) for p in real)
    if gross > MAX_GROSS * capital * 1.5:  # sanity: order flow itself absurd
        raise RuntimeError(f"planned order gross {gross:.0f} exceeds sanity bound; refusing")
    return plans


def check_reconciliation(current: dict[str, float], expected: dict,
                         capital: float, tol_frac: float = RECON_TOL) -> list[str]:
    """Venue positions must lie between last run's pre-trade state and its
    target (post-only fills may be partial). Pure logic, unit-tested.

    Returns violation notes; empty list = reconciled."""
    notes = []
    tol = tol_frac * capital
    pre_map = expected.get("pre", {})
    tgt_map = expected.get("target", {})
    for sym, tgt in tgt_map.items():
        pre = float(pre_map.get(sym, 0.0))
        lo, hi = min(pre, float(tgt)), max(pre, float(tgt))
        cur = float(current.get(sym, 0.0))
        if cur < lo - tol or cur > hi + tol:
            notes.append(
                f"{sym}: venue notional {cur:.2f} outside [{lo:.2f}, {hi:.2f}] ±{tol:.0f} "
                f"(pre {pre:.2f} -> target {tgt:.2f}) — manual trade, liquidation, "
                "or missed fill?")
    for sym, cur in current.items():
        if sym not in tgt_map and abs(float(cur)) > tol:
            notes.append(f"{sym}: unexpected venue position {float(cur):.2f} "
                         "(not in last run's book)")
    return notes


class OKXExecutor:
    def __init__(self, preset: BookPreset, capital: float,
                 state_dir: Path | str | None = None, maker_first: bool = True):
        self.preset = preset
        self.capital = capital
        self.maker_first = maker_first
        self.dir = Path(state_dir) if state_dir else DEFAULT_ROOT / preset.name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.halt_flag = self.dir / "HALTED"
        self.recon_flag = self.dir / "RECONCILE"
        self.hwm_file = self.dir / "hwm.json"
        self.expected_file = self.dir / "expected.json"
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

    def _place(self, p: dict):
        """Maker-first execution (E49: ~98% of our orders fill as post-only
        limits at the last close within the next bar, turning taker costs
        into rebates). Post-only at the current mid; the NEXT hourly run's
        reconciliation converts any unfilled remainder to market."""
        sym = _swap_symbol(p["symbol"])
        if self.maker_first:
            try:
                ticker = self.ex.fetch_ticker(sym)
                px = float(ticker.get("last") or ticker["close"])
                return self.ex.create_order(
                    sym, "limit", p["side"], p["contracts"], px,
                    params={"tdMode": "cross", "postOnly": True},
                )
            except Exception:  # noqa: BLE001 — post-only rejected: fall through
                pass
        return self.ex.create_order(sym, "market", p["side"], p["contracts"],
                                    params={"tdMode": "cross"})

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

        # Structural guard before ANY order path (including flatten): the
        # account we are about to trade must be the account we pinned.
        try:
            cfg = self.ex.privateGetAccountConfig()
            actual_uid = str((cfg.get("data") or [{}])[0].get("uid") or "")
        except Exception:  # noqa: BLE001 — unreadable config = unverifiable account
            actual_uid = ""
        print(f"  guard: {check_account_uid(os.environ.get('QTRADE_OKX_ACCOUNT_UID'), actual_uid, send)}")

        # Reconciliation: the venue's book must be explainable by our last run.
        if self.expected_file.exists():
            recon_notes = check_reconciliation(
                current, json.loads(self.expected_file.read_text()), managed)
            for n in recon_notes:
                print(f"  RECON WARN: {n}")
            if recon_notes and not self.recon_flag.exists():
                self.recon_flag.write_text(
                    f"{now}\n" + "\n".join(recon_notes) +
                    "\nVenue state is not explainable by the last run. Investigate "
                    "(manual trades? liquidation? missed fill?), then remove this "
                    "file to re-enable sending.\n")
        if self.recon_flag.exists() and send:
            print(f"  RECONCILE flag present ({self.recon_flag}) — sending disabled "
                  "(flatten included) until a human reviews and removes it")
            send = False

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
            order = self._place(p)
            pd.DataFrame([{**p, "ts": now, "order_id": order.get("id"),
                           "status": order.get("status")}]).to_csv(
                self.orders_file, mode="a",
                header=not self.orders_file.exists(), index=False)
            from .tca import record_fill
            record_fill(self.dir, now, p["symbol"], p["side"], p["contracts"],
                        closes.get(p["symbol"]), order)

        if send and any(p["contracts"] for p in plans):
            # snapshot for next run's reconciliation: fills may be partial, so
            # the venue book must land between `pre` and `target`
            self.expected_file.write_text(json.dumps({
                "ts": now,
                "pre": {s: round(current.get(s, 0.0), 2) for s in self.preset.symbols},
                "target": {s: round(targets[s] * managed, 2) for s in self.preset.symbols},
            }, indent=2))

        pd.DataFrame([{"ts": now, "usdt_equity": usdt_equity, "managed": managed,
                       "sent": send, "n_orders": sum(1 for p in plans if p["contracts"])}]
                     ).to_csv(self.equity_file, mode="a",
                              header=not self.equity_file.exists(), index=False)
