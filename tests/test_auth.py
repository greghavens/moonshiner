"""Per-provider credential resolution: env/file derivation and redaction."""
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

import common  # noqa: E402
from runtimes import auth  # noqa: E402

OPENROUTER = {"provider": "openrouter"}
ZAI = {"provider": "zai"}


class KeyDerivation(unittest.TestCase):
    def test_env_name_derives_from_provider(self):
        self.assertEqual(auth.key_env_name(OPENROUTER), "OPENROUTER_API_KEY")
        self.assertEqual(auth.key_env_name(ZAI), "ZAI_API_KEY")

    def test_explicit_key_env_wins(self):
        config = {"provider": "openrouter", "key_env": "MY_SPECIAL_KEY"}
        self.assertEqual(auth.key_env_name(config), "MY_SPECIAL_KEY")

    def test_file_name_derives_per_provider(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/xdg"}):
            self.assertEqual(str(auth.key_file_path(OPENROUTER)),
                             "/xdg/moonshiner-openrouter-key")
            self.assertEqual(str(auth.key_file_path(ZAI)),
                             "/xdg/moonshiner-zai-key")

    def test_two_providers_never_share_a_file(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/xdg"}):
            self.assertNotEqual(auth.key_file_path(OPENROUTER),
                                auth.key_file_path(ZAI))

    def test_explicit_file_name_wins(self):
        config = {"provider": "openrouter", "key_file_name": "custom-key"}
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/xdg"}):
            self.assertEqual(str(auth.key_file_path(config)), "/xdg/custom-key")

    def test_persist_file_derives_under_config_home(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/cfg"}):
            self.assertEqual(str(auth.key_persist_path(OPENROUTER)),
                             "/cfg/moonshiner/moonshiner-openrouter-key")

    def test_keyless_runtime_raises(self):
        with self.assertRaises(RuntimeError):
            auth.key_env_name({"cli": "codex"})
        with self.assertRaises(RuntimeError):
            auth.key_file_path({})

    def test_configured_key_env_names_skip_oauth_runtimes(self):
        config = {"runtimes": {
            "pi": {"provider": "openrouter"},
            "other": {"provider": "zai", "key_env": "ZAI_API_KEY"},
            "codex": {"cli": "codex"},
        }}
        self.assertEqual(common.provider_key_env_names(config),
                         ("OPENROUTER_API_KEY", "ZAI_API_KEY"))


class LoadProviderKey(unittest.TestCase):
    def _isolated(self):
        """Point all three key sources into an empty tempdir; return the
        (staged, persisted) paths so a test can create either."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        (root / "run").mkdir()
        (root / "cfg").mkdir()
        patcher = mock.patch.dict(os.environ, {
            "XDG_RUNTIME_DIR": str(root / "run"),
            "XDG_CONFIG_HOME": str(root / "cfg")})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        persisted = root / "cfg" / "moonshiner" / "moonshiner-openrouter-key"
        persisted.parent.mkdir(parents=True)
        return root / "run" / "moonshiner-openrouter-key", persisted

    def test_staged_wins_over_persistent(self):
        staged, persisted = self._isolated()
        staged.write_text("from-staged")
        persisted.write_text("from-persist")
        self.assertEqual(auth.load_provider_key(OPENROUTER), "from-staged")

    def test_persistent_survives_reboot_cleared_staging(self):
        # A reboot clears the tmpfs staged file; the persistent copy must
        # keep the run alive without re-staging.
        _, persisted = self._isolated()
        persisted.write_text("from-persist")
        self.assertEqual(auth.load_provider_key(OPENROUTER), "from-persist")

    def test_empty_staged_falls_through_to_persistent(self):
        staged, persisted = self._isolated()
        staged.write_text("   \n")
        persisted.write_text("from-persist")
        self.assertEqual(auth.load_provider_key(OPENROUTER), "from-persist")

    def test_env_wins_over_staged_file(self):
        with tempfile.TemporaryDirectory() as xdg:
            (pathlib.Path(xdg) / "moonshiner-openrouter-key").write_text("file")
            env = {"XDG_RUNTIME_DIR": xdg, "OPENROUTER_API_KEY": "from-env"}
            with mock.patch.dict(os.environ, env):
                self.assertEqual(auth.load_provider_key(OPENROUTER), "from-env")

    def test_staged_file_fallback_strips_whitespace(self):
        with tempfile.TemporaryDirectory() as xdg:
            (pathlib.Path(xdg) / "moonshiner-openrouter-key").write_text("k3y\n")
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": xdg}):
                os.environ.pop("OPENROUTER_API_KEY", None)
                self.assertEqual(auth.load_provider_key(OPENROUTER), "k3y")

    def test_missing_everywhere_names_every_source(self):
        self._isolated()
        with self.assertRaises(RuntimeError) as caught:
            auth.load_provider_key(OPENROUTER)
        message = str(caught.exception)
        self.assertIn("OPENROUTER_API_KEY", message)
        self.assertIn("moonshiner-openrouter-key", message)
        self.assertIn("stage_key.sh", message)

    def test_none_config_raises(self):
        with self.assertRaises(RuntimeError):
            auth.load_provider_key(None)


class ScrubConfiguredProviderKey(unittest.TestCase):
    def test_env_value_of_configured_provider_is_redacted(self):
        fake = "orv1-test-secret-value-123456"
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": fake}):
            out = common.scrub_text(f"authorization: Bearer {fake} sent")
        self.assertNotIn(fake, out)
        self.assertIn("[REDACTED_SECRET]", out)

    def test_staged_file_value_is_redacted(self):
        fake = "staged-secret-value-abcdef-987654"
        with tempfile.TemporaryDirectory() as xdg:
            (pathlib.Path(xdg) / "moonshiner-openrouter-key").write_text(fake)
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": xdg}):
                common._staged_secret_values.cache_clear()
                try:
                    out = common.scrub_text(f"leak {fake} end")
                finally:
                    common._staged_secret_values.cache_clear()
        self.assertNotIn(fake, out)
        self.assertIn("[REDACTED_SECRET]", out)

    def test_persisted_file_value_is_redacted(self):
        fake = "persist-secret-value-fedcba-135790"
        with tempfile.TemporaryDirectory() as root:
            key_dir = pathlib.Path(root) / "moonshiner"
            key_dir.mkdir()
            (key_dir / "moonshiner-openrouter-key").write_text(fake)
            env = {"XDG_CONFIG_HOME": root, "XDG_RUNTIME_DIR": root}
            with mock.patch.dict(os.environ, env):
                common._staged_secret_values.cache_clear()
                try:
                    out = common.scrub_text(f"leak {fake} end")
                finally:
                    common._staged_secret_values.cache_clear()
        self.assertNotIn(fake, out)
        self.assertIn("[REDACTED_SECRET]", out)


if __name__ == "__main__":
    unittest.main()
