import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.List;

public final class TestMain {
    private static final String CONTRACT_SHA256 =
            "bff92431d0070a9047ede074090f0bd0026b5ed663db2039d012e4a2798dc6d7";
    private static final String SOURCES_SHA256 =
            "06e1dcbc103d67421b6096e3c659efbd960ccc1900920b8f92de7b56a7e316c7";

    public static void main(String[] args) throws Exception {
        assertEquals(CONTRACT_SHA256, sha256(Path.of("docs/contract.json")),
                "docs/contract.json must remain pinned to the mock");
        assertEquals(SOURCES_SHA256, sha256(Path.of("docs/official_sources.json")),
                "official source provenance must remain unchanged");

        try (LoopbackVcfAutomationMock mock = new LoopbackVcfAutomationMock()) {
            VcfAutomationClient client =
                    new VcfAutomationClient(mock.baseUri(), "Bearer fixture-token");
            List<VcfAutomationClient.Deployment> deployments = client.listAllDeployments(2);

            assertEquals(List.of("dep-01", "dep-02", "dep-03", "dep-04", "dep-05"),
                    deployments.stream().map(VcfAutomationClient.Deployment::id).toList(),
                    "all pages must be returned in stable id order");
            assertEquals("Alpha", deployments.get(0).name(), "deployment name was not decoded");
            assertEquals("project-c", deployments.get(3).projectId(),
                    "deployment projectId was not decoded");

            assertWireContract(mock.requests(), "Bearer fixture-token", List.of(
                    "$top=2&$skip=0", "$top=2&$skip=2", "$top=2&$skip=4"));
        }

        try (LoopbackVcfAutomationMock mock = new LoopbackVcfAutomationMock()) {
            VcfAutomationClient client =
                    new VcfAutomationClient(mock.baseUri(), "ApiKey second-fixture");
            List<VcfAutomationClient.Deployment> deployments = client.listAllDeployments(3);

            assertEquals(List.of("dep-01", "dep-02", "dep-03", "dep-04", "dep-05"),
                    deployments.stream().map(VcfAutomationClient.Deployment::id).toList(),
                    "pagination must work when both count metadata fields are always absent");
            assertWireContract(mock.requests(), "ApiKey second-fixture",
                    List.of("$top=3&$skip=0", "$top=3&$skip=3"));
        }

        try (LoopbackVcfAutomationMock mock = new LoopbackVcfAutomationMock()) {
            VcfAutomationClient client =
                    new VcfAutomationClient(mock.baseUri(), "Bearer empty-fixture");
            assertEquals(List.of(), client.listAllDeployments(6),
                    "an omitted optional content field must decode as an empty collection");
            assertWireContract(mock.requests(), "Bearer empty-fixture",
                    List.of("$top=6&$skip=0"));
        }

        try (LoopbackVcfAutomationMock mock = new LoopbackVcfAutomationMock()) {
            VcfAutomationClient client =
                    new VcfAutomationClient(mock.baseUri(), "Bearer rejected-fixture");
            assertThrowsIOException(() -> client.listAllDeployments(7),
                    "a non-200 response must be reported as IOException");
            assertWireContract(mock.requests(), "Bearer rejected-fixture",
                    List.of("$top=7&$skip=0"));
        }

        System.out.println("PASS: complete pagination, stable ordering, and exact wire contract");
    }

    private static void assertWireContract(
            List<LoopbackVcfAutomationMock.RequestLog> requests,
            String authorization,
            List<String> expectedQueries) {
        assertEquals(expectedQueries.size(), requests.size(),
                "unexpected number of page requests");
        for (int i = 0; i < requests.size(); i++) {
            LoopbackVcfAutomationMock.RequestLog request = requests.get(i);
            assertEquals("GET", request.method(), "request method " + i);
            assertEquals("/iaas/api/deployments", request.rawPath(), "request path " + i);
            assertEquals(expectedQueries.get(i), request.rawQuery(), "raw query " + i);
            assertEquals(List.of(authorization), request.authorization(),
                    "Authorization header " + i);
            assertEquals(List.of("application/json"), request.accept(), "Accept header " + i);
            assertEquals(0, request.body().length, "GET body " + i);
            assertTrue(!request.rawQuery().contains("apiVersion"), "apiVersion must be omitted");
            assertTrue(!request.rawQuery().contains("$count"), "$count must be omitted");
            assertTrue(!request.rawQuery().contains("$filter"), "$filter must be omitted");
            assertTrue(!request.rawQuery().contains("=" + "&")
                            && !request.rawQuery().endsWith("="),
                    "unset query parameters must not be sent empty");
        }
    }

    private static void assertThrowsIOException(ThrowingAction action, String message)
            throws Exception {
        try {
            action.run();
        } catch (IOException expected) {
            return;
        }
        throw new AssertionError(message);
    }

    @FunctionalInterface
    private interface ThrowingAction {
        void run() throws Exception;
    }

    private static String sha256(Path path) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(path));
        StringBuilder out = new StringBuilder();
        for (byte b : digest) {
            out.append(String.format("%02x", b));
        }
        return out.toString();
    }

    private static void assertTrue(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void assertEquals(Object expected, Object actual, String message) {
        if (!expected.equals(actual)) {
            throw new AssertionError(message + ": expected=" + expected + ", actual=" + actual);
        }
    }
}
