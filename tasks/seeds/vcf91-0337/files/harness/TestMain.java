import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Runs successful and failed credential rotations against freshly seeded loopback mocks and
 * verifies the wire shape of the traffic each run produced.
 *
 * The successful scenarios differ in the API version the deployment advertises and in the secret
 * being rolled, so a client that hardcodes either of them fails the second scenario. A third
 * scenario makes the asynchronous update fail.
 *
 * Nothing here reaches the network beyond 127.0.0.1.
 */
public final class TestMain {

    private static final Path ROOT = Path.of(".");
    private static final Path CONTRACT = ROOT.resolve("docs/contract.json");
    private static final String TOKEN_A = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.scenario-a";
    private static final String TOKEN_B = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.scenario-b";
    private static final String TOKEN_C = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.scenario-c";

    private static final List<String> failures = new ArrayList<>();

    public static void main(String[] args) throws Exception {
        System.out.println("VCF Automation credential rotation - contract conformance");
        System.out.println("=========================================================");

        verifyProtectedFiles();

        runScenario("A", TOKEN_A,
                "2021-07-15",
                List.of("2019-01-15", "2020-08-25", "2021-07-15"),
                "Sfo!Winter2025#vc01",
                "Sfo!Summer2026#vc01");

        runScenario("B", TOKEN_B,
                "2024-05-30",
                List.of("2021-07-15", "2023-02-08", "2024-05-30"),
                "Lax!Winter2025#vc01",
                "Lax!Autumn2026#vc01");

        runFailureScenario("C", TOKEN_C,
                "2025-03-31",
                List.of("2023-02-08", "2024-05-30", "2025-03-31"),
                "Sea!Winter2025#vc01",
                "Sea!Spring2027#vc01");

        System.out.println();
        if (failures.isEmpty()) {
            System.out.println("ALL CHECKS PASSED");
            return;
        }
        System.out.println(failures.size() + " CHECK(S) FAILED");
        for (String failure : failures) {
            System.out.println("  - " + failure);
        }
        System.exit(1);
    }

    /* ---------------------------------------------------------------- scenario */

    private static void runScenario(String label, String token, String latestApiVersion,
                                    List<String> supportedApiVersions, String oldPassword,
                                    String newPassword) throws Exception {
        System.out.println();
        System.out.println("--- scenario " + label + " (deployment advertises apiVersion "
                + latestApiVersion + ") ---");

        MockAutomationServer mock = new MockAutomationServer(CONTRACT, token, latestApiVersion,
                supportedApiVersions, oldPassword);
        mock.start();
        Object result = null;
        Exception thrown = null;
        try {
            result = CredentialRotator.rotate(mock.baseUrl(), token,
                    MockAutomationServer.CLOUD_ACCOUNT_ID, newPassword);
        } catch (Exception failure) {
            thrown = failure;
        } finally {
            mock.stop();
        }
        mock.writeRequestLog(ROOT.resolve("build/request-log-" + label + ".json"));

        if (thrown != null) {
            report(label, "rotate() completes without throwing", false,
                    thrown.getClass().getSimpleName() + ": " + thrown.getMessage());
            printLog(mock);
            return;
        }

        checkResult(label, result, latestApiVersion);
        report(label, "the cloud account now authenticates with the new secret",
                newPassword.equals(mock.currentPassword()),
                "the account still holds a different secret after the rotation");

        WireVerifier.Expectation expectation = new WireVerifier.Expectation();
        expectation.bearerToken = token;
        expectation.apiVersion = latestApiVersion;
        expectation.cloudAccountId = MockAutomationServer.CLOUD_ACCOUNT_ID;
        expectation.newPassword = newPassword;
        expectation.oldPassword = oldPassword;
        expectation.accountName = mock.accountName();
        expectation.accountHostName = mock.accountHostName();
        expectation.accountUsername = mock.accountUsername();
        expectation.accountDcid = mock.accountDcid();
        expectation.regions = mock.expectedRegionSpecifications();
        expectation.rotationRequestId = MockAutomationServer.ROTATION_REQUEST_ID;
        expectation.minimumDrainPolls = 1;
        expectation.expectedRotationPolls = MockAutomationServer.ROTATION_POLLS_REQUIRED;

        WireVerifier verifier = new WireVerifier(mock.requestLog(), expectation, CONTRACT);
        verifier.verify();
        for (String check : verifier.checks()) {
            System.out.println("  " + check);
        }
        for (String failure : verifier.failures()) {
            failures.add("scenario " + label + ": " + failure);
        }
        if (!verifier.failures().isEmpty()) {
            printLog(mock);
        }
    }

