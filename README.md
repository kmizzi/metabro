# MetaBro 🤙

**Stop paddling for every wave by hand, brah.**

MetaBro is a Claude Code plugin that notices when you're fixing the *surface* (the output in front of you) instead of the *harness* (the CLAUDE.md rules, skills, hooks and tests that produce it). When it catches you, Claude opens its reply with one line of surfer-bro wisdom that names the exact fix:

> 🤙 **MetaBro:** Dude, "every Monday" + a hand-written 4-step list is literally a skill asking to be born — stash this as a `weekly-pr-digest` skill with a Monday-morning `/schedule` routine and stop paddling out for the same wave every week.

> 🤙 **MetaBro:** This has already been memory'd once and the mistake still came back, brah — time to paddle past memory and add a PreToolUse hook on Bash that blocks any `npm install` in this repo, so it's impossible instead of just discouraged.

Then Claude does your actual task as normal. The bit never gets in the way of the work.

## Install

```
/plugin marketplace add kmizzi/metabro
/plugin install metabro@metabro
```

## What it catches

| You do this | MetaBro suggests |
|---|---|
| Correct Claude ("I told you…", "you keep…", "not what I asked") | A CLAUDE.md rule with the exact wording, or a hook/test if it should be impossible |
| Type a standing instruction into a prompt ("from now on…", "always…") | Move it to CLAUDE.md. A prompt is forgotten when the session ends |
| Ask for the same thing again, in this session or across sessions | A skill or slash command that owns the task |
| Clean up after a "done" that wasn't ("I don't see it", "why is it still…", "did you process?") | A verify step inside the skill that did the work, so "done" means done |
| Hand-write a numbered procedure | It's already a skill body, so save it |
| Mention a recurring task ("every Monday…") | A skill plus a schedule |

Detection is local and cheap (pattern matching and word-set similarity) and adds no extra model calls. Claude writes the actual line, so the advice fits your situation instead of being canned.

## Modes

```
/metabro chill       # only loud signals, then quiet for 12 prompts
/metabro stoked      # default: clear signals, 4-prompt cooldown
/metabro full-send   # any signal, and Claude judges every prompt for meta potential
/metabro off
/metabro status
```

`METABRO_MODE=chill` in your environment overrides the setting.

## How meta are you?

```
/metabro audit
```

This scans your recent sessions in the current project and gives you a **Meta Score** (Kook → Whitewater Paddler → Line-up Local → Big Wave Legend) with your top real fixes, built from your own repeated prompts and corrections. Post your score.

## Only when you're watching

MetaBro stays silent in headless runs (`claude -p`, the Agent SDK, cron jobs and CI), so your automations never pick up surfer lines. It checks Claude Code's `CLAUDE_CODE_SESSION_ATTENDED` flag. Set `METABRO_ALLOW_HEADLESS=1` to test it headless.

## Privacy

Everything stays on your machine. To spot repeats across sessions, MetaBro keeps a small history in `~/.claude/metabro/history/` that holds **sets of words, never your prompt text**. Set `METABRO_NO_HISTORY=1` to turn it off, or delete the folder at any time.

## Requirements

`python3` on your PATH (preinstalled on macOS and most Linux). If it's missing, MetaBro stays silent and nothing breaks.

## Development

```
python3 -m unittest tests/test_metabro.py
METABRO_ALLOW_HEADLESS=1 claude --plugin-dir . -p "No, I told you already, use pnpm not npm"
```

MIT licensed.
