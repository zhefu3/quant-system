"""E63 observation book: convertible-bond double-low rotation, live on paper.

The research script (research/cb_double_low.py) IS the frozen protocol —
this book imports its eligibility filter and score so live and research
cannot drift. Accounting reuses ashare_ml's dict-based rebalance/mark
(double-entry: E63's frozen costs on the headline, zero-cost gross shadow).

Cadence: one decision per calendar month (lowest-20 double-low, equal
weight); daily marks refresh only the held bonds' quotes (~20 akshare calls).
Decision day refreshes the master list and every plausibly-live bond's value
history (~500 calls, polite sleep — HANDOFF hard rule).
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .ashare_ml import mark, rebalance
from .risk import RiskGate
from .timeouts import call_with_timeout as _call_with_timeout

REPO = Path(__file__).resolve().parents[2]
CB = REPO / "data_store" / "cn_cb"
ROOT = REPO / "outputs" / "paper" / "cb_double_low"
SLEEP = 0.15

spec = importlib.util.spec_from_file_location("cb_double_low",
                                              REPO / "research" / "cb_double_low.py")
R = importlib.util.module_from_spec(spec)
sys.modules.setdefault("cb_double_low", R)
spec.loader.exec_module(R)


def _refresh_values(codes: list[str], quiet: bool = True) -> int:
    import akshare as ak

    n = 0
    for code in codes:
        try:
            v = _call_with_timeout(ak.bond_zh_cov_value_analysis, 60.0, symbol=code)
            if v is not None and len(v):
                v.to_parquet(CB / "value" / f"{code}.parquet")
                n += 1
        except Exception:  # noqa: BLE001 — one bond must not kill the tick
            pass
        time.sleep(SLEEP)
    return n


def _live_candidates(master: pd.DataFrame) -> list[str]:
    """Bonds whose value history is recent (or brand-new listings)."""
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=40)
    out = []
    for code in master["债券代码"].astype(str):
        f = CB / "value" / f"{code}.parquet"
        if not f.exists():
            out.append(code)
            continue
        v = pd.read_parquet(f)
        if len(v) and pd.to_datetime(v["日期"].iloc[-1]) >= cutoff:
            out.append(code)
    return out


class CbBook:
    def __init__(self, state_dir: Path | str | None = None, init_cash: float = 10_000.0):
        self.dir = Path(state_dir) if state_dir else ROOT
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "monthly").mkdir(exist_ok=True)
        self.state_file = self.dir / "state.json"
        self.equity_file = self.dir / "equity.csv"
        self.trades_file = self.dir / "trades.csv"
        self.init_cash = init_cash

    def _state(self) -> dict:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text())
        return {"cash": self.init_cash, "positions": {},
                "gross_cash": self.init_cash, "gross_positions": {}, "last_mark": None}

    def _latest(self, codes) -> dict[str, float]:
        out = {}
        for c in codes:
            f = CB / "value" / f"{c}.parquet"
            if f.exists():
                v = pd.read_parquet(f)
                if len(v):
                    out[c] = float(pd.to_numeric(v["收盘价"], errors="coerce").iloc[-1])
        return out

    def _quote_days(self, codes) -> dict[str, str]:
        out = {}
        for c in codes:
            f = CB / "value" / f"{c}.parquet"
            if f.exists():
                v = pd.read_parquet(f)
                if len(v):
                    out[c] = pd.to_datetime(v["日期"].iloc[-1]).strftime("%Y-%m-%d")
        return out

    def _fresh(self, codes) -> tuple[set[str], str | None]:
        """(codes whose latest quote matches the market's newest quote date,
        that ref date). The reference is the POOL's max quote date, not the
        wall clock — quotes publish after the close and decision ticks can
        fire on weekends (rehearsal finding, 2026-07-21). A bond lagging the
        pool's newest date is suspended/delisted; trading it would fabricate
        a fill at a price that no longer exists. CB price bands not modeled
        (no fresh OHLC in value files; 一字板 rare in CBs — recorded)."""
        days = self._quote_days(codes)
        if not days:
            return set(), None
        ref = max(days.values())
        return {c for c, d in days.items() if d == ref}, ref

    def tick(self) -> dict:
        from ..presets import PRESETS
        import akshare as ak

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state = self._state()
        month_key = pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m")
        cache = self.dir / "monthly" / f"{month_key}.json"

        if not cache.exists():
            master = _call_with_timeout(ak.bond_zh_cov, 120.0)
            master.to_parquet(CB / "bonds.parquet")
            live = _live_candidates(master)
            print(f"[{now}] cb decision {month_key}: refreshing {len(live)} live bonds")
            _refresh_values(live)
            master = pd.read_parquet(CB / "bonds.parquet")
            master["code"] = master["债券代码"].astype(str)
            master = master.set_index("code")
            d = pd.Timestamp.now()
            cand = []
            for code in live:
                f = CB / "value" / f"{code}.parquet"
                if not f.exists():
                    continue
                v = pd.read_parquet(f)
                if not len(v):
                    continue
                px = float(pd.to_numeric(v["收盘价"], errors="coerce").iloc[-1])
                prem = float(pd.to_numeric(v["转股溢价率"], errors="coerce").iloc[-1])
                if pd.isna(px) or pd.isna(prem) or not R.eligible(master, code, d, px):
                    continue
                cand.append((code, px + prem))
            picks = [c for c, _ in sorted(cand, key=lambda kv: kv[1])[:R.K]]
            weights = {c: 1.0 / len(picks) for c in picks} if len(picks) >= R.K else {}
            gate = RiskGate(PRESETS["cb_double_low"].risk, self.dir)
            closes = self._latest(set(weights) | set(state["positions"]))
            net, _ = mark(state, closes)
            hwm = max(float(pd.read_csv(self.equity_file)["equity"].max()), net) \
                if self.equity_file.exists() else net
            weights, notes = gate.apply(weights, net / hwm - 1)
            # execution realism: stale-quoted names cannot trade at a price
            # that no longer exists — freeze and queue for the daily retry
            from . import cn_exec
            fresh, ref_day = self._fresh(set(weights) | set(state["positions"]))
            verdicts = {c: ("fill" if c in fresh else "suspended")
                        for c in set(weights) | set(state["positions"])}
            frozen, pending = cn_exec.split_executable(weights, state["positions"],
                                                       verdicts)
            state, fills = rebalance(state, weights, closes, fee=R.FEE,
                                     slip=R.SLIP, frozen=frozen)
            state["pending"] = pending
            state["last_retry_day"] = ref_day  # decision IS today's attempt
            for c in frozen:
                cn_exec.log_attempt(self.dir, now, c,
                                    "buy" if c in weights else "sell",
                                    "suspended", closes.get(c), None, 0)
            cache.write_text(json.dumps({"month": month_key, "n_pool": len(cand),
                                         "weights": weights,
                                         "deferred": sorted(pending)}, indent=2))
            for f in fills:
                pd.DataFrame([{**f, "ts": now}]).to_csv(
                    self.trades_file, mode="a",
                    header=not self.trades_file.exists(), index=False)
            print(f"[{now}] cb decision: pool {len(cand)}, {len(fills)} fills")
            for n_ in notes:
                print(f"  RISK: {n_}")

        today_cn = pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d")
        if state.get("last_mark") == today_cn:
            return {"ts": now, "skipped": "already marked"}
        held = set(state["positions"]) | set(state.get("gross_positions", {}))
        pending = dict(state.get("pending", {}))
        if held or pending:
            _refresh_values(sorted(held | set(pending)))

        # retry deferred orders whose quotes are fresh again — at most one
        # attempt per new market quote date
        if pending:
            from . import cn_exec
            fresh, ref_day = self._fresh(set(pending) | set(state["positions"]))
            if ref_day is None or state.get("last_retry_day") == ref_day:
                fresh = set()
            else:
                state["last_retry_day"] = ref_day
            retry_no = int(state.get("pending_retries", 0)) + 1
            for code in sorted(set(pending)):
                if code not in fresh:
                    continue
                closes_c = self._latest(set(state["positions"]) | {code})
                state, fills = rebalance(state, {code: pending[code]}, closes_c,
                                         fee=R.FEE, slip=R.SLIP, only={code})
                for f in fills:
                    pd.DataFrame([{**f, "ts": now}]).to_csv(
                        self.trades_file, mode="a",
                        header=not self.trades_file.exists(), index=False)
                    cn_exec.log_attempt(self.dir, now, code,
                                        "buy" if f["qty"] > 0 else "sell",
                                        "fill_retry", closes_c.get(code),
                                        f["price"], retry_no)
                pending.pop(code)
                print(f"  deferred CB order filled on retry {retry_no}: {code}")
            state["pending"] = pending
            state["pending_retries"] = retry_no
        closes = self._latest(held)
        net, gross = mark(state, closes)
        pd.DataFrame([{"ts": now, "equity": round(net, 2),
                       "gross_equity": round(gross, 2),
                       "n_positions": len(state["positions"])}]).to_csv(
            self.equity_file, mode="a", header=not self.equity_file.exists(), index=False)
        state["last_mark"] = today_cn
        self.state_file.write_text(json.dumps(state, indent=2))
        print(f"[{now}] cb_double_low equity {net:.2f} (gross {gross:.2f}) "
              f"positions {len(state['positions'])}")
        return {"ts": now, "equity": net}


def run_tick(state_dir: str | None = None) -> dict:
    return CbBook(state_dir=state_dir).tick()
