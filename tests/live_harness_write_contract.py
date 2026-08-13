"""Paid live contract test for every registered source harness.

This file is deliberately separate from the offline ``test_*.py`` suite: it
uses the installed, authenticated Codex, Claude Code, and Pi CLIs and makes a
real model request through each one.  It never substitutes an executable,
provider, credential proxy, event stream, or runtime call.

Run from the repository root with::

    python3 tests/live_harness_write_contract.py
"""
from __future__ import annotations

import copy
import json
import os
import pathlib
import sys
import uuid
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import common  # noqa: E402
import configuration  # noqa: E402
from runtimes import REGISTRY  # noqa: E402


MODELS = {
    "codex": os.environ.get("MOONSHINER_LIVE_CODEX_MODEL", "gpt-5.6-sol"),
    "claude-code": os.environ.get(
        "MOONSHINER_LIVE_CLAUDE_MODEL", "claude-opus-5"),
    "pi": os.environ.get(
        "MOONSHINER_LIVE_PI_MODEL", "moonshotai/kimi-k3"),
}


class RealHarnessWriteContract(unittest.TestCase):
    def test_every_registered_harness_authors_required_files(self):
        self.assertEqual(set(REGISTRY), set(MODELS))
        for harness, runtime_class in REGISTRY.items():
            with self.subTest(harness=harness):
                artifact_id = f"live-harness-{harness}-{uuid.uuid4().hex}"
                workspace = common.WORKSPACES / artifact_id
                output = workspace / ".harness-output"
                output.mkdir(parents=True)

                config = copy.deepcopy(configuration.load_config())
                role = {
                    "runtime": harness,
                    "model": MODELS[harness],
                    "reasoning": "low",
                    "timeout_s": 300,
                }
                runtime = runtime_class(config, role)
                runtime.preflight(require_auth=True)

                prompt = f"""Create exactly these required seed-author artifacts in the current working directory using your real file-writing tools:

1. task.json: valid JSON with exactly these two fields:
   {{"id": "{artifact_id}", "prompt": "live harness write contract"}}
2. files/answer.txt containing exactly: live harness write contract
3. reference_fix.patch containing a non-empty unified diff that creates answer.txt with that same text.

Do not inspect any parent or sibling directory. Do not use the network or MCP. Reply only after all three artifacts exist.
"""
                result = runtime.run_trace(
                    {"id": artifact_id}, workspace, out_dir=output,
                    system_prompt=(
                        "This is a live Moonshiner harness contract test. "
                        "Work only inside the current workspace."),
                    prompt=prompt,
                    tools=None,
                )

                self.assertEqual(0, result.return_code, result.error)
                self.assertTrue(result.stream_success, result.error)
                self.assertTrue(result.model_attested, result.observed_models)
                task_path = workspace / "task.json"
                self.assertTrue(task_path.is_file(), workspace)
                self.assertEqual(
                    {"id": artifact_id,
                     "prompt": "live harness write contract"},
                    json.loads(task_path.read_text()),
                )
                self.assertEqual(
                    "live harness write contract",
                    (workspace / "files" / "answer.txt").read_text().strip(),
                )
                self.assertTrue(
                    (workspace / "reference_fix.patch").read_text().strip())
                print(f"LIVE PASS {harness}: {workspace}", flush=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
