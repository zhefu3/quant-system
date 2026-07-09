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

# 组合回测: 多品种共享资金 (equal / inv_vol 分配)
python -m qtrade.cli portfolio --rules crypto_perp --strategy boll_revert \
    --param window=96 --param entry_z=2.0 --param side=both --param regime_window=720 \
    --allocation equal --vol-target 0.4

# 模拟盘: 每小时跑一个 tick, 状态在 outputs/paper/<preset>/
python -m qtrade.cli paper --preset crypto_core
# 自动化(可选, 每小时第5分钟自动跑; 卸载用 bootout):
#   cp deploy/com.qtrade.paper.plist ~/Library/LaunchAgents/
#   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.qtrade.paper.plist

# 模拟盘体检: 实时表现 vs 回测预期, 带 PASS/WARN 判定
python -m qtrade.cli paper-report

# 月度重校验(制度): 补数据->重跑组合->追加历史->对照审计带
python research/revalidate.py

# 实盘执行 (OKX 永续): 默认 dry-run 只打印计划订单; --send 才真下单
python -m qtrade.cli live --preset crypto_core --capital 3000
python -m qtrade.cli live --preset crypto_core --capital 3000 --send
python -m qtrade.cli live --preset crypto_core --capital 3000 --flatten --send  # 一键清仓
```

## 实盘接入指南（先 DEMO，后小钱）

**第一步（免费）：OKX 模拟交易。** okx.com 登录后 → 交易 → 模拟交易 → 创建模拟盘 API key。
模拟环境走真实 API 全流程，资金是假的，用它跑通至少两周。

**第二步（真钱）：现货账户开 API key 时只勾"交易"权限，绝不开"提币"；建议绑定 IP 白名单。**
本组合的忠实执行需要 **≥3000 USDT**（合约最小粒度决定，更小的资金凑不齐一张 ETH/SOL 合约）。

**密钥只放环境变量**（加到 ~/.zshrc，永远不要写进命令或代码）：

```bash
export OKX_API_KEY=...
export OKX_API_SECRET=...
export OKX_API_PASSPHRASE=...
export QTRADE_OKX_DEMO=1     # 模拟环境; 真实环境删掉这行
```

**内置护栏（硬编码）**：只管理 `--capital` 指定的额度；单品种权重 ≤15%；总敞口 ≤100%（不加杠杆）；
回撤 -20% 触发熔断（自动清仓 + 写 HALTED 标志，人工排查删除标志后才能重启）。

```bash

pytest tests/                # 跑防自欺测试
```

## 当前候选组合 "crypto_core"（定义在 qtrade/presets.py）

慢 CTA 趋势 (EWMA 96/288/720) + regime 对齐布林回归 (96, z=2, MA720)，
各腿 vol target 40%，风险 50/50，10 币种等权，eps=0.05。

3 年调参样本（2023-07 → 2026-07，费用+滑点后）：
**+47.8%（年化 ~14%），夏普 1.16，最大回撤 15.2%，牛熊两段皆正收益**。

**七年审判（2026-07-10）**：在参数从未见过的 2019-07→2023-07 纯净数据上
**+75.4%，夏普 1.11，回撤 10.5%**——七年 6 年为正，2022 大熊 +3.4%（基准 -76%）。
已知弱点：单边暴涨年（2024：-14.4%/DD 25%，基准 +105%），约每 5-7 年一遇。
五种机构式"改进"（参数集成/组合vol目标/基差carry/扩池/换周期）全部经预注册标准
检验后拒绝——散户费率下现构造接近局部最优。
详细实验记录（含所有失败尝试）见 research/log.md。

尚未建模：永续资金费率、极端行情滑点。**实盘前置条件：模拟盘 ≥1 个月运转正常。**

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
- [x] 组合层：多品种共享资金、equal/inv_vol 分配、Composite 多策略组合
- [x] 策略族：均值回归（regime 对齐）/ CTA 趋势 / 截面轮动（已淘汰）/ vol target
- [x] 3 年全周期验证 + 过拟合审计 → crypto_core 候选
- [x] 模拟盘 paper 命令（已启动记录）
- [ ] 永续资金费率建模；极端行情滑点模型；A股涨跌停约束
- [ ] A股/美股方向的策略研究（当前策略均为 crypto 验证）
- [ ] （远期）实盘执行 —— 前置条件：模拟盘 ≥1 个月正常且结果与回测一致

## 提醒

回测跑输就是跑输，报告会直说。任何策略在接真钱之前至少要过三关：
样本外为正、参数敏感性平缓（不是孤峰）、多品种可迁移。
