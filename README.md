# qtrade — 多市场量化研究与回测系统

研究回测优先的量化系统：加密货币先行（数据免费全量），A股/美股适配器随后加入。
架构按「统一数据模型 + 市场适配器 + 引擎无关策略层」设计，策略代码不依赖任何引擎/交易所。

## 诚实回测三原则（引擎内置强制，非自觉遵守）

1. **无未来函数**：bar t 收盘算出的信号，t+1 才成交（引擎自动 shift，测试 `test_no_lookahead_next_bar_execution` 把关）
2. **无免费午餐**：手续费+滑点必须 > 0，零成本配置直接抛异常
3. **样本外优先**：每次回测自动做时间切分，以样本外(默认后 30%)对比 buy&hold 下结论

## 快速开始

```bash
uv sync --extra dev          # 依赖 (Python 3.12, uv 管理)

# 拉数据 (全部免费公开源, 无需任何 API key)
python -m qtrade.cli fetch --market crypto --symbol BTC/USDT   --timeframe 5m --days 180
python -m qtrade.cli fetch --market ashare --symbol 600519.SH  --timeframe 5m --days 365
python -m qtrade.cli fetch --market us     --symbol SPY        --timeframe 1d --days 3650
python -m qtrade.cli coverage

# 回测 (报告落盘到 outputs/; --rules 可切换成本/约束包, 如现货数据+永续规则)
python -m qtrade.cli backtest --market crypto --rules crypto_perp --symbol BTC/USDT \
    --timeframe 1h --strategy ts_momentum --param lookback=168 --param allow_short=true

# 参数网格 + 敏感性热力图 (看平原还是孤峰)
python -m qtrade.cli scan --symbol BTC/USDT --timeframe 1h --strategy dual_ma \
    --grid fast=10,20,50,100 --grid slow=50,100,200,400

# walk-forward: 训练窗选参 -> 未见测试窗验证, 逐折报告
python -m qtrade.cli walkforward --symbol BTC/USDT --timeframe 1h --rules crypto_perp \
    --strategy ts_momentum --grid lookback=24,72,168,336 --grid vol_filter=true,false \
    --param allow_short=true --folds 5

pytest tests/                # 跑防自欺测试
```

## 结构

```
qtrade/
├── data/
│   ├── schema.py            # 统一 OHLCV 模型: UTC 索引, 去重升序
│   ├── store.py             # Parquet 落盘(增量合并) + DuckDB 查询
│   └── adapters/
│       ├── crypto_ccxt.py   # ccxt 分页拉取, binance→okx 兜底
│       ├── ashare_baostock.py # A股 5m+ (后复权, 沪深代码归一, 停牌行剔除)
│       └── us_yfinance.py   # 美股 (日线优先; intraday 受 Yahoo 深度限制)
├── markets/rules.py         # 市场规则包: 费率/滑点/做空/T+1/时区 (成本强制非零)
├── backtest/
│   ├── engine.py            # vectorbt 封装: shift(1) + 成本 + T+1 + IS/OOS 切分
│   └── report.py            # 控制台 + markdown 报告, 一句话诚实结论
├── strategies/
│   ├── base.py              # Strategy 接口: bars -> 目标仓位 {-1,0,1}
│   ├── dual_ma.py           # 双均线基线
│   └── momentum.py          # 时序动量 (可多空) + 波动率过滤基线
└── research/
    ├── grid.py              # 参数网格扫描 + 敏感性热力图
    └── walkforward.py       # 滚动训练/测试验证 + 逐折诚实结论
```

## 现状与路线

- [x] 三市场数据层全通：crypto (OKX)、A股 (baostock)、美股 (yfinance, 日线优先)
- [x] 回测闭环 + 做空 (crypto_perp) + T+1 (ashare)
- [x] 研究工具：参数网格 + 敏感性热力图 + walk-forward
- [x] 基线策略结论（诚实版）：
  - 双均线/动量在 5m 级别被成本碾压；1h 级别与基准打平
  - 多空动量 BTC walk-forward 5 折 3 胜 (+13pp)，但 **ETH 迁移失败 (-1.4pp) → 判定行情运气**
  - SPY 日线金叉 10 年样本外跑输 buy&hold 27pp（著名结果，系统如实复现）
- [ ] 组合层：多品种资金分配与风控
- [ ] 更多策略族：均值回归 / 截面动量轮动 / 波动率目标仓位
- [ ] 永续资金费率建模；A股涨跌停约束
- [ ] （远期）模拟盘/实盘执行 —— 仅当某策略经受住 walk-forward + 多品种迁移后再启动

## 提醒

回测跑输就是跑输，报告会直说。任何策略在接真钱之前至少要过三关：
样本外为正、参数敏感性平缓（不是孤峰）、多品种可迁移。
