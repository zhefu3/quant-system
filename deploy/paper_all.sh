#!/bin/zsh
# Hourly paper ticks for every tracked preset; keep going if one venue hiccups.
# futures_ibkr needs IB Gateway alive on :4002 — a dead gateway just logs a
# failed tick (positions freeze), same degradation as an OKX outage.
cd /Users/kelsey/qtrade
# cn_futures last: its data source (akshare) is the flakiest — a slow tick
# there must not delay the other books (each tick also has a 900s SIGALRM cap)
for p in crypto_core crypto_core_v2 crypto_core_4h futures_ibkr llm_agents etf_trend cn_futures ashare_ml; do
  .venv/bin/python -m qtrade.cli paper --preset "$p" || echo "[paper_all] $p tick failed"
done
