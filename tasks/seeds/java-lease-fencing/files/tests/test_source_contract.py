import re
import unittest
from pathlib import Path


SOURCE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "main"
    / "java"
    / "com"
    / "moonshiner"
    / "lease"
)


def without_comments(source):
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", source)


def braced_body(source, opening_brace):
    depth = 0
    for index in range(opening_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening_brace + 1:index]
    raise AssertionError("unterminated Java block")


def method_body(source, declaration):
    match = re.search(declaration + r"\s*\{", source)
    if match is None:
        raise AssertionError(f"missing Java method matching {declaration!r}")
    return braced_body(source, match.end() - 1)


def compact(source):
    return re.sub(r"\s+", "", without_comments(source))


class LeaseFencingSourceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.coordinator = (SOURCE_ROOT / "LeaseCoordinator.java").read_text(
            encoding="utf-8"
        )
        cls.acquire = method_body(
            cls.coordinator,
            r"public\s+synchronized\s+Optional<Lease>\s+tryAcquire"
            r"\(String\s+ownerId,\s*Duration\s+leaseDuration\)",
        )

    def test_acquisition_advances_persistent_token_after_exclusivity_check(self):
        source = without_comments(self.coordinator)
        field = re.search(
            r"private\s+long\s+(\w*[Ff]encing[Tt]oken\w*)\s*;", source
        )
        self.assertIsNotNone(field, "coordinator must retain the last issued token")
        counter = field.group(1)

        acquire = without_comments(self.acquire)
        failed_return = re.search(
            r"if\s*\(\s*current\s*!=\s*null\s*&&\s*now\.isBefore"
            r"\(\s*current\.expiresAt\(\)\s*\)\s*\)\s*\{\s*"
            r"return\s+Optional\.empty\(\)\s*;\s*\}",
            acquire,
        )
        self.assertIsNotNone(failed_return, "live leases must remain exclusive")

        issued = re.search(
            r"long\s+(\w+)\s*=\s*Math\.incrementExact\(\s*"
            + re.escape(counter)
            + r"\s*\)\s*;",
            acquire,
        )
        self.assertIsNotNone(issued, "successful acquisition must advance the token")
        token = issued.group(1)
        self.assertGreater(issued.start(), failed_return.end())

        acquired = re.search(
            r"Lease\s+(\w+)\s*=\s*new\s+Lease\(\s*ownerId\s*,\s*"
            + re.escape(token)
            + r"\s*,\s*now\.plus\(\s*leaseDuration\s*\)\s*\)\s*;",
            acquire,
        )
        self.assertIsNotNone(acquired, "new lease must carry the advanced token")
        self.assertGreater(acquired.start(), issued.end())

        persisted = re.search(
            re.escape(counter) + r"\s*=\s*" + re.escape(token) + r"\s*;",
            acquire,
        )
        self.assertIsNotNone(persisted, "issued token must be retained")
        self.assertGreater(persisted.start(), acquired.end())

    def test_renewal_preserves_epoch_token_and_requires_unexpired_current_lease(self):
        renew = compact(
            method_body(
                self.coordinator,
                r"public\s+synchronized\s+Lease\s+renew"
                r"\(Lease\s+lease,\s*Duration\s+leaseDuration\)",
            )
        )
        self.assertIn("!matchesCurrentEpoch(lease)", renew)
        self.assertIn("!now.isBefore(current.expiresAt())", renew)
        self.assertIn(
            "newLease(current.ownerId(),current.fencingToken(),"
            "now.plus(leaseDuration))",
            renew,
        )

    def test_store_and_worker_keep_the_fencing_boundary(self):
        store = compact((SOURCE_ROOT / "FencedResultStore.java").read_text(
            encoding="utf-8"
        ))
        worker = compact((SOURCE_ROOT / "LeaseWorker.java").read_text(
            encoding="utf-8"
        ))
        self.assertIn("if(fencingToken<highestAcceptedToken)", store)
        self.assertIn("highestAcceptedToken=fencingToken;", store)
        self.assertIn(
            "resultStore.commit(requireLease().fencingToken(),result);", worker
        )


if __name__ == "__main__":
    unittest.main()
