"""Generate the qtrade ops dashboard as one self-contained HTML file.

Reads live state (paper equities, US stances, work log, ledger, alert state)
and emits outputs/dashboard.html for publishing as a claude.ai artifact.
Rerun daily by the update-dashboard scheduled task.

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
    ("crypto_core", "Crypto 旗舰", "10币 趋势+回归 · 1h", "验证"),
    ("crypto_core_v2", "Crypto v2", "深熊过滤 · 平行 A/B", "平行"),
    ("crypto_core_15", "Crypto 15%档", "E71 风险档演示 · ×1.154", "演示"),
    ("crypto_core_4h", "Crypto 4h", "低频执行变体", "平行"),
    ("cn_futures", "国内商品", "14品种 CTA · 日频", "验证"),
    ("futures_ibkr", "美期货", "9品种 · 前瞻观察", "观察"),
    ("etf_trend", "美股 ETF", "10 ETF 长多趋势", "观察"),
    ("ashare_ml", "A股 ML", "沪深300 · top-50 月频", "观察"),
    ("cb_double_low", "转债双低", "top-20 月频", "观察"),
    ("llm_agents", "LLM 委员会", "vs 机械系统 A/B", "观察"),
    ("llm_us", "LLM 美股50", "E70 · vs SPY+机械趋势", "观察"),
]
FRONTIER = [("6%", "+4.0%", "−12.7%"), ("10%", "+6.5%", "−20.4%"),
            ("15%", "+9.4%", "−29.2%"), ("20%", "+12.2%", "−37.1%"),
            ("30%", "+17.1%", "−50.8%")]
# external strategy map (2026-08-05 survey, 12 agents; see docs/strategy-map)
PIPELINE_SHORTLIST = [
    ("再平衡时点分桶", "评估层 · 消除换仓日运气 >100bp/年方差 · 零成本"),
    ("A股执行时点覆盖层", "执行层 · T+1 隔夜折价实测 4-6bp/日 → +0.5-0.8%/年 · E72 预注册中"),
    ("极端资金费反转", "crypto 新信号族 · funding 历史已回填 2019 起 · E73 预注册排队"),
]
PIPELINE_COUNTS = ("3 入选", "6 暂存(带解锁条件)", "20 判死(墓碑入档)")


def daily_equity(key: str) -> pd.Series | None:
    f = REPO / "outputs" / "paper" / key / "equity.csv"
    if not f.exists():
        return None
    eq = pd.read_csv(f)
    ts = pd.to_datetime(eq["ts"], format="mixed", utc=True)
    s = pd.Series(eq["equity"].values, index=pd.DatetimeIndex(ts))
    return s.groupby(s.index.floor("D")).last()


def spark(eq: pd.Series, w: int = 220, h: int = 40) -> str:
    if len(eq) < 2:
        return '<div class="spark-empty">起账首日</div>'
    eq = eq.iloc[:: max(1, len(eq) // 120)]
    lo, hi = float(eq.min()), float(eq.max())
    pad = max((hi - lo) * 0.1, 1e-9)
    lo, hi = lo - pad, hi + pad
    xs = [i / (len(eq) - 1) * (w - 8) + 4 for i in range(len(eq))]
    ys = [h - 4 - (v - lo) / (hi - lo) * (h - 8) for v in eq]
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    base = ""
    if lo < 10000 < hi:
        by = h - 4 - (10000 - lo) / (hi - lo) * (h - 8)
        base = f'<line x1="4" y1="{by:.1f}" x2="{w-4}" y2="{by:.1f}" class="spark-base"/>'
    area = (f'M4,{h-4} L' + " L".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
            + f" L{xs[-1]:.1f},{h-4} Z")
    return (f'<svg viewBox="0 0 {w} {h}" class="spark" role="img" aria-label="权益">'
            f'{base}<path d="{area}" class="spark-fill"/>'
            f'<polyline points="{pts}" class="spark-line"/>'
            f'<circle cx="{xs[-1]:.1f}" cy="{ys[-1]:.1f}" r="3" class="spark-dot"/></svg>')


def combined_chart(w: int = 960, h: int = 200) -> str:
    frames = {}
    for key, *_ in BOOKS:
        s = daily_equity(key)
        if s is not None and len(s) > 1:
            frames[key] = s
    if not frames:
        return ""
    df = pd.DataFrame(frames).ffill().dropna(how="all")
    start = df.apply(lambda c: c.dropna().iloc[0])
    total = df.ffill().fillna(0).sum(axis=1) + (10000 * df.isna().iloc[0]).sum() * 0
    # normalize: sum of live books + 10k per not-yet-started book at each date
    n_books = df.shape[1]
    filled = df.copy()
    for c in filled.columns:
        first = filled[c].first_valid_index()
        filled.loc[:first, c] = filled.loc[:first, c].fillna(10000.0)
        filled[c] = filled[c].ffill()
    total = filled.sum(axis=1) / (n_books * 10000) * 100 - 100  # % on combined
    lo, hi = float(total.min()), float(total.max())
    pad = max((hi - lo) * 0.15, 0.05)
    lo, hi = lo - pad, hi + pad
    n = len(total)
    xs = [i / (n - 1) * (w - 70) + 54 for i in range(n)]
    ys = [h - 24 - (v - lo) / (hi - lo) * (h - 44) for v in total]
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    area = (f'M{xs[0]:.1f},{h-24} L' + " L".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
            + f" L{xs[-1]:.1f},{h-24} Z")
    grid, glabels = [], []
    for gv in (lo + pad, 0.0, hi - pad):
        gy = h - 24 - (gv - lo) / (hi - lo) * (h - 44)
        grid.append(f'<line x1="54" y1="{gy:.1f}" x2="{w-16}" y2="{gy:.1f}" class="grid"/>')
        glabels.append(f'<text x="48" y="{gy+4:.1f}" class="axis" text-anchor="end">{gv:+.2f}%</text>')
    d0, d1 = total.index[0].strftime("%m-%d"), total.index[-1].strftime("%m-%d")
    dates = [t.strftime("%m-%d") for t in total.index]
    vals = [f"{v:+.2f}%" for v in total]
    return f"""<div class="chartwrap"><svg id="combochart" viewBox="0 0 {w} {h}"
  data-xs="{','.join(f'{x:.1f}' for x in xs)}" data-dates="{','.join(dates)}" data-vals="{','.join(vals)}">
  {''.join(grid)}{''.join(glabels)}
  <path d="{area}" class="spark-fill"/>
  <polyline points="{pts}" class="chart-line"/>
  <circle cx="{xs[-1]:.1f}" cy="{ys[-1]:.1f}" r="3.5" class="spark-dot"/>
  <text x="54" y="{h-8}" class="axis">{d0}</text>
  <text x="{w-16}" y="{h-8}" class="axis" text-anchor="end">{d1}</text>
  <line id="xhair" x1="0" y1="20" x2="0" y2="{h-24}" class="xhair" visibility="hidden"/>
