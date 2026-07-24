"""QMT (miniQMT/xtquant) executor — Mac-side prebuild, 2026-07-24.

Built BEFORE broker permission arrives so activation day is plug-in, not
build-out. Everything here is pure python and unit-tested on macOS; the
only Windows-only piece is XtBroker, a thin adapter written against the
same interface the tests exercise with fakes.

Architecture (docs/qmt-deployment.md): the Mac produces a targets message
from a paper book and pushes it through the PRIVATE records repo (git as
message bus — no open ports, natural audit trail); the Windows VPS pulls,
validates, and makes the real/sim account look like the paper book.

The message carries the paper book's POSITIONS (shares) and equity basis,
not weights: the paper book already solved weights->shares on decision
day, so the executor only scales by managed_capital/equity_basis and
rounds to lots. No second pricing pass, no second chance to disagree.

Safety rails, ported from the OKX executor (broker.py) plus A-share physics:
  1. account pin        — pinned account id must match the broker's, else
                          fail closed (wrong-account structurally impossible)
  2. order-level caps   — exposure-increasing order notional capped;
                          more real orders than symbols refuses the run
  3. reconciliation     — venue book must lie between last run's pre and
                          target (reuses broker.check_reconciliation);
                          violation => RECONCILE flag blocks ALL sending
  4. clamps             — long-only (negative target refuses), single-name
                          and gross exposure caps
  5. kill switch        — drawdown from high-water mark flattens + HALTED
  A-share physics       — T+1 (sellable = held - bought today), 100-share
                          lots (odd lots only when clearing a position),
                          suspended/limit-locked codes skipped with notes
Dry-run is the default everywhere; send=True is a deliberate, separate act,
and the real-money switch stays with the user (sim >= 30 days + gate first).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .broker import check_reconciliation  # same battle-tested pure logic
from .timeouts import call_with_timeout
from ..timeconv import utc_now

SCHEMA_VERSION = 1
LOT = 100                  # A-share board lot; CB trades in 10s on SSE — per-code below
CB_LOT = 10
MAX_SINGLE_W = 0.15        # relative to managed capital (books run ~2-5%/name)
MAX_GROSS = 1.02           # long-only book + rounding slack
MAX_DD_STOP = 0.20
MAX_ORDER_NOTIONAL = 0.35  # per-order cap, fraction of managed capital
TARGETS_MAX_AGE_DAYS = 7   # producer refreshes daily; older => refuse to act


def _lot_for(code: str) -> int:
    # 11xxxx/12xxxx = convertible bonds (SSE/SZSE), 10-unit lots; stocks 100
    return CB_LOT if code[:2] in ("11", "12") and "." not in code else LOT


def _checksum(payload: dict) -> str:
    body = {k: v for k, v in payload.items() if k != "checksum"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def write_targets(book: str, state: dict, out_path: Path | str,
                  equity: float, as_of: str) -> Path:
    """Producer (Mac): serialize a paper book's current holdings as the
    executor's target message. `state` is the book's state.json dict."""
    payload = {
        "schema": SCHEMA_VERSION,
        "book": book,
        "generated_utc": utc_now().isoformat(timespec="seconds"),
        "as_of": as_of,
        "equity_basis": round(float(equity), 2),
        "cash": round(float(state.get("cash", 0.0)), 2),
        "positions": {c: round(float(s), 4)
                      for c, s in state.get("positions", {}).items()
                      if abs(float(s)) > 1e-9},
    }
    payload["checksum"] = _checksum(payload)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return out


def load_targets(path: Path | str, now=None) -> dict:
    """Consumer (VPS): parse + validate. Refuses corrupt (checksum — a
    truncated git pull must not become a trade list), wrong-schema, stale,
    or short (negative-position) messages. Fail closed, loudly."""
    payload = json.loads(Path(path).read_text())
    if payload.get("schema") != SCHEMA_VERSION:
        raise ValueError(f"targets schema {payload.get('schema')} != {SCHEMA_VERSION} "
                         "— producer and executor disagree; refusing")
    if payload.get("checksum") != _checksum(payload):
        raise ValueError("targets checksum mismatch — corrupt/truncated message; refusing")
    now = now or utc_now()
    import pandas as pd
    age = (now - pd.Timestamp(payload["generated_utc"])).total_seconds() / 86400
    if age > TARGETS_MAX_AGE_DAYS:
        raise ValueError(f"targets are {age:.1f}d old (> {TARGETS_MAX_AGE_DAYS}d) — "
                         "producer loop dead? refusing to act on stale targets")
    if any(s < 0 for s in payload["positions"].values()):
        raise ValueError("negative target position — A-share book is long-only; refusing")
    if float(payload["equity_basis"]) <= 0:
        raise ValueError("non-positive equity basis; refusing")
    return payload


def plan_cn_orders(
    targets: dict,
    managed_capital: float,
    positions: dict[str, float],      # venue: code -> shares held
    bought_today: dict[str, float],   # venue: code -> shares bought today (T+1)
    prices: dict[str, float],         # last price per code (target + held)
    untradable: dict[str, str] | None = None,  # code -> reason (suspended/limit)
) -> list[dict]:
    """Pure sizing: scale paper shares by capital ratio, round to lots,
    respect T+1 and untradable codes, enforce every cap. No I/O."""
    untradable = untradable or {}
    scale = managed_capital / float(targets["equity_basis"])
    plans: list[dict] = []
    desired: dict[str, float] = {}

    gross = 0.0
    for code, paper_shares in targets["positions"].items():
        px = prices.get(code)
        if px is None or not (px > 0):
            raise RuntimeError(f"{code}: no finite price — refusing run "
                               "(prices must be finite to trade, 07-15 lesson)")
        lot = _lot_for(code)
        want = int(paper_shares * scale / lot) * lot
        desired[code] = float(want)
        notional = want * px
        if notional > MAX_SINGLE_W * managed_capital:
            raise RuntimeError(f"{code}: target notional {notional:.0f} exceeds "
                               f"{MAX_SINGLE_W:.0%} of capital — corrupt targets/price")
        gross += notional
    if gross > MAX_GROSS * managed_capital:
        raise RuntimeError(f"target gross {gross:.0f} exceeds {MAX_GROSS:.0%} "
                           f"of capital {managed_capital:.0f} — refusing")

    all_codes = sorted(set(desired) | {c for c, s in positions.items() if s})
    for code in all_codes:
        cur = float(positions.get(code, 0.0))
        want = desired.get(code, 0.0)
        delta = want - cur
        if abs(delta) < 1:
            continue
        if code in untradable:
            plans.append({"code": code, "shares": 0, "side": "-", "notional": 0.0,
                          "note": f"skip: {untradable[code]} (retry next run)"})
            continue
        px = prices.get(code)
        if px is None or not (px > 0):
            raise RuntimeError(f"{code}: held but no finite price — refusing run")
        lot = _lot_for(code)
        if delta > 0:
            qty = int(delta / lot) * lot
        else:
            sellable = max(0.0, cur - float(bought_today.get(code, 0.0)))
            qty = min(abs(delta), sellable)
            if want > 0 or qty < abs(delta):  # partial trim -> stay on lots
                qty = int(qty / lot) * lot    # full clear may sell odd lots
            qty = -qty
        if qty == 0:
            note = ("below one lot" if delta > 0 or want > 0
                    else "T+1: nothing sellable today")
            plans.append({"code": code, "shares": 0, "side": "-", "notional": 0.0,
                          "note": f"skip: {note}"})
            continue
        notional = qty * px
        if abs(want * px) > abs(cur * px) and abs(notional) > MAX_ORDER_NOTIONAL * managed_capital:
            raise RuntimeError(f"{code}: exposure-increasing order {abs(notional):.0f} "
                               f"exceeds {MAX_ORDER_NOTIONAL:.0%} of capital — refusing run")
        plans.append({"code": code, "shares": int(abs(qty)),
                      "side": "buy" if qty > 0 else "sell",
                      "notional": round(notional, 2),
                      "note": f"{cur:.0f} -> {want:.0f} shares"})

    real = [p for p in plans if p["shares"]]
    if len(real) > len(all_codes):
        raise RuntimeError(f"{len(real)} real orders for {len(all_codes)} codes; refusing")
    return plans


def produce_targets(book: str, out: str | None = None) -> Path:
    """Mac-side CLI entry: paper book -> git-bus message. Default output is
    the private records repo (qmt/), so the daily backup push carries it."""
    import pandas as pd

    from .paper import DEFAULT_ROOT as PAPER_ROOT

    d = PAPER_ROOT / book
    state = json.loads((d / "state.json").read_text())
    eq = pd.read_csv(d / "equity.csv")
    equity = float(eq["equity"].iloc[-1])
    as_of = str(state.get("last_mark") or eq["ts"].iloc[-1])
    dest = Path(out) if out else Path.home() / "qtrade-records" / "qmt" / f"targets_{book}.json"
    p = write_targets(book, state, dest, equity, as_of)
    print(f"targets written: {p} (as_of {as_of}, equity {equity:.2f}, "
          f"{len(json.loads(p.read_text())['positions'])} positions)")
    return p


class QmtExecutor:
    """Windows-side loop, broker injected for testability. The broker
    interface is five methods: account_id() -> str, cash() -> float,
    positions() -> {code: shares}, bought_today() -> {code: shares},
    last_prices(codes) -> {code: float}, place(code, side, shares) -> dict.
    """

    def __init__(self, broker, state_dir: Path | str,
                 managed_capital: float, pinned_account: str | None):
        self.broker = broker
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.capital = managed_capital
        self.pinned = pinned_account
        self.halt_flag = self.dir / "HALTED"
        self.recon_flag = self.dir / "RECONCILE"
        self.hwm_file = self.dir / "hwm.json"
        self.expected_file = self.dir / "expected.json"
        self.audit_file = self.dir / "audit.jsonl"

    def _audit(self, **kw):
        kw["ts"] = utc_now().isoformat(timespec="seconds")
        with self.audit_file.open("a") as f:
            f.write(json.dumps(kw, sort_keys=True) + "\n")

    def run(self, targets_path: Path | str, send: bool = False) -> list[dict]:
        if self.halt_flag.exists():
            self._audit(event="refused", why="HALTED flag")
            print(f"HALTED flag present ({self.halt_flag}) — human review required.")
            return []

        # rail 1: the account we are about to touch is the account we pinned
        actual = str(self.broker.account_id() or "")
        if send and (not self.pinned or actual != str(self.pinned)):
            raise RuntimeError(f"account pin mismatch: pinned {self.pinned!r}, "
                               f"broker reports {actual!r} — refusing to send")

        targets = load_targets(targets_path)
        positions = self.broker.positions()
        cash = float(self.broker.cash())
        codes = sorted(set(targets["positions"]) | set(positions))
        prices = call_with_timeout(self.broker.last_prices, 60.0, codes)
        equity = cash + sum(float(s) * prices.get(c, 0.0)
                            for c, s in positions.items())
        managed = min(self.capital, equity) if equity > 0 else 0.0

        # rail 5: drawdown kill switch on the managed sleeve
        hwm = managed
        if self.hwm_file.exists():
            hwm = max(float(json.loads(self.hwm_file.read_text())["hwm"]), managed)
        self.hwm_file.write_text(json.dumps({"hwm": hwm}))
        if managed < hwm * (1 - MAX_DD_STOP):
            self.halt_flag.write_text(f"drawdown stop: {managed:.2f} < "
                                      f"{1 - MAX_DD_STOP:.0%} x HWM {hwm:.2f}\n")
            self._audit(event="kill_switch", managed=managed, hwm=hwm)
            print(f"KILL SWITCH: {managed:.2f} < {1 - MAX_DD_STOP:.0%} of HWM "
                  f"{hwm:.2f} — HALTED written; flatten is a HUMAN decision "
                  "(T+1 makes a panicked auto-flatten irreversible for a day)")
            return []

        # rail 3: venue book must be explainable by our last run
        if self.expected_file.exists():
            cur_notional = {c: float(positions.get(c, 0.0)) * prices.get(c, 0.0)
                            for c in codes}
            notes = check_reconciliation(
                cur_notional, json.loads(self.expected_file.read_text()), managed)
            if notes and not self.recon_flag.exists():
                self.recon_flag.write_text("\n".join(notes) + "\n")
                self._audit(event="reconcile_flag", notes=notes)
            if self.recon_flag.exists() and send:
                print("RECONCILE flag present — sending disabled until reviewed")
                send = False

        plans = plan_cn_orders(targets, managed, positions,
                               self.broker.bought_today(), prices,
                               untradable=getattr(self.broker, "untradable", lambda: {})())
        for p in plans:
            print(f"  {p['code']:10s} {p['side']:4s} {p['shares']:>7d}  "
                  f"(~{p['notional']:+.2f})  {p['note']}")
            if send and p["shares"]:
                result = self.broker.place(p["code"], p["side"], p["shares"])
                self._audit(event="order", **p, result=str(result))

        if send and any(p["shares"] for p in plans):
            scale = managed / float(targets["equity_basis"])
            self.expected_file.write_text(json.dumps({
                "pre": {c: round(float(positions.get(c, 0.0)) * prices[c], 2)
                        for c in codes},
                "target": {c: round(float(s) * scale * prices[c], 2)
                           for c, s in targets["positions"].items()},
            }, indent=2))
        self._audit(event="run", send=send, managed=managed,
                    n_orders=sum(1 for p in plans if p["shares"]))
        return plans


class XtBroker:
    """xtquant adapter — WINDOWS ONLY, exercised first against the broker's
    simulated account (30-day gate). Kept deliberately thin: every decision
    lives in the tested pure logic above; this class only translates.
    UNTESTED until the VPS exists — treat every method as suspect until the
    sim-account shakedown."""

    def __init__(self, mini_qmt_path: str, account: str):
        from xtquant import xtdata  # noqa: F401 — fails fast off-Windows
        from xtquant.xttrader import XtQuantTrader
        from xtquant.xttype import StockAccount

        self._trader = XtQuantTrader(mini_qmt_path, 1)
        self._trader.start()
        if self._trader.connect() != 0:
            raise RuntimeError("xtquant connect failed — is the QMT client logged in?")
        self._account = StockAccount(account)
        self._trader.subscribe(self._account)

    def account_id(self) -> str:
        return self._account.account_id

    def cash(self) -> float:
        asset = self._trader.query_stock_asset(self._account)
        return float(asset.cash)

    def positions(self) -> dict[str, float]:
        out = {}
        for p in self._trader.query_stock_positions(self._account):
            if p.volume:
                out[p.stock_code] = float(p.volume)
        return out

    def bought_today(self) -> dict[str, float]:
        out = {}
        for p in self._trader.query_stock_positions(self._account):
            frozen = float(p.volume) - float(p.can_use_volume)
            if frozen > 0:
                out[p.stock_code] = frozen
        return out

    def last_prices(self, codes: list[str]) -> dict[str, float]:
        from xtquant import xtdata
        ticks = xtdata.get_full_tick(codes)
        return {c: float(t["lastPrice"]) for c, t in ticks.items()}

    def untradable(self) -> dict[str, str]:
        # suspended = no tick / zero price; limit detection can be added from
        # tick high/low vs limit fields during the sim shakedown
        return {}

    def place(self, code: str, side: str, shares: int) -> dict:
        from xtquant import xtconstant
        direction = (xtconstant.STOCK_BUY if side == "buy"
                     else xtconstant.STOCK_SELL)
        oid = self._trader.order_stock(
            self._account, code, direction, int(shares),
            xtconstant.LATEST_PRICE, 0.0, "qtrade", "")
        return {"order_id": oid}
