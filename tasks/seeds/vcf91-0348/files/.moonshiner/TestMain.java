import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicReference;

public final class TestMain {
    private static final Map<String, String> EXPECTED_ROUTES = Map.of(
            "exchangeRefreshToken", "POST /oidc/oauth2/token",
            "getProjects", "GET /iaas/api/projects",
            "getCatalogItems", "GET /catalog/api/items",
            "requestCatalogItemInstances", "POST /catalog/api/items/{id}/request");

    private static final String PROJECT_NAME = "Platform R&D's \"Sandbox\"";
    private static final String CATALOG_ITEM_NAME = "Linux + Small & Secure";
    private static final String DEPLOYMENT_NAME = "training \"blue\"\\\\path\nline";
    private static final String REFRESH_TOKEN = "refresh+token&for=loopback test";
    private static final String BASIC = "Basic " + Base64.getEncoder().encodeToString(
            "test-client:test-secret".getBytes(StandardCharsets.UTF_8));

    public static void main(String[] args) throws Exception {
        assertEquals(EXPECTED_ROUTES, MockVcfServer.contractRoutes(),
                "mock route set drifted from contract");

        testSuccessfulWorkflow();
        testMissingExactNameFailures();
        testNonSuccessFailures();
        testInterruptionPropagation();

        System.out.println("PASS: VCF Automation workflow, failures, and interruption semantics verified");
    }

    private static void testSuccessfulWorkflow() throws Exception {
        testSuccessfulWorkflow(MockVcfServer.Behavior.SUCCESS);
        testSuccessfulWorkflow(MockVcfServer.Behavior.MATCHES_FIRST);
    }

    private static void testSuccessfulWorkflow(MockVcfServer.Behavior behavior) throws Exception {
        try (MockVcfServer mock = server(behavior)) {
            mock.start();
            VcfAutomationClient.DeploymentResult result = client(mock).deploy(
                    PROJECT_NAME, CATALOG_ITEM_NAME, DEPLOYMENT_NAME);

            assertEquals(mock.projectId(), result.projectId(), "wrong project ID returned");
            assertEquals(mock.catalogItemId(), result.catalogItemId(),
                    "wrong catalog item ID returned");
            assertEquals(mock.deploymentId(), result.deploymentId(),
                    "wrong deployment ID returned");
            verifyRequestLog(
                    mock.requestLog(), mock.projectId(), mock.catalogItemId(), DEPLOYMENT_NAME);
        }
    }

    private static void testMissingExactNameFailures() throws Exception {
        try (MockVcfServer mock = server(MockVcfServer.Behavior.MISSING_PROJECT_MATCH)) {
            mock.start();
            expectIOException("missing exact project", () -> client(mock).deploy(
                    PROJECT_NAME, CATALOG_ITEM_NAME, DEPLOYMENT_NAME),
                    "project", PROJECT_NAME);
        }

        try (MockVcfServer mock = server(MockVcfServer.Behavior.MISSING_CATALOG_MATCH)) {
            mock.start();
            expectIOException("missing exact catalog item", () -> client(mock).deploy(
                    PROJECT_NAME, CATALOG_ITEM_NAME, DEPLOYMENT_NAME),
                    "catalog item", CATALOG_ITEM_NAME);
        }
    }

    private static void testNonSuccessFailures() throws Exception {
        try (MockVcfServer mock = server(MockVcfServer.Behavior.TOKEN_ERROR)) {
            mock.start();
            expectIOException("token service failure", () -> client(mock).deploy(
                    PROJECT_NAME, CATALOG_ITEM_NAME, DEPLOYMENT_NAME),
                    "503", "token_service_unavailable");
        }

        try (MockVcfServer mock = server(MockVcfServer.Behavior.CATALOG_RETRY_ERROR)) {
            mock.start();
            expectIOException("catalog failure after token refresh", () -> client(mock).deploy(
                    PROJECT_NAME, CATALOG_ITEM_NAME, DEPLOYMENT_NAME),
                    "503", "catalog_service_unavailable");
        }

        try (MockVcfServer mock = server(MockVcfServer.Behavior.DEPLOYMENT_ERROR)) {
            mock.start();
            expectIOException("deployment request failure", () -> client(mock).deploy(
                    PROJECT_NAME, CATALOG_ITEM_NAME, DEPLOYMENT_NAME),
                    "403", "request_forbidden");
        }
    }

    private static void testInterruptionPropagation() throws Exception {
        try (MockVcfServer mock = server(MockVcfServer.Behavior.BLOCK_TOKEN)) {
            mock.start();
            AtomicReference<Throwable> failure = new AtomicReference<>();
            Thread caller = new Thread(() -> {
                try {
                    client(mock).deploy(PROJECT_NAME, CATALOG_ITEM_NAME, DEPLOYMENT_NAME);
                    failure.set(new AssertionError("interrupted deploy unexpectedly returned"));
                } catch (Throwable thrown) {
                    failure.set(thrown);
                }
            }, "interrupted-deploy-test");
            caller.start();
            try {
                assertTrue(mock.awaitBlockedTokenRequest(),
                        "client did not reach the blocking token endpoint");
                caller.interrupt();
                caller.join(5_000);
                assertTrue(!caller.isAlive(), "interrupted deploy did not terminate promptly");
                assertTrue(failure.get() instanceof InterruptedException,
                        "deploy did not propagate InterruptedException; got " + failure.get());
            } finally {
                mock.releaseBlockedTokenResponse();
                caller.join(5_000);
            }
        }
    }

    private static MockVcfServer server(MockVcfServer.Behavior behavior) throws IOException {
        return new MockVcfServer(
                BASIC, REFRESH_TOKEN, PROJECT_NAME, CATALOG_ITEM_NAME, behavior);
    }

