# qtrade 会话交接文档（2026-07-15 深夜刷新）

> 新会话必读。项目在 `~/qtrade`，命令用 `.venv/bin/python -m qtrade.cli ...`。
> 完整实验记录（E1-E65，含全部失败与两起数据事故）在 `research/log.md`；本文件只给现状和待办。
> 判断项目历史必须引 log 原文（"关闭"≠"边缘档"≠"观察"）。

## 一、项目一句话

九本纸面账并行（2 验证 + 2 平行 + 5 观察），覆盖 crypto/国内商品/美期货/美 ETF/A股/转债
五资产类 + 机械/ML/LLM/估值轮动四信号源；执行层 OKX 就绪、IBKR 运行、QMT 待开通；
全程预注册纪律，用户手动账户作为第八条对照曲线。

## 二、九本账本（health 一览即全貌）

| 账本 | 性质/验证 | 关键数字与备注 |
|------|----------|---------------|
| **crypto_core** | ★★★ 7年验证 | OOS Sharpe 1.11/DD 10.5%；唯一实盘资格（OKX 3000U 已获同意但**用户对虚拟币保留，搁置**） |
| crypto_core_v2 | 平行 A/B | **10-07 裁决**（90天), paper-ab 已带统计裁决器 |
| crypto_core_4h | 低频备选 | 与 1h 打平、费 -25% |
| **cn_futures** | ★★★ E50b 8年审计 | Sharpe 0.48/与crypto相关 -0.10 |
| futures_ibkr | 观察（E40b 未过门槛） | 解锁路径(2)攒记录；需 IB Gateway 4002 在线（每日重登！） |
| llm_agents | 观察（E60，无法回测） | LLM委员会 vs crypto_core 同宇宙 A/B；2027-01-14 评估；$30/月上限有机器盯 |
| ashare_ml | 观察（E61=E47 前瞻） | 双轨记账测衰减；hfq+时间戳约定修复后干净重启（07-15） |
| etf_trend | 观察（E62 长多趋势） | 33年净 Sharpe 0.58/危机年全正；美股实盘最近候选；NaN 事故修复后重建（07-15） |
| cb_double_low | 观察（E63 转债双低） | 后段 +8.9%/0.91 **但 2021 依赖警示入档**（剔除后仅+3.4%）；月频 top-20 |

组合层 E51b：仅对验证账本生效，观察账本测试锁定在外。
**手动账户对照**：`manual-log` 记录（已播种；具体数字在 outputs/paper/manual/，不入公开库）；用户每月报一个数。

## 三、已关闭方向（勿重开，重开条件在 log 各判决）

股票截面动量(3宇宙)、A股价格因子、A股线性基本面因子、crypto基差carry、参数集成、
组合级vol target、分套节流、扩池16币、低beta倾斜、E48换手缓冲、商品carry(E52)、
商品截面动量(E53)、E54真实ETF防守、E59公共因子库(461因子无增量)、
E64广度扩展300→800(月频无增量,重开须分钟线)、
E65转债下修博弈(过后段门槛但与cb_double_low收益相关0.77/下修仅16%驱动,不建账关闭;
重开须公告日历史数据)

## 四、恢复运行清单（新会话第一件事）

1. `launchctl list | grep qtrade` + `cli health`（九本账+HALTED/RECONCILE 标记）
2. `cli weekly`：统计检验、衰减状态机、手动对照、llm 成本、制度到期，全在里面
3. 密钥位置：ANTHROPIC_API_KEY 在 ~/.zshenv（launchd 天然可见）；TUSHARE_TOKEN 在
   ~/.zshrc（心跳脚本已 source）；OKX 尚未配置

## 五、关键架构（07-14/15 大量新增）

- **targets_fn 账本模式**：无 Strategy 的账本（llm_agents/ashare_ml/cb_book）注入
  targets_fn 或专用 Book 类，共享 RiskGate/记账/记录格式
