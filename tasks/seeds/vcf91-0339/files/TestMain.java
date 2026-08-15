import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/** Protected executable acceptance harness. */
public final class TestMain {
    private static final String TOKEN = "loopback-token-91";

    public static void main(String[] args) throws Exception {
        WireVerifier.verifyProtectedContract();
        successfulRequestPollsAndCollectionsAreSorted();
        failedRequestIsObservedAtItsTerminalState();
        utf8PathAndUnsetOptionalsUseTheExactWireContract();
        malformedCatalogAcceptanceIsRejected();
        apiErrorsExposeSafeStructuredDetailsForEveryOperation();
        transportInterruptionRestoresTheInterruptFlag();
        System.out.println("PASS: VCF Automation contract, polling, request log, and stable sorting");
    }

    private static void successfulRequestPollsAndCollectionsAreSorted() throws Exception {
        try (MockVcfAutomation mock = new MockVcfAutomation()) {
            VcfAutomationClient client = new VcfAutomationClient(
                    mock.baseUrl() + "/", TOKEN, Duration.ZERO);

            Map<String, Object> inputs = new LinkedHashMap<>();
            inputs.put("cpu", 2);
            VcfAutomationClient.CatalogItemRequest request =
                    new VcfAutomationClient.CatalogItemRequest(
                            "edge-app", inputs, "project-7", "edge \"rollout\"\n雪", "v2.0");

            VcfAutomationClient.ProvisionResult result =
                    client.requestAndWait("catalog ok", request);
            eq("dep-ok", result.deploymentId(), "result deployment id");
            eq("edge-app", result.deploymentName(), "result deployment name");
            eq("CREATE_SUCCESSFUL", result.status(), "result terminal status");
            eq(3, result.pollCount(), "detail reads before success");

            List<VcfAutomationClient.Deployment> first = client.listDeployments();
            List<VcfAutomationClient.Deployment> second = client.listDeployments();
            assertSorted(first, "first changing-order response");
            assertSorted(second, "second changing-order response");
            eq(List.of("Alpha", "Alpha", "Zulu"), first.stream().map(
                    VcfAutomationClient.Deployment::name).toList(), "first sorted names");
            eq(List.of("Alpha", "Alpha", "Zulu"), second.stream().map(
                    VcfAutomationClient.Deployment::name).toList(), "second sorted names");
            eq(List.of("dep-a", "dep-b", "dep-z"), first.stream().map(
                    VcfAutomationClient.Deployment::id).toList(), "name tie sorted by id");
            eq("project-8", first.get(1).projectId(), "collection deployment project id");
            WireVerifier.require(first != second, "each list call must return a fresh list");
            eq(List.of(List.of("dep-z", "dep-b", "dep-a"),
                            List.of("dep-a", "dep-z", "dep-b")),
                    mock.collectionResponseOrders(), "mock must flip collection order");

            List<MockVcfAutomation.LoggedRequest> log = mock.requestLog();
            eq(6, log.size(), "success scenario request count");
            assertRequest(log.get(0), "POST", "/catalog/api/items/catalog%20ok/request", true);
            eq("{\"deploymentName\":\"edge-app\",\"inputs\":{\"cpu\":2},"
                            + "\"projectId\":\"project-7\","
                            + "\"reason\":\"edge \\\"rollout\\\"\\n雪\","
                            + "\"version\":\"v2.0\"}",
                    log.get(0).body(), "catalog request body bytes");
            for (int i = 1; i <= 3; i++) {
                assertRequest(log.get(i), "GET", "/deployment/api/deployments/dep-ok", false);
            }
            assertRequest(log.get(4), "GET", "/deployment/api/deployments", false);
            assertRequest(log.get(5), "GET", "/deployment/api/deployments", false);
        }
    }

    private static void failedRequestIsObservedAtItsTerminalState() throws Exception {
        try (MockVcfAutomation mock = new MockVcfAutomation()) {
            VcfAutomationClient client = new VcfAutomationClient(
                    mock.baseUrl(), TOKEN, Duration.ZERO);
            VcfAutomationClient.CatalogItemRequest request =
                    new VcfAutomationClient.CatalogItemRequest(
                            "broken-app", Map.of(), "project-7", null, null);

            try {
                client.requestAndWait("catalog-fail", request);
                throw new AssertionError("CREATE_FAILED must raise DeploymentFailedException");
            } catch (VcfAutomationClient.DeploymentFailedException expected) {
                eq(2, expected.pollCount(), "failed request poll count");
                eq("dep-fail", expected.deployment().id(), "failed deployment id");
                eq("CREATE_FAILED", expected.deployment().status(), "failed terminal status");
                eq("FAILED", expected.deployment().lastRequestStatus(), "failed request status");
                eq("datastore exhausted", expected.deployment().lastRequestDetails(),
                        "server failure details");
            }

            List<MockVcfAutomation.LoggedRequest> log = mock.requestLog();
            eq(3, log.size(), "failed scenario stops at terminal state");
            assertRequest(log.get(0), "POST", "/catalog/api/items/catalog-fail/request", true);
            eq("{\"deploymentName\":\"broken-app\",\"projectId\":\"project-7\"}",
                    log.get(0).body(), "unset optional fields omitted");
            assertRequest(log.get(1), "GET", "/deployment/api/deployments/dep-fail", false);
            assertRequest(log.get(2), "GET", "/deployment/api/deployments/dep-fail", false);
        }
    }