    /** Exercises the required FAILED terminal state and verifies that polling stops immediately. */
    private static void runFailureScenario(String label, String token, String latestApiVersion,
                                           List<String> supportedApiVersions, String oldPassword,
                                           String newPassword) throws Exception {
        System.out.println();
        System.out.println("--- scenario " + label + " (rotation tracker ends FAILED) ---");

        MockAutomationServer mock = new MockAutomationServer(CONTRACT, token, latestApiVersion,
                supportedApiVersions, oldPassword, true);
        mock.start();
        Exception thrown = null;
        try {
            CredentialRotator.rotate(mock.baseUrl(), token,
                    MockAutomationServer.CLOUD_ACCOUNT_ID, newPassword);
        } catch (Exception failure) {
            thrown = failure;
        } finally {
            mock.stop();
        }
        mock.writeRequestLog(ROOT.resolve("build/request-log-" + label + ".json"));

        report(label, "rotate() raises when the update tracker ends FAILED", thrown != null,
                "the FAILED tracker was returned as if the rotation had succeeded");
        report(label, "a failed rotation does not apply the replacement secret",
                oldPassword.equals(mock.currentPassword()),
                "the account secret changed even though its update tracker failed");

        WireVerifier.Expectation expectation = new WireVerifier.Expectation();
        expectation.bearerToken = token;
        expectation.apiVersion = latestApiVersion;
        expectation.cloudAccountId = MockAutomationServer.CLOUD_ACCOUNT_ID;
        expectation.newPassword = newPassword;
        expectation.oldPassword = oldPassword;
        expectation.accountName = mock.accountName();
        expectation.accountHostName = mock.accountHostName();
        expectation.accountUsername = mock.accountUsername();
        expectation.accountDcid = mock.accountDcid();
        expectation.regions = mock.expectedRegionSpecifications();
        expectation.rotationRequestId = MockAutomationServer.ROTATION_REQUEST_ID;
        expectation.minimumDrainPolls = 1;
        expectation.expectedRotationPolls = MockAutomationServer.ROTATION_POLLS_REQUIRED;

        WireVerifier verifier = new WireVerifier(mock.requestLog(), expectation, CONTRACT);
        verifier.verify();
        for (String check : verifier.checks()) {
            System.out.println("  " + check);
        }
        for (String failure : verifier.failures()) {
            failures.add("scenario " + label + ": " + failure);
        }
        if (!verifier.failures().isEmpty()) {
            printLog(mock);
        }
    }

    @SuppressWarnings("unchecked")
    private static void checkResult(String label, Object result, String apiVersion) {
        if (!(result instanceof Map)) {
            report(label, "rotate() returns a Map summarising the rotation", false,
                    "returned " + (result == null ? "null" : result.getClass().getName()));
            return;
        }
        Map<String, Object> summary = (Map<String, Object>) result;
        report(label, "the summary reports the terminal status FINISHED",
                "FINISHED".equals(summary.get("status")), "status=" + summary.get("status"));
        report(label, "the summary reports the apiVersion the deployment advertised",
                apiVersion.equals(summary.get("apiVersion")), "apiVersion=" + summary.get("apiVersion"));
        report(label, "the summary reports the id of the request tracker the update returned",
                MockAutomationServer.ROTATION_REQUEST_ID.equals(summary.get("requestId")),
                "requestId=" + summary.get("requestId"));
        Object drained = summary.get("drainedRequestIds");
        report(label, "the summary lists the in-flight requests once, in first-observed order",
                drained instanceof List && ((List<?>) drained).equals(List.of(
                        MockAutomationServer.INFLIGHT_REQUEST_ID,
                        MockAutomationServer.SECOND_INFLIGHT_REQUEST_ID)),
                "drainedRequestIds=" + drained);
    }

