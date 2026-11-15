"""Render the static site: site/templates + site/static -> build/site. Payloads come from build_metrics.

    make site
"""

from __future__ import annotations

import shutil
import sys

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.adapters.source import PROJECT

SITE = PROJECT / "site"
OUT = PROJECT / "build" / "site"

PAGES = {
    "index": ("Overview", "Is anything off right now?"),
    "cycling": ("Cycling", "Is my cycling fitness coming back, and is my training distributed the way I intend?"),
    "map": ("Map", "Where have I ridden?"),
    "strength": ("Strength", "Am I progressing on my lifts, and is each muscle group getting enough work?"),
    "health": ("Health", "Is my body recovering normally, and what moves my sleep and recovery?"),
    "reviews": ("Reviews", "How did each finished week go, in a short written review?"),
}


def main():
    env = Environment(loader=FileSystemLoader(SITE / "templates"), autoescape=select_autoescape(["html"]))
    tpl = env.get_template("base.html")
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "static").exists():
        shutil.rmtree(OUT / "static")
    shutil.copytree(SITE / "static", OUT / "static")
    for page, (title, question) in PAGES.items():
        if not (OUT / "data" / f"{page}.js").exists():
            print(f"[site] warning: build/site/data/{page}.js missing (run make metrics)", file=sys.stderr)
        if not (SITE / "static" / f"{page}.js").exists():
            print(f"[site] skipping {page}: no site/static/{page}.js yet", file=sys.stderr)
            continue
        inline_svg = (SITE / "static" / "body.svg").read_text() if page == "strength" else None
        (OUT / f"{page}.html").write_text(tpl.render(page=page, title=title, question=question, inline_svg=inline_svg))
        print(f"[site] {page}.html", file=sys.stderr)


if __name__ == "__main__":
    main()