    private static void utf8PathAndUnsetOptionalsUseTheExactWireContract() throws Exception {
        try (MockVcfAutomation mock = new MockVcfAutomation()) {
            VcfAutomationClient client = new VcfAutomationClient(
                    mock.baseUrl(), TOKEN, Duration.ZERO);
            VcfAutomationClient.CatalogItemRequest request =
                    new VcfAutomationClient.CatalogItemRequest(
                            "minimal", null, "project-7", " \t", "");

            VcfAutomationClient.ProvisionResult result =
                    client.requestAndWait("café/雪", request);
            eq("dep-min", result.deploymentId(), "UTF-8 request deployment id");
            eq(1, result.pollCount(), "immediately terminal deployment poll count");

            List<MockVcfAutomation.LoggedRequest> log = mock.requestLog();
            eq(2, log.size(), "UTF-8 request count");
            assertRequestWithEquivalentPercentHex(log.get(0), "POST",
                    "/catalog/api/items/caf%C3%A9%2F%E9%9B%AA/request", true);
            eq("{\"deploymentName\":\"minimal\",\"projectId\":\"project-7\"}",
                    log.get(0).body(), "null and blank optional fields omitted");
            WireVerifier.require(!log.get(0).body().contains("null"),
                    "catalog body must not invent JSON null values for unset optionals");
            assertRequest(log.get(1), "GET", "/deployment/api/deployments/dep-min", false);
        }
    }

    private static void malformedCatalogAcceptanceIsRejected() throws Exception {
        try (MockVcfAutomation mock = new MockVcfAutomation()) {
            VcfAutomationClient client = new VcfAutomationClient(
                    mock.baseUrl(), TOKEN, Duration.ZERO);
            VcfAutomationClient.CatalogItemRequest request =
                    new VcfAutomationClient.CatalogItemRequest(
                            "edge-app", Map.of(), "project-7", null, null);

            for (String catalogId : List.of("catalog-empty", "catalog-many", "catalog-blank")) {
                try {
                    client.requestAndWait(catalogId, request);
                    throw new AssertionError(catalogId + " must raise VcfAutomationProtocolException");
                } catch (VcfAutomationClient.VcfAutomationProtocolException expected) {
                    assertSafeMessage(expected, "catalog protocol failure");
                }
            }

            List<MockVcfAutomation.LoggedRequest> log = mock.requestLog();
            eq(3, log.size(), "invalid acceptance responses must not be polled");
            assertRequest(log.get(0), "POST",
                    "/catalog/api/items/catalog-empty/request", true);
            assertRequest(log.get(1), "POST",
                    "/catalog/api/items/catalog-many/request", true);
            assertRequest(log.get(2), "POST",
                    "/catalog/api/items/catalog-blank/request", true);
        }
    }

    private static void apiErrorsExposeSafeStructuredDetailsForEveryOperation() throws Exception {
        try (MockVcfAutomation mock = new MockVcfAutomation()) {
            VcfAutomationClient client = new VcfAutomationClient(
                    mock.baseUrl(), TOKEN, Duration.ZERO);
            VcfAutomationClient.CatalogItemRequest request =
                    new VcfAutomationClient.CatalogItemRequest(
                            "edge-app", Map.of(), "project-7", null, null);

            expectApiException("requestCatalogItemInstances", 202,
                    () -> client.requestAndWait("catalog-api-error", request));
            expectApiException("getDeploymentById", 503,
                    () -> client.requestAndWait("catalog-detail-error", request));
            mock.failNextCollection(206,
                    "{\"error\":\"raw-response-secret loopback-token-91\"}");
            expectApiException("getDeployments", 206, client::listDeployments);

            List<MockVcfAutomation.LoggedRequest> log = mock.requestLog();
            eq(4, log.size(), "API error request count");
            assertRequest(log.get(0), "POST",
                    "/catalog/api/items/catalog-api-error/request", true);
            assertRequest(log.get(1), "POST",
                    "/catalog/api/items/catalog-detail-error/request", true);
            assertRequest(log.get(2), "GET",
                    "/deployment/api/deployments/dep-http-error", false);
            assertRequest(log.get(3), "GET", "/deployment/api/deployments", false);
        }
    }

