"""MetaBro core: spots prompts that are working the surface instead of the harness.

Everything here is local and cheap: regexes, word-set similarity, and a small
history of *word sets* (never raw prompt text). No network, no model calls.
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path

HOME = Path(os.environ.get("METABRO_HOME", Path.home() / ".claude" / "metabro"))

MODES = {
    # min_score: how loud a signal must be; cooldown: prompts to stay quiet after speaking
    "chill": {"min_score": 4, "cooldown": 12, "always_judge": False},
    "stoked": {"min_score": 2, "cooldown": 4, "always_judge": False},
    "full-send": {"min_score": 1, "cooldown": 0, "always_judge": True},
}
DEFAULT_MODE = "stoked"
MODE_ALIASES = {"fullsend": "full-send", "full_send": "full-send", "full send": "full-send"}

# (pattern, weight, kind). Kinds map to the harness move MetaBro suggests.
PATTERNS = [
    (r"\bi (already )?(said|told you|asked( you)?)\b", 2, "correction"),
    (r"\byou (forgot|missed|ignored)\b", 2, "correction"),
    (r"\b(you keep|why do you keep|how many times|every (single )?time)\b", 2, "correction"),
    (r"\bnot what i (asked|wanted|said|meant)\b", 2, "correction"),
    (r"\bas i (said|mentioned|explained)\b", 2, "correction"),
    (r"\bfor the (second|third|fourth|fifth|nth|\d+(st|nd|rd|th)) time\b", 3, "correction"),
    (r"\bsame (mistake|thing|bug|error|problem) again\b", 3, "correction"),
    (r"\bstop (doing|using|adding|writing|making)\b", 2, "correction"),
    (r"\b(remember (to|that)|from now on|in the future,)\b", 2, "durable_instruction"),
    (r"(^|[.!?]\s+|\.\.\.\s*)(always|never) (?!mind\b)[a-z]", 2, "durable_instruction"),
    (r"\b(every|each) (day|morning|night|week|month|sprint|release|deploy|monday|tuesday|wednesday|thursday|friday)\b"
     r"|\b(same as|like) (last time|yesterday|last week)\b", 3, "recurring"),
    # "It said done, but it isn't": the follow-up that a verify step inside the skill should have caught.
    (r"\bi (don'?t'?|do not|didn'?t'?|can'?t'?) see (the|it|any|my|an?|that|this)\b", 2, "verification"),
    (r"\bwhy\b[^.?!\n]{0,40}\bstill\b", 2, "verification"),
    (r"\bstill (showing|shows|there|listed|appears?|showing up|has|have)\b", 2, "verification"),
    (r"\bdid y\w{0,3}u (actually |even )?(process|send|update|run|save|commit|deploy|check)\b", 2, "verification"),
    (r"\bwhy (didn'?t|hasn'?t|wasn'?t|isn'?t)\b[^.?!\n]{0,40}\b(updated?|work(ed|ing)?|show(n|ing)?|sent|saved|run|deleted|removed)\b",
     2, "verification"),
    (r"^\s*no[,.!]", 1, "correction"),
    (r"\bagain\b", 1, "correction"),
    (r"\bstill (broken|failing|wrong|not working|doesn'?t work)\b", 1, "correction"),
]
COMPILED = [(re.compile(p, re.I), w, k) for p, w, k in PATTERNS]

STOPWORDS = set(
    "the and for that this with you are was have has can will from into then than them they "
    "what when where which while about just like make sure please could would should there "
    "here its it's our your out all any but not use using get got let lets now also some".split()
)

SIMILAR = 0.6
MIN_WORDS = 5
HISTORY_KEEP = 400


def word_set(text):
    return {w for w in re.findall(r"[a-z0-9_./-]{3,}", text.lower()) if w not in STOPWORDS}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def procedure_steps(text):
    """Number of explicit steps in a hand-written procedure (numbered list or then-chains)."""
    # a step needs a few words; "1. sure 2. sure" is answering questions, not writing a procedure
    # Inline lists count too: "1. pull PRs 2. group them 3. ...". Take the longest 1..k run.
    found = {int(n) for n in re.findall(r"(?:^|(?<=\s))(\d{1,2})[.)][ \t]+\S+[ \t]+(?!\d{1,2}[.)])\S", text, re.M)}
    numbered = 0
    while numbered + 1 in found:
        numbered += 1
    chained = len(re.findall(r"\b(then|after that|next|finally)\b", text, re.I))
    return max(numbered, chained + 1 if chained >= 2 else 0)


# ---------- transcripts ----------

INJECTED_PREFIXES = (
    "<",  # <command-name>, <task-notification>, <local-command-stdout>, <system-reminder>, ...
    "Caveat:",
    "This session is being continued",
    "Base directory for this skill",
    "[Request interrupted",
)
INJECTED_BLOCKS = re.compile(r"<(system-reminder|pasted_content)[^>]*>.*?</\1[^>]*>", re.S)
MAX_PROMPT_CHARS = 4000  # longer than this is pasted material, not a typed prompt


def prompt_text(entry):
    """User-typed text from one transcript line, or None (tool results, meta, injected text)."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isCompactSummary") \
            or entry.get("isSidechain") or entry.get("isVisibleInTranscriptOnly"):
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list):
        parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        content = "\n".join(parts) if parts else None
    if not isinstance(content, str):
        return None
    return typed_text(content)


def typed_text(text):
    """The part of a prompt the user typed, or None when Claude Code injected it (task notifications, ...)."""
    text = INJECTED_BLOCKS.sub("", text or "").strip()
    if not text or text.startswith(INJECTED_PREFIXES) or len(text) > MAX_PROMPT_CHARS:
        return None
    return text


