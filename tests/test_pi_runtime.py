"""Pi runtime provisioning: the models.json the sandboxed agent runs on.

Offline — exercises only config generation, no pi process and no network.
"""
import json
import pathlib
import sys
import tempfile
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from runtimes.credential_proxy import DUMMY_TOKEN  # noqa: E402
from runtimes.pi import PiRuntime  # noqa: E402


def _provider_entry(runtime_config: dict) -> dict:
    config = {"runtimes": {"pi": dict(runtime_config, provider="openrouter")}}
    runtime = PiRuntime(config, {"model": "moonshotai/kimi-k3"})
    with tempfile.TemporaryDirectory() as tmp:
        runtime._prepare_runtime(pathlib.Path(tmp), "http://127.0.0.1:1")
        models = json.loads(
            (pathlib.Path(tmp) / "config" / "models.json").read_text())
    return models["providers"]["openrouter"]


class ModelsJson(unittest.TestCase):
    def test_output_budget_defaults_beyond_pi_16k(self):
        # pi fills in maxTokens=16384 when the entry omits it; reasoning-max
        # turns overrun that and get truncated with stopReason "length", so
        # the generated entry must always carry an explicit budget.
        entry = _provider_entry({})["models"][0]
        self.assertEqual(entry["maxTokens"], 131072)
        self.assertTrue(entry["reasoning"])

    def test_output_budget_configurable(self):
        entry = _provider_entry({"max_output_tokens": 65536})["models"][0]
        self.assertEqual(entry["maxTokens"], 65536)

    def test_sandbox_only_ever_sees_dummy_credentials(self):
        provider = _provider_entry({})
        self.assertEqual(provider["baseUrl"], "http://127.0.0.1:1")
        self.assertEqual(provider["apiKey"], DUMMY_TOKEN)


if __name__ == "__main__":
    unittest.main()
