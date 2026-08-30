import java.io.IOException;
import java.net.URI;
import java.net.http.HttpHeaders;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class TestMain {
    private static final Path CONTRACT = Path.of("docs", "contract.json");

    public static void main(String[] args) throws Exception {
        provenanceVerifierFailsClosed();
        validationMakesNoTraffic();
        interruptionIsPreserved();
        runSuccess(ContractMockServer.Scenario.successWithOptionals());
        runSuccess(ContractMockServer.Scenario.successWithoutOptionals());
        runSuccess(ContractMockServer.Scenario.scenario(
                ContractMockServer.Scenario.Fault.EMPTY_OPTIONAL_VALUES));
        runSuccess(ContractMockServer.Scenario.scenario(
                ContractMockServer.Scenario.Fault.PATCH_RESPONSE_FINISHED));
        runFailedDrain();
        runFailedUpdate();
        runRejectedSuccessBodies();
        System.out.println("PASS: VCF Automation credential rotation contract");
    }

    private static void runSuccess(ContractMockServer.Scenario scenario) throws Exception {
        List<ContractMockServer.LoggedExchange> exchanges;
        try (ContractMockServer mock = new ContractMockServer(CONTRACT, scenario)) {
            mock.start();
            VcfAutomationCredentialRotator client = new VcfAutomationCredentialRotator(
                    mock.origin(), scenario.bearerToken, scenario.apiVersion, Duration.ZERO);
            VcfAutomationCredentialRotator.RotationResult result =
                    client.rotate(scenario.accountName, scenario.newPassword);

            equal(scenario.targetAccountId, result.cloudAccountId(), "returned cloud account id");
            equal(List.of(scenario.firstDrainId, scenario.secondDrainId), result.drainedRequestIds(),
                    "drained ids preserve lookup order");
            equal(scenario.updateRequestId, result.updateRequestId(), "returned update tracker id");
            equal("FINISHED", result.status(), "terminal status");
            expectThrows(UnsupportedOperationException.class,
                    () -> result.drainedRequestIds().add("mutable"), "drained ids must be immutable");
            exchanges = mock.requestLog();
        }
        assertTranscript(exchanges, scenario, false);
    }

    private static void runFailedUpdate() throws Exception {
        ContractMockServer.Scenario scenario = ContractMockServer.Scenario.failedUpdate();
        List<ContractMockServer.LoggedExchange> exchanges;
        try (ContractMockServer mock = new ContractMockServer(CONTRACT, scenario)) {
            mock.start();
            VcfAutomationCredentialRotator client = new VcfAutomationCredentialRotator(
                    mock.origin(), scenario.bearerToken, scenario.apiVersion, Duration.ZERO);
            try {
                client.rotate(scenario.accountName, scenario.newPassword);
                throw new AssertionError("FAILED update tracker must throw");
            } catch (VcfAutomationCredentialRotator.RotationFailedException expected) {
                equal(scenario.updateRequestId, expected.requestId(), "failed request id");
                check(!expected.getMessage().contains(scenario.newPassword), "failure leaked password");
                check(!expected.getMessage().contains(scenario.bearerToken), "failure leaked token");
            }
            exchanges = mock.requestLog();
        }
        assertTranscript(exchanges, scenario, true);
    }

    private static void runFailedDrain() throws Exception {
        ContractMockServer.Scenario scenario = ContractMockServer.Scenario.scenario(
                ContractMockServer.Scenario.Fault.DRAIN_FAILED);
        List<ContractMockServer.LoggedExchange> exchanges;
        try (ContractMockServer mock = new ContractMockServer(CONTRACT, scenario)) {
            mock.start();
            VcfAutomationCredentialRotator client = new VcfAutomationCredentialRotator(
                    mock.origin(), scenario.bearerToken, scenario.apiVersion, Duration.ZERO);
            try {
                client.rotate(scenario.accountName, scenario.newPassword);
                throw new AssertionError("FAILED drain tracker must throw");
            } catch (VcfAutomationCredentialRotator.RotationFailedException expected) {
                equal(scenario.firstDrainId, expected.requestId(), "failed drain request id");
                check(!expected.getMessage().contains(scenario.newPassword), "failure leaked password");
                check(!expected.getMessage().contains(scenario.bearerToken), "failure leaked token");
            }
            exchanges = mock.requestLog();
        }
        equal(List.of("listRequestTrackers", "getRequestTracker", "getRequestTracker"),
                operationIds(exchanges), "failed drain must abort before account lookup");
        verifyIdentifierProvenance(exchanges);
    }

    private static void runRejectedSuccessBodies() throws Exception {
        List<String> listOnly = List.of("listRequestTrackers");
        List<String> firstPoll = List.of("listRequestTrackers", "getRequestTracker");
        List<String> throughLookup = List.of("listRequestTrackers", "getRequestTracker",
                "getRequestTracker", "getRequestTracker", "listVSphereCloudAccounts");
        List<String> throughPatch = List.of("listRequestTrackers", "getRequestTracker",
                "getRequestTracker", "getRequestTracker", "listVSphereCloudAccounts",
                "updateVSphereCloudAccountAsync");

        expectProtocolFailure(ContractMockServer.Scenario.Fault.DUPLICATE_TRACKER_ID, listOnly);
        expectProtocolFailure(ContractMockServer.Scenario.Fault.UNKNOWN_TRACKER_STATUS, listOnly);
        expectProtocolFailure(ContractMockServer.Scenario.Fault.MISSING_TRACKER_VALUE, listOnly);
        expectProtocolFailure(ContractMockServer.Scenario.Fault.MALFORMED_TRACKER_JSON, listOnly);
        expectProtocolFailure(ContractMockServer.Scenario.Fault.REJECTED_LIST_STATUS, listOnly);
        expectProtocolFailure(ContractMockServer.Scenario.Fault.TRACKER_ID_MISMATCH, firstPoll);
        expectProtocolFailure(ContractMockServer.Scenario.Fault.DUPLICATE_ACCOUNT_ID, throughLookup);
        expectProtocolFailure(ContractMockServer.Scenario.Fault.DUPLICATE_ACCOUNT_NAME, throughLookup);
        expectProtocolFailure(ContractMockServer.Scenario.Fault.MISSING_ACCOUNT_VALUE, throughLookup);
        expectProtocolFailure(ContractMockServer.Scenario.Fault.NULL_OPTIONAL_ACCOUNT_VALUE, throughLookup);
        expectProtocolFailure(ContractMockServer.Scenario.Fault.MALFORMED_ACCOUNT_JSON, throughLookup);
        expectProtocolFailure(ContractMockServer.Scenario.Fault.REJECTED_PATCH_STATUS, throughPatch);
        expectProtocolFailure(ContractMockServer.Scenario.Fault.MALFORMED_PATCH_JSON, throughPatch);
        expectProtocolFailure(ContractMockServer.Scenario.Fault.UNKNOWN_PATCH_STATUS, throughPatch);
    }

    private static void expectProtocolFailure(ContractMockServer.Scenario.Fault fault,
                                              List<String> expectedOperations) throws Exception {
        ContractMockServer.Scenario scenario = ContractMockServer.Scenario.scenario(fault);
        List<ContractMockServer.LoggedExchange> exchanges;
        try (ContractMockServer mock = new ContractMockServer(CONTRACT, scenario)) {
            mock.start();
            VcfAutomationCredentialRotator client = new VcfAutomationCredentialRotator(
                    mock.origin(), scenario.bearerToken, scenario.apiVersion, Duration.ZERO);
            try {
                client.rotate(scenario.accountName, scenario.newPassword);
                throw new AssertionError(fault + " success response must be rejected");
            } catch (IOException expected) {
                String message = String.valueOf(expected.getMessage());
                check(!message.contains(scenario.bearerToken), fault + " leaked bearer token");
                check(!message.contains(scenario.newPassword), fault + " leaked new password");
                check(!message.contains("response-marker"), fault + " leaked response body");
                check(!message.contains("malformed-tracker-json"), fault + " leaked response body");
                check(!message.contains("malformed-account-json"), fault + " leaked response body");
                check(!message.contains("malformed-patch-json"), fault + " leaked response body");
            }
            exchanges = mock.requestLog();
        }
        equal(expectedOperations, operationIds(exchanges), fault + " traffic cutoff");
    }

    private static void validationMakesNoTraffic() throws Exception {
        ContractMockServer.Scenario scenario = ContractMockServer.Scenario.successWithOptionals();
        try (ContractMockServer mock = new ContractMockServer(CONTRACT, scenario)) {
            mock.start();
            VcfAutomationCredentialRotator client = new VcfAutomationCredentialRotator(
                    mock.origin(), scenario.bearerToken, scenario.apiVersion, Duration.ZERO);
            expectThrows(IllegalArgumentException.class, () -> client.rotate(" ", "secret"),
                    "blank account name");
            expectThrows(IllegalArgumentException.class, () -> client.rotate(null, "secret"),
                    "null account name");
            expectThrows(IllegalArgumentException.class, () -> client.rotate(scenario.accountName, ""),
                    "blank password");
            expectThrows(IllegalArgumentException.class, () -> client.rotate(scenario.accountName, null),
                    "null password");
            equal(0, mock.requestLog().size(), "invalid rotate must make no request");
        }
        expectThrows(IllegalArgumentException.class,
                () -> new VcfAutomationCredentialRotator(null, "token", "2023-01-01", Duration.ZERO),
                "null origin");
        expectThrows(IllegalArgumentException.class,
                () -> new VcfAutomationCredentialRotator(URI.create("ftp://example.test"), "token",
                        "2023-01-01", Duration.ZERO), "non-http origin");
        expectThrows(IllegalArgumentException.class,
                () -> new VcfAutomationCredentialRotator(URI.create("https://example.test/path"), "token",
                        "2023-01-01", Duration.ZERO), "origin path");
        expectThrows(IllegalArgumentException.class,
                () -> new VcfAutomationCredentialRotator(URI.create("https://example.test"), "bad\rvalue",
                        "2023-01-01", Duration.ZERO), "unsafe bearer token");
        expectThrows(IllegalArgumentException.class,
                () -> new VcfAutomationCredentialRotator(URI.create("https://example.test"), " ",
                        "2023-01-01", Duration.ZERO), "blank bearer token");
        expectThrows(IllegalArgumentException.class,
                () -> new VcfAutomationCredentialRotator(URI.create("https://example.test"), "token",
                        "2023-1-01", Duration.ZERO), "malformed api version");
        expectThrows(IllegalArgumentException.class,
                () -> new VcfAutomationCredentialRotator(URI.create("https://example.test"), "token",
                        null, Duration.ZERO), "null api version");
        expectThrows(IllegalArgumentException.class,
                () -> new VcfAutomationCredentialRotator(URI.create("https://example.test"), "token",
                        "2023-01-01", Duration.ofMillis(-1)), "negative poll interval");
        expectThrows(IllegalArgumentException.class,
                () -> new VcfAutomationCredentialRotator(URI.create("https://example.test"), "token",
                        "2023-01-01", null), "null poll interval");
    }

    private static void interruptionIsPreserved() throws Exception {
        ContractMockServer.Scenario scenario = ContractMockServer.Scenario.successWithOptionals();
        try (ContractMockServer mock = new ContractMockServer(CONTRACT, scenario)) {
            mock.start();
            VcfAutomationCredentialRotator client = new VcfAutomationCredentialRotator(
                    mock.origin(), scenario.bearerToken, scenario.apiVersion, Duration.ZERO);
            Thread.currentThread().interrupt();
            try {
                client.rotate(scenario.accountName, scenario.newPassword);
                throw new AssertionError("an interrupted HTTP call must propagate interruption");
            } catch (InterruptedException expected) {
                check(Thread.currentThread().isInterrupted(), "propagated interruption flag was cleared");
            } finally {
                Thread.interrupted();
            }
        }
    }

    private static void assertTranscript(List<ContractMockServer.LoggedExchange> exchanges,
                                         ContractMockServer.Scenario scenario, boolean failed) {
        List<String> operations = operationIds(exchanges);
        List<String> expectedOperations = scenario.fault
                == ContractMockServer.Scenario.Fault.PATCH_RESPONSE_FINISHED
                ? List.of("listRequestTrackers", "getRequestTracker", "getRequestTracker",
                        "getRequestTracker", "listVSphereCloudAccounts",
                        "updateVSphereCloudAccountAsync", "getRequestTracker")
                : List.of("listRequestTrackers", "getRequestTracker", "getRequestTracker",
                        "getRequestTracker", "listVSphereCloudAccounts",
                        "updateVSphereCloudAccountAsync", "getRequestTracker", "getRequestTracker");
        equal(expectedOperations, operations, "ordered operation transcript");

        int patchCount = 0;
        for (ContractMockServer.LoggedExchange exchange : exchanges) {
            check(exchange.operationId() != null, "mock received an unnamed operation");
            check(exchange.responseStatus() < 400, "mock rejected request: " + exchange.rawTarget());
            check(exchange.rawTarget().endsWith("?apiVersion=" + scenario.apiVersion),
                    "apiVersion missing or not sole query");
            check(!exchange.rawTarget().contains("$top") && !exchange.rawTarget().contains("$skip"),
                    "unset paging parameter sent");
            headerExactly(exchange, "Accept", "application/json");
            headerExactly(exchange, "Authorization", "Bearer " + scenario.bearerToken);
            check(!new String(exchange.requestBody(), StandardCharsets.UTF_8).contains(scenario.bearerToken),
                    "bearer token appeared in a body");
            check(!exchange.rawTarget().contains(scenario.newPassword), "new password appeared in URL");
            if (exchange.method().equals("GET")) {
                equal(0, exchange.requestBody().length, "GET body length");
                check(headerValues(exchange, "Content-Type").isEmpty(), "GET sent Content-Type");
            } else {
                patchCount++;
                equal("PATCH", exchange.method(), "only non-GET method");
                headerExactly(exchange, "Content-Type", "application/json");
                check(headerValues(exchange, "Transfer-Encoding").isEmpty(), "PATCH was chunked");
                List<String> lengths = headerValues(exchange, "Content-Length");
                equal(List.of(Integer.toString(exchange.requestBody().length)), lengths,
                        "PATCH fixed content length");
                Object body = TestJson.parse(new String(exchange.requestBody(), StandardCharsets.UTF_8));
                equal(scenario.expectedPatch(), body, "PATCH read-to-write projection");
                @SuppressWarnings("unchecked")
                Map<String, Object> patchBody = (Map<String, Object>) body;
                equal(scenario.newPassword, patchBody.get("password"), "new password in PATCH body");
            }
        }
        equal(1, patchCount, "one credential mutation");
        verifyIdentifierProvenance(exchanges);

        ContractMockServer.LoggedExchange terminal = exchanges.get(exchanges.size() - 1);
        @SuppressWarnings("unchecked")
        Map<String, Object> terminalBody = (Map<String, Object>) TestJson.parse(terminal.responseBody());
        equal(failed ? "FAILED" : "FINISHED", terminalBody.get("status"), "terminal mock state");
    }

    /**
     * Fails if any identifier-bearing operation uses an id that was not learned from its own
     * earlier lookup or mutation response in this exact transcript.
     */
    @SuppressWarnings("unchecked")
    static void verifyIdentifierProvenance(List<ContractMockServer.LoggedExchange> exchanges) {
        Set<String> accountIds = new LinkedHashSet<>();
        Set<String> trackerIds = new LinkedHashSet<>();
        for (ContractMockServer.LoggedExchange exchange : exchanges) {
            String operation = exchange.operationId();
            if ("listRequestTrackers".equals(operation)) {
                Map<String, Object> body = object(exchange.responseBody());
                addIds(body.get("content"), trackerIds, "request tracker lookup");
            } else if ("listVSphereCloudAccounts".equals(operation)) {
                Map<String, Object> body = object(exchange.responseBody());
                addIds(body.get("content"), accountIds, "cloud account lookup");
            } else if ("updateVSphereCloudAccountAsync".equals(operation)) {
                String used = identifier(exchange.rawTarget(), "/iaas/api/cloud-accounts-vsphere/");
                check(accountIds.contains(used), "cloud account id was not returned by this lookup: " + used);
                Map<String, Object> body = object(exchange.responseBody());
                Object returned = body.get("id");
                check(returned instanceof String && !((String) returned).isBlank(),
                        "PATCH did not return a tracker id");
                trackerIds.add((String) returned);
            } else if ("getRequestTracker".equals(operation)) {
                String used = identifier(exchange.rawTarget(), "/iaas/api/request-tracker/");
                check(trackerIds.contains(used), "tracker id was not returned by this lookup: " + used);
            }
        }
    }

    private static void provenanceVerifierFailsClosed() {
        String lookup = TestJson.stringify(Map.of("content", List.of(Map.of("id", "returned-id"))));
        String patch = TestJson.stringify(Map.of("id", "tracker-from-patch"));
        List<ContractMockServer.LoggedExchange> forged = List.of(
                new ContractMockServer.LoggedExchange("listVSphereCloudAccounts", "GET",
                        "/iaas/api/cloud-accounts-vsphere?apiVersion=2023-01-01", Map.of(), new byte[0], 200, lookup),
                new ContractMockServer.LoggedExchange("updateVSphereCloudAccountAsync", "PATCH",
                        "/iaas/api/cloud-accounts-vsphere/not-returned?apiVersion=2023-01-01",
                        Map.of(), new byte[0], 202, patch));
        expectThrows(AssertionError.class, () -> verifyIdentifierProvenance(forged),
                "identifier provenance oracle must fail closed");
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(String json) {
        Object value = TestJson.parse(json);
        check(value instanceof Map<?, ?>, "response must be object");
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static void addIds(Object content, Set<String> destination, String source) {
        check(content instanceof List<?>, source + " content missing");
        for (Object value : (List<Object>) content) {
            check(value instanceof Map<?, ?>, source + " item is not object");
            Object id = ((Map<String, Object>) value).get("id");
            check(id instanceof String && !((String) id).isBlank(), source + " id missing");
            check(destination.add((String) id), source + " returned duplicate id");
        }
    }

    private static String identifier(String rawTarget, String prefix) {
        int start = rawTarget.indexOf(prefix);
        check(start >= 0, "identifier path prefix missing");
        start += prefix.length();
        int query = rawTarget.indexOf('?', start);
        check(query > start, "identifier path segment missing");
        return percentDecode(rawTarget.substring(start, query));
    }

    private static String percentDecode(String raw) {
        java.io.ByteArrayOutputStream bytes = new java.io.ByteArrayOutputStream();
        for (int i = 0; i < raw.length(); i++) {
            char c = raw.charAt(i);
            if (c == '%') {
                check(i + 2 < raw.length(), "truncated percent encoding");
                int high = Character.digit(raw.charAt(++i), 16);
                int low = Character.digit(raw.charAt(++i), 16);
                check(high >= 0 && low >= 0, "invalid percent encoding");
                bytes.write((high << 4) | low);
            } else {
                bytes.writeBytes(String.valueOf(c).getBytes(StandardCharsets.UTF_8));
            }
        }
        return bytes.toString(StandardCharsets.UTF_8);
    }

    private static void headerExactly(ContractMockServer.LoggedExchange exchange,
                                      String name, String value) {
        equal(List.of(value), headerValues(exchange, name), "header " + name);
    }

    private static List<String> operationIds(List<ContractMockServer.LoggedExchange> exchanges) {
        return exchanges.stream().map(ContractMockServer.LoggedExchange::operationId).toList();
    }

    private static List<String> headerValues(ContractMockServer.LoggedExchange exchange, String name) {
        ArrayList<String> result = new ArrayList<>();
        exchange.requestHeaders().forEach((key, values) -> {
            if (key.equalsIgnoreCase(name)) result.addAll(values);
        });
        return result;
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private static void equal(Object expected, Object actual, String message) {
        if (!java.util.Objects.equals(expected, actual)) {
            throw new AssertionError(message + ": expected=" + expected + " actual=" + actual);
        }
    }

    private static void expectThrows(Class<? extends Throwable> type, ThrowingAction action, String message) {
        try {
            action.run();
        } catch (Throwable throwable) {
            if (type.isInstance(throwable)) return;
            throw new AssertionError(message + ": wrong exception " + throwable.getClass().getName(), throwable);
        }
        throw new AssertionError(message + ": no exception");
    }

    @FunctionalInterface
    private interface ThrowingAction {
        void run() throws Exception;
    }
}
