"""Daily PIT snapshot collector for CB clause-game events (started 2026-07-21).

E65 closed on one sentence: "announcement-date history is not purchasable."
This collector builds that dataset by living forward — the only way retail
ever owns unique data. One snapshot per CN day of (a) the jsl redemption
table (强赎 lifecycle: trigger counters + announcement status) and (b) the
master list (转股价 among others), plus an append-only EVENT log derived by
diffing consecutive snapshots:

  redeem_status   强赎状态 transition (e.g. -> 已公告强赎: an announcement
                  DATE, captured the day it happens)
  conv_price      转股价 change (a decrease = 下修 taking effect)
  redeem_new      bond enters the redemption-watch table

In N months this is point-in-time announcement data nobody sells; E65's
reopen condition manufactures itself. Costs two polite API calls per day.

Layout (data_store/cn_cb_events/):
  redeem/YYYY-MM-DD.parquet   daily 强赎表快照
  master/YYYY-MM-DD.parquet   daily 全量名单快照
  events.csv                  append-only derived event log
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qtrade.live.timeouts import call_with_timeout  # noqa: E402

ROOT = REPO / "data_store" / "cn_cb_events"


def detect_events(prev_redeem: pd.DataFrame | None, cur_redeem: pd.DataFrame,
                  prev_master: pd.DataFrame | None, cur_master: pd.DataFrame,
                  date: str) -> list[dict]:
    """Pure diff logic (unit-tested). First run (no prev) yields no events —
    a baseline is not news."""
    events: list[dict] = []
    if prev_redeem is not None and len(prev_redeem):
        p = prev_redeem.set_index(prev_redeem["代码"].astype(str))
        c = cur_redeem.set_index(cur_redeem["代码"].astype(str))
        for code in c.index:
            if code not in p.index:
                events.append({"date": date, "code": code, "type": "redeem_new",
                               "old": "", "new": str(c.loc[code].get("强赎状态", "")),
                               "name": str(c.loc[code].get("名称", ""))})
                continue
            old_s = str(p.loc[code].get("强赎状态", ""))
            new_s = str(c.loc[code].get("强赎状态", ""))
            if old_s != new_s:
                events.append({"date": date, "code": code, "type": "redeem_status",
                               "old": old_s, "new": new_s,
                               "name": str(c.loc[code].get("名称", ""))})
    if prev_master is not None and len(prev_master):
        pm = prev_master.set_index(prev_master["债券代码"].astype(str))
        cm = cur_master.set_index(cur_master["债券代码"].astype(str))
        for code in cm.index.intersection(pm.index):
            old_px = pd.to_numeric(pm.loc[code].get("转股价"), errors="coerce")
            new_px = pd.to_numeric(cm.loc[code].get("转股价"), errors="coerce")
            if pd.notna(old_px) and pd.notna(new_px) and old_px != new_px:
                events.append({"date": date, "code": code, "type": "conv_price",
                               "old": float(old_px), "new": float(new_px),
                               "name": str(cm.loc[code].get("债券简称", ""))})
    return events


def _latest_before(dirpath: Path, day: str) -> pd.DataFrame | None:
    files = sorted(f for f in dirpath.glob("*.parquet") if f.stem < day)
    return pd.read_parquet(files[-1]) if files else None


def main() -> int:
    import akshare as ak

    day = pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d")
    (ROOT / "redeem").mkdir(parents=True, exist_ok=True)
    (ROOT / "master").mkdir(parents=True, exist_ok=True)
    if (ROOT / "redeem" / f"{day}.parquet").exists():
        print(f"[cb_events] {day} already collected")
        return 0

    redeem = call_with_timeout(ak.bond_cb_redeem_jsl, 90.0)
    master = call_with_timeout(ak.bond_zh_cov, 120.0)
    if redeem is None or not len(redeem) or master is None or not len(master):
        print("[cb_events] empty response — not writing a snapshot")
        return 1

    prev_r = _latest_before(ROOT / "redeem", day)
    prev_m = _latest_before(ROOT / "master", day)
    events = detect_events(prev_r, redeem, prev_m, master, day)

    redeem.to_parquet(ROOT / "redeem" / f"{day}.parquet")
    master.to_parquet(ROOT / "master" / f"{day}.parquet")
    if events:
        f = ROOT / "events.csv"
        pd.DataFrame(events).to_csv(f, mode="a", header=not f.exists(), index=False)
    print(f"[cb_events] {day}: redeem {len(redeem)} rows, master {len(master)} rows, "
          f"{len(events)} events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