    private static void transportInterruptionRestoresTheInterruptFlag() throws Exception {
        try (MockVcfAutomation mock = new MockVcfAutomation()) {
            VcfAutomationClient client = new VcfAutomationClient(
                    mock.baseUrl(), TOKEN, Duration.ofSeconds(30));
            VcfAutomationClient.CatalogItemRequest request =
                    new VcfAutomationClient.CatalogItemRequest(
                            "interrupt-me", Map.of(), "project-7", null, null);
            AtomicReference<Throwable> observed = new AtomicReference<>();
            AtomicBoolean interruptFlag = new AtomicBoolean();
            Thread worker = new Thread(() -> {
                try {
                    client.requestAndWait("catalog-interrupt", request);
                    observed.set(new AssertionError("interrupted transport unexpectedly completed"));
                } catch (Throwable failure) {
                    observed.set(failure);
                    interruptFlag.set(Thread.currentThread().isInterrupted());
                }
            }, "vcf-interrupt-test");

            worker.start();
            try {
                WireVerifier.require(mock.awaitInterruptDetailRequest(),
                        "client did not immediately issue the first deployment detail GET");
                worker.interrupt();
                worker.join(5_000);
            } finally {
                mock.releaseInterruptDetailResponse();
            }
            if (worker.isAlive()) {
                worker.interrupt();
                worker.join(1_000);
                throw new AssertionError("interrupted client call did not terminate");
            }
            WireVerifier.require(
                    observed.get() instanceof VcfAutomationClient.VcfAutomationTransportException,
                    "interrupted send must raise VcfAutomationTransportException, got "
                            + observed.get());
            WireVerifier.require(interruptFlag.get(),
                    "transport interruption must restore the worker interrupt flag");
            assertSafeMessage(observed.get(), "transport interruption");

            List<MockVcfAutomation.LoggedRequest> log = mock.requestLog();
            eq(2, log.size(), "interrupted request sequence");
            assertRequest(log.get(0), "POST",
                    "/catalog/api/items/catalog-interrupt/request", true);
            assertRequest(log.get(1), "GET",
                    "/deployment/api/deployments/dep-interrupt", false);
        }
    }

    private static void expectApiException(
            String operationId, int status, Runnable operation) {
        try {
            operation.run();
            throw new AssertionError(operationId + " must reject HTTP " + status);
        } catch (VcfAutomationClient.VcfAutomationApiException expected) {
            eq(operationId, expected.operationId(), "API exception operation id");
            eq(status, expected.status(), "API exception HTTP status");
            assertSafeMessage(expected, "API exception");
        }
    }

    private static void assertSafeMessage(Throwable failure, String context) {
        String message = String.valueOf(failure.getMessage());
        WireVerifier.require(!message.contains(TOKEN),
                context + " message disclosed the bearer token");
        WireVerifier.require(!message.contains("raw-response-secret"),
                context + " message disclosed the raw response body");
    }

    private static void assertRequest(
            MockVcfAutomation.LoggedRequest request,
            String method,
            String rawPath,
            boolean hasEntity) {
        eq(method, request.method(), "request method for " + rawPath);
        eq(rawPath, request.rawPath(), "request path");
        eq(null, request.rawQuery(), "focused operation carries no query");
        eq("application/json", request.firstHeader("Accept"), "Accept header");
        eq("Bearer " + TOKEN, request.firstHeader("Authorization"), "Authorization header");
        if (hasEntity) {
            eq("application/json", request.firstHeader("Content-Type"), "Content-Type header");
            WireVerifier.require(!request.body().isEmpty(), "POST must carry an entity");
        } else {
            eq(null, request.firstHeader("Content-Type"), "GET has no Content-Type");
            eq("", request.body(), "GET has no body");
        }
    }

    private static void assertRequestWithEquivalentPercentHex(
            MockVcfAutomation.LoggedRequest request,
            String method,
            String rawPath,
            boolean hasEntity) {
        WireVerifier.require(rawPath.equalsIgnoreCase(request.rawPath()),
                "request path: expected percent-encoded " + rawPath
                        + ", got " + request.rawPath());
        assertRequest(request, method, request.rawPath(), hasEntity);
    }

    private static void assertSorted(
            List<VcfAutomationClient.Deployment> deployments,
            String context) {
        for (int i = 1; i < deployments.size(); i++) {
            VcfAutomationClient.Deployment left = deployments.get(i - 1);
            VcfAutomationClient.Deployment right = deployments.get(i);
            int byName = left.name().compareTo(right.name());
            WireVerifier.require(byName < 0 || (byName == 0 && left.id().compareTo(right.id()) <= 0),
                    context + " is not sorted by name then id");
        }
    }

    private static void eq(Object expected, Object actual, String context) {
        if (!java.util.Objects.equals(expected, actual)) {
            throw new AssertionError(context + ": expected " + expected + ", got " + actual);
        }
    }
}
