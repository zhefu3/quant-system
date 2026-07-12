# qtrade 会话交接文档（2026-07-12）

> 新会话必读。项目在 `~/Desktop/量化`，命令用 `.venv/bin/python -m qtrade.cli ...`。
> 完整实验记录（E1-E51，含全部失败）在 `research/log.md`；本文件只给现状和待办。

## 一、项目一句话

多市场量化系统：crypto 主力策略已过七年验证并在模拟盘运行；A股/美股/期货各线
均已用数据判明生死；执行层（OKX/IBKR）就绪等账号；全程预注册纪律。

## 二、各账本现状

| 账本 | 状态 | 关键数字 |
|------|------|---------|
| **crypto_core**（趋势+regime回归，10币） | ✅ 已验证，三份模拟盘运行中 | 纯净OOS Sharpe 1.11/DD 10.5%；预期年化8-14%；E29: 亏损年概率28% |
| crypto_core_v2（空头深熊确认） | 与 v1 并行 A/B（始 07-09） | 三面板一致更优但未达换代线；90天后裁决（paper-ab 命令） |
| crypto_core_4h | 平行低频备选，模拟盘运行中 | 与1h打平，费用-25% |
| **E47 A股 ML 指增**（LightGBM 18特征） | ⚠️ 边缘档存档 | 毛超额+6.3%/年真实；净+2.5%/IR 0.22 不达标；低佣通道(万一免五)可复活 |
| E42 A股防守 ETF 配置 | ✅ 可用（管闲钱） | 15年逐年全正，DD 1.2%，债性~3.6%/年 |
| 期货趋势（美国，IBKR） | 构造健康，等数据终审 | E41 ETF代理: Sharpe 0.31+危机alpha(2008 +16.7)；差距=品种广度+杠杆 |
| **E50b 国内商品 CTA** | ✅ 已立项，模拟盘运行中(07-12起) | 逐合约后复权拼接后 Sharpe 0.48/OOS 0.67/最差年-2.3%；拼接污染实证(RB差37pp)；换月成本估算后~0.41 压线过 |
| **E51 组合层** | ✅ 蓝图完成 | 三账本相关≈0，组合 Sharpe 1.01；三档分配方案在 log |

## 三、已关闭方向（勿重开，除非条件变化）

股票截面动量(3宇宙)、A股价格因子、A股线性基本面因子(红利2019已死)、
基差carry、参数集成、组合级vol target、分套节流、扩池16币、低beta倾斜、
E48换手缓冲。重开条件各自记录在 log.md。

## 四、恢复运行清单（新会话第一件事）

1. **模拟盘心跳**（会话断了就停了）: `zsh deploy/paper_all.sh`（每小时一次，
   现含 cn_futures，其数据刷新有当日标记文件、每小时调用零网络成本；
   或让用户装 launchd: deploy/ 下模板+两条命令，见 README）
2. cn_futures 数据链: qtrade/data/cn_futures.py（E50b 冻结拼接规则的唯一实现，
   research 与 live 共用）；逐合约仓库 data_store/cn_contracts/ 每日增量自刷新
3. 检查 `qtrade.cli weekly` 制度到期提醒

## 五、等用户的事（周一 2026-07-14 承诺）

1. **IBKR 模拟账户**（审批中）→ ib_async 已装 → 连通(7497) → CONTFUT 后复权数据
   → 重跑 E40 26年终审 → 过关即在模拟账户自动跑期货账本
2. **OKX 实盘 key**（3000U 小额方案已获用户同意，替代30天模拟等待）→
   环境变量(OKX_API_KEY/SECRET/PASSPHRASE, 勿经聊天) → 连通 → dry-run →
   用户确认 → 开跑（执行器已含 maker_first 挂单模式, E49: 省~0.6%/年）
3. TUSHARE_TOKEN 已在 ~/.zshrc（注意: harness shell 不自动 source，命令前加
   `source ~/.zshrc`）

## 六、纪律（硬约束）

- 改 preset 必须预注册标准再实验（Sharpe≥+0.1/DD≤+2pp/费用≤1.5×，双面板同过）
- 所有实验写 log.md 含失败；密钥永不经聊天/命令回显
- 免费 API 限速礼貌（baostock 拉黑过一次：单线程+sleep）
- 多源数据入库统一时间戳约定（吃过隔行NaN的亏）
- 提交前必须跑测试且注意管道吞退出码（用 pipefail）

## 七、工具速查

explain(决策链) / weekly(周报) / paper-ab(A/B对比) / paper-report(体检+regime) /
universe_score.py(边际贡献选池,季度) / revalidate.py(月度) /
可视化仪表盘: https://claude.ai/code/artifact/c6777f4e-d054-4e87-aa25-087600298711
