# qtrade 运维手册 — 出事了怎么办

## 熔断触发（outputs/live/<preset>/HALTED 出现）

系统已自动清仓并拒绝再运行。这是设计行为，不是故障。

1. 看 `orders.csv` 最后几笔 + `equity.csv`：确认是回撤熔断（-20%）而非数据错误
2. 跑 `python research/revalidate.py`：策略在最新数据上是否仍在审计带内
3. 若策略未退化（回撤属正常分布内，参照 E29：一年 6% 概率）→ 删除 HALTED 文件重启
4. 若策略退化（revalidate WARN）→ 保持停机，等季度重评裁决

## 模拟盘/实盘与回测走势背离（paper-report 出 WARN）

- 单项 WARN + regime context 显示"单边暴涨区"→ 已知弱势 regime，按 playbook 预期管理，不动
- 多项 WARN 或常态区间里持续跑输 → 停止加仓，跑 revalidate + universe_score 找原因
- **纪律：任何情况下不因为几天的 P&L 手动改参数**

## 数据问题

- OKX 连不上：适配器自动切换交易所序列；若全部失败，tick 会报错但不会乱下单（无数据=无动作）
- 数据缺口：store 增量合并会自愈——重跑 `qtrade.cli fetch` 即可补齐
- baostock 慢/挂：只影响 A股研究，不影响 crypto 生产路径

## 机器/进程问题

- **停机 <6 小时：无需任何处理**（E32：信号衰减极慢，晚 6 小时仅 -2pp）
- 停机 >1 天：手动跑一次 `qtrade.cli paper`（或 live），让仓位追上目标即可
- launchd 日志在 `outputs/paper/launchd.log`；任务卸载：`launchctl bootout gui/$(id -u)/com.qtrade.paper`

## 制度日历

| 频率 | 动作 | 工具 |
|------|------|------|
| 每小时 | 模拟盘/实盘 tick（自动） | launchd / cron |
| 每周看一眼 | 健康检查 | `qtrade.cli paper-report` |
| 每月 | 策略退化检测 | `research/revalidate.py` |
| 每季度 | 品种池重评 + v2 晋升裁决 | `research/universe_score.py` + log 预注册规则 |

## 红线（永远不做）

- 不在命令行/代码/聊天中出现 API key 值
- 不开提币权限的 API key
- 不因单日/单周行情手动 override 系统仓位
- 不跳过预注册门槛改参数——想改，先写标准再跑实验
