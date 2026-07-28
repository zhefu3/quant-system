"""Assemble the E1–E67 trial-family ledger and derive the DSR trial counts.

Inputs are three extraction files produced by independent readers of
research/log.md (each entry cites its line range, so every count is
checkable against the source). This script adds the two judgment layers the
raw extraction deliberately left out:

  - family_id: one economic hypothesis = one family. Measurement, attribution,
    infra, ops and incident rows carry family "none" and never count as trials.
  - n_upper: a per-entry candidate ceiling. Where the log names a grid, the
    grid is the ceiling; where it is vague ("?"), a recorded per-entry guess
    stands in — always at or above the logged count, never below.

Outputs research/artifacts/trial_ledger.json plus the two N figures for the
DSR scenario table, restricted to entries that queried the CRYPTO panel
(the flagship's history — the deflation target):

  N_family  distinct candidate-generating families on that panel (optimistic
            floor: the defensible minimum)
  N_upper   3 x sum of per-entry ceilings (frozen multiplier for the unlogged
            residue: notebook fiddling,看图后放弃, discarded sketches)

Usage: .venv/bin/python research/build_trial_ledger.py
"""

from __future__ import annotations

import json
from pathlib import Path

SRC = Path("/private/tmp/claude-501/-Users-kelsey-qtrade/"
           "83421c3e-176e-4474-aa38-bba3df3d28c0/scratchpad/ledger")
OUT = Path(__file__).resolve().parents[1] / "research" / "artifacts" / "trial_ledger.json"

UNLOGGED_MULTIPLIER = 3  # frozen before computing DSR

# family assignment: exp-prefix -> (family_id, panel_tag)
FAMILIES = {
    "E1": ("crypto-trend", "crypto"), "E2": ("crypto-trend", "crypto"),
    "E3": ("crypto-trend", "crypto"), "E4": ("crypto-trend", "crypto"),
    "E5": ("calibration", "us"), "E6": ("crypto-trend", "crypto"),
    "E7": ("crypto-meanrev", "crypto"), "E8": ("crypto-meanrev", "crypto"),
    "E9": ("crypto-meanrev", "crypto"), "E10": ("crypto-meanrev", "crypto"),
    "E11": ("crypto-meanrev", "crypto"), "E12": ("crypto-xsmom", "crypto"),
    "E13": ("crypto-trend", "crypto"), "E14-E15": ("crypto-composite", "crypto"),
    "E16": ("crypto-composite", "crypto"), "E17": ("measurement", "crypto"),
    "E18": ("measurement", "crypto"), "E19": ("equity-price-ta", "ashare"),
    "E20": ("equity-xsmom", "ashare"), "E21": ("equity-xsmom", "us"),
    "E22": ("equity-index-timing", "ashare"),
    "E23": ("measurement", "crypto"), "E24": ("crypto-composite", "crypto"),
    "E25": ("crypto-meanrev", "crypto"), "E26": ("crypto-sizing", "crypto"),
    "E27": ("crypto-carry", "crypto"), "E28": ("crypto-universe", "crypto"),
    "E29": ("measurement", "crypto"), "E30": ("measurement", "crypto"),
    "E31": ("crypto-throttle", "crypto"), "E32": ("measurement", "crypto"),
    "E33": ("measurement", "crypto"), "E34": ("crypto-composite", "crypto"),
    "E35": ("crypto-composite", "crypto"), "E36": ("crypto-factors", "crypto"),
    "E37": ("crypto-alloc-tilt", "crypto"), "E38": ("crypto-entry-filter", "crypto"),
    "E39": ("equity-xsmom", "ashare"), "E40": ("futures-trend", "us_fut"),
    "E40b": ("futures-trend", "us_fut"), "E41": ("etf-trend", "us"),
    "E42": ("ashare-etf-rotation", "ashare"), "E43": ("infra", "ashare"),
    "E45": ("equity-price-factors", "ashare"), "E46": ("equity-fundamental", "ashare"),
    "E47": ("equity-ml", "ashare"), "E48": ("equity-ml", "ashare"),
    "E49": ("execution-upgrade", "crypto"), "E50": ("cnfut-trend", "cn_fut"),
    "E50b": ("cnfut-trend", "cn_fut"), "E51": ("portfolio-layer", "multi"),
    "E51b": ("portfolio-layer", "multi"), "E52": ("cnfut-carry", "cn_fut"),
    "E53": ("cnfut-xsmom", "cn_fut"), "E54": ("ashare-etf-rotation", "ashare"),
    "E55": ("cnfut-universe", "cn_fut"), "E56": ("measurement", "crypto"),
    "E57": ("measurement", "cn_fut"), "E58": ("measurement", "cn_fut"),
    "E59": ("equity-ml", "ashare"), "E60": ("llm-agents", "crypto_fwd"),
    "E61": ("equity-ml", "ashare"), "E62": ("etf-trend", "us"),
    "E63": ("cb-doublelow", "cb"), "E64": ("equity-ml", "ashare"),
    "E65": ("cb-revision", "cb"), "E66": ("crypto-sizing", "crypto"),
    "E67": ("cb-lowpremium", "cb"),
}
# per-entry candidate ceilings where the log was vague ("?" rows) or where a
# stated sweep implies more than the headline count
N_UPPER_OVERRIDES = {
    "E3": 12,      # 4 lookbacks x assumed 3 vol_filter grades
    "E20": 5,      # "same pattern as E21" (5 variants)
    "E27": 5,      # "under any parameters" implies an internal sweep
    "E28": 2,      # two eps fairness variants
    "E36": 27,     # 9 families x 3 horizons
    "E55": 17,     # 16 new symbols + the 30-pool composite
    "E59": 360,    # prescreen pool actually scored on A-share panel
    "E62": 2, "E66": 1, "E67": 1, "E63": 1, "E65": 1,  # single frozen specs
    "决策能力冲刺": 2,  # universe_score v1 discarded, v2 kept
}
# a vague text row never contributes more than this without an explicit
# override — stops bar counts and row counts masquerading as candidates
FALLBACK_CAP = 30
NON_TRIAL = {"measurement", "infra", "calibration", "none", "portfolio-layer",
             "execution-upgrade", "llm-agents"}


