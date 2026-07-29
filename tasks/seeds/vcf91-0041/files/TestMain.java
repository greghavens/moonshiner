import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.TimeZone;

public class TestMain {
    private static final String SOURCES_SHA256 =
            "b1c3c1f7a67302a45b3bbd3ffb54c6fec4ca46dffee202e80996ba7d3c09a36f";

    public static void main(String[] args) throws Exception {
        Locale.setDefault(Locale.ROOT);
        TimeZone.setDefault(TimeZone.getTimeZone("UTC"));

        verifyOfficialSourceManifest();
        testSequentialFailureReportAndExactWireShape();
        System.out.println("PASS: VCF 9.1 credential workflow contract");
    }

    private static void verifyOfficialSourceManifest() throws IOException {
        Path sourcePath = Path.of("docs", "official_sources.json");
        check(Files.isRegularFile(sourcePath), "official source manifest is missing");
        byte[] bytes = Files.readAllBytes(sourcePath);
        equal(SOURCES_SHA256, sha256(bytes), "official source manifest changed");
        String manifest = new String(bytes, StandardCharsets.UTF_8);
        contains(manifest, "\"repositoryCommit\": "
                + "\"3949fc33339fc5ea1b77eadb258f1cf49aa88e26\"",
                "manifest must pin the repository commit");
        contains(manifest, "\"specPath\": "
                + "\"specifications/sddc-manager/sddc-manager-openapi.json\"",
                "manifest must name the OpenAPI specification path");
        contains(manifest, "\"operationId\": \"updateOrRotatePasswords\"",
                "manifest must name updateOrRotatePasswords");
        contains(manifest, "\"operationId\": \"getCredentialsTask\"",
                "manifest must name getCredentialsTask");
    }

    private static void testSequentialFailureReportAndExactWireShape() throws Exception {
        try (MockSddcManager mock = new MockSddcManager()) {
            SddcManagerClient client = new SddcManagerClient(
                    mock.baseUri(),
                    "test-access-token",
                    Duration.ZERO,
                    4);

            var first = new SddcManagerClient.PasswordChange(
                    "VCENTER",
                    null,
                    "vc-01",
                    "administrator@vsphere.local",
                    "N3w\"Pass\\one\nline",
                    null,
                    null);
            var second = new SddcManagerClient.PasswordChange(
                    "NSXT_MANAGER",
                    "sfo-nsx01",
                    null,
                    "admin",
                    "Another-Pass-42!",
                    "API",
                    null);
            var mustNotRun = new SddcManagerClient.PasswordChange(
                    "ESXI",
                    "sfo-esx03",
                    null,
                    "root",
                    "Must-Not-Be-Sent",
                    "SSH",
                    "SYSTEM");

            SddcManagerClient.ChangeReport empty =
                    client.updatePasswordsSequentially(List.of());
            equal(List.of(), empty.steps(), "empty workflow must have no steps");
            check(!empty.successful(), "empty workflow is not a successful change");
            equal(0, mock.requestLog().size(), "empty workflow must make no requests");

            SddcManagerClient.ChangeReport report =
                    client.updatePasswordsSequentially(List.of(first, second, mustNotRun));

            check(!report.successful(), "report must show the later failure");
            equal(2, report.steps().size(),
                    "report must retain the first result and stop after the second");

            var firstResult = report.steps().get(0);
            equal("vc-01", firstResult.resourceKey(), "first resource key");
            equal("task vcenter/1", firstResult.taskId(), "first task id");
            equal("SUCCESSFUL", firstResult.status(), "first status");
            equal(null, firstResult.errorCode(), "successful step error code");
            equal(null, firstResult.errorMessage(), "successful step error message");

            var secondResult = report.steps().get(1);
            equal("sfo-nsx01", secondResult.resourceKey(), "second resource key");
            equal("task nsx/2", secondResult.taskId(), "second task id");
            equal("FAILED", secondResult.status(), "second status");
            equal("VCF_CREDENTIAL_0042", secondResult.errorCode(), "failed step error code");
            equal("Password rejected by NSX \"history\" policy.",
                    secondResult.errorMessage(), "failed step error message");

            expectUnsupported(
                    () -> report.steps().add(firstResult),
                    "report steps must be immutable");

            List<MockSddcManager.RequestLogEntry> requests = mock.requestLog();
            equal(5, requests.size(),
                    "wire log must contain two PATCH requests and three polls");

            assertPatch(requests.get(0),
                    "{\"operationType\":\"UPDATE\",\"elements\":[{"
                            + "\"resourceId\":\"vc-01\","
                            + "\"resourceType\":\"VCENTER\","
                            + "\"credentials\":[{"
                            + "\"username\":\"administrator@vsphere.local\","
                            + "\"password\":\"N3w\\\"Pass\\\\one\\nline\"}]}]}");
            assertGet(requests.get(1), "/v1/credentials/tasks/task%20vcenter%2F1");
            assertGet(requests.get(2), "/v1/credentials/tasks/task%20vcenter%2F1");
            assertPatch(requests.get(3),
                    "{\"operationType\":\"UPDATE\",\"elements\":[{"
                            + "\"resourceName\":\"sfo-nsx01\","
                            + "\"resourceType\":\"NSXT_MANAGER\","
                            + "\"credentials\":[{"
                            + "\"credentialType\":\"API\","
                            + "\"username\":\"admin\","
                            + "\"password\":\"Another-Pass-42!\"}]}]}");
            assertGet(requests.get(4), "/v1/credentials/tasks/task%20nsx%2F2");

            String allPatchBodies = requests.get(0).body() + requests.get(3).body();
            check(!allPatchBodies.contains("\"autoRotatePolicy\""),
                    "unset autoRotatePolicy must be omitted");
            check(!allPatchBodies.contains("\"accountType\""),
                    "unset accountType must be omitted");
            check(!requests.get(0).body().contains("\"resourceName\""),
                    "unset resourceName must be omitted");
            check(!requests.get(0).body().contains("\"credentialType\""),
                    "unset credentialType must be omitted");
            check(!requests.get(3).body().contains("\"resourceId\""),
                    "unset resourceId must be omitted");
            check(!allPatchBodies.contains(":null"),
                    "unset optional fields must never be sent as null");
            check(!allPatchBodies.contains(":\"\""),
                    "unset optional fields must never be sent empty");
            check(!allPatchBodies.contains("Must-Not-Be-Sent"),
                    "third change must not be sent after failure");
        }
    }

