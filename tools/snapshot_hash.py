"""Content-hash manifest for verdict data: free sources rewrite history.

A verdict anchored only to a git commit is not reproducible when the data
under it can be silently revised (re-adjusted prices, backfilled bars, fixed
outliers). From 2026-07-29 every formal verdict stores a manifest of what the
data actually WAS: sha256 per parquet, row count, index range. Historical
verdicts get a backfilled manifest of the CURRENT store state, marked
post_hoc — it proves what the data looks like today, not what it looked like
then, and says so.

Usage:
    .venv/bin/python tools/snapshot_hash.py crypto etf cn_contracts
    .venv/bin/python tools/snapshot_hash.py --all
Writes research/artifacts/snapshots/manifest_<utcdate>.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "data_store"
OUT_DIR = ROOT / "research" / "artifacts" / "snapshots"


def hash_file(p: Path) -> dict:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    entry = {"path": str(p.relative_to(ROOT)), "sha256": h.hexdigest(),
             "bytes": p.stat().st_size}
    try:
        df = pd.read_parquet(p)
        entry["rows"] = len(df)
        if len(df) and isinstance(df.index, pd.DatetimeIndex):
            entry["start"] = str(df.index[0])
            entry["end"] = str(df.index[-1])
    except Exception:
        pass  # non-bar parquet or unreadable: the byte hash still stands
    return entry


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("partitions", nargs="*", help="data_store subdirs to hash")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--post-hoc", action="store_true",
                    help="mark this manifest as a backfill of current state")
    args = ap.parse_args()

    parts = ([p.name for p in STORE.iterdir() if p.is_dir()]
             if args.all else args.partitions)
    if not parts:
        sys.exit("name partitions or pass --all")

    files = []
    for part in sorted(parts):
        base = STORE / part
        if not base.exists():
            print(f"  (skip missing partition {part})")
            continue
        files += sorted(base.rglob("*.parquet"))
    entries = [hash_file(p) for p in files]

    now = datetime.now(timezone.utc)
    manifest = {"generated_utc": now.isoformat(timespec="seconds"),
                "post_hoc": bool(args.post_hoc),
                "partitions": sorted(parts),
                "n_files": len(entries),
                "files": entries}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"manifest_{now.strftime('%Y%m%d_%H%M')}.json"
    out.write_text(json.dumps(manifest, indent=1))
    print(f"{len(entries)} files hashed -> {out}")


if __name__ == "__main__":
    main()
