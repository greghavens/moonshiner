"""Paid, real-harness contracts for OpenCode providers and Pi with ZenMux.

This is deliberately separate from offline discovery. It requires the real
OpenCode 1.18.18 and Pi CLIs plus provider credentials and model IDs:

  MOONSHINER_LIVE_OPENROUTER_MODEL
  MOONSHINER_LIVE_ZENMUX_MODEL
"""
from __future__ import annotations

import copy
import json
import os
import pathlib
import sys
import unittest
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import common  # noqa: E402
import configuration  # noqa: E402
from runtimes.opencode import OpenCodeRuntime  # noqa: E402
from runtimes.pi import PiRuntime  # noqa: E402


PROVIDERS = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "model_env": "MOONSHINER_LIVE_OPENROUTER_MODEL",
    },
    "zenmux": {
        "base_url": "https://zenmux.ai/api/v1",
        "key_env": "ZENMUX_API_KEY",
        "model_env": "MOONSHINER_LIVE_ZENMUX_MODEL",
    },
}


class RealProviderContracts(unittest.TestCase):
    def _model(self, provider: str) -> str:
        name = PROVIDERS[provider]["model_env"]
        value = os.environ.get(name, "").strip()
        self.assertTrue(value, f"set ${name} to the exact provider model ID")
        return value

    def _workspace(self, label: str) -> pathlib.Path:
        workspace = common.WORKSPACES / f"{label}-{uuid.uuid4().hex}"
        (workspace / ".harness-output").mkdir(parents=True)
        return workspace

    def _runtime_config(self, harness: str, provider: str) -> tuple[dict, dict]:
        config = copy.deepcopy(configuration.load_config())
        model = self._model(provider)
        runtime_config = config["runtimes"][harness]
        runtime_config.update({
            "provider": provider,
            "display_provider": provider,
            "base_url": PROVIDERS[provider]["base_url"],
            "key_env": PROVIDERS[provider]["key_env"],
        })
        role = {"runtime": harness, "model": model,
                "reasoning": "low", "timeout_s": 600}
        return config, role

    def test_opencode_openrouter_and_zenmux_emit_authoritative_native_traces(self):
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                config, role = self._runtime_config("opencode", provider)
                runtime = OpenCodeRuntime(config, role)
                runtime.preflight(require_auth=True)
                workspace = self._workspace(f"live-opencode-{provider}")
                prompt = ("Create answer.txt using a genuine OpenCode file-writing "
                          "tool. Its contents must be exactly this line, including "
                          "the final newline:\nOpenCode native contract\n")
                result = runtime.run_trace(
                    {"id": workspace.name}, workspace,
                    out_dir=workspace / ".harness-output",
                    system_prompt="must not reach OpenCode", prompt=prompt)
                self.assertEqual(result.return_code, 0, result.error)
                self.assertTrue(result.stream_success, result.error)
                self.assertTrue(result.model_attested, result.provenance)
                self.assertEqual((workspace / "answer.txt").read_text(),
                                 "OpenCode native contract\n")
                session = json.loads(result.raw_path.read_text())
                first_user = next(item for item in session
                                  if item["info"]["role"] == "user")
                self.assertEqual(len(first_user["parts"]), 1)
                self.assertEqual(first_user["parts"][0]["type"], "text")
                self.assertEqual(first_user["parts"][0]["text"], prompt)
                tools = [part for item in session for part in item["parts"]
                         if part["type"] == "tool"]
                self.assertTrue(tools)
                self.assertTrue(all(part["state"]["status"] in
                                    {"completed", "error"} for part in tools))
                self.assertTrue(result.provenance["tool_schemas"])
                common.remove_workspace(workspace)

    def test_existing_pi_adapter_runs_unchanged_with_zenmux(self):
        config, role = self._runtime_config("pi", "zenmux")
        runtime = PiRuntime(config, role)
        runtime.preflight(require_auth=True)
        workspace = self._workspace("live-pi-zenmux")
        prompt = ("Create answer.txt using your genuine file-writing tools. "
                  "The file must contain exactly: Pi ZenMux native contract\n")
        result = runtime.run_trace(
            {"id": workspace.name}, workspace,
            out_dir=workspace / ".harness-output",
            system_prompt="", prompt=prompt)
        self.assertEqual(result.return_code, 0, result.error)
        self.assertTrue(result.stream_success, result.error)
        self.assertTrue(result.model_attested, result.provenance)
        self.assertEqual((workspace / "answer.txt").read_text(),
                         "Pi ZenMux native contract\n")
        common.remove_workspace(workspace)


if __name__ == "__main__":
    unittest.main(verbosity=2)