def read_prompts(transcript_path, limit=40):
    out = []
    try:
        with open(transcript_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    text = prompt_text(json.loads(line))
                except ValueError:
                    continue
                if text:
                    out.append(text)
    except OSError:
        return []
    return out[-limit:]


# ---------- history (word sets only) ----------

def history_path(cwd):
    key = hashlib.sha1((cwd or "?").encode()).hexdigest()[:12]
    return HOME / "history" / f"{key}.jsonl"


def load_history(cwd):
    rows = []
    try:
        with open(history_path(cwd), encoding="utf-8") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return rows


def save_history(cwd, rows):
    path = history_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows[-HISTORY_KEEP:]:
            fh.write(json.dumps(r) + "\n")
    os.replace(tmp, path)


# ---------- scoring ----------

def pattern_signals(prompt):
    signals, seen = [], set()
    for rx, weight, kind in COMPILED:
        m = rx.search(prompt)
        if m and (kind, m.group(0).lower()) not in seen:
            seen.add((kind, m.group(0).lower()))
            signals.append({"kind": kind, "weight": weight, "detail": f'said "{m.group(0).strip()}"'})
    return signals


FOLLOW_UP_KINDS = {"correction", "verification"}


def score_prompt(prompt, earlier_prompts=(), history=(), session_id=None):
    """Return (score, signals). Each signal: {kind, weight, detail}."""
    signals = pattern_signals(prompt)

    # A run of fix-ups is the real tell: one "I don't see it" is a hiccup, three in a row is a broken workflow.
    if any(s["kind"] in FOLLOW_UP_KINDS and s["weight"] >= 2 for s in signals):
        recent = [p for p in list(earlier_prompts)[-4:]
                  if any(s["kind"] in FOLLOW_UP_KINDS and s["weight"] >= 2 for s in pattern_signals(p))]
        if recent:
            signals.append({"kind": "follow_up_streak", "weight": 2,
                            "detail": f"{len(recent) + 1} fix-up prompts in the last {min(len(earlier_prompts), 4) + 1}"})

    words = word_set(prompt)
    if len(words) >= MIN_WORDS:
        repeats = sum(1 for p in earlier_prompts if jaccard(words, word_set(p)) >= SIMILAR)
        if repeats:
            signals.append({"kind": "repeat_session", "weight": 3,
                            "detail": f"near-duplicate of {repeats} earlier prompt(s) this session"})
        other_sessions = {
            h.get("s") for h in history
            if h.get("s") != session_id and jaccard(words, set(h.get("k", []))) >= SIMILAR
        }
        if other_sessions:
            n = len(other_sessions)
            signals.append({"kind": "repeat_cross", "weight": 3 if n >= 2 else 2,
                            "detail": f"you've asked something like this in {n} other session(s) here"})

    steps = procedure_steps(prompt)
    if steps >= 4:
        signals.append({"kind": "procedure", "weight": 2,
                        "detail": f"hand-wrote a {steps}-step procedure"})

    # One weak "again" alone is noise; drop the weight-1 signals unless something else fired.
    if signals and all(s["weight"] == 1 for s in signals) and len(signals) == 1:
        signals = []
    return sum(s["weight"] for s in signals), signals


MOVES = {
    "correction": "a CLAUDE.md rule (quote the exact wording to add), or a hook/test/lint if the mistake should be impossible rather than discouraged",
    "durable_instruction": "a CLAUDE.md rule, because an instruction typed into a prompt evaporates when the session ends (quote the exact line to add)",
    "repeat_session": "fixing the cause rather than re-asking: a rule, a test that pins the behaviour, or a hook",
    "repeat_cross": "a skill or slash command that owns this recurring task (name it, e.g. /deploy-check)",
    "recurring": "a skill for the task plus a schedule (a cron job or a /schedule routine) so it runs without being asked",
    "verification": "a verify step inside the skill or workflow that did the work (name the exact check: re-read the record, reload the list, confirm the draft is gone), so 'done' means done before it reports back",
    "follow_up_streak": "the root cause of the repeated fix-ups: usually a missing verify step in the skill that produced the result, or a rule about what 'done' means",
    "procedure": "a skill: this step list is the skill body already; save it once and invoke it by name",
}


# ---------- config + per-session state ----------

def get_mode():
    raw = os.environ.get("METABRO_MODE")
    if not raw:
        try:
            raw = json.loads((HOME / "config.json").read_text()).get("mode")
        except (OSError, ValueError, AttributeError):
            raw = None
    raw = (raw or DEFAULT_MODE).strip().lower()
    raw = MODE_ALIASES.get(raw, raw)
    return raw if raw in MODES or raw == "off" else DEFAULT_MODE


def set_mode(mode):
    mode = MODE_ALIASES.get(mode.strip().lower(), mode.strip().lower())
    if mode not in MODES and mode != "off":
        raise ValueError(f"unknown mode {mode!r}; pick chill, stoked, full-send or off")
    HOME.mkdir(parents=True, exist_ok=True)
    (HOME / "config.json").write_text(json.dumps({"mode": mode}) + "\n")
    return mode


def state_path(session_id):
    safe = re.sub(r"[^A-Za-z0-9_-]", "", session_id or "nosession")[:64]
    return HOME / "state" / f"{safe}.json"


def load_state(session_id):
    try:
        return json.loads(state_path(session_id).read_text())
    except (OSError, ValueError):
        return {"n": 0, "last": None}


def save_state(session_id, state):
    path = state_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))
    # Prune state files older than a week, opportunistically.
    if state["n"] % 25 == 0:
        cutoff = time.time() - 7 * 86400
        for f in path.parent.glob("*.json"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass
