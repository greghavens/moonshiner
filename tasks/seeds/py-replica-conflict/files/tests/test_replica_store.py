import unittest

from replica_store import ReplicaStore, compare_vectors


class VersionVectorTests(unittest.TestCase):
    def test_vectors_form_a_partial_order(self):
        self.assertEqual(compare_vectors({"seed": 1}, {"seed": 1}), "equal")
        self.assertEqual(
            compare_vectors({"seed": 1}, {"seed": 1, "east": 1}),
            "before",
        )
        self.assertEqual(
            compare_vectors({"seed": 1, "east": 2}, {"seed": 1, "east": 1}),
            "after",
        )
        self.assertEqual(
            compare_vectors({"seed": 1, "east": 1}, {"seed": 1, "west": 1}),
            "concurrent",
        )
        self.assertEqual(
            compare_vectors({"east": 2, "west": 1}, {"east": 1, "west": 2}),
            "concurrent",
        )
        self.assertEqual(
            compare_vectors({"east": 1, "west": 2}, {"east": 2, "west": 1}),
            "concurrent",
        )


class ReplicaMergeTests(unittest.TestCase):
    def make_offline_edits(self):
        seed = ReplicaStore("seed")
        seed.put("note", "initial")
        east = seed.fork("east")
        west = seed.fork("west")
        east.put("note", "edited in east")
        west.put("note", "edited in west")
        return east, west

    def test_offline_edits_are_preserved_as_a_conflict(self):
        east, west = self.make_offline_edits()

        east.merge(west)

        siblings = east.siblings("note")
        self.assertEqual(
            [(entry.value, entry.vector) for entry in siblings],
            [
                ("edited in east", {"seed": 1, "east": 1}),
                ("edited in west", {"seed": 1, "west": 1}),
            ],
        )

    def test_causal_update_replaces_every_observed_conflict(self):
        east, west = self.make_offline_edits()
        east.merge(west)

        resolved = east.put("note", "reviewed resolution")

        self.assertEqual(resolved.vector, {"seed": 1, "east": 2, "west": 1})
        siblings = east.siblings("note")
        self.assertEqual(len(siblings), 1)
        self.assertEqual(siblings[0].value, "reviewed resolution")

    def test_tombstone_conflicts_with_an_unseen_live_update(self):
        seed = ReplicaStore("seed")
        seed.put("draft", "v1")
        writer = seed.fork("writer")
        eraser = seed.fork("eraser")
        writer.put("draft", "v2")
        eraser.delete("draft")

        writer.merge(eraser)

        siblings = writer.siblings("draft")
        self.assertEqual(len(siblings), 2)
        self.assertEqual({entry.deleted for entry in siblings}, {False, True})

        writer.delete("draft")
        final = writer.siblings("draft")
        self.assertEqual(len(final), 1)
        self.assertTrue(final[0].deleted)
        self.assertEqual(
            final[0].vector,
            {"seed": 1, "writer": 2, "eraser": 1},
        )

    def test_serialization_is_stable_across_merge_order(self):
        east, west = self.make_offline_edits()
        east_first = east.fork("east-copy")
        west_first = west.fork("west-copy")

        east_first.merge(west)
        west_first.merge(east)

        expected = (
            '{"clock":{"east":1,"seed":1,"west":1},"entries":{"note":['
            '{"deleted":false,"value":"edited in east","vector":{"east":1,"seed":1}},'
            '{"deleted":false,"value":"edited in west","vector":{"seed":1,"west":1}}]}}'
        )
        self.assertEqual(east_first.to_json(), expected)
        self.assertEqual(west_first.to_json(), expected)


if __name__ == "__main__":
    unittest.main()
