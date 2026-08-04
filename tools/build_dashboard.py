"""Generate the qtrade ops dashboard as one self-contained HTML file.

Reads live state (paper equities, US stances, work log, alert state) and emits
outputs/dashboard.html for publishing as a claude.ai artifact. Rerun daily by
the update-dashboard scheduled task so the page tracks the machine.

Usage: .venv/bin/python tools/build_dashboard.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html import escape
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "outputs" / "dashboard.html"

BOOKS = [
    ("crypto_core", "Crypto 旗舰", "10币 趋势+回归 1h"),
    ("crypto_core_v2", "Crypto v2", "深熊过滤平行臂"),
    ("crypto_core_4h", "Crypto 4h", "低频执行变体"),
    ("cn_futures", "国内商品", "14品种 CTA 日频"),
    ("futures_ibkr", "美期货", "9品种 观察账本"),
    ("etf_trend", "美股 ETF", "10 ETF 长多趋势"),
    ("ashare_ml", "A股 ML", "沪深300 内 top-50"),
    ("cb_double_low", "转债双低", "月频 top-20"),
    ("llm_agents", "LLM 委员会", "vs 机械系统 A/B"),
]


def spark(daily: pd.Series, w: int = 220, h: int = 44) -> str:
    if len(daily) < 2:
        return ""
    eq = (1 + daily).cumprod() * 10000
    eq = eq.iloc[:: max(1, len(eq) // 120)]
    lo, hi = float(eq.min()), float(eq.max())
    pad = max((hi - lo) * 0.1, 1e-9)
    lo, hi = lo - pad, hi + pad
    xs = [i / (len(eq) - 1) * (w - 8) + 4 for i in range(len(eq))]
    ys = [h - 4 - (v - lo) / (hi - lo) * (h - 8) for v in eq]
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    base_y = h - 4 - (10000 - lo) / (hi - lo) * (h - 8)
    base = (f'<line x1="4" y1="{base_y:.1f}" x2="{w-4}" y2="{base_y:.1f}" '
            'class="spark-base"/>' if lo < 10000 < hi else "")
    area = (f'M4,{h-4} L' + " L".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
            + f" L{xs[-1]:.1f},{h-4} Z")
    return (f'<svg viewBox="0 0 {w} {h}" class="spark" role="img" '
            f'aria-label="权益曲线">{base}'
            f'<path d="{area}" class="spark-fill"/>'
            f'<polyline points="{pts}" class="spark-line"/>'
            f'<circle cx="{xs[-1]:.1f}" cy="{ys[-1]:.1f}" r="3" class="spark-dot"/></svg>')


def book_cards() -> tuple[str, float]:
    cards, total = [], 0.0
    for key, name, sub in BOOKS:
        f = REPO / "outputs" / "paper" / key / "equity.csv"
        if not f.exists():
            continue
        eq = pd.read_csv(f)
        ts = pd.to_datetime(eq["ts"], format="mixed", utc=True)
        s = pd.Series(eq["equity"].values, index=pd.DatetimeIndex(ts))
        daily = s.groupby(s.index.floor("D")).last().pct_change().dropna()
        cur = float(s.iloc[-1])
        total += cur
        ret = (cur / float(s.iloc[0]) - 1) * 100
        days = (ts.iloc[-1] - ts.iloc[0]).total_seconds() / 86400
        cls = "up" if ret >= 0 else "down"
        cards.append(f"""<div class="card">
  <div class="card-head"><span class="book-name">{escape(name)}</span>
  <span class="ret {cls}">{ret:+.2f}%</span></div>
  <div class="book-sub">{escape(sub)} · {days:.0f} 天</div>
  {spark(daily)}
  <div class="book-eq">{cur:,.2f}</div></div>""")
    return "\n".join(cards), total


def us_panel() -> str:
    f = REPO / "outputs" / "us_watch_state.json"
    if not f.exists():
        return ""
    st = json.loads(f.read_text())
    chips = []
    for sym, stance in sorted(st.get("stances", {}).items()):
        cls = "long" if stance == "LONG" else "flat"
        label = "持有" if stance == "LONG" else "现金"
        chips.append(f'<span class="chip {cls}">{escape(sym)} · {label}</span>')
    return "".join(chips)


def work_feed(n: int = 7) -> str:
    log = (REPO / "research" / "log.md").read_text().splitlines()
    heads = [ln[4:] for ln in log if ln.startswith("### ")][-n:]
    items = "".join(f"<li>{escape(h)}</li>" for h in reversed(heads))
    n_exp = len({m.group(1) for ln in log
                 for m in [re.match(r"### (E\d+)", ln)] if m})
    return f'<ol class="feed">{items}</ol>', n_exp


def alerts_line() -> str:
    f = REPO / "outputs" / "alerts_state.json"
    try:
        st = json.loads(f.read_text())
        finds = st.get("findings", [])
        if finds:
            return ('<span class="chip warn">⚠ ' + escape(finds[0][:60])
                    + (f" 等 {len(finds)} 项" if len(finds) > 1 else "") + "</span>")
    except Exception:
        pass
    return '<span class="chip clear">✓ ALL CLEAR</span>'


def main() -> None:
    cards, total = book_cards()
    feed, n_exp = work_feed()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    combined = (total / 90000 - 1) * 100
    ccls = "up" if combined >= 0 else "down"

    html = f"""<title>qtrade · 自主量化研究系统</title>
