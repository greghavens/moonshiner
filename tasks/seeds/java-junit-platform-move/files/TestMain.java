import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Objects;

public final class TestMain {
    private static int passed;
    private static int failed;

    interface Body { void run() throws Exception; }

    private static void test(String name, Body body) {
        try {
            body.run();
            passed++;
            System.out.println("PASS " + name);
        } catch (Throwable error) {
            failed++;
            System.out.println("FAIL " + name + ": " + error);
        }
    }

    private static void eq(String what, Object expected, Object actual) {
        if (!Objects.equals(expected, actual)) {
            throw new AssertionError(what + ": expected <" + expected + "> got <" + actual + ">");
        }
    }

    public static void main(String[] args) {
        test("one platform plan discovers current and legacy tests", () -> {
            TestProbe.reset();
            List<PlatformContract.CaseResult> results = new JunitMigrationSuite().execute();
            eq("ids", List.of(
                    "CurrentPricingTests.basePrice",
                    "CurrentPricingTests.discountTier[2]",
                    "CurrentPricingTests.discountTier[4]",
                    "CurrentPricingTests.discountTier[8]",
                    "LegacyInventoryTests.availableSkuLoads",
                    "LegacyInventoryTests.missingSkuKeepsExpectedFailureSemantics"
            ), results.stream().map(PlatformContract.CaseResult::id).toList());
            eq("outcomes", List.of(
                    PlatformContract.Outcome.PASSED,
                    PlatformContract.Outcome.PASSED,
                    PlatformContract.Outcome.PASSED,
                    PlatformContract.Outcome.PASSED,
                    PlatformContract.Outcome.PASSED,
                    PlatformContract.Outcome.PASSED
            ), results.stream().map(PlatformContract.CaseResult::outcome).toList());
            eq("expected detail", "expected IllegalArgumentException",
                    results.get(5).detail());
        });

        test("parameterized cases and per-case lifecycle are retained", () -> {
            TestProbe.reset();
            new JunitMigrationSuite().execute();
            eq("lifecycle evidence", List.of(
                    "current:before", "current:base", "current:after",
                    "current:before", "current:tier:2", "current:after",
                    "current:before", "current:tier:4", "current:after",
                    "current:before", "current:tier:8", "current:after",
                    "legacy:before", "legacy:available", "legacy:after",
                    "legacy:before", "legacy:expected-body", "legacy:after"
            ), TestProbe.events());
        });

        test("a declared expected exception must actually be thrown", () -> {
            TestProbe.reset();
            List<PlatformContract.CaseResult> results =
                    new VintageEngineAdapter().execute(LegacyExpectedFailureCounterexample.class);
            eq("one counterexample", 1, results.size());
            eq("outcome", PlatformContract.Outcome.FAILED, results.get(0).outcome());
            eq("diagnostic", "expected IllegalStateException but nothing was thrown",
                    results.get(0).detail());
            eq("cleanup still ran", List.of(
                    "counterexample:before", "counterexample:body", "counterexample:after"
            ), TestProbe.events());
        });

        test("protected migration notes pin engine and lifecycle behavior", () -> {
            String notes = Files.readString(Path.of("contracts/junit_platform_migration_notes.md"));
            for (String phrase : List.of(
                    "discovers tests through engines",
                    "every `@ValueSource` invocation",
                    "registering a Vintage-compatible engine",
                    "runs `@LegacyAfter` even when the body throws",
                    "returning normally is a failure")) {
                if (!notes.contains(phrase)) throw new AssertionError("missing note: " + phrase);
            }
        });

        System.out.println("checks: " + passed + " passed, " + failed + " failed");
        System.exit(failed == 0 ? 0 : 1);
    }
}