</svg><div id="tip" class="tip" hidden></div></div>
<script>
(function(){{
  var svg=document.getElementById('combochart'), tip=document.getElementById('tip'),
      xh=document.getElementById('xhair');
  var xs=svg.dataset.xs.split(',').map(Number), ds=svg.dataset.dates.split(','),
      vs=svg.dataset.vals.split(',');
  svg.addEventListener('mousemove',function(e){{
    var r=svg.getBoundingClientRect(), x=(e.clientX-r.left)*({w}/r.width), best=0,bd=1e9;
    for(var i=0;i<xs.length;i++){{var d=Math.abs(xs[i]-x); if(d<bd){{bd=d;best=i;}}}}
    xh.setAttribute('x1',xs[best]); xh.setAttribute('x2',xs[best]);
    xh.setAttribute('visibility','visible');
    tip.hidden=false; tip.textContent=ds[best]+' · '+vs[best];
    tip.style.left=(e.clientX-r.left+14)+'px'; tip.style.top='8px';
  }});
  svg.addEventListener('mouseleave',function(){{tip.hidden=true;xh.setAttribute('visibility','hidden');}});
}})();
</script>"""


def book_cards() -> tuple[str, float, int]:
    cards, total, n = [], 0.0, 0
    for key, name, sub, tier in BOOKS:
        s = daily_equity(key)
        if s is None:
            continue
        cur = float(s.iloc[-1])
        total += cur
        n += 1
        ret = (cur / float(s.iloc[0]) - 1) * 100
        cls = "up" if ret >= 0 else "down"
        cards.append(f"""<div class="card">
  <div class="card-head"><span class="book-name">{escape(name)}
  <span class="tier">{tier}</span></span><span class="ret {cls}">{ret:+.2f}%</span></div>
  <div class="book-sub">{escape(sub)} · {len(s)-1} 交易日</div>
  {spark(s.pct_change().dropna().pipe(lambda r:(1+r).cumprod()*10000) if len(s)>1 else s)}
  <div class="book-eq">{cur:,.2f}</div></div>""")
    return "\n".join(cards), total, n


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
    ts = st.get("ts", "")[:16]
    return ("".join(chips)
            + f'<div class="muted" style="margin-top:6px">信号时点 {escape(ts)} UTC · '
              '翻向即时推送已启用 · 每日收盘摘要定时任务运行中</div>')


def work_feed(n: int = 8) -> tuple[str, int]:
    log = (REPO / "research" / "log.md").read_text().splitlines()
    heads = [ln[4:] for ln in log if ln.startswith("### ")][-n:]
    items = "".join(f"<li>{escape(h)}</li>" for h in reversed(heads))
    n_exp = len({m.group(1) for ln in log
                 for m in [re.match(r"### (E\d+)", ln)] if m})
    return f'<ol class="feed">{items}</ol>', n_exp


def report_shelf(n: int = 8) -> str:
    pdfs = sorted((REPO / "docs").glob("*.pdf"), key=lambda p: p.stat().st_mtime,
                  reverse=True)[:n]
    rows = "".join(
        f'<tr><td class="mono">{datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d")}</td>'
        f"<td>{escape(p.stem)}</td></tr>" for p in pdfs)
    return f'<table class="shelf">{rows}</table>'


def stat_tiles(n_exp: int, n_books: int) -> str:
    snaps = len(list((REPO / "data_store" / "market_snapshots").rglob("*.parquet"))) \
        if (REPO / "data_store" / "market_snapshots").exists() else 0
    ev = REPO / "data_store" / "cn_cb_events" / "events.csv"
    n_ev_days = len(list((REPO / "data_store" / "cn_cb_events" / "redeem").glob("*.parquet"))) \
        if ev.parent.exists() else 0
    tiles = [(f"{n_exp}", "编号实验(含全部失败)"), (f"{n_books}", "并行纸面账本"),
             ("5", "自制时点数据流"), (f"{snaps + n_ev_days}", "已存档数据快照")]
    return "".join(f'<div class="tile"><div class="tile-num">{v}</div>'
                   f'<div class="tile-label">{k}</div></div>' for v, k in tiles)


def alerts_line() -> str:
    f = REPO / "outputs" / "alerts_state.json"
    try:
        finds = json.loads(f.read_text()).get("findings", [])
        if finds:
            return ('<span class="chip warn">⚠ ' + escape(finds[0][:60])
                    + (f" 等 {len(finds)} 项" if len(finds) > 1 else "") + "</span>")
    except Exception:
        pass
    return '<span class="chip clear">✓ ALL CLEAR</span>'


def frontier_table() -> str:
    rows = ""
    for vol, ret, dd in FRONTIER:
        sel = ' class="sel"' if vol == "15%" else ""
        rows += f"<tr{sel}><td>{vol}</td><td>{ret}</td><td>{dd}</td></tr>"
    return f"""<table class="frontier"><tr><th>波动档</th><th>年化(7年历史)</th>