<style>
:root {{
  --bg:#F7F6F2; --surface:#FFFFFF; --ink:#23303B; --muted:#6B7683;
  --line:#E3E1DA; --accent:#A87B2F; --up:#2E7D5B; --down:#B5533C;
  --chip-good-bg:#E6F0EA; --chip-warn-bg:#F6ECD9; --chip-flat-bg:#EDEBE5;
}}
@media (prefers-color-scheme: dark) {{ :root {{
  --bg:#10161D; --surface:#1A222B; --ink:#D8DFE6; --muted:#8C97A3;
  --line:#2A3540; --accent:#C9A050; --up:#4CAF82; --down:#D0705A;
  --chip-good-bg:#1E3229; --chip-warn-bg:#33291A; --chip-flat-bg:#232B33;
}} }}
:root[data-theme="dark"] {{
  --bg:#10161D; --surface:#1A222B; --ink:#D8DFE6; --muted:#8C97A3;
  --line:#2A3540; --accent:#C9A050; --up:#4CAF82; --down:#D0705A;
  --chip-good-bg:#1E3229; --chip-warn-bg:#33291A; --chip-flat-bg:#232B33;
}}
:root[data-theme="light"] {{
  --bg:#F7F6F2; --surface:#FFFFFF; --ink:#23303B; --muted:#6B7683;
  --line:#E3E1DA; --accent:#A87B2F; --up:#2E7D5B; --down:#B5533C;
  --chip-good-bg:#E6F0EA; --chip-warn-bg:#F6ECD9; --chip-flat-bg:#EDEBE5;
}}
* {{ box-sizing:border-box }}
body {{ background:var(--bg); color:var(--ink); margin:0;
  font:15px/1.6 -apple-system,"PingFang SC","Segoe UI",sans-serif; }}
.wrap {{ max-width:1060px; margin:0 auto; padding:32px 20px 64px }}
h1,h2,.mono {{ font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace }}
h1 {{ font-size:22px; letter-spacing:.02em; margin:0 }}
h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.14em;
  color:var(--muted); margin:44px 0 14px; border-bottom:1px solid var(--line);
  padding-bottom:8px }}
.statusband {{ display:flex; flex-wrap:wrap; gap:12px; align-items:baseline;
  margin-top:10px }}
.bignum {{ font-family:ui-monospace,Menlo,monospace; font-size:34px;
  font-variant-numeric:tabular-nums }}
.up {{ color:var(--up) }} .down {{ color:var(--down) }}
.muted {{ color:var(--muted); font-size:13px }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(250px,1fr));
  gap:14px }}
.card {{ background:var(--surface); border:1px solid var(--line);
  border-radius:6px; padding:14px 16px }}