    private static VcfAutomationClient client(MockVcfServer mock) {
        return new VcfAutomationClient(mock.baseUri(), BASIC, REFRESH_TOKEN);
    }

    private static void verifyRequestLog(
            List<MockVcfServer.RequestEntry> log,
            String projectId,
            String catalogItemId,
            String deploymentName) {
        assertEquals(6, log.size(), "workflow made an unexpected number of requests");
        Set<String> allowedOperations = EXPECTED_ROUTES.keySet();
        for (MockVcfServer.RequestEntry entry : log) {
            assertTrue(allowedOperations.contains(entry.operation()),
                    "client called an operation outside docs/contract.json: "
                            + entry.method() + " " + entry.path());
        }

        int initialToken = indexOf(log, "exchangeRefreshToken", 200, 0);
        int projectLookup = indexOf(log, "getProjects", 200, initialToken + 1);
        int expiredCatalog = indexOf(log, "getCatalogItems", 401, projectLookup + 1);
        int refreshedToken = indexOf(log, "exchangeRefreshToken", 200, expiredCatalog + 1);
        int successfulCatalog = indexOf(log, "getCatalogItems", 200, refreshedToken + 1);
        int deploymentRequest = indexOf(
                log, "requestCatalogItemInstances", 200, successfulCatalog + 1);
        assertEquals(List.of(0, 1, 2, 3, 4, 5), List.of(
                initialToken, projectLookup, expiredCatalog,
                refreshedToken, successfulCatalog, deploymentRequest),
                "workflow request order changed");

        MockVcfServer.RequestEntry projectEntry = log.get(projectLookup);
        MockVcfServer.RequestEntry expiredEntry = log.get(expiredCatalog);
        MockVcfServer.RequestEntry catalogEntry = log.get(successfulCatalog);
        MockVcfServer.RequestEntry deploymentEntry = log.get(deploymentRequest);

        assertTrue(projectEntry.identifiersReturned().contains(projectId),
                "project ID was not returned by the project lookup");
        assertEquals(expiredEntry.rawQuery(), catalogEntry.rawQuery(),
                "catalog query changed while recovering from 401");
        assertTrue(!expiredEntry.authorization().equals(catalogEntry.authorization()),
                "expired access token was reused after refresh");

        Map<String, List<String>> projectQuery = MockVcfServer.parseQuery(projectEntry.rawQuery());
        assertEquals(List.of("name eq 'Platform R&D''s \"Sandbox\"'"),
                projectQuery.get("$filter"), "project lookup filter was not encoded correctly");
        Map<String, List<String>> catalogQuery = MockVcfServer.parseQuery(catalogEntry.rawQuery());
        assertEquals(List.of(CATALOG_ITEM_NAME), catalogQuery.get("search"),
                "catalog search was not encoded correctly");
        assertEquals(List.of(projectId), catalogQuery.get("projects"),
                "catalog lookup used a project identifier its lookup did not return");
        assertTrue(catalogEntry.identifiersReturned().contains(catalogItemId),
                "catalog item ID was not returned by the catalog lookup");
        assertEquals("/catalog/api/items/" + catalogItemId + "/request", deploymentEntry.path(),
                "deployment request used a catalog item identifier its lookup did not return");
        assertEquals(projectId, jsonField(deploymentEntry.body(), "projectId"),
                "deployment request used a project identifier its lookup did not return");
        assertEquals(deploymentName, jsonField(deploymentEntry.body(), "deploymentName"),
                "deployment name was not JSON encoded correctly");
    }

    private static int indexOf(
            List<MockVcfServer.RequestEntry> log, String operation, int status, int start) {
        for (int i = Math.max(0, start); i < log.size(); i++) {
            MockVcfServer.RequestEntry entry = log.get(i);
            if (entry.operation().equals(operation) && entry.status() == status) {
                return i;
            }
        }
        throw new AssertionError("request log lacks " + operation + " with status " + status
                + " after index " + start + "\nlog=" + summarize(log));
    }

    private static List<String> summarize(List<MockVcfServer.RequestEntry> log) {
        return log.stream()
                .map(entry -> entry.operation() + ":" + entry.status())
                .toList();
    }

    private static String jsonField(String body, String field) {
        java.util.regex.Matcher matcher = java.util.regex.Pattern.compile(
                "\\\"" + java.util.regex.Pattern.quote(field)
                        + "\\\"\\s*:\\s*\\\"((?:\\\\.|[^\\\"\\\\])*)\\\"")
                .matcher(body);
        return matcher.find() ? unescapeJson(matcher.group(1)) : null;
    }

    private static String unescapeJson(String value) {
        StringBuilder result = new StringBuilder();
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (c != '\\') {
                result.append(c);
                continue;
            }
            char escaped = value.charAt(++i);
            result.append(switch (escaped) {
                case '\"' -> '\"';
                case '\\' -> '\\';
                case 'n' -> '\n';
                case 'r' -> '\r';
                case 't' -> '\t';
                default -> escaped;
            });
        }
        return result.toString();
    }

    private static void expectIOException(
            String label, ThrowingAction action, String... messageFragments) throws Exception {
        try {
            action.run();
        } catch (IOException expected) {
            String message = String.valueOf(expected.getMessage());
            for (String fragment : messageFragments) {
                assertTrue(message.contains(fragment),
                        label + " did not mention " + fragment + "; message=" + message);
            }
            return;
        }
        throw new AssertionError(label + " did not throw IOException");
    }

    @FunctionalInterface
    private interface ThrowingAction {
        void run() throws Exception;
    }

    private static void assertTrue(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void assertEquals(Object expected, Object actual, String message) {
        if (!java.util.Objects.equals(expected, actual)) {
            throw new AssertionError(message + ": expected=" + expected + ", actual=" + actual);
        }
    }
}
