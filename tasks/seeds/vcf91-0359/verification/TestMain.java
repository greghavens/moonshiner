import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Protected acceptance harness. The expected terminal statuses, request IDs and
 * detail strings are generated per run by verify.py and handed to this harness
 * only, so the client must obtain them from the loopback contract fixture.
 */
public final class TestMain {
    private record Expected(String actionId, String requestId, String status, String details) {}

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 4) {
            throw new IllegalArgumentException("usage: TestMain BASE_URL TOKEN RESOURCE_ID EXPECTED_FILE");
        }

        List<Expected> expected = readExpected(Path.of(args[3]));
        check(expected.size() == 3, "harness expected three planned steps, got " + expected.size());

        Map<String, Object> cpuInputs = new LinkedHashMap<>();
        cpuInputs.put("cpuCount", 8);
        Map<String, Object> memoryInputs = new LinkedHashMap<>();
        memoryInputs.put("memoryInMB", 32768);
        Map<String, Object> leaseInputs = new LinkedHashMap<>();
        leaseInputs.put("expirationDate", "2026-12-31T23:59:59Z");

        List<AutomationChangeClient.ChangeStep> plan = List.of(
                new AutomationChangeClient.ChangeStep(
                        "resize-cpu", cpuInputs, "quarterly capacity change"),
                new AutomationChangeClient.ChangeStep(
                        "resize-memory", memoryInputs, "quarterly capacity change"),
                new AutomationChangeClient.ChangeStep(
                        "extend-lease", leaseInputs, "quarterly capacity change"));

        List<AutomationChangeClient.StepResult> results = AutomationChangeClient.runChange(
                URI.create(args[0]), args[1], args[2], plan);

        check(results != null, "runChange returned null");
        check(results.size() == expected.size(),
                "expected " + expected.size() + " reported steps, got " + results.size());

        for (int index = 0; index < expected.size(); index++) {
            assertResult(results.get(index), expected.get(index), index);
        }

        System.out.println("TEST_MAIN_OK");
    }

    private static List<Expected> readExpected(Path path) throws Exception {
        List<Expected> expected = new ArrayList<>();
        for (String line : Files.readAllLines(path, StandardCharsets.UTF_8)) {
            if (line.isBlank()) {
                continue;
            }
            String[] fields = line.split("\t", -1);
            if (fields.length != 4) {
                throw new IllegalStateException("malformed harness expectation line: " + line);
            }
            expected.add(new Expected(fields[0], fields[1], fields[2], fields[3]));
        }
        return expected;
    }

    private static void assertResult(
            AutomationChangeClient.StepResult actual, Expected want, int index) {
        String where = "step " + index + " (" + want.actionId() + ")";
        check(actual != null, "null StepResult for " + where);
        check(want.actionId().equals(actual.actionId()),
                "wrong actionId for " + where + ": " + actual.actionId());
        check(want.requestId().equals(actual.requestId()),
                "wrong requestId for " + where + ": " + actual.requestId());
        check(want.status().equals(actual.status()),
                "wrong terminal status for " + where + ": " + actual.status());
        check(want.details().equals(actual.details()),
                "wrong terminal details for " + where + ": " + actual.details());
    }
}