<th>最大回撤</th></tr>{rows}</table>
<div class="muted">同一引擎、同一 Sharpe,只改部署风险。用户已选 15% 档(高亮),
E71 演示账本 crypto_core_15 于 2026-08-05 起账。收益的代价是回撤,无第三种货币。</div>"""


def main() -> None:
    cards, total, n_books = book_cards()
    feed, n_exp = work_feed()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    combined = (total / (n_books * 10000) - 1) * 100
    ccls = "up" if combined >= 0 else "down"

    html = f"""<title>qtrade · 自主量化研究系统</title>
<style>
:root {{
  --bg:#F7F6F2; --surface:#FFFFFF; --ink:#23303B; --muted:#6B7683;
  --line:#E3E1DA; --accent:#A87B2F; --up:#2E7D5B; --down:#B5533C;
  --chip-good-bg:#E6F0EA; --chip-warn-bg:#F6ECD9; --chip-flat-bg:#EDEBE5;
  --sel-bg:#F4EBDD;
}}
@media (prefers-color-scheme: dark) {{ :root {{
  --bg:#10161D; --surface:#1A222B; --ink:#D8DFE6; --muted:#8C97A3;
  --line:#2A3540; --accent:#C9A050; --up:#4CAF82; --down:#D0705A;
  --chip-good-bg:#1E3229; --chip-warn-bg:#33291A; --chip-flat-bg:#232B33;
  --sel-bg:#2C2A20;
}} }}
:root[data-theme="dark"] {{
  --bg:#10161D; --surface:#1A222B; --ink:#D8DFE6; --muted:#8C97A3;
  --line:#2A3540; --accent:#C9A050; --up:#4CAF82; --down:#D0705A;
  --chip-good-bg:#1E3229; --chip-warn-bg:#33291A; --chip-flat-bg:#232B33;
  --sel-bg:#2C2A20;
}}
:root[data-theme="light"] {{
  --bg:#F7F6F2; --surface:#FFFFFF; --ink:#23303B; --muted:#6B7683;
  --line:#E3E1DA; --accent:#A87B2F; --up:#2E7D5B; --down:#B5533C;
  --chip-good-bg:#E6F0EA; --chip-warn-bg:#F6ECD9; --chip-flat-bg:#EDEBE5;
  --sel-bg:#F4EBDD;
}}
* {{ box-sizing:border-box }}
body {{ background:var(--bg); color:var(--ink); margin:0;
  font:15px/1.6 -apple-system,"PingFang SC","Segoe UI",sans-serif; }}
