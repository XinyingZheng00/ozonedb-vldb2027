#!/usr/bin/env python3
"""Render one of the self-contained HTML charts under bench/usecases/charts/
to a PDF snapshot. The script uses a headless Chromium (Playwright).

The charts are interactive: hover tooltips, legend click-to-isolate, and a
table-view toggle. None of that survives a PDF. A PDF is a flat image of
the state of the page at render time (the default state, nothing hovered
or toggled). If you want the interactive version, open the .html file
in a browser.

One-time setup:
    pip3 install playwright
    python3 -m playwright install --with-deps chromium

Usage:
    python3 bench/usecases/render_chart_pdf.py bench/usecases/charts/phase1_rhizome_chart.html
    python3 bench/usecases/render_chart_pdf.py <chart.html> --out <chart.pdf> --width 1120
"""
import argparse
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("html_path", help="path to the chart's .html file")
    ap.add_argument("--out", default=None, help="output .pdf path (default: same name as input, .pdf extension)")
    ap.add_argument("--width", type=int, default=1120, help="render viewport width in px")
    ap.add_argument("--selector", default=".viz-root", help="CSS selector whose scrollHeight sets the PDF page height")
    args = ap.parse_args()

    html_path = Path(args.html_path).resolve()
    if not html_path.exists():
        print(f"FATAL: {html_path} not found", file=sys.stderr)
        sys.exit(1)
    out_path = Path(args.out) if args.out else html_path.with_suffix(".pdf")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("FATAL: playwright not installed. Run:\n"
              "  pip3 install playwright\n"
              "  python3 -m playwright install --with-deps chromium", file=sys.stderr)
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": args.width, "height": 900})
        page.goto(html_path.as_uri())
        page.wait_for_timeout(300)  # let the chart's own JS finish drawing
        height = page.evaluate(f"document.querySelector('{args.selector}').scrollHeight")
        page.set_viewport_size({"width": args.width, "height": int(height) + 40})
        page.pdf(
            path=str(out_path),
            width=f"{args.width}px",
            height=f"{int(height) + 40}px",
            print_background=True,
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
        )
        browser.close()

    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
