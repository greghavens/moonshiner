import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Drives one credential rotation against a loopback stand-in for SDDC Manager, then asserts what
 * the client returned and what it actually put on the wire.
 *
 * <p>Run it from the repository root with {@code java TestMain.java}.
 */
public final class TestMain {

    private static final long WATCHDOG_SECONDS = 75;

    public static void main(String[] args) throws Exception {
        Thread watchdog = new Thread(() -> {
            try {
                Thread.sleep(WATCHDOG_SECONDS * 1000L);
            } catch (InterruptedException interrupted) {
                return;
            }
            System.out.println();
            System.out.println("FAIL  the run did not finish within " + WATCHDOG_SECONDS + "s");
            System.out.flush();
            Runtime.getRuntime().halt(1);
        }, "watchdog");
        watchdog.setDaemon(true);
        watchdog.start();

        Path logPath = Files.createTempFile("sddc-request-log-", ".jsonl");
        MockSddcManager mock = new MockSddcManager(Path.of("docs", "contract.json"), logPath);
        List<String> failures = new ArrayList<>();
        RotationResult result = null;
        Throwable thrown = null;

        try {
            mock.start();
            System.out.println("stand-in SDDC Manager listening on " + mock.baseUrl());
            try (SddcCredentialClient client = new SddcCredentialClient(
                    mock.baseUrl(), MockSddcManager.USERNAME, MockSddcManager.PASSWORD)) {
                result = client.rotateSshPasswords(MockSddcManager.HOSTS);
            } catch (Throwable failure) {
                thrown = failure;
            }
        } finally {
            mock.stop();
        }

        System.out.println();
        if (thrown != null) {
            failures.add("rotateSshPasswords threw " + thrown);
            System.out.println("--- the client threw ---");
            thrown.printStackTrace(System.out);
            System.out.println();
        }
        if (mock.gateTimedOut()) {
            System.out.println("--- note ---");
            System.out.println("The stand-in expires the access token only while it is holding "
                    + MockSddcManager.GATE_PARTIES + " credential lookups at once. It gave up waiting "
                    + "with these lookups in flight: " + mock.gateArrivals());
            System.out.println();
        }

        System.out.println("--- returned result ---");
        if (result == null) {
            failures.add("rotateSshPasswords returned no result");
            System.out.println("(none)");
        } else {
            System.out.println(result);
            expect(failures, "taskId",
                    MockSddcManager.CREDENTIALS_TASK_ID.equals(result.taskId()),
                    "expected " + MockSddcManager.CREDENTIALS_TASK_ID + " but was " + result.taskId());
            expect(failures, "status", "SUCCESSFUL".equals(result.status()),
                    "expected SUCCESSFUL but was " + result.status());
            List<String> expectedCredentialIds = new ArrayList<>(
                    MockSddcManager.USER_CREDENTIAL_IDS.values());
            expectedCredentialIds.sort(String::compareTo);
            expect(failures, "credentialIds", expectedCredentialIds.equals(result.credentialIds()),
                    "expected " + expectedCredentialIds + " but was " + result.credentialIds());
            expect(failures, "accessTokenRefreshCount", result.accessTokenRefreshCount() == 1,
                    "expected 1 but was " + result.accessTokenRefreshCount());
        }

        List<Map<String, Object>> log = readLog(logPath);
        System.out.println();
        System.out.println("--- request log (" + log.size() + " exchanges) ---");
        for (Map<String, Object> entry : log) {
            System.out.printf("  %2s %-24s %-6s %s%s -> %s%n",
                    string(entry.get("seq")),
                    entry.get("operationId") == null ? "(not in contract)" : entry.get("operationId"),
                    string(entry.get("method")),
                    string(entry.get("path")),
                    entry.get("rawQuery") == null ? "" : "?" + entry.get("rawQuery"),
                    string(entry.get("status")));
        }

        System.out.println();
        System.out.println("--- wire contract ---");
        for (WireVerifier.Check check : WireVerifier.verify(log)) {
            System.out.printf("  %-4s %s%n", check.passed() ? "PASS" : "FAIL", check.name());
            if (!check.passed()) {
                System.out.println("       " + check.detail());
                failures.add(check.name() + ": " + check.detail());
            }
        }

        if (!failures.isEmpty()) {
            System.out.println();
            System.out.println("--- request bodies ---");
            for (Map<String, Object> entry : log) {
                String body = string(entry.get("body"));
                if (!body.isEmpty()) {
                    System.out.println("  " + string(entry.get("seq")) + " "
                            + string(entry.get("operationId")) + ": " + body);
                }
            }
        }

        Files.deleteIfExists(logPath);

        System.out.println();
        if (failures.isEmpty()) {
            System.out.println("OK  " + log.size() + " exchanges, every contract check passed");
            System.exit(0);
        }
        System.out.println("FAILED  " + failures.size() + " check(s):");
        for (String failure : failures) {
            System.out.println("  - " + failure);
        }
        System.exit(1);
    }

    private static void expect(List<String> failures, String name, boolean condition, String detail) {
        System.out.printf("  %-4s %s%n", condition ? "PASS" : "FAIL", name);
        if (!condition) {
            System.out.println("       " + detail);
            failures.add(name + ": " + detail);
        }
    }

    private static List<Map<String, Object>> readLog(Path logPath) throws Exception {
        List<Map<String, Object>> entries = new ArrayList<>();
        if (!Files.exists(logPath)) {
            return entries;
        }
        for (String line : Files.readAllLines(logPath, StandardCharsets.UTF_8)) {
            if (!line.isBlank()) {
                entries.add(Json.asObject(Json.parse(line)));
            }
        }
        entries.sort((left, right) -> Long.compare(
                ((Number) left.get("seq")).longValue(), ((Number) right.get("seq")).longValue()));
        return entries;
    }

    private static String string(Object value) {
        return value == null ? "" : String.valueOf(value);
    }
}
