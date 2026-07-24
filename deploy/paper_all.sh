#!/bin/zsh
# Hourly paper ticks for every tracked preset; keep going if one venue hiccups.
# futures_ibkr needs IB Gateway alive on :4002 — a dead gateway just logs a
# failed tick (positions freeze), same degradation as an OKX outage.
cd /Users/kelsey/qtrade
# cn_futures last: its data source (akshare) is the flakiest — a slow tick
# there must not delay the other books (each tick also has a 900s SIGALRM cap)
for p in crypto_core crypto_core_v2 crypto_core_4h futures_ibkr llm_agents etf_trend cn_futures ashare_ml cb_double_low; do
  .venv/bin/python -m qtrade.cli paper --preset "$p" || echo "[paper_all] $p tick failed"
done

# Hourly health with push alerting (2026-07-19): a WARN interrupts the human
# via macOS notification instead of waiting for a manual check. De-dup state
# in outputs/alerts_state.json keeps a standing WARN from spamming.
.venv/bin/python -m qtrade.cli health --alert || echo "[paper_all] health check failed"

# Daily PIT snapshot of CB clause-game events (2026-07-21): builds the
# announcement-date dataset that E65's reopen condition needs and nobody
# sells — two polite API calls, diffed into an event log. Runs BEFORE the
# records backup so today's snapshot rides today's backup push.
events_marker="outputs/cb_events_$(date +%Y-%m-%d).done"
if [[ ! -f $events_marker ]]; then
  touch "$events_marker"
  .venv/bin/python - <<'PY' || echo "[paper_all] cb_events collection failed"
import subprocess, sys
r = subprocess.run([".venv/bin/python", "research/collect_cb_events.py"], timeout=300)
sys.exit(r.returncode)
PY
fi

# Daily off-site backup of forward records (2026-07-21): the paper records
# are irreplaceable evidence; one push per day to the private records repo.
# Subprocess timeout so a hung push can never block the next hourly loop.
backup_marker="outputs/records_backup_$(date +%Y-%m-%d).done"
if [[ ! -f $backup_marker ]]; then
  touch "$backup_marker"
  # QMT git-bus producer (prebuilt 07-24): refresh the targets messages daily
  # so the future VPS consumer's 7-day freshness guard is already satisfied
  # on activation day; the backup push right below carries them off-site.
  .venv/bin/python -m qtrade.cli qmt-targets --book ashare_ml >/dev/null 2>&1 || true
  .venv/bin/python -m qtrade.cli qmt-targets --book cb_double_low >/dev/null 2>&1 || true
  .venv/bin/python - <<'PY' || echo "[paper_all] records backup failed"
import subprocess, sys
r = subprocess.run(["/bin/zsh", "deploy/backup_records.sh"], timeout=300)
sys.exit(r.returncode)
PY
fi

# Monthly revalidation, institutionalized (2026-07-19; was a manual script).
# Runs AFTER all book ticks so books never wait on it. One attempt per month
# (marker written before the run): a failure surfaces via `cli weekly`'s
# 制度到期提醒 going stale, never as an hourly retry hammering free APIs.
# The subprocess timeout keeps a hung revalidate from blocking next hour's
# launchd instance — the exact incident class of 07-14/07-16.
month_marker="outputs/revalidate_$(date +%Y-%m).attempted"
if [[ ! -f $month_marker ]]; then
  touch "$month_marker"
  .venv/bin/python - >> outputs/revalidate.log 2>&1 <<'PY' || echo "[paper_all] monthly revalidate failed (outputs/revalidate.log)"
import subprocess, sys
r = subprocess.run([".venv/bin/python", "research/revalidate.py"], timeout=1800)
sys.exit(r.returncode)
PY
fi
