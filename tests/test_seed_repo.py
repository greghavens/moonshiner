"""Authored seeds are written into the Moonshiner clone, never beside it."""
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import seed_repo  # noqa: E402


def _repo(path: pathlib.Path, branch: str = "main") -> pathlib.Path:
    (path / "tasks" / "seeds").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"],
                   cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    (path / "README.md").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)
    return path


class ConfiguredPath(unittest.TestCase):
    def test_explicit_path_wins(self):
        config = {"seeds": {"repo_path": "/tmp/example-clone"}}
        self.assertEqual(seed_repo.configured_path(config),
                         pathlib.Path("/tmp/example-clone").resolve())

    def test_blank_falls_back_to_the_default_location(self):
        self.assertEqual(seed_repo.configured_path({"seeds": {"repo_path": ""}}),
                         seed_repo.DEFAULT_PATH.resolve())

    def test_seeds_dir_is_the_repository_corpus(self):
        config = {"seeds": {"repo_path": "/tmp/example-clone"}}
        self.assertEqual(seed_repo.seeds_dir(config).parts[-2:],
                         ("tasks", "seeds"))


class Ensure(unittest.TestCase):
    def test_accepts_a_clean_checkout_on_main(self):
        with tempfile.TemporaryDirectory() as name:
            path = _repo(pathlib.Path(name) / "clone")
            self.assertEqual(seed_repo.ensure(path), path)

    def test_untracked_seeds_are_not_an_error(self):
        """Authored seeds are untracked until the sync commits them."""
        with tempfile.TemporaryDirectory() as name:
            path = _repo(pathlib.Path(name) / "clone")
            (path / "tasks" / "seeds" / "new-seed").mkdir()
            (path / "tasks" / "seeds" / "new-seed" / "task.json").write_text("{}")
            self.assertEqual(seed_repo.ensure(path), path)

    def test_refuses_a_checkout_on_another_branch(self):
        with tempfile.TemporaryDirectory() as name:
            path = _repo(pathlib.Path(name) / "clone", branch="wip")
            with self.assertRaises(SystemExit):
                seed_repo.ensure(path)

    def test_refuses_a_non_moonshiner_checkout(self):
        with tempfile.TemporaryDirectory() as name:
            path = pathlib.Path(name) / "clone"
            path.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
            with self.assertRaises(SystemExit):
                seed_repo.ensure(path)

    def test_refuses_to_clone_over_unrelated_content(self):
        with tempfile.TemporaryDirectory() as name:
            path = pathlib.Path(name) / "occupied"
            path.mkdir()
            (path / "notes.txt").write_text("mine\n")
            with self.assertRaises(SystemExit):
                seed_repo.ensure(path, remote="https://example.invalid/x.git")


if __name__ == "__main__":
    unittest.main()
