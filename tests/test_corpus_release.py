"""What a seed-corpus release promises, and what it must not."""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import corpus


def _seed(seeds: pathlib.Path, name: str) -> pathlib.Path:
    directory = seeds / name
    directory.mkdir(parents=True)
    (directory / "task.json").write_text(json.dumps({"id": name, "lang": "python"}))
    (directory / "verify.py").write_text("print('ok')\n")
    return directory


class AReleaseDescribesItsOwnTree(unittest.TestCase):
    """The manifest is the only thing standing between a project and a corpus
    that is not the one the release cut. It has to describe what a project
    will actually have on disk -- nothing else, and nothing missing."""

    def test_bytecode_never_reaches_a_seeds_fingerprint(self):
        """Installing the package byte-compiles the corpus riding along in it.

        The manifest was then computed from the installed copy while the
        archive was built from the checkout, and every seed shipping a `.py`
        fingerprinted differently -- 866 of them. `moonshiner seeds update`
        refused the release it had just downloaded.
        """
        with tempfile.TemporaryDirectory() as tmp:
            seeds = pathlib.Path(tmp) / "tasks" / "seeds"
            directory = _seed(seeds, "py-example")
            clean = corpus.manifest(seeds, version="2026.08.19.1")

            cache = directory / "__pycache__"
            cache.mkdir()
            (cache / "verify.cpython-313.pyc").write_bytes(b"\x00compiled")
            (directory / "verify.pyc").write_bytes(b"\x00compiled")

            self.assertEqual(clean["seeds"],
                             corpus.manifest(seeds, version="2026.08.19.1")["seeds"])
            corpus.verify(seeds, clean)

    def test_a_seed_the_archive_really_carries_is_still_fingerprinted(self):
        with tempfile.TemporaryDirectory() as tmp:
            seeds = pathlib.Path(tmp) / "tasks" / "seeds"
            directory = _seed(seeds, "py-example")
            before = corpus.manifest(seeds, version="2026.08.19.1")
            (directory / "fixture.json").write_text("{}")
            after = corpus.manifest(seeds, version="2026.08.19.1")
            self.assertNotEqual(before["seeds"], after["seeds"])
            with self.assertRaises(ValueError):
                corpus.verify(seeds, before)


if __name__ == "__main__":
    unittest.main()
