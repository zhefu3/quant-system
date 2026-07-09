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

# 拉数据 (OKX 公开行情, 无需 API key; Binance 被屏蔽时自动切换)
python -m qtrade.cli fetch --symbol BTC/USDT --timeframe 5m --days 180
python -m qtrade.cli coverage

# 回测 (报告落盘到 outputs/)
python -m qtrade.cli backtest --symbol BTC/USDT --timeframe 1h \
    --strategy dual_ma --param fast=20 --param slow=100

pytest tests/                # 跑防自欺测试
```

## 结构

```
qtrade/
├── data/
│   ├── schema.py            # 统一 OHLCV 模型: UTC 索引, 去重升序
│   ├── store.py             # Parquet 落盘(增量合并) + DuckDB 查询
│   └── adapters/
│       └── crypto_ccxt.py   # ccxt 分页拉取, binance→okx 兜底  [A股/美股待加]
├── markets/rules.py         # 市场规则包: 费率/滑点/做空/T+1 (成本强制非零)
├── backtest/
│   ├── engine.py            # vectorbt 封装: shift(1) + 成本 + IS/OOS 切分
│   └── report.py            # 控制台 + markdown 报告, 一句话诚实结论
└── strategies/
    ├── base.py              # Strategy 接口: bars -> 目标仓位 {0,1}
    ├── dual_ma.py           # 双均线基线
    └── momentum.py          # 时序动量 + 波动率过滤基线
```

## 现状与路线

- [x] 加密货币数据层 + 回测闭环（BTC/ETH 5m×180d, 1h×400d 已落地）
- [x] 基线策略：双均线 / 时序动量 —— **均未跑赢 buy&hold，属预期**（朴素基线 + 熊市样本 + 真实成本）
- [ ] 研究工具：参数网格 + walk-forward 验证 + 参数敏感性热力图
- [ ] A股适配器 (baostock 5分钟线) + T+1/涨跌停规则细化
- [ ] 美股适配器 (先日线, yfinance)
- [ ] 组合层：多品种资金分配与风控
- [ ] （远期）模拟盘/实盘执行 —— 仅当某策略经受住 walk-forward 检验后再启动

## 提醒

回测跑输就是跑输，报告会直说。任何策略在接真钱之前至少要过三关：
样本外为正、参数敏感性平缓（不是孤峰）、多品种可迁移。
