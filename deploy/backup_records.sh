#!/bin/zsh
# Off-site backup of the irreplaceable forward records (2026-07-21).
# The paper equity curves, trades, LLM decision archives, and exec logs are
# time-stamped evidence that cannot be regenerated — code lives in the main
# repo, but these lived only on one disk until now. Destination is the
# PRIVATE repo zhefu3/qtrade-records (manual-account data stays private).
# Invoked daily from paper_all.sh under a subprocess timeout; exits 0 when
# there is nothing new.
set -e
SRC=/Users/kelsey/qtrade
DST=/Users/kelsey/qtrade-records

[[ -d $DST/.git ]] || { echo "[backup] $DST missing — bootstrap first"; exit 1; }
rsync -a --delete "$SRC/outputs/paper/" "$DST/paper/"
[[ -d $SRC/outputs/live ]] && rsync -a --delete "$SRC/outputs/live/" "$DST/live/"
# the forward-collected PIT event dataset is irreplaceable BY DESIGN —
# its whole value is that it cannot be re-fetched later
[[ -d $SRC/data_store/cn_cb_events ]] && rsync -a --delete "$SRC/data_store/cn_cb_events/" "$DST/cn_cb_events/"
cp "$SRC/research/revalidation_history.csv" "$DST/" 2>/dev/null || true

cd "$DST"
git add -A
git diff --cached --quiet && exit 0
git commit -q -m "records $(date -u +%Y-%m-%dT%H:%MZ)"
git push -q origin main
echo "[backup] records pushed $(date -u +%H:%MZ)"