def classify(exp: str) -> tuple[str, str]:
    for key in sorted(FAMILIES, key=len, reverse=True):
        if exp == key or exp.startswith(key + " ") or exp.startswith(key + "("):
            return FAMILIES[key]
    return ("none", "none")  # infra / ops / incidents / meta rows


def ceiling(entry: dict) -> int:
    exp = entry["exp"]
    for key in sorted(N_UPPER_OVERRIDES, key=len, reverse=True):
        # digit boundary: "E3" must not swallow "E31"
        if exp == key or (exp.startswith(key)
                          and not exp[len(key):len(key) + 1].isdigit()):
            return N_UPPER_OVERRIDES[key]
    n = entry.get("n_logged")
    if isinstance(n, int):
        return n
    if isinstance(n, str):
        digits = [int(t) for t in
                  "".join(c if c.isdigit() else " " for c in n).split()]
        if digits:
            return min(max(1, digits[0]), FALLBACK_CAP)
    return 1


def main() -> None:
    entries = []
    for p in ("part1", "part2", "part3"):
        entries += json.loads((SRC / f"{p}.json").read_text())
    seen = set()
    ledger = []
    for e in entries:
        key = (e["exp"], e.get("lines"))
        if key in seen:
            continue
        seen.add(key)
        fam, panel = classify(e["exp"])
        row = {**e, "family_id": fam, "panel_tag": panel,
               "is_trial": fam not in NON_TRIAL, "n_upper": ceiling(e)}
        ledger.append(row)

    crypto_trials = [r for r in ledger
                     if r["is_trial"] and r["panel_tag"] == "crypto"]
    fams = sorted({r["family_id"] for r in crypto_trials})
    sum_upper = sum(r["n_upper"] for r in crypto_trials)
    n_upper = sum_upper * UNLOGGED_MULTIPLIER

    out = {"generated": "2026-07-28", "source": "research/log.md (three independent extractions, line-cited)",
           "unlogged_multiplier": UNLOGGED_MULTIPLIER,
           "crypto_panel": {"n_family": len(fams), "families": fams,
                            "sum_logged_ceilings": sum_upper,
                            "n_upper": n_upper},
           "entries": ledger}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"ledger rows: {len(ledger)} ({sum(r['is_trial'] for r in ledger)} trials, "
          f"{len(ledger) - sum(r['is_trial'] for r in ledger)} measurement/infra/ops)")
    print(f"crypto panel: {len(crypto_trials)} trial rows, "
          f"{len(fams)} families -> N_family={len(fams)}")
    print(f"  families: {fams}")
    print(f"  sum of logged ceilings={sum_upper} x{UNLOGGED_MULTIPLIER} -> N_upper={n_upper}")
    print(f"saved -> {OUT}")


if __name__ == "__main__":
    main()
