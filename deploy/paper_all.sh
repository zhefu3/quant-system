#!/bin/zsh
# Hourly paper ticks for every tracked preset; keep going if one venue hiccups.
cd /Users/kelsey/Desktop/量化
for p in crypto_core crypto_core_v2 crypto_core_4h cn_futures; do
  .venv/bin/python -m qtrade.cli paper --preset "$p" || echo "[paper_all] $p tick failed"
done
