"""E61 observation book: E47's LightGBM index-enhancement, live on paper.

E47 verdict (archived-marginal): gross excess +6.3%/yr is real, retail costs
eat it to +2.5% — below the 3%/yr gate. This book builds the forward record
that (a) tests whether the signal decays out-of-sample-in-time, and (b) is
ready the day a low-commission channel makes an E49 revival test pass.

Protocol is E47 verbatim (frozen): LightGBM with frozen hyperparameters,
expanding-window training, QUARTERLY refits (first decision of Jan/Apr/Jul/
Oct; other months reuse the cached model), month-end features -> next-month
book, top-50 equal weight within point-in-time HS300 membership.

Double-entry accounting: the headline equity pays E47's frozen retail costs;
a zero-cost GROSS shadow equity is recorded alongside, separating "signal
decay" from "cost drag" — the two questions this book exists to answer.

Known simplifications (E61 prereg): T+1 and price-limit locks are not
simulated (monthly top-50 turnover ~1.06 makes both negligible; revisit if
paper drifts from expectation). Fractional shares allowed on paper.

This book does NOT use PaperTrader (its universe is ~300 changing names, not
a fixed symbol list); it keeps the same on-disk record format (state.json /
equity.csv / trades.csv) so health checks and reports work unchanged.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .risk import RiskGate

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "outputs" / "paper" / "ashare_ml"
MONTHLY = ROOT / "monthly"
PIT = REPO / "data_store" / "pit_ts"

TOP_K = 50
FEE = 0.0008        # E47 frozen: commission ~0.025% + stamp duty, symmetrized
SLIP = 0.001        # E47 frozen slippage per side
BENCH = "000300.SH"
REFIT_MONTHS = (1, 4, 7, 10)  # quarterly refit anchors
STALE_TRADING_DAYS = 5


def _ml_enhance():
    """Import research/ml_enhance.py (E47's pipeline) as a module — the
    archived research code IS the frozen protocol; no re-implementation."""
    spec = importlib.util.spec_from_file_location(
        "ml_enhance", REPO / "research" / "ml_enhance.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("ml_enhance", mod)
    spec.loader.exec_module(mod)
    return mod


# -- pure helpers (unit-tested) -------------------------------------------------

def top_k_weights(scores: pd.Series, members: set[str], k: int = TOP_K) -> dict[str, float]:
    """Equal-weight top-k by predicted score within current membership."""
    s = scores[[c for c in scores.index if c in members]].dropna()
    picks = s.sort_values(ascending=False).head(k)
    if picks.empty:
        return {}
    w = 1.0 / len(picks)
    return {c: w for c in picks.index}


def rebalance(state: dict, weights: dict[str, float], closes: dict[str, float],
              fee: float = FEE, slip: float = SLIP) -> tuple[dict, list[dict]]:
    """Simulate fills at close with E47's frozen costs. Returns (state, fills).
    Gross shadow (cost-free) is tracked in state['gross_positions']/'gross_cash'."""
    cash, pos = state["cash"], dict(state["positions"])
    gcash = state.get("gross_cash", cash)
    gpos = dict(state.get("gross_positions", pos))
    equity = cash + sum(q * closes.get(c, 0.0) for c, q in pos.items())
    fills = []
    for code in sorted(set(pos) | set(weights)):
        px = closes.get(code)
        if px is None or px <= 0:
            continue
        # trade to the exact target QUANTITY (slippage hits cash, not shares) —
        # dividing notional by the slipped price would overshoot exits and
        # leave a phantom short in a long-only book
        tgt_qty = weights.get(code, 0.0) * equity / px
        qty = tgt_qty - pos.get(code, 0.0)
        if abs(qty * px) < 1e-9:
            continue
        side = 1 if qty > 0 else -1
        fill_px = px * (1 + side * slip)
        cost = abs(qty * px) * fee
        cash -= qty * fill_px + cost
        # gross shadow: same target quantity, zero fee/slip
        gcash -= (tgt_qty - gpos.get(code, 0.0)) * px
        if tgt_qty == 0.0:
            pos.pop(code, None)
            gpos.pop(code, None)
        else:
            pos[code] = tgt_qty
            gpos[code] = tgt_qty
        fills.append({"symbol": code, "qty": round(qty, 4), "price": round(fill_px, 4),
                      "fee": round(cost, 4), "target_w": round(weights.get(code, 0.0), 4)})
    state = dict(state, cash=cash, positions=pos, gross_cash=gcash, gross_positions=gpos)
    return state, fills


def mark(state: dict, closes: dict[str, float]) -> tuple[float, float]:
    """(net_equity, gross_shadow_equity) at the given closes."""
    net = state["cash"] + sum(q * closes.get(c, 0.0) for c, q in state["positions"].items())
    gross = state.get("gross_cash", state["cash"]) + sum(
        q * closes.get(c, 0.0) for c, q in state.get("gross_positions",
                                                     state["positions"]).items())
    return net, gross


# -- tushare incremental refresh --------------------------------------------------

def _pro():
    import tushare as ts

    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise EnvironmentError("TUSHARE_TOKEN not set (see HANDOFF)")
    return ts.pro_api(token)


def refresh_daily(pro, store, union: list[str], last: pd.Timestamp) -> int:
    """One pro.daily / pro.daily_basic call per missing trade date (batch API:
    each call returns the whole market). Idempotent via BarStore merge."""
    added = 0
    day = last + pd.Timedelta(days=1)
    today = pd.Timestamp.now(tz="Asia/Shanghai").normalize().tz_localize(None)
    uni = set(union)
    while day <= today:
        ds = day.strftime("%Y%m%d")
        bars = pro.daily(trade_date=ds)
        if bars is not None and len(bars):
            bars = bars[bars["ts_code"].isin(uni)]
            for code, g in bars.groupby("ts_code"):
                idx = pd.to_datetime(g["trade_date"]).dt.tz_localize("UTC")
                df = pd.DataFrame({"open": g["open"].values, "high": g["high"].values,
                                   "low": g["low"].values, "close": g["close"].values,
                                   "volume": g["vol"].values},
                                  index=pd.DatetimeIndex(idx, name="ts"))
                store.save(df, "ashare_ts", code, "1d")
            db = pro.daily_basic(trade_date=ds,
                                 fields="ts_code,trade_date,pe_ttm,pb,total_mv,turnover_rate")
            if db is not None and len(db):
                db = db[db["ts_code"].isin(uni)]
                for code, g in db.groupby("ts_code"):
                    f = PIT / "daily_basic" / f"{code.replace('.', '_')}.parquet"
                    if f.exists():
                        old = pd.read_parquet(f)
                        g = pd.concat([old, g]).drop_duplicates(
                            subset="trade_date", keep="last")
                    f.parent.mkdir(parents=True, exist_ok=True)
                    g.reset_index(drop=True).to_parquet(f)
            added += 1
        day += pd.Timedelta(days=1)
    return added


def refresh_monthly_pit(pro, union: list[str]) -> None:
    """Decision-day only: membership snapshot + fundamentals (ann_date PIT)."""
    now = pd.Timestamp.now(tz="Asia/Shanghai")
    w = pro.index_weight(index_code=BENCH,
                         start_date=(now - pd.Timedelta(days=45)).strftime("%Y%m%d"),
                         end_date=now.strftime("%Y%m%d"))
    if w is not None and len(w):
        f = PIT / "hs300_weights.parquet"
        old = pd.read_parquet(f)
        merged = pd.concat([old, w.rename(columns={"index_code": "index_code"})])
        merged = merged.drop_duplicates(subset=["trade_date", "con_code"], keep="last")
        merged.to_parquet(f)
    import time

    (PIT / "fina").mkdir(parents=True, exist_ok=True)
    for code in union:
        try:
            fi = pro.fina_indicator(ts_code=code, fields="ts_code,ann_date,end_date,"
                                                         "roe,netprofit_yoy")
            if fi is not None and len(fi):
                fi.to_parquet(PIT / "fina" / f"{code.replace('.', '_')}.parquet")
        except Exception:  # noqa: BLE001 — one symbol must not kill the refresh
            pass
        time.sleep(0.12)  # polite rate limit (HANDOFF hard rule)


# -- monthly decision --------------------------------------------------------------

def decide_month(month_key: str) -> dict[str, float]:
    """Run E47's pipeline: train on all completed months, predict the latest
    month-end cross-section, pick top-50 in current membership."""
    import lightgbm as lgb

    M = _ml_enhance()
    closes, vols, panels, membership = M.load_panels()
    feats = M.build_features(closes, vols, panels)

    m_close = closes.resample("ME").last()
    fwd = m_close.pct_change().shift(-1)
    member_map = {s: set(g["code"]) for s, g in membership.groupby("snap")}
    snap_dates = sorted(member_map)

    rows, pred_rows = [], []
    for d in m_close.index:
        snap = max((s for s in snap_dates if pd.Timestamp(s, tz="UTC") <= d), default=None)
        if snap is None or len(closes.loc[:d]) < 260:
            continue
        di = closes.loc[:d].index[-1]
        fvals = {name: f.loc[di] for name, f in feats.items()}
        members = [c for c in member_map[snap] if c in closes.columns]
        for c in members:
            row = {name: fvals[name].get(c) for name in feats}
            row.update({"date": d, "code": c, "target": fwd.loc[d].get(c)})
            (pred_rows if d == m_close.index[-1] else rows).append(row)
    import numpy as np

    train = pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan).dropna(subset=["target"])
    cur = pd.DataFrame(pred_rows).replace([np.inf, -np.inf], np.nan)
    feat_cols = list(feats)

    model_file = ROOT / "model.pkl"
    month = int(month_key.split("-")[1])
    if model_file.exists() and month not in REFIT_MONTHS:
        model = pickle.loads(model_file.read_bytes())
    else:
        cut = int(len(train) * 0.9)
        model = lgb.LGBMRegressor(**M.LGB_PARAMS)
        model.fit(train[feat_cols].iloc[:cut], train["target"].iloc[:cut],
                  eval_set=[(train[feat_cols].iloc[cut:], train["target"].iloc[cut:])],
                  callbacks=[lgb.early_stopping(50, verbose=False)])
        model_file.parent.mkdir(parents=True, exist_ok=True)
        model_file.write_bytes(pickle.dumps(model))

    scores = pd.Series(model.predict(cur[feat_cols]), index=cur["code"].values)
    latest_snap = max(snap_dates)
    return top_k_weights(scores, member_map[latest_snap])


# -- the book ---------------------------------------------------------------------

class AshareMlBook:
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

    def tick(self) -> dict:
        from ..data.store import BarStore
        from ..presets import PRESETS

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        store = BarStore()
        weights_pq = pd.read_parquet(PIT / "hs300_weights.parquet")
        union = sorted(weights_pq["con_code"].unique())

        # incremental data refresh (cheap: one API call per missing trade date)
        bench_probe = store.load("ashare_index", "SH_000300", "1d")
        last_bar = store.load("ashare_ts", union[0], "1d").index[-1].tz_localize(None)
        pro = _pro()
        refresh_daily(pro, store, union, last_bar)
        self._refresh_bench(pro, bench_probe, store)

        # monthly decision (cached); PIT refresh only on decision day
        month_key = pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m")
        cache = self.dir / "monthly" / f"{month_key}.json"
        state = self._state()
        if not cache.exists():
            refresh_monthly_pit(pro, union)
            weights = decide_month(month_key)
            gate = RiskGate(PRESETS["ashare_ml"].risk, self.dir)
            hwm = self._hwm()
            closes = self._latest_closes(store, set(weights) | set(state["positions"]))
            net, _ = mark(state, closes)
            weights, notes = gate.apply(weights, net / max(hwm, net) - 1)
            state, fills = rebalance(state, weights, closes)
            cache.write_text(json.dumps({"month": month_key, "weights": weights,
                                         "n": len(weights)}, indent=2))
            for f in fills:
                pd.DataFrame([{**f, "ts": now}]).to_csv(
                    self.trades_file, mode="a",
                    header=not self.trades_file.exists(), index=False)
            print(f"[{now}] ashare_ml decision {month_key}: {len(weights)} names, "
                  f"{len(fills)} fills")
            for n in notes:
                print(f"  RISK: {n}")

        # daily mark-to-market (skip if today already marked)
        today_cn = pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d")
        if state.get("last_mark") == today_cn:
            print(f"[{now}] ashare_ml already marked {today_cn}")
            return {"ts": now, "skipped": "already marked"}
        closes = self._latest_closes(store, set(state["positions"]) |
                                     set(state.get("gross_positions", {})))
        net, gross = mark(state, closes)
        bench = store.load("ashare_index", "SH_000300", "1d")["close"].iloc[-1]
        pd.DataFrame([{"ts": now, "equity": round(net, 2),
                       "gross_equity": round(gross, 2), "bench": float(bench),
                       "n_positions": len(state["positions"])}]).to_csv(
            self.equity_file, mode="a", header=not self.equity_file.exists(), index=False)
        state["last_mark"] = today_cn
        self.state_file.write_text(json.dumps(state, indent=2))
        print(f"[{now}] ashare_ml equity {net:.2f} (gross shadow {gross:.2f}) "
              f"positions {len(state['positions'])}")
        return {"ts": now, "equity": net, "gross_equity": gross}

    def _hwm(self) -> float:
        if self.equity_file.exists():
            return float(pd.read_csv(self.equity_file)["equity"].max())
        return self.init_cash

    def _latest_closes(self, store, codes) -> dict[str, float]:
        out = {}
        for c in codes:
            try:
                b = store.load("ashare_ts", c, "1d")
                if len(b):
                    out[c] = float(b["close"].iloc[-1])
            except FileNotFoundError:
                pass
        return out

    def _refresh_bench(self, pro, probe, store) -> None:
        last = probe.index[-1].tz_localize(None)
        df = pro.index_daily(ts_code=BENCH,
                             start_date=(last + pd.Timedelta(days=1)).strftime("%Y%m%d"))
        if df is not None and len(df):
            idx = pd.to_datetime(df["trade_date"]).dt.tz_localize("UTC")
            bars = pd.DataFrame({"open": df["open"].values, "high": df["high"].values,
                                 "low": df["low"].values, "close": df["close"].values,
                                 "volume": df["vol"].values},
                                index=pd.DatetimeIndex(idx, name="ts")).sort_index()
            store.save(bars, "ashare_index", "SH_000300", "1d")


def run_tick(state_dir: str | None = None) -> dict:
    return AshareMlBook(state_dir=state_dir).tick()
