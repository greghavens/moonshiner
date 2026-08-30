import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLDecoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class TestMain {
    private static final String CONTRACT_SHA256 = "e22e0ff98ef032f56db4705aaae25990e2cb85f5588201d1e3c19053c9ad7d1d";
    private static final Map<String, String> CONTRACT_OPERATIONS = Map.of(
            "getDeploymentRequests", "GET /deployment/api/deployments/{deploymentId}/requests",
            "getRequestEvents", "GET /deployment/api/requests/{requestId}/events",
            "getEventLogs", "GET /deployment/api/requests/{requestId}/events/{eventId}/logs");
    private static final Map<String, String> OFFICIAL_SOURCES = Map.of(
            "getDeploymentRequests",
            "https://developer.broadcom.com/xapis/vm-apps-org-deployment/latest/deployment/api/deployments/deploymentId/requests/get/",
            "getRequestEvents",
            "https://developer.broadcom.com/xapis/vm-apps-org-deployment/latest/deployment/api/requests/requestId/events/get/",
            "getEventLogs",
            "https://developer.broadcom.com/xapis/vm-apps-org-deployment/latest/deployment/api/requests/requestId/events/eventId/logs/get/");

    public static void main(String[] args) throws Exception {
        verifyContractAndSources();
        testCollectsEventsAndRelevantLogs();
        testNoFailedRequestStopsAfterLookup();
        testRequestFailureStopsImmediately();
        testEventFailureStopsBeforeLogs();
        testLogFailureIsReported();
        testMalformedRequiredDataIsReported();
        testMockRejectsOperationOutsideContract();
        testIdentifierVerifierNegativeControls();
        System.out.println("ALL TESTS PASSED");
    }

    private static void testCollectsEventsAndRelevantLogs() throws Exception {
        String deploymentId = "dep/qa ?*+%~é 17";
        try (ContractMock server = new ContractMock(Fixture.NORMAL, deploymentId)) {
            AutomationClient client = new AutomationClient(server.baseUri(), server.bearerToken);
            AutomationClient.FailureDiagnosis diagnosis = client.diagnoseLatestFailure(deploymentId);

            check(server.requestId.equals(diagnosis.requestId()),
                    "diagnosis must use the failed request id returned by lookup");
            check(server.requestName.equals(diagnosis.requestName()),
                    "failed request name must be returned");
            check(server.requestDetails.equals(diagnosis.requestDetails()),
                    "failed request details must be returned");

            List<AutomationClient.EventEvidence> evidence = diagnosis.events();
            check(evidence.size() == 3, "all event pages must be retained");
            assertEvent(evidence.get(0), server.noLogEventId, server.planningName,
                    server.planningDetails, false, List.of());
            assertEvent(evidence.get(1), server.provisionEventId, server.provisionName,
                    server.provisionDetails, true, List.of(
                            new AutomationClient.LogLine(
                                    server.firstRowNumber, server.firstTimestamp, server.firstMessage),
                            new AutomationClient.LogLine(
                                    server.secondRowNumber, server.secondTimestamp, server.secondMessage)));
            assertEvent(evidence.get(2), server.cleanupEventId, server.cleanupName,
                    server.cleanupDetails, true, List.of(
                            new AutomationClient.LogLine(
                                    server.cleanupRowNumber,
                                    server.cleanupTimestamp,
                                    server.cleanupMessage)));

            List<LoggedExchange> log = server.requestLog();
            check(log.size() == 6,
                    "flow must make two request-page, two event-page, and two log calls");
            check("getDeploymentRequests".equals(log.get(0).operationId())
                            && "getDeploymentRequests".equals(log.get(1).operationId()),
                    "failed-request lookup must complete before event collection");
            check(countOperation(log, "getDeploymentRequests") == 2
                            && countOperation(log, "getRequestEvents") == 2
                            && countOperation(log, "getEventLogs") == 2,
                    "only the required lookup, event, and relevant log calls are allowed");

            Map<String, String> firstQuery = queryParameters(log.get(0).rawQuery());
            Map<String, String> secondQuery = queryParameters(log.get(1).rawQuery());
            check("0".equals(firstQuery.get("page")) && "1".equals(secondQuery.get("page")),
                    "request lookup must follow zero-based pages");
            check(List.of(firstQuery, secondQuery).stream()
                            .allMatch(q -> "20".equals(q.get("size"))
                                    && "createdAt,DESC".equals(q.get("sort"))),
                    "every request page must use size 20 and newest-first sorting");
            List<LoggedExchange> eventLookups = log.stream()
                    .filter(e -> "getRequestEvents".equals(e.operationId())).toList();
            check(eventLookups.size() == 2
                            && "0".equals(queryParameters(eventLookups.get(0).rawQuery()).get("page"))
                            && "1".equals(queryParameters(eventLookups.get(1).rawQuery()).get("page"))
                            && eventLookups.stream().allMatch(e -> "20".equals(
                                    queryParameters(e.rawQuery()).get("size"))),
                    "all event pages must be fetched with size 20");
            check(log.stream()
                            .filter(e -> "getDeploymentRequests".equals(e.operationId()))
                            .allMatch(e -> pathContainsEncodedSegment(e.rawPath(), deploymentId)),
                    "deployment id must be percent-encoded as one path segment");
            check(log.stream().filter(e -> "getRequestEvents".equals(e.operationId()))
                            .allMatch(e -> pathContainsEncodedSegment(e.rawPath(), server.requestId)),
                    "request id must be percent-encoded as one path segment");
            check(log.stream().filter(e -> "getEventLogs".equals(e.operationId()))
                            .allMatch(e -> pathContainsEncodedSegment(e.rawPath(), server.requestId))
                            && log.stream().anyMatch(e -> "getEventLogs".equals(e.operationId())
                                    && pathContainsEncodedSegment(e.rawPath(), server.provisionEventId))
                            && log.stream().anyMatch(e -> "getEventLogs".equals(e.operationId())
                                    && pathContainsEncodedSegment(e.rawPath(), server.cleanupEventId)),
                    "request and event ids in log paths must be percent-encoded segments");
            check(log.stream().noneMatch(e -> "getEventLogs".equals(e.operationId())
                            && pathContainsEncodedSegment(e.rawPath(), server.noLogEventId)),
                    "hasLogs false must not cause a log request");
            assertHeaders(log, server.bearerToken);
            assertIdentifiersCameFromOwnLookup(log);
        }
    }

    private static void assertEvent(
            AutomationClient.EventEvidence actual,
            String eventId,
            String name,
            String details,
            boolean hasLogs,
            List<AutomationClient.LogLine> logs) {
        check(eventId.equals(actual.eventId())
                        && name.equals(actual.name())
                        && details.equals(actual.details())
                        && hasLogs == actual.hasLogs()
                        && logs.equals(actual.logs()),
                "event evidence must preserve every returned field and log row in order");
    }

    private static void testNoFailedRequestStopsAfterLookup() throws Exception {
        try (ContractMock server = new ContractMock(Fixture.NO_FAILURE, "deployment-no-failure")) {
            AutomationClient client = new AutomationClient(server.baseUri(), server.bearerToken);
            try {
                client.diagnoseLatestFailure("deployment-no-failure");
                throw new AssertionError("missing failed request must throw IOException");
            } catch (IOException expected) {
                // Required failure type.
            }
            List<LoggedExchange> log = server.requestLog();
            check(operationIds(log).equals(List.of("getDeploymentRequests")),
                    "no failed request must stop before event or log calls");
        }
    }

    private static void testRequestFailureStopsImmediately() throws Exception {
        try (ContractMock server = new ContractMock(Fixture.REQUEST_ERROR, "deployment-request-error")) {
            expectIOException(server, "deployment-request-error");
            check(operationIds(server.requestLog()).equals(List.of("getDeploymentRequests")),
                    "request HTTP failure must stop immediately");
        }
    }

    private static void testEventFailureStopsBeforeLogs() throws Exception {
        try (ContractMock server = new ContractMock(Fixture.EVENT_ERROR, "deployment-event-error")) {
            AutomationClient client = new AutomationClient(server.baseUri(), server.bearerToken);
            try {
                client.diagnoseLatestFailure("deployment-event-error");
                throw new AssertionError("event HTTP failure must throw IOException");
            } catch (IOException expected) {
                // Required failure type.
            }
            List<LoggedExchange> log = server.requestLog();
            check(operationIds(log).equals(List.of(
                            "getDeploymentRequests", "getDeploymentRequests", "getRequestEvents")),
                    "event failure must stop before log retrieval");
            check(log.stream().noneMatch(e -> "getEventLogs".equals(e.operationId())),
                    "event failure must not be followed by guessed log requests");
        }
    }

    private static void testLogFailureIsReported() throws Exception {
        try (ContractMock server = new ContractMock(Fixture.LOG_ERROR, "deployment-log-error")) {
            expectIOException(server, "deployment-log-error");
            check(operationIds(server.requestLog()).equals(List.of(
                            "getDeploymentRequests", "getDeploymentRequests",
                            "getRequestEvents", "getEventLogs")),
                    "log HTTP failure must be surfaced without unrelated requests");
            assertIdentifiersCameFromOwnLookup(server.requestLog());
        }
    }

    private static void testMalformedRequiredDataIsReported() throws Exception {
        for (Fixture fixture : List.of(
                Fixture.MALFORMED_REQUEST, Fixture.MALFORMED_EVENT, Fixture.MALFORMED_LOG)) {
            String deploymentId = "deployment-malformed-" + fixture.name().toLowerCase(Locale.ROOT);
            try (ContractMock server = new ContractMock(fixture, deploymentId)) {
                expectIOException(server, deploymentId);
            }
        }
    }

    private static void expectIOException(ContractMock server, String deploymentId) throws Exception {
        AutomationClient client = new AutomationClient(server.baseUri(), server.bearerToken);
        try {
            client.diagnoseLatestFailure(deploymentId);
            throw new AssertionError("failure response must throw IOException");
        } catch (IOException expected) {
            // Required failure type.
        }
    }

    private static void testMockRejectsOperationOutsideContract() throws Exception {
        try (ContractMock server = new ContractMock(Fixture.NORMAL, "deployment-contract-only")) {
            HttpRequest request = HttpRequest.newBuilder(
                            server.baseUri().resolve("/deployment/api/diagnose/guess"))
                    .header("Authorization", "Bearer " + server.bearerToken)
                    .GET()
                    .build();
            HttpResponse<String> response = HttpClient.newHttpClient().send(
                    request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            check(response.statusCode() == 404,
                    "the mock must not serve an operation outside the pinned contract");
            check(server.requestLog().get(0).operationId() == null,
                    "an unrouted request must not be labeled as a contract operation");
        }
    }

    private static void testIdentifierVerifierNegativeControls() {
        List<LoggedExchange> wrongRequest = List.of(
                fabricated("getDeploymentRequests",
                        "/deployment/api/deployments/dep/requests",
                        200, "{\"content\":[{\"id\":\"request-from-own-lookup\"}],\"last\":true}"),
                fabricated("getRequestEvents",
                        "/deployment/api/requests/unrelated-request/events",
                        200, "{\"content\":[],\"last\":true}"));
        expectProvenanceFailure(wrongRequest, "request id negative control");

        List<LoggedExchange> wrongEvent = List.of(
                fabricated("getDeploymentRequests",
                        "/deployment/api/deployments/dep/requests",
                        200, "{\"content\":[{\"id\":\"request-from-own-lookup\"}],\"last\":true}"),
                fabricated("getRequestEvents",
                        "/deployment/api/requests/request-from-own-lookup/events",
                        200, "{\"content\":[{\"id\":\"event-from-own-lookup\"}],\"last\":true}"),
                fabricated("getEventLogs",
                        "/deployment/api/requests/request-from-own-lookup/events/unrelated-event/logs",
                        200, "{\"content\":[]}"));
        expectProvenanceFailure(wrongEvent, "event id negative control");

        List<LoggedExchange> lookupAfterUse = List.of(
                fabricated("getRequestEvents",
                        "/deployment/api/requests/late-request/events",
                        200, "{\"content\":[{\"id\":\"late-event\"}],\"last\":true}"),
                fabricated("getDeploymentRequests",
                        "/deployment/api/deployments/dep/requests",
                        200, "{\"content\":[{\"id\":\"late-request\"}],\"last\":true}"));
        expectProvenanceFailure(lookupAfterUse, "preceding lookup negative control");
    }

    private static LoggedExchange fabricated(
            String operationId, String rawPath, int responseStatus, String responseBody) {
        return new LoggedExchange(operationId, "GET", rawPath, null, "", Map.of(),
                responseStatus, responseBody);
    }

    private static void expectProvenanceFailure(List<LoggedExchange> log, String context) {
        try {
            assertIdentifiersCameFromOwnLookup(log);
            throw new AssertionError(context + " was accepted");
        } catch (AssertionError expected) {
            check(expected.getMessage().contains("own lookup"),
                    context + " must fail specifically at identifier provenance");
        }
    }

    private static List<String> operationIds(List<LoggedExchange> log) {
        return log.stream().map(LoggedExchange::operationId).toList();
    }

    private static long countOperation(List<LoggedExchange> log, String operationId) {
        return log.stream().filter(e -> operationId.equals(e.operationId())).count();
    }

    private static void assertHeaders(List<LoggedExchange> log, String bearerToken) {
        for (LoggedExchange exchange : log) {
            check(headerContains(exchange.headers(), "Authorization", "Bearer " + bearerToken),
                    "every operation must send bearer authentication");
            check(headerContains(exchange.headers(), "Accept", "application/json"),
                    "every operation must accept JSON");
            check(exchange.requestBody().isEmpty(), "documented GET operations must have no body");
        }
    }

    private static boolean headerContains(Map<String, List<String>> headers, String name, String value) {
        return headers.entrySet().stream()
                .filter(e -> e.getKey().equalsIgnoreCase(name))
                .flatMap(e -> e.getValue().stream())
                .anyMatch(v -> v.equalsIgnoreCase(value));
    }

    private static void assertIdentifiersCameFromOwnLookup(List<LoggedExchange> log) {
        Set<String> requestIds = new HashSet<>();
        Map<String, Set<String>> eventIdsByRequest = new HashMap<>();
        for (LoggedExchange exchange : log) {
            if ("getDeploymentRequests".equals(exchange.operationId())
                    && exchange.responseStatus() >= 200 && exchange.responseStatus() < 300) {
                requestIds.addAll(jsonObjectIds(exchange.responseBody()));
            } else if ("getRequestEvents".equals(exchange.operationId())) {
                Matcher path = Pattern.compile("^/deployment/api/requests/([^/]+)/events$")
                        .matcher(exchange.rawPath());
                check(path.matches(), "event lookup path is malformed");
                String requestId = decode(path.group(1));
                check(requestIds.contains(requestId),
                        "requestId " + requestId + " was not returned by this client's own lookup");
                if (exchange.responseStatus() >= 200 && exchange.responseStatus() < 300) {
                    eventIdsByRequest.computeIfAbsent(requestId, ignored -> new HashSet<>())
                            .addAll(jsonObjectIds(exchange.responseBody()));
                }
            } else if ("getEventLogs".equals(exchange.operationId())) {
                Matcher path = Pattern.compile(
                                "^/deployment/api/requests/([^/]+)/events/([^/]+)/logs$")
                        .matcher(exchange.rawPath());
                check(path.matches(), "event log path is malformed");
                String requestId = decode(path.group(1));
                String eventId = decode(path.group(2));
                check(requestIds.contains(requestId),
                        "requestId " + requestId + " was not returned by this client's own lookup");
                check(eventIdsByRequest.getOrDefault(requestId, Set.of()).contains(eventId),
                        "eventId " + eventId + " was not returned by this client's own lookup");
            }
        }
        check(!requestIds.isEmpty(), "own lookup did not return a request id");
    }

    private static Set<String> jsonObjectIds(String json) {
        Matcher matcher = Pattern.compile("\\\"id\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(json);
        Set<String> result = new HashSet<>();
        while (matcher.find()) {
            result.add(matcher.group(1));
        }
        return result;
    }

    private static Map<String, String> queryParameters(String rawQuery) {
        Map<String, String> result = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return result;
        }
        for (String pair : rawQuery.split("&")) {
            String[] pieces = pair.split("=", 2);
            result.put(decode(pieces[0]), decode(pieces.length == 2 ? pieces[1] : ""));
        }
        return result;
    }

    private static String decode(String value) {
        return URLDecoder.decode(value, StandardCharsets.UTF_8);
    }

    private static String encodeExpectedSegment(String value) {
        StringBuilder encoded = new StringBuilder();
        for (byte current : value.getBytes(StandardCharsets.UTF_8)) {
            int octet = current & 0xff;
            if ((octet >= 'a' && octet <= 'z')
                    || (octet >= 'A' && octet <= 'Z')
                    || (octet >= '0' && octet <= '9')
                    || octet == '-' || octet == '.' || octet == '_' || octet == '~') {
                encoded.append((char) octet);
            } else {
                encoded.append('%');
                encoded.append("0123456789ABCDEF".charAt(octet >>> 4));
                encoded.append("0123456789ABCDEF".charAt(octet & 0x0f));
            }
        }
        return encoded.toString();
    }

    private static boolean pathContainsEncodedSegment(String rawPath, String value) {
        String actual = rawPath.toLowerCase(Locale.ROOT);
        String expected = encodeExpectedSegment(value).toLowerCase(Locale.ROOT);
        return actual.contains(expected) || actual.contains(expected.replace("~", "%7e"));
    }

    private static void verifyContractAndSources() throws Exception {
        byte[] contractBytes = Files.readAllBytes(Path.of("docs", "contract.json"));
        String actualHash = java.util.HexFormat.of().formatHex(
                MessageDigest.getInstance("SHA-256").digest(contractBytes));
        check(CONTRACT_SHA256.equals(actualHash),
                "docs/contract.json does not match the mock's pinned contract");

        String contract = new String(contractBytes, StandardCharsets.UTF_8);
        String sources = Files.readString(Path.of("docs", "official_sources.json"));
        check(contract.contains("reference documentation rather than from a published API specification"),
                "contract must state that it is reference-derived, not a published specification");
        check(contract.contains("vmware/vcf-api-specs"),
                "contract must record the absent published specification context");
        for (Map.Entry<String, String> operation : CONTRACT_OPERATIONS.entrySet()) {
            String[] methodAndPath = operation.getValue().split(" ", 2);
            check(contract.contains("\"operationId\": \"" + operation.getKey() + "\"")
                            && contract.contains("\"method\": \"" + methodAndPath[0] + "\"")
                            && contract.contains("\"path\": \"" + methodAndPath[1] + "\""),
                    "contract is missing pinned operation " + operation.getKey());
            String sourceUrl = OFFICIAL_SOURCES.get(operation.getKey());
            check(contract.contains("\"source\": \"" + sourceUrl + "\""),
                    "contract operation is missing its official source URL");
            check(sources.contains("\"url\": \"" + sourceUrl + "\"")
                            && sources.contains("\"operation\": \"" + operation.getKey() + "\""),
                    "official source ledger is missing " + operation.getKey());
        }
        Matcher dates = Pattern.compile("\\\"fetchedOn\\\"\\s*:\\s*\\\"\\d{4}-\\d{2}-\\d{2}\\\"")
                .matcher(sources);
        int dateCount = 0;
        while (dates.find()) {
            dateCount++;
        }
        check(dateCount == CONTRACT_OPERATIONS.size(),
                "every official source must record its fetch date");
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private enum Fixture {
        NORMAL,
        NO_FAILURE,
        REQUEST_ERROR,
        EVENT_ERROR,
        LOG_ERROR,
        MALFORMED_REQUEST,
        MALFORMED_EVENT,
        MALFORMED_LOG
    }

    private record LoggedExchange(
            String operationId,
            String method,
            String rawPath,
            String rawQuery,
            String requestBody,
            Map<String, List<String>> headers,
            int responseStatus,
            String responseBody) {
    }

    private record Route(String operationId, List<String> pathParameters) {
    }

    private static final class ContractMock implements AutoCloseable {
        private final HttpServer server;
        private final Fixture fixture;
        private final String deploymentId;
        private final String bearerToken;
        private final List<LoggedExchange> requestLog = new CopyOnWriteArrayList<>();
        private final String requestId;
        private final String laterFailedRequestId;
        private final String requestName;
        private final String requestDetails;
        private final String noLogEventId;
        private final String provisionEventId;
        private final String cleanupEventId;
        private final String planningName;
        private final String planningDetails;
        private final String provisionName;
        private final String provisionDetails;
        private final String cleanupName;
        private final String cleanupDetails;
        private final long firstRowNumber;
        private final long secondRowNumber;
        private final long cleanupRowNumber;
        private final String firstTimestamp;
        private final String secondTimestamp;
        private final String cleanupTimestamp;
        private final String firstMessage;
        private final String secondMessage;
        private final String cleanupMessage;

        ContractMock(Fixture fixture, String deploymentId) throws IOException {
            this.fixture = fixture;
            this.deploymentId = deploymentId;
            this.bearerToken = "fixture-token-" + UUID.randomUUID();
            this.requestId = "failed/request ?*+%~é-" + UUID.randomUUID();
            this.laterFailedRequestId = "later/failed ?*+%~é-" + UUID.randomUUID();
            this.requestName = "Deploy Linux workload " + UUID.randomUUID();
            this.requestDetails = "Provisioning stopped during image mapping " + UUID.randomUUID();
            this.noLogEventId = "event/plan ?*+%~é-" + UUID.randomUUID();
            this.provisionEventId = "event/provision ?*+%~é-" + UUID.randomUUID();
            this.cleanupEventId = "event/cleanup ?*+%~é-" + UUID.randomUUID();
            this.planningName = "Planning " + UUID.randomUUID();
            this.planningDetails = "Inputs accepted " + UUID.randomUUID();
            this.provisionName = "Provision " + UUID.randomUUID();
            this.provisionDetails = "Machine allocation failed " + UUID.randomUUID();
            this.cleanupName = "Cleanup " + UUID.randomUUID();
            this.cleanupDetails = "Rollback ran " + UUID.randomUUID();
            long rowSeed = randomRowNumber();
            this.firstRowNumber = rowSeed + 2;
            this.secondRowNumber = rowSeed;
            this.cleanupRowNumber = randomRowNumber();
            this.firstTimestamp = randomTimestamp("2026-08-16T10:01:01.");
            this.secondTimestamp = randomTimestamp("2026-08-16T10:01:02.");
            this.cleanupTimestamp = randomTimestamp("2026-08-16T10:02:01.");
            this.firstMessage = "quota check passed " + UUID.randomUUID();
            this.secondMessage = "image mapping not found " + UUID.randomUUID();
            this.cleanupMessage = "rollback completed " + UUID.randomUUID();
            this.server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            this.server.createContext("/", this::handle);
            this.server.start();
        }

        private static long randomRowNumber() {
            return Math.floorMod(UUID.randomUUID().getMostSignificantBits(), 1_000_000L) + 1;
        }

        private static String randomTimestamp(String prefix) {
            return prefix + UUID.randomUUID().toString().substring(0, 6) + "Z";
        }

        URI baseUri() {
            return URI.create("http://" + server.getAddress().getHostString()
                    + ":" + server.getAddress().getPort() + "/");
        }

        List<LoggedExchange> requestLog() {
            return List.copyOf(requestLog);
        }

        private void handle(HttpExchange exchange) throws IOException {
            String method = exchange.getRequestMethod();
            String rawPath = exchange.getRequestURI().getRawPath();
            String rawQuery = exchange.getRequestURI().getRawQuery();
            String requestBody = new String(
                    exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            Route route = route(rawPath);
            int status;
            String response;

            if (route == null) {
                status = 404;
                response = "{\"error\":\"operation is not in the pinned contract\"}";
            } else if (!"GET".equals(method)) {
                status = 405;
                response = "{\"error\":\"method is not in the pinned contract\"}";
            } else if (!("Bearer " + bearerToken).equals(
                    exchange.getRequestHeaders().getFirst("Authorization"))) {
                status = 401;
                response = "{\"error\":\"unauthorized\"}";
            } else {
                Response routed = respond(route, queryParameters(rawQuery));
                status = routed.status();
                response = routed.body();
            }

            requestLog.add(new LoggedExchange(
                    route == null ? null : route.operationId(), method, rawPath, rawQuery,
                    requestBody, copyHeaders(exchange.getRequestHeaders()), status, response));
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            byte[] bytes = response.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(status, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        }

        private Response respond(Route route, Map<String, String> query) {
            return switch (route.operationId()) {
                case "getDeploymentRequests" -> deploymentRequests(route, query);
                case "getRequestEvents" -> requestEvents(route, query);
                case "getEventLogs" -> eventLogs(route);
                default -> new Response(500, "{\"error\":\"mock route mismatch\"}");
            };
        }

        private Response deploymentRequests(Route route, Map<String, String> query) {
            if (!deploymentId.equals(route.pathParameters().get(0))) {
                return new Response(404, "{\"error\":\"deployment not found\"}");
            }
            int page = integerQuery(query, "page", 0);
            if (fixture == Fixture.REQUEST_ERROR) {
                return new Response(502, "{\"error\":\"request index unavailable\"}");
            }
            if (fixture == Fixture.MALFORMED_REQUEST) {
                return new Response(200, "{\"content\":{},\"last\":true}");
            }
            if (fixture == Fixture.NO_FAILURE) {
                return new Response(200,
                        "{\"content\":["
                                + "{\"id\":\"lowercase-near-match\",\"name\":\"Near match\","
                                + "\"details\":\"not exact\",\"status\":\"failed\"},"
                                + "{\"id\":\"spaced-near-match\",\"name\":\"Near match\","
                                + "\"details\":\"not exact\",\"status\":\"FAILED \"},"
                                + "{\"id\":\"successful-only\",\"name\":\"Earlier request\","
                                + "\"details\":\"completed\",\"status\":\"SUCCESSFUL\"}],\"last\":true}");
            }
            if (page == 0) {
                return new Response(200,
                        "{\"content\":["
                                + "{\"id\":\"newer-lowercase\",\"name\":\"Near match\","
                                + "\"details\":\"not exact\",\"status\":\"failed\"},"
                                + "{\"id\":\"newer-spaced\",\"name\":\"Near match\","
                                + "\"details\":\"not exact\",\"status\":\"FAILED \"},"
                                + "{\"id\":\"newer-successful\",\"name\":\"Health check\","
                                + "\"details\":\"Provisioning failed but the request recovered\","
                                + "\"status\":\"SUCCESSFUL\"}],\"last\":false}");
            }
            if (page == 1) {
                return new Response(200,
                        "{\"content\":[{\"id\":\"" + requestId
                                + "\",\"name\":\"" + requestName + "\","
                                + "\"details\":\"" + requestDetails + "\","
                                + "\"status\":\"FAILED\"},"
                                + "{\"id\":\"" + laterFailedRequestId
                                + "\",\"name\":\"Older failed request\","
                                + "\"details\":\"must not be selected\","
                                + "\"status\":\"FAILED\"}],\"last\":true}");
            }
            return new Response(200, "{\"content\":[],\"last\":true}");
        }

        private Response requestEvents(Route route, Map<String, String> query) {
            if (!requestId.equals(route.pathParameters().get(0))) {
                return new Response(404, "{\"error\":\"request not found\"}");
            }
            if (fixture == Fixture.EVENT_ERROR) {
                return new Response(503, "{\"error\":\"event store unavailable\"}");
            }
            if (fixture == Fixture.MALFORMED_EVENT) {
                return new Response(200,
                        "{\"content\":[{\"details\":\"" + provisionDetails
                                + "\",\"hasLogs\":\"yes\",\"id\":\"" + provisionEventId
                                + "\",\"name\":\"" + provisionName + "\"}],\"last\":true}");
            }
            if (fixture == Fixture.LOG_ERROR || fixture == Fixture.MALFORMED_LOG) {
                return new Response(200,
                        "{\"content\":[{\"details\":\"" + provisionDetails
                                + "\",\"hasLogs\":true,\"id\":\"" + provisionEventId
                                + "\",\"name\":\"" + provisionName + "\"}],\"last\":true}");
            }
            int page = integerQuery(query, "page", 0);
            if (page == 0) {
                return new Response(200,
                        "{\"content\":["
                                + "{\"details\":\"" + planningDetails + "\",\"hasLogs\":false,\"id\":\""
                                + noLogEventId + "\",\"name\":\"" + planningName + "\","
                                + "\"timestamp\":\"2026-08-16T10:00:00Z\"},"
                                + "{\"details\":\"" + provisionDetails + "\",\"hasLogs\":true,\"id\":\""
                                + provisionEventId + "\",\"name\":\"" + provisionName + "\","
                                + "\"timestamp\":\"2026-08-16T10:01:00Z\"}],\"last\":false}");
            }
            if (page == 1) {
                return new Response(200,
                        "{\"content\":[{\"details\":\"" + cleanupDetails + "\",\"hasLogs\":true,\"id\":\""
                                + cleanupEventId + "\",\"name\":\"" + cleanupName + "\","
                                + "\"timestamp\":\"2026-08-16T10:02:00Z\"}],\"last\":true}");
            }
            return new Response(200, "{\"content\":[],\"last\":true}");
        }

        private Response eventLogs(Route route) {
            String routedRequestId = route.pathParameters().get(0);
            String eventId = route.pathParameters().get(1);
            if (!requestId.equals(routedRequestId)) {
                return new Response(404, "{\"error\":\"request not found\"}");
            }
            if (provisionEventId.equals(eventId)) {
                if (fixture == Fixture.LOG_ERROR) {
                    return new Response(502, "{\"error\":\"log store unavailable\"}");
                }
                if (fixture == Fixture.MALFORMED_LOG) {
                    return new Response(200,
                            "{\"content\":[{\"message\":\"" + firstMessage
                                    + "\",\"rownum\":1.5,\"timestamp\":\""
                                    + firstTimestamp + "\"}]}");
                }
                return new Response(200,
                        "{\"content\":["
                                + "{\"eof\":false,\"id\":\"log-a\",\"message\":\"" + firstMessage + "\","
                                + "\"rownum\":" + firstRowNumber + ",\"timestamp\":\"" + firstTimestamp + "\"},"
                                + "{\"eof\":true,\"id\":\"log-b\","
                                + "\"message\":\"" + secondMessage + "\","
                                + "\"rownum\":" + secondRowNumber + ",\"timestamp\":\"" + secondTimestamp + "\"}]}");
            }
            if (cleanupEventId.equals(eventId)) {
                return new Response(200,
                        "{\"content\":[{\"eof\":true,\"id\":\"log-c\","
                                + "\"message\":\"" + cleanupMessage + "\",\"rownum\":"
                                + cleanupRowNumber + ",\"timestamp\":\"" + cleanupTimestamp + "\"}]}");
            }
            return new Response(404, "{\"error\":\"event not found\"}");
        }

        private static int integerQuery(Map<String, String> query, String key, int defaultValue) {
            try {
                return Integer.parseInt(query.getOrDefault(key, Integer.toString(defaultValue)));
            } catch (NumberFormatException exception) {
                return defaultValue;
            }
        }

        private static Route route(String rawPath) {
            Matcher deploymentRequests = Pattern.compile(
                            "^/deployment/api/deployments/([^/]+)/requests$")
                    .matcher(rawPath);
            if (deploymentRequests.matches()) {
                return new Route("getDeploymentRequests", List.of(decode(deploymentRequests.group(1))));
            }
            Matcher requestEvents = Pattern.compile(
                            "^/deployment/api/requests/([^/]+)/events$")
                    .matcher(rawPath);
            if (requestEvents.matches()) {
                return new Route("getRequestEvents", List.of(decode(requestEvents.group(1))));
            }
            Matcher eventLogs = Pattern.compile(
                            "^/deployment/api/requests/([^/]+)/events/([^/]+)/logs$")
                    .matcher(rawPath);
            if (eventLogs.matches()) {
                return new Route("getEventLogs", List.of(
                        decode(eventLogs.group(1)), decode(eventLogs.group(2))));
            }
            return null;
        }

        private static Map<String, List<String>> copyHeaders(Headers source) {
            Map<String, List<String>> copy = new LinkedHashMap<>();
            source.forEach((key, value) -> copy.put(key, List.copyOf(value)));
            return Map.copyOf(copy);
        }

        @Override
        public void close() {
            server.stop(0);
        }
    }

    private record Response(int status, String body) {
    }
}