    /* ---------------------------------------------------------------- integrity */

    private static void verifyProtectedFiles() throws Exception {
        Path manifest = ROOT.resolve("harness/protected.sha256");
        List<String> mismatched = new ArrayList<>();
        Set<String> expectedFiles = new LinkedHashSet<>();
        expectedFiles.add("harness/protected.sha256");
        expectedFiles.add("src/CredentialRotator.java");
        for (String line : Files.readAllLines(manifest)) {
            String trimmed = line.trim();
            if (trimmed.isEmpty() || trimmed.startsWith("#")) {
                continue;
            }
            int split = trimmed.indexOf("  ");
            String expected = trimmed.substring(0, split);
            String relative = trimmed.substring(split + 2);
            expectedFiles.add(relative);
            Path file = ROOT.resolve(relative);
            if (!Files.exists(file)) {
                mismatched.add(relative + " (missing)");
                continue;
            }
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            String actual = HexFormat.of().formatHex(digest.digest(Files.readAllBytes(file)));
            if (!expected.equals(actual)) {
                mismatched.add(relative + " (modified)");
            }
        }

        Set<String> actualFiles = new LinkedHashSet<>();
        try (var paths = Files.walk(ROOT)) {
            paths.filter(Files::isRegularFile)
                    .map(ROOT::relativize)
                    .map(Path::toString)
                    .map(value -> value.replace('\\', '/'))
                    .filter(value -> !value.startsWith(".git/"))
                    .filter(value -> !value.startsWith(".sandbox-home/"))
                    .filter(value -> !value.startsWith(".toolchain/"))
                    .filter(value -> !value.startsWith("build/"))
                    .forEach(actualFiles::add);
        }
        Set<String> unexpected = new LinkedHashSet<>(actualFiles);
        unexpected.removeAll(expectedFiles);
        Set<String> absent = new LinkedHashSet<>(expectedFiles);
        absent.removeAll(actualFiles);
        if (!unexpected.isEmpty()) {
            mismatched.add("unexpected files " + unexpected);
        }
        if (!absent.isEmpty()) {
            mismatched.add("missing files " + absent);
        }
        boolean intact = mismatched.isEmpty();
        report("integrity", "the contract, the mock and the harness are unmodified", intact,
                "changed: " + mismatched);
        if (!intact) {
            System.out.println();
            System.out.println("The graded fixtures were altered. Restore them and rerun; only "
                    + "src/CredentialRotator.java is yours to edit.");
            for (String failure : failures) {
                System.out.println("  - " + failure);
            }
            System.exit(1);
        }
    }

    /* ---------------------------------------------------------------- output */

    private static void printLog(MockAutomationServer mock) {
        System.out.println("  request log:");
        for (MockAutomationServer.Recorded r : mock.requestLog()) {
            System.out.println("    " + r);
            if (r.body != null && !r.body.isEmpty()) {
                System.out.println("        body: " + truncate(r.body));
            }
        }
    }

    private static String truncate(String value) {
        return value.length() <= 400 ? value : value.substring(0, 400) + "...";
    }

    private static void report(String label, String description, boolean passed, String detail) {
        System.out.println("  " + (passed ? "PASS  " : "FAIL  ") + description
                + (passed || detail == null || detail.isEmpty() ? "" : "\n        " + detail));
        if (!passed) {
            failures.add(label.equals("integrity") ? description
                    : "scenario " + label + ": " + description
                      + (detail == null || detail.isEmpty() ? "" : " -- " + detail));
        }
    }

    private TestMain() {
    }
}