.wrap {{ max-width:1080px; margin:0 auto; padding:30px 20px 64px }}
h1,h2,.mono {{ font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace }}
h1 {{ font-size:21px; letter-spacing:.02em; margin:0 }}
h2 {{ font-size:12.5px; text-transform:uppercase; letter-spacing:.14em;
  color:var(--muted); margin:42px 0 14px; border-bottom:1px solid var(--line);
  padding-bottom:8px }}
.statusband {{ display:flex; flex-wrap:wrap; gap:14px; align-items:baseline; margin-top:10px }}
.bignum {{ font-family:ui-monospace,Menlo,monospace; font-size:32px;
  font-variant-numeric:tabular-nums }}
.up {{ color:var(--up) }} .down {{ color:var(--down) }}
.muted {{ color:var(--muted); font-size:13px }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:12px; margin-top:18px }}
.tile {{ background:var(--surface); border:1px solid var(--line); border-radius:6px;
  padding:12px 14px }}
.tile-num {{ font-family:ui-monospace,Menlo,monospace; font-size:24px }}
.tile-label {{ color:var(--muted); font-size:12.5px }}
.grid2 {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:13px }}
.card {{ background:var(--surface); border:1px solid var(--line); border-radius:6px;
  padding:13px 15px }}
.card-head {{ display:flex; justify-content:space-between; align-items:baseline }}
.book-name {{ font-weight:600 }}
.tier {{ font-size:10.5px; color:var(--muted); border:1px solid var(--line);
  border-radius:4px; padding:1px 5px; margin-left:6px; vertical-align:1px;
  font-family:ui-monospace,Menlo,monospace }}
.book-sub {{ color:var(--muted); font-size:12.5px; margin:2px 0 8px }}
.book-eq {{ font-family:ui-monospace,Menlo,monospace; font-size:13px;
  color:var(--muted); margin-top:6px; font-variant-numeric:tabular-nums }}
.ret {{ font-family:ui-monospace,Menlo,monospace; font-size:15px;
  font-variant-numeric:tabular-nums }}
.spark {{ width:100%; height:40px; display:block }}
.spark-empty {{ height:40px; display:flex; align-items:center; color:var(--muted);
  font-size:12px }}
.spark-line {{ fill:none; stroke:var(--accent); stroke-width:2 }}
.chart-line {{ fill:none; stroke:var(--accent); stroke-width:2.25 }}
.spark-fill {{ fill:var(--accent); opacity:.09 }}
.spark-dot {{ fill:var(--accent) }}
.spark-base {{ stroke:var(--muted); stroke-width:1; stroke-dasharray:2 4; opacity:.5 }}
.grid {{ stroke:var(--line); stroke-width:1 }}
.axis {{ fill:var(--muted); font-family:ui-monospace,Menlo,monospace; font-size:10.5px }}
.xhair {{ stroke:var(--muted); stroke-width:1; stroke-dasharray:3 3 }}
.chartwrap {{ position:relative; background:var(--surface); border:1px solid var(--line);
  border-radius:6px; padding:10px }}
.chartwrap svg {{ width:100%; height:auto; display:block }}
.tip {{ position:absolute; background:var(--surface); border:1px solid var(--line);
  border-radius:4px; padding:3px 9px; font:12px ui-monospace,Menlo,monospace;
  pointer-events:none }}
.chip {{ display:inline-block; padding:3px 10px; border-radius:99px; font-size:12.5px;
  font-family:ui-monospace,Menlo,monospace; margin:0 6px 6px 0;
  border:1px solid var(--line); background:var(--chip-flat-bg) }}
