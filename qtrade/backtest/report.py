"""Render backtest results to console text and a saved markdown report."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .engine import BacktestResult

DEFAULT_OUT = Path(__file__).resolve().parents[2] / "outputs"


def render_text(result: BacktestResult) -> str:
    df = result.to_frame()
    lines = [
        f"strategy : {result.strategy}",
        f"symbol   : {result.symbol}  ({result.timeframe})",
        "",
        df.to_string(),
        "",
        _verdict(df),
    ]
    return "\n".join(lines)


def _verdict(df) -> str:
    """One honest sentence: did it beat buy & hold out of sample?"""
    oos = df.loc["out_of_sample"]
    edge = oos["total_return_pct"] - oos["benchmark_return_pct"]
    if edge > 0:
        return f"样本外跑赢 buy&hold {edge:.1f} 个百分点 — 值得进一步验证（参数敏感性、多品种）。"
    return f"样本外跑输 buy&hold {abs(edge):.1f} 个百分点 — 该配置暂无优势，别上实盘。"


def save_markdown(result: BacktestResult, out_dir: Path = DEFAULT_OUT) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_symbol = result.symbol.replace("/", "_")
    p = out_dir / f"backtest_{safe_symbol}_{result.timeframe}_{ts}.md"
    df = result.to_frame()
    p.write_text(
        f"# Backtest — {result.strategy}\n\n"
        f"- symbol: `{result.symbol}` @ {result.timeframe}\n"
        f"- generated: {ts} UTC\n\n"
        f"{df.to_markdown()}\n\n> {_verdict(df)}\n",
        encoding="utf-8",
    )
    return p
