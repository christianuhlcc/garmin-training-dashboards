#!/usr/bin/env python3
"""AI review of the last complete ISO week, generated once per week via the Claude Code CLI.

    garmin-dashboards/.venv/bin/python weekly_review.py            # last complete week, only if no review exists yet
    garmin-dashboards/.venv/bin/python weekly_review.py --week 2026-11-05 --force
    garmin-dashboards/.venv/bin/python weekly_review.py --dry-run  # print the brief, don't call Claude

Pipeline: weekly brief (garmin-dashboards/src/weekly_brief.py, every number precomputed) -> `claude -p` with the
project skill `weekly-training-review` (.claude/skills/) and a JSON schema -> stored in garmin.db table
`weekly_reviews` and data/weekly_reviews/<ISO week>.{json,md}. Runs from daily.sh after the dashboards are rebuilt.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DASH = ROOT / "garmin-dashboards"
sys.path.insert(0, str(DASH))
from src import weekly_brief  # noqa: E402  (needs the dashboards venv: pandas, duckdb)
from src.metrics import common  # noqa: E402

DB = ROOT / "garmin.db"
OUT = ROOT / "data" / "weekly_reviews"
SKILL = ROOT / ".claude" / "skills" / "weekly-training-review" / "SKILL.md"
MAX_BUDGET_USD = "3"

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "headline": {"type": "string", "description": "<= 90 characters, the key takeaway"},
        "summary": {"type": "string", "description": "80-160 words of markdown prose"},
        "ratings": {"type": "array", "minItems": 4, "maxItems": 4, "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"area": {"enum": ["recovery", "strength", "cycling", "consistency"]},
                           "rating": {"enum": ["good", "ok", "watch", "concern"]},
                           "reason": {"type": "string"}},
            "required": ["area", "rating", "reason"]}},
        "highlights": {"type": "array", "maxItems": 4, "items": {"type": "string"}},
        "concerns": {"type": "array", "maxItems": 4, "items": {"type": "string"}},
        "recommendations": {"type": "array", "maxItems": 3, "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"action": {"type": "string"}, "why": {"type": "string"}}, "required": ["action", "why"]}},
        "confidence": {"enum": ["high", "medium", "low"]},
    },
    "required": ["headline", "summary", "ratings", "highlights", "concerns", "recommendations", "confidence"],
}

TABLE = """CREATE TABLE IF NOT EXISTS weekly_reviews (
    week_iso TEXT PRIMARY KEY, week_start TEXT, week_end TEXT, generated_at TEXT, model TEXT, cost_usd REAL,
    skill_used INTEGER, headline TEXT, review_json TEXT, review_md TEXT, brief_json TEXT)"""


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] weekly review: {msg}", flush=True)


def claude_bin() -> str:
    found = shutil.which("claude") or shutil.which("claude", path=str(Path.home() / ".local" / "bin"))
    if not found:
        raise SystemExit("claude CLI not found (expected on PATH or in ~/.local/bin)")
    return found


def call_claude(brief: dict) -> dict:
    prompt = ("Use the weekly-training-review skill to evaluate this weekly brief and return the review through the "
              "structured output. Every number you mention must come from the brief.\n\n```json\n"
              + json.dumps(brief, ensure_ascii=False, indent=1) + "\n```")
    cmd = [claude_bin(), "-p", prompt, "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
           "--no-session-persistence", "--tools", "Skill", "--allowedTools", "Skill", "--max-budget-usd", MAX_BUDGET_USD]
    env = dict(os.environ, PATH=os.environ.get("PATH", "") + os.pathsep + str(Path.home() / ".local" / "bin"))
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=900, env=env, stdin=subprocess.DEVNULL)
    try:
        out = json.loads(res.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude returned no JSON (exit {res.returncode}): {res.stderr[-500:] or res.stdout[-500:]}")
    if out.get("is_error") or not out.get("structured_output"):
        raise RuntimeError(f"claude error: {out.get('subtype')} {str(out.get('result'))[:300]}")
    return out


def to_markdown(week: dict, rv: dict) -> str:
    icon = {"good": "✓", "ok": "·", "watch": "!", "concern": "▲"}
    lines = [f"# Week {week['iso']} ({week['start']} – {week['end']})", "", f"**{rv['headline']}**", "", rv["summary"], "",
             "| Area | Rating | Why |", "|---|---|---|"]
    lines += [f"| {r['area']} | {icon.get(r['rating'], '')} {r['rating']} | {r['reason']} |" for r in rv["ratings"]]
    for title, key in (("Highlights", "highlights"), ("Concerns", "concerns")):
        if rv[key]:
            lines += ["", f"## {title}", *[f"- {x}" for x in rv[key]]]
    if rv["recommendations"]:
        lines += ["", "## Next week", *[f"{i}. **{x['action']}** ({x['why']})" for i, x in enumerate(rv["recommendations"], 1)]]
    lines += ["", f"_Confidence: {rv['confidence']}_"]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--week", type=date.fromisoformat, help="any day of the week to review (default: last complete week)")
    ap.add_argument("--force", action="store_true", help="regenerate even if a review exists")
    ap.add_argument("--dry-run", action="store_true", help="print the brief and exit")
    ap.add_argument("--no-build", action="store_true", help="don't run `make metrics` first")
    args = ap.parse_args()

    monday = common.week_start(args.week) if args.week else weekly_brief.last_complete_week(date.today())
    iso = weekly_brief.iso_label(monday)
    db = sqlite3.connect(DB)
    db.execute(TABLE)
    if not args.force and not args.dry_run and db.execute("SELECT 1 FROM weekly_reviews WHERE week_iso = ?", (iso,)).fetchone():
        log(f"{iso} already reviewed, nothing to do")
        return 0
    if not args.no_build:
        subprocess.run(["make", "-s", "-C", str(DASH), "metrics"], check=True, capture_output=True)
    brief = weekly_brief.build_brief(monday)
    if args.dry_run:
        print(json.dumps(brief, indent=1, ensure_ascii=False))
        return 0
    if brief["data_through"] < brief["week"]["end"]:
        log(f"{iso} not fully synced yet (data through {brief['data_through']}), skipping")
        return 0

    log(f"generating review for {iso} via Claude CLI ...")
    out = call_claude(brief)
    rv = out["structured_output"]
    model = ",".join((out.get("modelUsage") or {}).keys())
    skill_used = int((out.get("num_turns") or 1) > 1)  # the skill is loaded through a tool call (an extra turn)
    md = to_markdown(brief["week"], rv)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{iso}.md").write_text(md)
    (OUT / f"{iso}.json").write_text(json.dumps({"week": brief["week"], "review": rv, "model": model,
                                                 "cost_usd": out.get("total_cost_usd"), "brief": brief}, indent=1, ensure_ascii=False))
    db.execute("INSERT OR REPLACE INTO weekly_reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (iso, brief["week"]["start"], brief["week"]["end"], datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                model, out.get("total_cost_usd"), skill_used, rv["headline"], json.dumps(rv, ensure_ascii=False), md,
                json.dumps(brief, ensure_ascii=False)))
    db.commit()
    log(f"{iso}: \"{rv['headline']}\" (model {model}, ${out.get('total_cost_usd', 0):.2f}, skill used: {bool(skill_used)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