.chip.long {{ background:var(--chip-good-bg); color:var(--up); border-color:transparent }}
.chip.clear {{ background:var(--chip-good-bg); color:var(--up) }}
.chip.warn {{ background:var(--chip-warn-bg); color:var(--down) }}
.feed {{ margin:0; padding-left:20px }} .feed li {{ margin:7px 0; max-width:76ch }}
table {{ border-collapse:collapse; font-variant-numeric:tabular-nums }}
.frontier td,.frontier th {{ border:1px solid var(--line); padding:5px 14px;
  font-family:ui-monospace,Menlo,monospace; font-size:13px; text-align:right }}
.frontier th {{ color:var(--muted); font-weight:500; font-size:11.5px;
  text-transform:uppercase; letter-spacing:.08em }}
.frontier tr.sel td {{ background:var(--sel-bg); font-weight:700 }}
.frontier {{ margin-bottom:10px }}
.shelf td {{ padding:4px 14px 4px 0; border-bottom:1px solid var(--line); font-size:13.5px }}
.about {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:13px }}
.about .card p {{ margin:6px 0; font-size:13.5px }}
.foot {{ margin-top:48px; color:var(--muted); font-size:12.5px;
  border-top:1px solid var(--line); padding-top:14px; max-width:80ch }}
</style>
<div class="wrap">
<header>
  <div class="muted mono">QTRADE · AUTONOMOUS QUANT RESEARCH · SINCE 2026-06</div>
  <h1>十一本纸面账,一台机器,全程预注册</h1>
  <div class="statusband">
    <span class="bignum {ccls}">{combined:+.2f}%</span>
    <span class="muted">十一本账合计(各 10k 起,纸面模拟)</span>
    {alerts_line()}
    <span class="muted">生成于 {now}</span>
  </div>
  <div class="tiles">{stat_tiles(n_exp, n_books)}</div>
</header>

<h2>合计权益曲线(悬停查看逐日)</h2>
{combined_chart()}

<h2>纸面账本</h2>
<div class="grid2">{cards}</div>

<h2>风险档位 · E71(用户已选 15% 档)</h2>
{frontier_table()}

<h2>美股信号面板 · etf_trend(预注册机械信号,非投资建议)</h2>
<div>{us_panel()}</div>

<h2>研究管线 · 外部策略地图(29 家族证据审判,2026-08-05)</h2>
<div class="muted" style="margin-bottom:10px">{' · '.join(PIPELINE_COUNTS)}
——入选 ≠ 立项,每项须过预注册与 placebo 对照;判死名单含美股隔夜漂移(NY Fed
2026 宣判)、美股 PEAD、52周高点、商品季节性等,墓碑全档在仓库。</div>
<div class="about">{''.join(f'<div class="card"><div class="book-name">{escape(n)}</div><p>{escape(d)}</p></div>' for n, d in PIPELINE_SHORTLIST)}</div>

<h2>机器此刻在做什么(研究日志最近条目)</h2>
{feed}

<h2>报告架(md 源与 PDF 均在仓库 docs/)</h2>
{report_shelf()}

<h2>系统与纪律</h2>
<div class="about">
  <div class="card"><div class="book-name">这是什么</div>
    <p>单机自主量化研究系统:{n_exp} 个编号实验(失败全记录),十一本纸面账每小时
    自动记账,每日异地备份 + 死人开关,监控自带暗窗报警。</p></div>
  <div class="card"><div class="book-name">证据口径</div>
    <p>经外部评审四轮 + 过程零假设检验(E69-B):历史回测数字不构成独立证据;
    资本讨论只引前瞻记录与外部先验。收益 = Sharpe × 风险档位,档位是人的决定。</p></div>
  <div class="card"><div class="book-name">运营者(Claude)的边界</div>
    <p>造系统、跑研究、写报告;不执行真实交易、不碰凭据、不给个性化投资建议。
    真钱开关永远在人手里。</p></div>
  <div class="card"><div class="book-name">自制数据资产</div>
    <p>五条"活在前面"的时点数据流每日采集:转债条款事件、crypto 资金费/持仓量、
    A股涨停池、龙虎榜——三年后买不到的数据,从今天开始存。</p></div>
</div>

<div class="foot">纸面账 = 模拟成交,非真实资金;所有收益数字为模拟结果。本页为
系统状态展示,不构成任何投资建议。页面由 tools/build_dashboard.py 从仓库实时状态
生成,每日自动重新发布。</div>
</div>"""
    OUT.write_text(html)
    print(f"dashboard -> {OUT} ({len(html)} bytes)")


if __name__ == "__main__":
    main()