    private static void assertPatch(
            MockSddcManager.RequestLogEntry request,
            String expectedBody) {
        equal("PATCH", request.method(), "PATCH method");
        equal("/v1/credentials", request.rawPath(), "PATCH path");
        equal(null, request.rawQuery(), "PATCH must not have a query");
        equal("Bearer test-access-token", request.header("Authorization"),
                "PATCH authorization");
        equal("application/json", request.header("Accept"), "PATCH accept");
        equal("application/json", request.header("Content-Type"), "PATCH content type");
        equal(expectedBody, request.body(), "PATCH body bytes");
    }

    private static void assertGet(
            MockSddcManager.RequestLogEntry request,
            String expectedPath) {
        equal("GET", request.method(), "GET method");
        equal(expectedPath, request.rawPath(), "GET path");
        equal(null, request.rawQuery(), "GET must not have a query");
        equal("Bearer test-access-token", request.header("Authorization"),
                "GET authorization");
        equal("application/json", request.header("Accept"), "GET accept");
        equal(null, request.header("Content-Type"),
                "bodyless GET must not send a content type");
        equal("", request.body(), "GET must not send a body");
    }

    private static String sha256(byte[] bytes) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(bytes));
        } catch (NoSuchAlgorithmException impossible) {
            throw new AssertionError(impossible);
        }
    }

    private static void contains(String value, String fragment, String message) {
        check(value.contains(fragment), message);
    }

    private static void expectUnsupported(ThrowingRunnable runnable, String message)
            throws Exception {
        try {
            runnable.run();
            throw new AssertionError(message);
        } catch (UnsupportedOperationException expected) {
            // Expected.
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void equal(Object expected, Object actual, String message) {
        if (!java.util.Objects.equals(expected, actual)) {
            throw new AssertionError(
                    message + ": expected <" + expected + "> but was <" + actual + ">");
        }
    }

    @FunctionalInterface
    private interface ThrowingRunnable {
        void run() throws Exception;
    }
}
