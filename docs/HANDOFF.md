# qtrade 会话交接文档（2026-07-12 深夜刷新）

> 新会话必读。项目在 `~/qtrade`（桌面 `量化` 是符号链接；2026-07-12 因 launchd/TCC 移出桌面），命令用 `.venv/bin/python -m qtrade.cli ...`。
> 完整实验记录（E1-E55，含全部失败）在 `research/log.md`；本文件只给现状和待办。

## 一、项目一句话

多市场量化系统：crypto 主力策略七年验证 + 国内商品 CTA（E50b 干净数据立项）双进攻账本
同时模拟盘运行；组合层三账本相关≈0；执行层（OKX/IBKR）就绪等账号；全程预注册纪律。

## 二、各账本现状

| 账本 | 状态 | 关键数字 |
|------|------|---------|
| **crypto_core**（趋势+regime回归，10币） | ✅ 已验证，三份模拟盘运行中 | 纯净OOS Sharpe 1.11/DD 10.5%；预期年化8-14%；E29: 亏损年概率28% |
| crypto_core_v2（空头深熊确认） | 与 v1 并行 A/B（始 07-09） | 90天后裁决（paper-ab 命令） |
| crypto_core_4h | 平行低频备选，模拟盘运行中 | 与1h打平，费用-25% |
| **cn_futures**（国内商品CTA 14品种） | ✅ E50b立项，模拟盘运行中(07-12起) | 干净拼接 Sharpe 0.48/OOS 0.67/最差年-2.3%；亏损年概率37%；与crypto相关**-0.10** |
| E47 A股 ML 指增 | ⚠️ 边缘档存档 | 毛+6.3%/年真实；净+2.5%不达标；低佣通道可复活 |
| E42 A股防守 ETF | ⚠️ E54降级：指数层结论 | 真实ETF价格上"债性防守"不成立(DD 5.9%>4%门槛)，是代理伪影 |
| 期货趋势（美国，IBKR） | ⚠️ 研究未部署；**观察账本 futures_ibkr 模拟盘 07-13 起** | E40b门槛(b)未过(窗口Sharpe -0.15)；观察账本走判决解锁路径(2)，observation-only不进组合层；需 IB Gateway 活着(4002) |
| **E51b 组合层** | ✅ 已刷新（真实CTA账本） | 今日可部署3账本逆vol Sharpe 0.96/maxDD-2.4%；4账本1.07 |
| E55 品种池 14→30 | ❌ 判决：保持14池 | 新16品种自身无信号(-0.01)，扩池只稀释；数据资产保留(2643合约) |

## 三、已关闭方向（勿重开，除非条件变化，各自重开条件在 log）

股票截面动量(3宇宙)、A股价格因子、A股线性基本面因子、crypto基差carry(E27)、
参数集成、组合级vol target、分套节流、扩池16币、低beta倾斜、E48换手缓冲、
**商品期限结构carry(E52边缘,2021起衰减,勿部署)**、**商品截面动量(E53拒绝,与趋势同族)**、
**E54真实ETF防守(2/3门槛不过,重新定位须新预注册)**

## 四、恢复运行清单（新会话第一件事）

1. **心跳已交给 launchd**（2026-07-12 装好，每小时:05 自动跑，关会话/重启都在）:
   `launchctl list | grep qtrade` 验证；日志 `outputs/paper/launchd.log`；
   手动补跑: `zsh ~/.qtrade-paper.sh`
2. **体检**: `.venv/bin/python -m qtrade.cli health`（数据完整性+心跳新鲜度+HALTED标记）
3. 检查 `qtrade.cli weekly` 制度到期提醒

## 五、关键架构（今晚新增）

- **qtrade/data/cn_futures.py**: E50b 冻结拼接规则唯一实现（主力=昨日OI最大+只向后
  换月+乘法后复权），research 与 live 共用；合约仓库 data_store/cn_contracts/ 自增量刷新
- **qtrade/live/risk.py**: pre-trade 风控闸门（数据新鲜度拒单/权重±25%/gross 2.0/
  回撤熔断=1.5×回测maxDD→清仓+HALTED标记人工复核），限额挂 preset，paper与实盘共用
- **qtrade/live/healthcheck.py**: `cli health`，已接入 weekly
- 适配器派发: qtrade/data/adapters/make_adapter(market)；cn 日线收盘戳完成判定
  与 crypto 开盘戳不同（signals.drop_in_progress 缝）

## 六、等用户的事（周一 2026-07-14 承诺）

1. **IB Gateway 保持在线**：futures_ibkr 观察账本每小时 tick 需要 4002 端口活着；
   Gateway 掉线=跳 tick 持仓冻结（launchd.log 会记 "futures_ibkr tick failed"）。
   Gateway 每日会话到期需重登（或配自动重启）
2. **OKX 实盘 key**（3000U 小额已同意）→ 环境变量（勿经聊天）→ dry-run → 确认开跑
3. TUSHARE_TOKEN 在 ~/.zshrc（harness shell 要先 `source ~/.zshrc`）

## 七、纪律（硬约束）

- 改 preset 必须预注册标准再实验；所有实验写 log.md 含失败
- 密钥永不经聊天/命令回显；免费 API 限速礼貌（单线程+sleep）
- 多源数据入库统一时间戳约定；提交前跑测试（pipefail）
- 东财 fund_etf_hist_em 已拒连（2026-07-12），ETF 数据走 tushare fund_daily×fund_adj

## 八、工具速查

explain(决策链) / weekly(周报+体检) / health(体检) / paper-ab / paper-report /
universe_score.py(季度) / revalidate.py(月度) / monte_carlo_cn.py(cn风险画像) /
可视化仪表盘: https://claude.ai/code/artifact/c6777f4e-d054-4e87-aa25-087600298711
