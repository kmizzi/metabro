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


def run_hook(payload, home, mode=None):
    env = {**os.environ, "METABRO_HOME": str(home), "CLAUDE_PLUGIN_ROOT": str(ROOT)}
    env.pop("METABRO_MODE", None)
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

    def test_session_repeat(self):
        p = "fix the flaky checkout test in orders spec failing timezone"
        _, sig = core.score_prompt(p, earlier_prompts=["fix the flaky checkout test in orders spec, timezone failing"])
        self.assertIn("repeat_session", {s["kind"] for s in sig})

    def test_cross_session_repeat(self):
        p = "write the weekly status update from merged pull requests"
        hist = [{"s": s, "k": sorted(core.tokens(p))} for s in ("a", "b")]
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
        # history stores token sets, never raw text
        raw = next((self.home / "history").glob("*.jsonl")).read_text()
        self.assertNotIn("weekly status update", raw)

    def test_garbage_stdin_never_breaks(self):
        env = {**os.environ, "METABRO_HOME": str(self.home)}
        out = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True, env=env)
        self.assertEqual((out.returncode, out.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
