"""Markdown report -> PDF via headless Chrome (user standing request
2026-07-26: all reports delivered as PDF).

Usage: .venv/bin/python tools/md2pdf.py docs/report.md [out.pdf]

Chrome is the renderer because it is the only thing on this machine that
typesets CJK well without installing a TeX stack; the CSS below keeps the
output print-clean (page margins via @page, no browser headers).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import markdown

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

CSS = """
@page { size: A4; margin: 18mm 16mm; }
body { font-family: 'PingFang SC', 'Helvetica Neue', sans-serif;
       font-size: 10.5pt; line-height: 1.55; color: #1a1a1a; max-width: none; }
h1 { font-size: 17pt; border-bottom: 2px solid #333; padding-bottom: 4px; }
h2 { font-size: 13.5pt; margin-top: 1.4em; border-bottom: 1px solid #bbb;
     padding-bottom: 2px; }
h3 { font-size: 11.5pt; margin-top: 1.1em; }
code { font-family: Menlo, monospace; font-size: 9pt;
       background: #f2f2f2; padding: 0 3px; border-radius: 3px; }
pre { background: #f6f6f6; padding: 8px; border-radius: 4px;
      font-size: 8.5pt; overflow-x: hidden; white-space: pre-wrap; }
blockquote { border-left: 3px solid #999; margin-left: 0; padding-left: 12px;
             color: #444; }
table { border-collapse: collapse; width: 100%; font-size: 9.5pt; }
th, td { border: 1px solid #ccc; padding: 4px 7px; text-align: left; }
th { background: #efefef; }
li { margin: 2px 0; }
h1, h2, h3 { page-break-after: avoid; }
pre, blockquote, table { page-break-inside: avoid; }
"""


def convert(md_path: Path, pdf_path: Path) -> Path:
    body = markdown.markdown(
        md_path.read_text(), extensions=["tables", "fenced_code", "sane_lists"])
    html = (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<style>{CSS}</style></head><body>{body}</body></html>")
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html)
        tmp = f.name
    r = subprocess.run(
        [CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={pdf_path}", f"file://{tmp}"],
        capture_output=True, timeout=120)
    Path(tmp).unlink(missing_ok=True)
    if r.returncode != 0 or not pdf_path.exists():
        raise RuntimeError(f"chrome pdf failed: {r.stderr.decode()[-400:]}")
    return pdf_path


if __name__ == "__main__":
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_suffix(".pdf")
    print(convert(src, dst))
