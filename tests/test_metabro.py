import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import metabro_core as core  # noqa: E402

HOOK = ROOT / "hooks" / "metabro.py"


def run_hook(payload, home, mode=None, **extra_env):
    env = {**os.environ, "METABRO_HOME": str(home), "CLAUDE_PLUGIN_ROOT": str(ROOT)}
    for k in ("METABRO_MODE", "METABRO_ALLOW_HEADLESS", "CLAUDE_CODE_ENTRYPOINT"):
        env.pop(k, None)
    env["CLAUDE_CODE_SESSION_ATTENDED"] = "1"
    env.update(extra_env)
    env = {k: v for k, v in env.items() if v is not None}
    if mode:
        env["METABRO_MODE"] = mode
    out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                         capture_output=True, text=True, env=env, timeout=10)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"] if out.stdout.strip() else None


def transcript(path, prompts):
    with open(path, "w") as fh:
        for p in prompts:
            fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": p}}) + "\n")
            fh.write(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}}) + "\n")


class Scoring(unittest.TestCase):
    def test_clean_prompt_scores_zero(self):
        self.assertEqual(core.score_prompt("Add a CSV export button to the orders page")[0], 0)

    def test_lone_again_is_noise(self):
        self.assertEqual(core.score_prompt("run the build again")[0], 0)

    def test_correction(self):
        score, sig = core.score_prompt("No, I told you to use factories, you keep writing raw inserts")
        self.assertGreaterEqual(score, 4)
        self.assertIn("correction", {s["kind"] for s in sig})

    def test_durable_instruction(self):
        _, sig = core.score_prompt("From now on use pnpm instead of npm in this repo")
        self.assertIn("durable_instruction", {s["kind"] for s in sig})

    def test_false_positives_stay_quiet(self):
        for p in ("Never mind, revert that last change",
                  "should the revaluation apply going forward or as of the original date?",
                  "1. what are prism lines? 2. sure 3. sure 4. sure"):
            self.assertEqual(core.score_prompt(p)[0], 0, p)

    def test_always_after_ellipsis(self):
        _, sig = core.score_prompt("don't use artifacts... always use local files for reports")
        self.assertIn("durable_instruction", {s["kind"] for s in sig})

    def test_procedure(self):
        p = "1. pull master\n2. run migrations\n3. run tests\n4. build assets\n5. deploy"
        self.assertIn("procedure", {s["kind"] for s in core.score_prompt(p)[1]})

    def test_inline_procedure_and_recurring(self):
        p = ("Same thing as every Monday: 1. pull the merged PRs 2. group them by area "
             "3. write a summary per area 4. post it as a draft")
        kinds = {s["kind"] for s in core.score_prompt(p)[1]}
        self.assertTrue({"procedure", "recurring"} <= kinds, kinds)

    def test_verification_streak_fires_in_chill(self):
        # Real sequence from a call-processing session: each prompt cleans up after a "done" that wasn't.
        earlier = ["check the recording and draft the follow up",
                   "[Image #4] i dont' see the update... abe still showing up on my dial list"]
        score, sig = core.score_prompt("i just connected a call to jeffrey as well... did you process", earlier)
        self.assertGreaterEqual(score, core.MODES["chill"]["min_score"])
        self.assertIn("follow_up_streak", {s["kind"] for s in sig})

    def test_single_verification_is_stoked_only(self):
        score, _ = core.score_prompt("i dont see the draft in gws-sku")
        self.assertTrue(core.MODES["stoked"]["min_score"] <= score < core.MODES["chill"]["min_score"])

    def test_plain_why_question_is_quiet(self):
        self.assertEqual(core.score_prompt("why does recontour have multiple connections")[0], 0)

    def test_session_repeat(self):
        p = "fix the flaky checkout test in orders spec failing timezone"
        _, sig = core.score_prompt(p, earlier_prompts=["fix the flaky checkout test in orders spec, timezone failing"])
        self.assertIn("repeat_session", {s["kind"] for s in sig})

    def test_cross_session_repeat(self):
        p = "write the weekly status update from merged pull requests"
        hist = [{"s": s, "k": sorted(core.word_set(p))} for s in ("a", "b")]
        score, sig = core.score_prompt(p, history=hist, session_id="c")
        self.assertIn("repeat_cross", {s["kind"] for s in sig})
        self.assertGreaterEqual(score, 3)

    def test_transcript_parsing_skips_tool_results_and_commands(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "t.jsonl"
            transcript(t, ["hello there", "<command-name>/clear</command-name>"])
            self.assertEqual(core.read_prompts(t), ["hello there"])


class Hook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def payload(self, prompt, session="s1", tpath=""):
        return {"prompt": prompt, "session_id": session, "cwd": "/proj", "transcript_path": tpath,
                "hook_event_name": "UserPromptSubmit"}

    def test_fires_on_correction_and_names_a_harness_move(self):
        ctx = run_hook(self.payload("No, I told you to use factories, you keep writing raw inserts"), self.home)
        self.assertIn("MetaBro", ctx)
        self.assertIn("CLAUDE.md", ctx)

    def test_silent_on_clean_prompt(self):
        self.assertIsNone(run_hook(self.payload("Add a CSV export button to the orders page"), self.home))

    def test_off_mode(self):
        self.assertIsNone(run_hook(self.payload("I told you already, you forgot again"), self.home, mode="off"))

    def test_skips_slash_commands(self):
        self.assertIsNone(run_hook(self.payload("/metabro audit"), self.home))

    def test_skips_task_notifications(self):
        # A background agent's result arrives as a prompt; its "each release" + numbered steps aren't the user's habit.
        note = ("<task-notification>\n<task-id>a1</task-id>\n<result>Increment version on each release.\n"
                "1. Choose a name\n2. Validate it\n3. Fill metadata\n4. Test it\n</result>\n</task-notification>")
        wrapped = "<system-reminder>\n[SYSTEM NOTIFICATION - NOT USER INPUT]\n" + note + "\n</system-reminder>"
        for prompt in (note, wrapped):
            self.assertIsNone(run_hook(self.payload(prompt), self.home, mode="full-send"))

    def test_cooldown_in_stoked(self):
        bad = "No, I told you to use factories, you keep writing raw inserts"
        self.assertIsNotNone(run_hook(self.payload(bad), self.home))
        self.assertIsNone(run_hook(self.payload(bad + " seriously"), self.home))

    def test_chill_ignores_medium_signal(self):
        self.assertIsNone(run_hook(self.payload("From now on use pnpm here"), self.home, mode="chill"))

    def test_full_send_judges_clean_prompts(self):
        ctx = run_hook(self.payload("Add a CSV export button to the orders page"), self.home, mode="full-send")
        self.assertIn("Judge this prompt yourself", ctx)

    def test_cross_session_history_fires(self):
        p = "write the weekly status update from merged pull requests for the team"
        for s in ("a", "b"):
            run_hook(self.payload(p, session=s), self.home)
        ctx = run_hook(self.payload(p, session="c"), self.home)
        self.assertIn("other session", ctx)
        # history stores word sets, never raw text
        raw = next((self.home / "history").glob("*.jsonl")).read_text()
        self.assertNotIn("weekly status update", raw)

    def test_skips_headless_sessions(self):
        bad = "No, I told you to use factories, you keep writing raw inserts"
        self.assertIsNone(run_hook(self.payload(bad), self.home, CLAUDE_CODE_SESSION_ATTENDED="0"))
        # older Claude Code without the attended flag: fall back to the entrypoint
        self.assertIsNone(run_hook(self.payload(bad, session="s2"), self.home,
                                   CLAUDE_CODE_SESSION_ATTENDED=None, CLAUDE_CODE_ENTRYPOINT="sdk-cli"))
        self.assertIsNotNone(run_hook(self.payload(bad, session="s3"), self.home,
                                      CLAUDE_CODE_SESSION_ATTENDED=None, CLAUDE_CODE_ENTRYPOINT="cli"))
        # opt back in for testing
        self.assertIsNotNone(run_hook(self.payload(bad, session="s4"), self.home,
                                      CLAUDE_CODE_SESSION_ATTENDED="0", METABRO_ALLOW_HEADLESS="1"))
        # a skipped headless run leaves no state or history behind
        self.assertFalse((self.home / "state" / "s1.json").exists())

    def test_garbage_stdin_never_breaks(self):
        env = {**os.environ, "METABRO_HOME": str(self.home)}
        out = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True, env=env)
        self.assertEqual((out.returncode, out.stdout), (0, ""))


class Audit(unittest.TestCase):
    def inventory(self, home, cwd):
        sys.path.insert(0, str(ROOT / "skills" / "metabro" / "scripts"))
        import audit
        from unittest import mock
        with mock.patch.object(Path, "home", return_value=home):
            return audit.harness_inventory(cwd)

    def test_home_directory_is_not_counted_twice(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / ".claude" / "skills" / "one").mkdir(parents=True)
            (home / ".claude" / "skills" / "one" / "SKILL.md").write_text("x")
            (home / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"Stop": [{}]}}))
            inv = self.inventory(home, home)
            self.assertEqual((inv["project_skills"], inv["user_skills"], inv["hooks"]), (0, 1, 1))
            proj = home / "proj"
            (proj / ".claude" / "skills" / "two").mkdir(parents=True)
            (proj / ".claude" / "skills" / "two" / "SKILL.md").write_text("x")
            self.assertEqual(self.inventory(home, proj)["project_skills"], 1)


if __name__ == "__main__":
    unittest.main()
