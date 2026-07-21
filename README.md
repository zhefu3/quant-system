# qtrade

**A solo quantitative research program run like a lab: every strategy idea is pre-registered with frozen success criteria *before* the backtest runs, every failure is published in the lab log, and nine surviving strategies trade in parallel on paper across five asset classes.**

67 experiments so far. Most of them are documented failures — by design. The [lab log](research/log.md) (1000+ lines, E1–E67) is the primary artifact of this project; the code is the instrument.

> ⚠️ Research project. Nothing here is investment advice, and no real money is deployed.

---

## Why this exists

Retail quant strategies mostly die from self-deception: lookahead bias, cost amnesia, cherry-picked backtest windows, and quietly moving the goalposts after seeing results. This project attacks that problem with **process, enforced by code**:

1. **Pre-registration.** Before any backtest runs, the hypothesis, the exact spec (one variant — no parameter fishing), the cost model, and the pass/fail gate are frozen into the lab log. The verdict branch is written down *before* the result exists.
2. **Honest engine invariants.** The backtest engine refuses to run with zero costs, executes signals at t+1 (lookahead structurally impossible, regression-tested), and auto-splits out-of-sample.
3. **Negative results are first-class.** 18 formal verdicts are recorded; most closed the direction. Each closure includes explicit *reopen conditions* so dead ideas stay dead until the world actually changes.
4. **Forward paper records over backtests.** Anything that passes a gate earns only a *paper* book. Promotion requires the forward record itself to clear statistical adjudication — a backtest is never the final word.

### A worked example of the discipline (E65)

The last experiment is the best illustration. Hypothesis: deep-discount convertible bonds embed a "downward revision" option worth harvesting. The frozen gate **passed** cleanly (post-2021: net +10.7%/yr, Sharpe 1.31, maxDD −8.3%, robust to 2× slippage and to excluding the best year). A weaker process would have shipped it.

Then the pre-committed attribution ran: only **16% of returns** came from actual revision events — 84% was generic deep-discount beta — and the monthly returns correlated **0.77** with an existing book despite ~0% holdings overlap. Verdict recorded: *gate passed* (no retroactive goalpost-moving), but the direction was **closed as redundant** rather than funded. Passing a frozen gate is necessary, not sufficient.

---

## The nine paper books

All books run live on paper (hourly launchd ticks), share one risk/accounting layer, and are strictly firewalled: observation books can never enter the portfolio layer or be cited as evidence for allocation.

| Book | Asset class | Signal source | Status | Headline evidence |
|---|---|---|---|---|
| `crypto_core` | Crypto perp (10 coins) | Mechanical: slow CTA trend + regime-gated mean-reversion | **Validated** | 7y pure-OOS audit: Sharpe 1.11, maxDD 10.5%, +3.4% in the 2022 crash (benchmark −76%) |
| `crypto_core_v2` | Crypto perp | Variant of core | Parallel A/B | 90-day statistical adjudication vs v1, decided by a frozen adjudicator, not vibes |
| `crypto_core_4h` | Crypto perp | Same, 4h bars | Parallel | Matches 1h performance at −25% fees |
| `cn_futures` | CN commodity futures (13) | Mechanical trend/carry | **Validated** | 8y audit: Sharpe 0.48, **−0.10 correlation** to crypto book |
| `futures_ibkr` | US futures | Mechanical | Observation | Failed its gate (E40b) — building the forward record required to unlock |
| `llm_agents` | Crypto perp | **LLM committee** vs mechanical twin, same universe | Observation A/B | Tests whether LLM judgment adds anything; hard $30/mo API budget with automated enforcement |
| `ashare_ml` | A-shares (CSI 300) | LightGBM cross-section, quarterly retrain | Observation | Dual-track accounting measures live alpha decay vs frozen backtest |
| `etf_trend` | US ETFs (10) | Long-only trend rotation | Observation | 33y backtest: net Sharpe 0.58, positive in every crisis year |
| `cb_double_low` | CN convertible bonds | Double-low rotation | Observation | Passed its gate **with a recorded sensitivity warning** (result concentrates in 2021) — the warning ships with the book |

An eighth curve — the human's own discretionary account — runs as a permanent control against the machines.

### Closed directions (partial list)

Cross-sectional equity momentum (3 universes) · A-share price & linear fundamental factors · crypto basis carry · parameter ensembles · portfolio-level vol targeting · universe expansion to 16 coins · low-beta tilt · commodity carry & cross-sectional momentum · a 461-factor public factor library (no incremental alpha over 17 hand-built features) · breadth expansion 300→800 names (dilutes signal at monthly frequency) · convertible downward-revision game (see E65 above) · implied-vol position sizing (the forward-looking mechanism is real; the variance-risk-premium tax is bigger) · the CB community's favorite low-premium weekly rotation (real signal, dies on replicable costs — passes at 5bp slippage, fails at the pre-committed 10bp)

Every closure lives in [research/log.md](research/log.md) with its reopen condition.

