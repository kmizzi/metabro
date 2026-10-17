#!/usr/bin/env python3
"""MetaBro UserPromptSubmit hook.

Scores the prompt locally. When it looks like surface-level work that the
harness should own, it adds context telling Claude to open its reply with one
MetaBro line that names a concrete harness fix. It never blocks a prompt and
always exits 0, so a MetaBro bug can't break anyone's session.
"""

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "lib"))

VOICE = (
    "Voice: laid-back surfer bro (dude, brah, gnarly, stoked, paddle, swell, wipeout, 🤙). "
    "Roast the habit, never the person. One or two sentences, max."
)


def build_context(mode, signals):
    import metabro_core as core

    if signals:
        found = "; ".join(s["detail"] for s in signals)
        kinds = list(dict.fromkeys(s["kind"] for s in signals))
        moves = " / ".join(core.MOVES[k] for k in kinds)
        return (
            f"[MetaBro plugin, mode={mode}] This prompt looks like surface-level work the harness should own. "
            f"Signals: {found}. Likely harness move: {moves}.\n"
            "Before anything else, open your reply with exactly one line formatted as "
            "'🤙 **MetaBro:** <line>'. The line must name ONE concrete, specific harness change for THIS situation "
            "(the exact CLAUDE.md wording, the skill name and what it does, the hook event and what it checks, the test to add). "
            "Generic advice like 'consider automating this' does not count. "
            "Suggest the change only: do not edit CLAUDE.md, skills, hooks or settings yourself unless the user asks "
            "(they can reply 'bro, do it'). "
            f"{VOICE} "
            "If, on reflection, the prompt is a legitimate one-off and no harness change would help, skip the line entirely. "
            "Then do the user's actual task normally, at full quality, in your usual voice. The bit must never degrade the work."
        )
    return (
        f"[MetaBro plugin, mode=full-send] Judge this prompt yourself: is the user doing by hand something the harness "
        "(CLAUDE.md rules, skills, slash commands, hooks, tests, scripts) should own? Only if clearly yes, open your reply "
        "with one line '🤙 **MetaBro:** <line>' naming the specific harness change. "
        f"{VOICE} Otherwise say nothing about it. Then do the task normally."
    )


def unattended():
    """True for headless runs (claude -p, the Agent SDK, cron jobs), where nobody reads the bit."""
    if os.environ.get("METABRO_ALLOW_HEADLESS"):
        return False
    attended = os.environ.get("CLAUDE_CODE_SESSION_ATTENDED")
    if attended is not None:
        return attended == "0"
    return os.environ.get("CLAUDE_CODE_ENTRYPOINT", "cli").startswith("sdk")


def main():
    if unattended():
        return
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    import metabro_core as core

    mode = core.get_mode()
    if mode == "off":
        return
    stripped = core.typed_text(data.get("prompt"))
    if not stripped or stripped.startswith(("/", "!", "#")) or "metabro" in stripped.lower():
        return

    session_id = data.get("session_id") or "nosession"
    cwd = data.get("cwd") or os.getcwd()
    cfg = core.MODES[mode]

    earlier = core.read_prompts(data.get("transcript_path") or "")
    if earlier and earlier[-1].strip() == stripped:
        earlier = earlier[:-1]  # transcript may already contain this prompt

    history = [] if os.environ.get("METABRO_NO_HISTORY") else core.load_history(cwd)
    score, signals = core.score_prompt(stripped, earlier, history, session_id)

    if not os.environ.get("METABRO_NO_HISTORY"):
        words = sorted(core.word_set(stripped))
        if len(words) >= core.MIN_WORDS:
            history.append({"s": session_id, "t": int(time.time()), "k": words})
            core.save_history(cwd, history)

    state = core.load_state(session_id)
    state["n"] += 1
    cooling = state["last"] is not None and state["n"] - state["last"] <= cfg["cooldown"]

    context = None
    if score >= cfg["min_score"] and not cooling:
        context = build_context(mode, signals)
        state["last"] = state["n"]
    elif cfg["always_judge"] and not cooling and len(stripped) >= 20:
        context = build_context(mode, [])
    core.save_state(session_id, state)

    if context:
        print(json.dumps({
            "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}
        }))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # never break the user's session
        if os.environ.get("METABRO_DEBUG"):
            print(f"metabro: {exc!r}", file=sys.stderr)
    sys.exit(0)
