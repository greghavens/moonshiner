"""Quick self-check the depot crew runs after touching routing tables.

Run directly:  python3 checks.py
"""

import unittest

import postal
import stamps


class DepotChecks(unittest.TestCase):
    def test_zip_codes(self):
        self.assertEquals(postal.is_zip("02134"), True)
        self.assertEquals(postal.is_zip("02134-1021"), True)
        self.assertEquals(postal.is_zip("0213"), False)

    def test_routing_labels(self):
        self.assertEquals(postal.is_route("NE 140"), True)
        self.assertEquals(postal.is_route("NE140"), False)

    def test_stamp_mentions_service(self):
        line = stamps.stamp_line("P-0001", "PRIORITY")
        self.assertEquals("PRIORITY" in line, True)


if __name__ == "__main__":
    unittest.main()
