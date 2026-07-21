from __future__ import annotations

import unittest

from rendering import registered_names, render


class DefaultRegistryTests(unittest.TestCase):
    def test_builtin_renderers_are_registered(self) -> None:
        self.assertEqual(("json", "text"), registered_names())

    def test_builtin_renderers_keep_their_behavior(self) -> None:
        self.assertEqual('{"a":1,"b":2}', render("json", {"b": 2, "a": 1}))
        self.assertEqual("17", render("text", 17))


if __name__ == "__main__":
    unittest.main()
