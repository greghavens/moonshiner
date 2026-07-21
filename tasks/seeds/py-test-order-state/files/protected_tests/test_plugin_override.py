from __future__ import annotations

import unittest

from rendering import register, registered_names, render, unregister
from rendering.testing import preserved_registry


class PluginOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry_fixture = preserved_registry()
        self.registry_fixture.__enter__()
        self.addCleanup(self.registry_fixture.__exit__, None, None, None)

    def test_plugin_can_replace_the_available_renderers(self) -> None:
        register("persistent", lambda value: f"[{value}]")

        with preserved_registry():
            unregister("json")
            register("text", lambda value: f"text:{value}", replace=True)
            register("compact", lambda value: f"<{value}>")

            self.assertEqual(
                ("compact", "persistent", "text"), registered_names()
            )
            self.assertEqual("text:17", render("text", 17))
            self.assertEqual("<report>", render("compact", "report"))

        self.assertEqual(("json", "persistent", "text"), registered_names())
        self.assertEqual("17", render("text", 17))
        self.assertEqual("[report]", render("persistent", "report"))
        with self.assertRaisesRegex(KeyError, "unknown renderer: compact"):
            render("compact", "report")


if __name__ == "__main__":
    unittest.main()
