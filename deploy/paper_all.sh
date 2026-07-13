#!/bin/zsh
# Hourly paper ticks for every tracked preset; keep going if one venue hiccups.
# futures_ibkr needs IB Gateway alive on :4002 — a dead gateway just logs a
# failed tick (positions freeze), same degradation as an OKX outage.
cd /Users/kelsey/qtrade
for p in crypto_core crypto_core_v2 crypto_core_4h cn_futures futures_ibkr; do
  .venv/bin/python -m qtrade.cli paper --preset "$p" || echo "[paper_all] $p tick failed"
done
