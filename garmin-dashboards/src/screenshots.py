"""Headless-Chrome screenshots of every built page at 1440 px and 390 px, light and dark, for visual QA.

    make screenshots            -> build/screenshots/<page>-<width>-<theme>.png
    .venv/bin/python -m src.screenshots health 1440 light
Console errors from the pages are printed (Chrome logging).
"""

from __future__ import annotations

import re
import subprocess
import sys

from src.adapters.source import PROJECT

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SITE = PROJECT / "build" / "site"
OUT = PROJECT / "build" / "screenshots"
PAGES = ["index", "cycling", "map", "strength", "health", "reviews"]
WIDTHS = {1440: 6400, 390: 15000}
THEMES = {"light": "1", "dark": "0"}  # blink preferredColorScheme: 0 = dark, 1 = light


def shoot(page: str, width: int, theme: str) -> list[str]:
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{page}-{width}-{theme}.png"
    url, window_w = (SITE / f"{page}.html").as_uri(), width
    if width < 500:
        # desktop Chrome won't lay out narrower than ~500 px, so render phone widths inside an iframe
        frame = SITE / f"_frame-{page}-{width}.html"
        frame.write_text(f'<!doctype html><body style="margin:0"><iframe src="{page}.html" '
                         f'style="width:{width}px;height:{WIDTHS[width]}px;border:0;display:block"></iframe>')
        url, window_w = frame.as_uri(), 520
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={window_w},{WIDTHS[width]}",
           f"--blink-settings=preferredColorScheme={THEMES[theme]}", "--virtual-time-budget=6000",
           "--enable-logging=stderr", "--v=0", f"--screenshot={target}", url]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if width < 500 and target.exists():
        from PIL import Image
        Image.open(target).crop((0, 0, width, WIDTHS[width])).save(target)
        (SITE / f"_frame-{page}-{width}.html").unlink(missing_ok=True)
    errors = [l for l in res.stderr.splitlines() if "CONSOLE" in l and re.search(r"error|Uncaught", l, re.I)]
    print(f"{target.name}: {'ok' if target.exists() else 'FAILED'}" + (f", {len(errors)} console errors" if errors else ""))
    return errors


def main():
    args = sys.argv[1:]
    pages = [a for a in args if a in PAGES] or [p for p in PAGES if (SITE / f"{p}.html").exists()]
    widths = [int(a) for a in args if a.isdigit()] or list(WIDTHS)
    themes = [a for a in args if a in THEMES] or list(THEMES)
    errors = []
    for p in pages:
        for w in widths:
            for t in themes:
                errors += shoot(p, w, t)
    for e in sorted(set(errors)):
        print("  !", e[:300])


if __name__ == "__main__":
    main()
