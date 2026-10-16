#!/usr/bin/env python3
"""MetaBro audit: how meta has this project been? Prints one JSON report.

Reads this project's Claude Code transcripts locally (~/.claude/projects/<dir>)
and the harness files on disk. Nothing leaves the machine.

usage: audit.py [--cwd PATH] [--sessions N] [--all-projects]
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
import metabro_core as core  # noqa: E402

HARNESS_WORDS = re.compile(
    r"\b(claude\.md|agents\.md|skill|skills|hook|hooks|settings\.json|slash command|subagent|"
    r"plugin|lint rule|pre-commit|automate|automation)\b", re.I)


def project_dir(cwd):
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd)


def clip(text, n=140):
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def harness_inventory(cwd):
    cwd, home = Path(cwd), Path.home()
    def count(p, pattern):
        return len(list(p.glob(pattern))) if p.exists() else 0
    # Run from the home directory, the project's .claude/ IS the user's, so count it once.
    at_home = cwd.resolve() == home.resolve()
    project = [] if at_home else [cwd / ".claude" / "settings.json", cwd / ".claude" / "settings.local.json"]
    hooks = 0
    for s in project + [home / ".claude" / "settings.json"]:
        try:
            hooks += sum(len(v) for v in json.loads(s.read_text()).get("hooks", {}).values())
        except (OSError, ValueError, AttributeError):
            pass
    return {
        "project_claude_md": (cwd / "CLAUDE.md").exists()
                             or (not at_home and (cwd / ".claude" / "CLAUDE.md").exists()),
        "user_claude_md": (home / ".claude" / "CLAUDE.md").exists(),
        "project_skills": 0 if at_home else count(cwd / ".claude" / "skills", "*/SKILL.md"),
        "user_skills": count(home / ".claude" / "skills", "*/SKILL.md"),
        "commands": (0 if at_home else count(cwd / ".claude" / "commands", "*.md"))
                    + count(home / ".claude" / "commands", "*.md"),
        "hooks": hooks,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", default=str(Path.cwd()))
    ap.add_argument("--sessions", type=int, default=30)
    ap.add_argument("--all-projects", action="store_true")
    args = ap.parse_args()

    if args.all_projects:
        files = list((Path.home() / ".claude" / "projects").glob("*/*.jsonl"))
    else:
        files = list(project_dir(args.cwd).glob("*.jsonl"))
    files = sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)[: args.sessions]

    prompts = []  # (session, text)
    for f in files:
        for text in core.read_prompts(f, limit=10_000):
            t = text.strip()
            if t and not t.startswith(("/", "!", "#")):
                prompts.append((f.stem, t))
    prompts = prompts[-1500:]

    corrections, durable, procedures, harness_moves = [], [], [], 0
    for _, text in prompts:
        _, sig = core.score_prompt(text)
        kinds = {s["kind"] for s in sig}
        if "correction" in kinds:
            corrections.append(text)
        if "durable_instruction" in kinds:
            durable.append(text)
        if "procedure" in kinds:
            procedures.append(text)
        if HARNESS_WORDS.search(text):
            harness_moves += 1

    # Greedy clustering of similar prompts; keep clusters spanning 2+ sessions.
    toks = [core.tokens(t) for _, t in prompts]
    used, clusters = set(), []
    for i, ti in enumerate(toks):
        if i in used or len(ti) < core.MIN_TOKENS:
            continue
        members = [i] + [j for j in range(i + 1, len(toks))
                         if j not in used and core.jaccard(ti, toks[j]) >= core.SIMILAR]
        sessions = {prompts[m][0] for m in members}
        if len(sessions) >= 2:
            used.update(members)
            clusters.append({"sessions": len(sessions), "times": len(members),
                             "example": clip(prompts[members[-1]][1])})
    clusters.sort(key=lambda c: (c["sessions"], c["times"]), reverse=True)

    n = max(len(prompts), 1)
    inv = harness_inventory(args.cwd)
    inv_points = (4 * inv["project_claude_md"] + 2 * inv["user_claude_md"]
                  + min(4, inv["project_skills"] + inv["user_skills"])
                  + min(3, inv["hooks"]) + min(2, inv["commands"]))
    score = (50
             + min(25, round(harness_moves / n * 250))
             + inv_points
             - min(30, round(len(corrections) / n * 100))
             - min(20, 4 * len(clusters))
             - min(10, 2 * len(procedures))
             - min(10, 2 * len(durable)))
    score = max(0, min(100, score))
    rank = ("Kook" if score < 40 else "Whitewater Paddler" if score < 60
            else "Line-up Local" if score < 80 else "Big Wave Legend")

    print(json.dumps({
        "scope": "all projects" if args.all_projects else args.cwd,
        "sessions_scanned": len(files),
        "prompts_scanned": len(prompts),
        "meta_score": score,
        "rank": rank,
        "harness_moves": harness_moves,
        "corrections": {"count": len(corrections), "examples": [clip(t) for t in corrections[-5:]]},
        "durable_instructions_in_prompts": {"count": len(durable), "examples": [clip(t) for t in durable[-5:]]},
        "hand_written_procedures": {"count": len(procedures), "examples": [clip(t) for t in procedures[-3:]]},
        "repeated_across_sessions": clusters[:5],
        "harness_inventory": inv,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