.card-head {{ display:flex; justify-content:space-between; align-items:baseline }}
.book-name {{ font-weight:600 }}
.book-sub {{ color:var(--muted); font-size:12.5px; margin:2px 0 8px }}
.book-eq {{ font-family:ui-monospace,Menlo,monospace; font-size:13px;
  color:var(--muted); margin-top:6px; font-variant-numeric:tabular-nums }}
.ret {{ font-family:ui-monospace,Menlo,monospace; font-size:15px;
  font-variant-numeric:tabular-nums }}
.spark {{ width:100%; height:44px; display:block }}
.spark-line {{ fill:none; stroke:var(--accent); stroke-width:2 }}
.spark-fill {{ fill:var(--accent); opacity:.09 }}
.spark-dot {{ fill:var(--accent) }}
.spark-base {{ stroke:var(--muted); stroke-width:1; stroke-dasharray:2 4; opacity:.5 }}
.chip {{ display:inline-block; padding:3px 10px; border-radius:99px;
  font-size:12.5px; font-family:ui-monospace,Menlo,monospace; margin:0 6px 6px 0;
  border:1px solid var(--line); background:var(--chip-flat-bg) }}
.chip.long {{ background:var(--chip-good-bg); color:var(--up); border-color:transparent }}
.chip.clear {{ background:var(--chip-good-bg); color:var(--up) }}
.chip.warn {{ background:var(--chip-warn-bg); color:var(--down) }}
.feed {{ margin:0; padding-left:20px }} .feed li {{ margin:7px 0; max-width:72ch }}
.about {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
  gap:14px }}
.about .card p {{ margin:6px 0; font-size:14px }}
.foot {{ margin-top:48px; color:var(--muted); font-size:12.5px;
  border-top:1px solid var(--line); padding-top:14px }}
</style>
<div class="wrap">
<header>
  <div class="muted mono">QTRADE · AUTONOMOUS QUANT RESEARCH · SINCE 2026-06</div>
  <h1>九本纸面账,一台机器,全程预注册</h1>
  <div class="statusband">
    <span class="bignum {ccls}">{combined:+.2f}%</span>
    <span class="muted">九本账合计(各 10k 起,纸面)</span>
    {alerts_line()}
    <span class="muted">生成于 {now}</span>
  </div>
</header>

<h2>纸面账本</h2>
<div class="grid">{cards}</div>

<h2>美股信号面板 · etf_trend(预注册机械信号,非投资建议)</h2>
<div>{us_panel()}</div>

<h2>机器此刻在做什么(研究日志最近条目)</h2>
{feed}

<h2>系统与纪律</h2>
<div class="about">
  <div class="card"><div class="book-name">这是什么</div>
    <p>单机自主量化研究系统:{n_exp} 个编号实验(含全部失败),九本纸面账每小时自动
    记账,每日异地备份 + 死人开关,监控自带暗窗报警。</p></div>
  <div class="card"><div class="book-name">证据口径</div>
    <p>经外部评审四轮 + 自建过程零假设检验(E69-B):历史回测数字不构成独立证据;
    一切资本讨论只引前瞻记录与外部先验。</p></div>
  <div class="card"><div class="book-name">运营者(Claude)的边界</div>
    <p>造系统、跑研究、写报告;不执行真实交易、不碰凭据、不给个性化投资建议。
    真钱开关永远在人手里。</p></div>
  <div class="card"><div class="book-name">自制数据资产</div>
    <p>五条"活在前面"的时点数据流每日采集:转债条款事件、crypto 资金费/持仓量、
    A股涨停池、龙虎榜——三年后买不到的数据,从今天开始存。</p></div>
</div>

<div class="foot">纸面账 = 模拟成交,非真实资金。本页为系统状态展示,不构成任何
投资建议。数据截至页面生成时刻,由 tools/build_dashboard.py 从仓库实时状态生成。</div>
</div>"""
    OUT.write_text(html)
    print(f"dashboard -> {OUT} ({len(html)} bytes)")


if __name__ == "__main__":
    main()