---

## Statistical machinery

Forward records are short and noisy, so inference is explicit and frozen ahead of time (`qtrade/live/stats.py`, `decay.py`):

- **Bootstrap confidence intervals** on live Sharpe; **drawdown permutation tests** ("is this drawdown luck?")
- **A/B adjudicator** for strategy variants — promotion requires a full quarter of parallel records and a pre-committed decision rule
- **Alpha-decay state machine** with frozen thresholds; two consecutive warning weeks force a formal review
- **Luck-vs-skill checks** in every weekly report: realized vol and drawdown continuously reconciled against backtest expectations
- **Cross-book redundancy watch**: pairwise return correlations across the forward records, flagged weekly — E65's lesson institutionalized (0% holdings overlap can still be 0.77 return correlation)
- **Observability that interrupts**: health WARNs push notifications (de-duplicated), two independent sources cross-check daily closes, and a TCA scaffold stands ready to reconcile assumed vs paid costs from the first real fill

## Engineering for hostile data

Free data sources fail in creative ways. Three incidents (each fully documented as a postmortem in the log) turned into permanent, mechanical defenses:

- **Adjusted-price convention violation** (raw prices appended to adjusted series → fake −99% cliffs): repaired via quarantine-not-delete, then a **write-time circuit breaker** — a refresh implying >50% single-day moves on >10% of names is rejected before it can touch disk.
- **NaN OHLC rows from Yahoo** cascading into paper books: three-layer fix — adapter drops close-less bars, the trader **refuses to fill at any non-finite price** (fail-safe shared by all nine books), sentinel test locks the behavior in.
- **A swallowed deadline** (the tick's one-shot SIGALRM raised an `Exception`-class timeout that a per-item `except` absorbed — disarming the deadline and hanging a book 10h on a timeout-less TLS read): the deadline is now **`BaseException`-derived and unswallowable**, with per-item soft timeouts in daemon threads so a stalled endpoint costs one item, not the tick.
- All external calls carry **hard wall-clock timeouts** (a silent API hang once froze a book for hours; never again).
- **A-share paper fills respect the real market's refusals**: suspensions and one-way limit boards reject orders into a retried pending queue, and every attempt lands in an execution log — the paper twin of the live TCA stream. A suspended holding can no longer be phantom-sold at its stale price.

## Execution safety (five layers)

The live broker path (crypto exchange adapter; dry-run by default) stacks: account UID pinning → per-order caps with reduce-only semantics → exchange-side reconciliation that raises a `RECONCILE` flag on any mismatch → weight/gross clamps → a drawdown circuit breaker that flattens and writes a `HALTED` flag requiring human review to clear. Real-money switches are owned by the human, gated behind ≥30 days of clean paper records — and none have been flipped.

---

## Architecture

```
qtrade/
├── data/          # unified OHLCV schema (UTC, deduped) + Parquet/DuckDB store
│   └── adapters/  # 7 sources: ccxt, baostock, tushare, akshare, IB, yfinance
├── markets/       # per-market rule packs: fees, slippage, shorting, T+1 — costs must be nonzero
├── backtest/      # vectorbt engine: t+1 execution, enforced costs, auto IS/OOS split
├── strategies/    # CTA trend, mean-reversion, momentum, cross-section, composites, overlays
├── live/          # 9 paper books, risk gate, bootstrap/permutation stats, decay state
│                  # machine, LLM committee, weekly digest, health checks
├── factors/       # 461-factor library (verdict: no increment — kept as screening infra)
└── research/      # one frozen script per experiment + log.md, the lab notebook
```

~37k lines of Python, 114 tests (self-deception guards included: lookahead, zero-cost, NaN-fill, unswallowable-deadline, and no-phantom-fill sentinels). Single CLI: `python -m qtrade.cli {fetch,backtest,scan,walkforward,portfolio,paper,explain,weekly,health,tca,live}`.

## Quickstart

```bash
uv sync --extra dev
python -m qtrade.cli fetch --market crypto --symbol BTC/USDT --timeframe 1h --days 365
python -m qtrade.cli backtest --market crypto --rules crypto_perp --symbol BTC/USDT \
    --timeframe 1h --strategy ts_momentum --param lookback=168
python -m qtrade.cli weekly   # the one command: health + stats + A/B + decay + cost caps
pytest tests/                 # the anti-self-deception suite
```

## Where to look if you're evaluating this project

1. **[research/log.md](research/log.md)** — the lab notebook. Read any pre-registration and its verdict; note the frozen gates, the failures, and the two incident postmortems.
2. **`qtrade/live/stats.py` + `decay.py`** — inference on live records: bootstrap CIs, permutation tests, the A/B adjudicator, the decay state machine.
3. **`qtrade/backtest/engine.py` + `tests/`** — the honesty invariants and the tests that enforce them.
4. **`qtrade/live/cb_book.py`** — a full observation book: the research script *is* the frozen protocol, imported directly so live and research cannot drift.