- **qtrade/live/**: stats.py(bootstrap CI/回撤置换/AB裁决器) + decay.py(衰减状态机,
  连续2周warning触发复审) + manual.py(第八曲线) + llm_agents.py(委员会+结果反思)
- **qtrade/factors/**: 461 公共因子库(MIT, 已判无增量, 留作预筛基建) + registry
- **执行安全五层**（OKX broker.py, QMT 执行器照此复制）: UID 钉扎 → 订单级上限
  (reduce-only 语义) → 交易所对账 RECONCILE 旗标 → 权重/gross 钳制 → 回撤熔断
- **数据完整性闸门**（本周两起事故的产物）: A股刷新 hfq×adj_factor+悬崖熔断;
  US 适配器 dropna(close); PaperTrader 非有限价格跳 tick; SIGALRM 900s 墙钟
- 数据仓库: cn_cb(转债1016只含退市) + csi500 时点宇宙(1413成员) + 隔离区
  _quarantine_20260715(事故审计用)
- **前瞻记录异地备份**(07-21): 每日推送私有仓 zhefu3/qtrade-records(权益/成交/
  决策/执行日志 = 不可再生证据), paper_all 内嵌, 300s 超时保护
- **机构化观测层**(07-19, 对照"正规机构差距"清单补齐, 全部只观测不改行为):
  主动告警(health --alert → macOS 通知, 去重防刷屏, 已挂 paper_all 每小时) +
  多源对账(BTC/ETH 双所对比、单所可达时对存储兜底; A股 tushare vs akshare 按日
  期对齐比收益) + 跨账本相关矩阵进 weekly(E65 教训制度化, >0.6 标记) +
  TCA 脚手架(实盘路径记录到达价/成交价, cli tca 报告, 首笔真实成交起积累)

## 六、等用户的事

1. **券商一个电话办三件事**: 开 miniQMT(门槛以券商为准) + 谈佣金
   + 确认程序化报备(报"中低频轮动")。回来带佣金数字 → E49 当晚出判决;
   权限下来 → 我造 QMT 执行器(dry-run 默认, 真钱开关锁在"模拟≥30天+过门槛"后)
2. ~~IB Gateway 每日重登~~ **已解决(07-20)**: IBC 全自动化验收通过——开机自启/
   崩溃自愈(kill -9 实测 ~24s 复活)/每日 14:45 自重启/凭据自动登录(paper,
   config.ini 已 600)/掉线 push 告警。唯一残余人工: 若 IB 周日强登出遇 2FA 需
   手机点一下(paper 账户通常无)
3. **每月 manual-log 一个数字**（手动账户收益%, 对照实验的燃料）
4. OKX 实盘维持搁置（用户虚拟币偏好，不催）

## 七、我方待办（下会话优先级）

1. ~~E65 转债下修博弈~~ **已结(07-15 关闭)**: 过后段门槛(+10.7%/Sharpe1.31)但归因证伪
   ——下修事件仅 16% 贡献、84% 是深折价 beta、与 cb_double_low 收益相关 0.77;用户选不建账。
   研究脚本 research/cb_downward_revision.py 留档。A股免费矿到此为止(下一块须公告日历史)
2. QMT 执行器（等用户权限）；分钟线 ¥2000 决策（等佣金落地一起议）
3. 例行: 07-21 llm_agents 首条反思；8月初三本月频账首次换仓质量检查；10-07 A/B 裁决

## 八、纪律（硬约束，本周新增三条）

- 改 preset 必须预注册；所有实验含失败写 log；密钥永不经聊天
- 免费 API 限速礼貌；多源数据统一时间戳约定；提交前跑测试(pipefail)
- **接手已有数据仓库前必须读原抓取脚本的约定**（07-15 hfq 事故教训）
- **所有出网调用必须有硬超时**（07-14 挂死事故）；**价格必须有限才可成交**（07-15 NaN 事故）
- 我不执行真实交易、不碰用户凭据——造系统，开关在用户

## 九、工具速查

explain / weekly（统计+衰减+手动对照+llm成本+跨账本相关）/ health [--alert] /
tca（实付vs假设成本）/ rebalance-report（换仓质量+锁定期代价）/ paper-ab（统计裁决器）/
paper-report（luck检验）/
manual-log / revalidate.py(已自动化,每月1号) / universe_score.py(季度)
