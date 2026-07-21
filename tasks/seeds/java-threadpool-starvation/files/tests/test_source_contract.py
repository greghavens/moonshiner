import re
import unittest
from pathlib import Path


SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "main"
    / "java"
    / "incident"
    / "PricingBatchService.java"
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


class PricingBatchServiceSourceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE_PATH.read_text(encoding="utf-8")
        cls.clean_source = without_comments(cls.source)
        cls.constructor = method_body(
            cls.clean_source,
            r"public\s+PricingBatchService"
            r"\(int\s+workers,\s*int\s+queueCapacity,\s*"
            r"PriceClient\s+priceClient\)",
        )

        assignments = re.findall(
            r"this\.(\w+)\s*=\s*newBoundedExecutor\s*\(\s*"
            r"workers\s*,\s*queueCapacity\s*,\s*\"([^\"]+)\"\s*\)",
            cls.constructor,
        )
        cls.executor_by_prefix = {
            prefix: field for field, prefix in assignments
        }

    def test_constructor_creates_two_distinct_bounded_domains(self):
        fields = re.findall(
            r"private\s+final\s+ThreadPoolExecutor\s+(\w+)\s*;",
            self.clean_source,
        )
        self.assertEqual(2, len(fields), "service must own exactly two executors")
        self.assertEqual(
            {"pricing-request-", "pricing-dependency-"},
            set(self.executor_by_prefix),
            "constructor must create the two diagnostic execution domains",
        )
        self.assertEqual(
            set(fields),
            set(self.executor_by_prefix.values()),
            "every owned executor must use the bounded factory",
        )
        self.assertEqual(
            2,
            len(set(self.executor_by_prefix.values())),
            "request and dependency work must use distinct executors",
        )

    def test_factory_preserves_fixed_bounded_caller_runs_backpressure(self):
        factory = method_body(
            self.clean_source,
            r"private\s+static\s+ThreadPoolExecutor\s+newBoundedExecutor"
            r"\(\s*int\s+workers,\s*int\s+queueCapacity,\s*"
            r"String\s+threadPrefix\)",
        )
        self.assertRegex(
            factory,
            r"new\s+ThreadPoolExecutor\s*\(\s*workers\s*,\s*workers\s*,",
            "core and maximum pool sizes must both use the configured worker bound",
        )
        self.assertRegex(
            factory,
            r"new\s+ArrayBlockingQueue\s*<>\s*\(\s*queueCapacity\s*\)",
            "queue admission must use the configured ArrayBlockingQueue bound",
        )
        self.assertRegex(
            factory,
            r"new\s+ThreadPoolExecutor\.CallerRunsPolicy\s*\(\s*\)",
            "saturation must retain caller-runs backpressure",
        )
        self.assertNotRegex(
            self.clean_source,
            r"newCachedThreadPool|newWorkStealingPool|LinkedBlockingQueue|"
            r"SynchronousQueue",
            "unbounded or substitute execution strategies are not allowed",
        )

    def test_blocking_client_work_is_submitted_only_to_dependency_domain(self):
        self.assertIn("pricing-request-", self.executor_by_prefix)
        self.assertIn("pricing-dependency-", self.executor_by_prefix)
        request_executor = self.executor_by_prefix["pricing-request-"]
        dependency_executor = self.executor_by_prefix["pricing-dependency-"]
        price_one = compact(
            method_body(
                self.clean_source,
                r"private\s+int\s+priceOne\(String\s+sku\)\s+throws\s+Exception",
            )
        )
        direct = (
            "return"
            + dependency_executor
            + ".submit(()->priceClient.loadPriceCents(sku)).get();"
        )
        assigned = re.fullmatch(
            r"(?:Future<Integer>|var)(\w+)="
            + re.escape(dependency_executor)
            + r"\.submit\(\(\)->priceClient\.loadPriceCents\(sku\)\);"
            + r"return\1\.get\(\);",
            price_one,
        )
        self.assertTrue(
            price_one == direct or assigned is not None,
            "priceOne must preserve blocking Future exception behavior in the "
            "dependency domain",
        )
        self.assertNotIn(request_executor + ".submit", price_one)
        self.assertNotRegex(price_one, r"\.get\([^)]")

    def test_request_ordering_and_failure_contract_are_preserved(self):
        self.assertIn("pricing-request-", self.executor_by_prefix)
        request_executor = self.executor_by_prefix["pricing-request-"]
        price_batch = compact(
            method_body(
                self.clean_source,
                r"public\s+List<Integer>\s+priceBatch"
                r"\(List<String>\s+skus\)\s+throws\s+"
                r"InterruptedException,\s*ExecutionException",
            )
        )
        submit = (
            "pending.add("
            + request_executor
            + ".submit(()->priceOne(sku)));"
        )
        collect = (
            "for(Future<Integer>price:pending){prices.add(price.get());}"
        )
        immutable_result = "returnList.copyOf(prices);"
        self.assertIn(submit, price_batch)
        self.assertIn(collect, price_batch)
        self.assertIn(immutable_result, price_batch)
        self.assertLess(price_batch.index(submit), price_batch.index(collect))
        self.assertLess(
            price_batch.index(collect), price_batch.index(immutable_result)
        )
        self.assertIn(
            "catch(InterruptedException|ExecutionException|RuntimeExceptionfailure)",
            price_batch,
        )
        self.assertIn("price.cancel(true);", price_batch)
        self.assertIn("throwfailure;", price_batch)

    def test_snapshots_and_close_cover_both_owned_executors(self):
        self.assertIn("pricing-request-", self.executor_by_prefix)
        self.assertIn("pricing-dependency-", self.executor_by_prefix)
        request_executor = self.executor_by_prefix["pricing-request-"]
        dependency_executor = self.executor_by_prefix["pricing-dependency-"]

        snapshots = compact(
            method_body(
                self.clean_source,
                r"public\s+List<ExecutorSnapshot>\s+executorSnapshots\(\)",
            )
        )
        request_snapshot = f'snapshot("requests",{request_executor})'
        dependency_snapshot = (
            f'snapshot("dependencies",{dependency_executor})'
        )
        self.assertIn(request_snapshot, snapshots)
        self.assertIn(dependency_snapshot, snapshots)
        self.assertEqual(2, snapshots.count("snapshot("))
        self.assertLess(
            snapshots.index(request_snapshot),
            snapshots.index(dependency_snapshot),
            "diagnostics must expose both domains in stable order",
        )

        close = compact(
            method_body(self.clean_source, r"public\s+void\s+close\(\)")
        )
        self.assertEqual(
            {f"stop({request_executor});", f"stop({dependency_executor});"},
            set(re.findall(r"stop\(\w+\);", close)),
            "close must stop every owned executor",
        )
        self.assertEqual(2, close.count("stop("))


if __name__ == "__main__":
    unittest.main()
