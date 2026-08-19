import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.ByteArrayOutputStream;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.Executors;
import java.util.concurrent.ExecutorService;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Loopback-only VCF Automation double whose route allow-list is loaded from contract.json. */
final class ContractMockServer implements AutoCloseable {
    record LoggedExchange(String operationId, String method, String rawTarget,
                          Map<String, List<String>> requestHeaders, byte[] requestBody,
                          int responseStatus, String responseBody) {
        LoggedExchange {
            requestHeaders = Map.copyOf(requestHeaders);
            requestBody = requestBody.clone();
        }

        @Override
        public byte[] requestBody() {
            return requestBody.clone();
        }
    }

    static final class Scenario {
        enum Fault {
            NONE,
            DRAIN_FAILED,
            DUPLICATE_TRACKER_ID,
            UNKNOWN_TRACKER_STATUS,
            MISSING_TRACKER_VALUE,
            MALFORMED_TRACKER_JSON,
            REJECTED_LIST_STATUS,
            DUPLICATE_ACCOUNT_ID,
            DUPLICATE_ACCOUNT_NAME,
            MISSING_ACCOUNT_VALUE,
            NULL_OPTIONAL_ACCOUNT_VALUE,
            EMPTY_OPTIONAL_VALUES,
            MALFORMED_ACCOUNT_JSON,
            REJECTED_PATCH_STATUS,
            MALFORMED_PATCH_JSON,
            UNKNOWN_PATCH_STATUS,
            PATCH_RESPONSE_FINISHED,
            TRACKER_ID_MISMATCH
        }

        final String accountName = "payments-vcenter-" + suffix();
        final String targetAccountId = "ca/" + suffix() + " snow-\u96ea";
        final String decoyAccountId = "ca-" + suffix();
        final String bearerToken = "bearer-" + suffix();
        final String apiVersion;
        final String newPassword = "new-\"\\-secret-" + suffix();
        final String firstDrainId = "request/" + suffix() + "-\u96ea";
        final String secondDrainId = "request?" + suffix() + " two-\u00e9";
        final String alreadyFinishedId = "request-" + suffix();
        final String updateRequestId = "request#" + suffix() + " final-\u96ea";
        final String hostName = "vc-" + suffix() + ".example.test";
        final String username;
        final String description;
        final String dcid;
        final boolean updateFails;
        final Fault fault;

        private Scenario(boolean optionalsPresent, boolean updateFails, String apiVersion, Fault fault) {
            this.apiVersion = apiVersion;
            this.updateFails = updateFails;
            this.fault = fault;
            this.description = fault == Fault.EMPTY_OPTIONAL_VALUES ? ""
                    : optionalsPresent ? "primary endpoint " + suffix() : null;
            this.dcid = fault == Fault.EMPTY_OPTIONAL_VALUES ? ""
                    : optionalsPresent ? "dc-" + suffix() : null;
            this.username = fault == Fault.EMPTY_OPTIONAL_VALUES ? ""
                    : optionalsPresent ? "svc-automation@corp.example" : null;
        }

        static Scenario successWithOptionals() {
            return scenario(Fault.NONE);
        }

        static Scenario successWithoutOptionals() {
            return new Scenario(false, false, "2023-01-01", Fault.NONE);
        }

        static Scenario failedUpdate() {
            return new Scenario(true, true, "2021-07-15", Fault.NONE);
        }

        static Scenario scenario(Fault fault) {
            return new Scenario(true, false, "2021-07-15", fault);
        }

        Map<String, Object> expectedPatch() {
            LinkedHashMap<String, Object> body = new LinkedHashMap<>();
            body.put("name", accountName);
            if (description != null) body.put("description", description);
            body.put("hostName", hostName);
            if (dcid != null) body.put("dcid", dcid);
            if (username != null) body.put("username", username);
            body.put("password", newPassword);
            body.put("regions", List.of(
                    map("name", "Datacenter:dc-" + targetAccountId.substring(3, 11),
                            "externalRegionId", "Datacenter:dc-" + targetAccountId.substring(3, 11)),
                    map("name", "Datacenter:edge-" + targetAccountId.substring(3, 11),
                            "externalRegionId", "Datacenter:edge-" + targetAccountId.substring(3, 11))));
            return body;
        }

        private static long nextSuffix;

        private static synchronized String suffix() {
            return String.format("%016x", ++nextSuffix);
        }
    }

    private record Route(String operationId, String method, String template, Pattern pattern) {
        static Route from(String operationId, String method, String template) {
            StringBuilder regex = new StringBuilder("^");
            int cursor = 0;
            Matcher placeholders = Pattern.compile("\\{[^/{}]+}").matcher(template);
            while (placeholders.find()) {
                regex.append(Pattern.quote(template.substring(cursor, placeholders.start())));
                regex.append("([^/]+)");
                cursor = placeholders.end();
            }
            regex.append(Pattern.quote(template.substring(cursor))).append('$');
            return new Route(operationId, method, template, Pattern.compile(regex.toString()));
        }

        String identifier(String rawPath) {
            Matcher matcher = pattern.matcher(rawPath);
            return matcher.matches() && matcher.groupCount() == 1 ? decodePathSegment(matcher.group(1)) : null;
        }

        boolean matches(String requestMethod, String rawPath) {
            return method.equals(requestMethod) && pattern.matcher(rawPath).matches();
        }
    }

    private final HttpServer server;
    private final ExecutorService executor;
    private final Scenario scenario;
    private final List<Route> routes;
    private final List<LoggedExchange> log = Collections.synchronizedList(new ArrayList<>());
    private final Map<String, Integer> trackerPolls = new LinkedHashMap<>();
    private final Set<String> drained = new LinkedHashSet<>();
    private volatile boolean trackerCollectionRead;
    private volatile boolean accountCollectionRead;
    private volatile boolean updateSubmitted;

    ContractMockServer(Path contractPath, Scenario scenario) throws IOException {
        this.scenario = scenario;
        this.routes = loadRoutes(contractPath);
        Set<String> ids = new LinkedHashSet<>();
        for (Route route : routes) ids.add(route.operationId);
        Set<String> expected = Set.of("listVSphereCloudAccounts", "updateVSphereCloudAccountAsync",
                "listRequestTrackers", "getRequestTracker");
        if (!ids.equals(expected)) throw new IllegalArgumentException("unexpected contract operation set");
        this.server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        this.server.createContext("/", this::handle);
        this.executor = Executors.newCachedThreadPool();
        this.server.setExecutor(executor);
    }

    void start() {
        server.start();
    }

    URI origin() {
        return URI.create("http://127.0.0.1:" + server.getAddress().getPort());
    }

    List<LoggedExchange> requestLog() {
        synchronized (log) {
            return List.copyOf(log);
        }
    }

    Scenario scenario() {
        return scenario;
    }

    @Override
    public void close() {
        server.stop(0);
        executor.shutdownNow();
    }

    private void handle(HttpExchange exchange) throws IOException {
        byte[] requestBody = exchange.getRequestBody().readAllBytes();
        String method = exchange.getRequestMethod();
        String rawPath = exchange.getRequestURI().getRawPath();
        String rawTarget = exchange.getRequestURI().toASCIIString();
        Route route = routes.stream().filter(candidate -> candidate.matches(method, rawPath))
                .findFirst().orElse(null);

        Response response;
        if (route == null) {
            response = json(404, map("message", "operation is not in contract"));
        } else if (!validCommonWire(exchange, requestBody, method)) {
            response = json(400, map("message", "wire does not match focused contract"));
        } else {
            response = dispatch(route, rawPath, requestBody);
        }

        Map<String, List<String>> headerCopy = new LinkedHashMap<>();
        exchange.getRequestHeaders().forEach((key, value) -> headerCopy.put(key, List.copyOf(value)));
        log.add(new LoggedExchange(route == null ? null : route.operationId, method, rawTarget,
                headerCopy, requestBody, response.status, response.body));

        byte[] bytes = response.body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(response.status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    private boolean validCommonWire(HttpExchange exchange, byte[] body, String method) {
        Headers headers = exchange.getRequestHeaders();
        if (!single(headers, "Accept", "application/json")) return false;
        if (!single(headers, "Authorization", "Bearer " + scenario.bearerToken)) return false;
        String expectedQuery = "apiVersion=" + scenario.apiVersion;
        if (!expectedQuery.equals(exchange.getRequestURI().getRawQuery())) return false;
        if (method.equals("GET")) {
            return body.length == 0 && headers.get("Content-Type") == null;
        }
        return method.equals("PATCH") && single(headers, "Content-Type", "application/json")
                && body.length > 0 && headers.get("Transfer-Encoding") == null;
    }

    private Response dispatch(Route route, String rawPath, byte[] requestBody) {
        return switch (route.operationId) {
            case "listRequestTrackers" -> listRequestTrackers();
            case "getRequestTracker" -> getRequestTracker(route.identifier(rawPath));
            case "listVSphereCloudAccounts" -> listVSphereCloudAccounts();
            case "updateVSphereCloudAccountAsync" -> updateAccount(route.identifier(rawPath), requestBody);
            default -> json(404, map("message", "operation is not implemented by mock"));
        };
    }

    private Response listRequestTrackers() {
        if (trackerCollectionRead || accountCollectionRead || updateSubmitted) {
            return json(409, map("message", "tracker collection may be read only once and first"));
        }
        trackerCollectionRead = true;
        if (scenario.fault == Scenario.Fault.MALFORMED_TRACKER_JSON) {
            return new Response(200, "{malformed-tracker-json");
        }
        if (scenario.fault == Scenario.Fault.REJECTED_LIST_STATUS) {
            return new Response(503, TestJson.stringify(map("message",
                    "response-marker " + scenario.bearerToken + " " + scenario.newPassword)));
        }
        Object first = tracker(scenario.firstDrainId,
                scenario.fault == Scenario.Fault.UNKNOWN_TRACKER_STATUS ? "QUEUED" : "INPROGRESS", 15);
        if (scenario.fault == Scenario.Fault.MISSING_TRACKER_VALUE) {
            first = map("progress", 15L, "status", "INPROGRESS", "id", scenario.firstDrainId);
        }
        Object second = scenario.fault == Scenario.Fault.DUPLICATE_TRACKER_ID
                ? tracker(scenario.firstDrainId, "INPROGRESS", 70)
                : tracker(scenario.secondDrainId, "INPROGRESS", 70);
        List<Object> content = List.of(first,
                tracker(scenario.alreadyFinishedId, "FINISHED", 100), second);
        return json(200, map("content", content, "totalElements", 3L, "numberOfElements", 3L));
    }

    private Response getRequestTracker(String id) {
        if (id == null) return json(404, map("message", "missing tracker id"));
        if (id.equals(scenario.firstDrainId)) {
            int poll = trackerPolls.merge(id, 1, Integer::sum);
            String status = poll == 1 ? "INPROGRESS"
                    : scenario.fault == Scenario.Fault.DRAIN_FAILED ? "FAILED" : "FINISHED";
            if (status.equals("FINISHED")) drained.add(id);
            String returnedId = scenario.fault == Scenario.Fault.TRACKER_ID_MISMATCH
                    ? id + "-mismatch" : id;
            return json(200, tracker(returnedId, status, status.equals("INPROGRESS") ? 65 : 100));
        }
        if (id.equals(scenario.secondDrainId)) {
            trackerPolls.merge(id, 1, Integer::sum);
            drained.add(id);
            return json(200, tracker(id, "FINISHED", 100));
        }
        if (id.equals(scenario.alreadyFinishedId)) {
            trackerPolls.merge(id, 1, Integer::sum);
            return json(200, tracker(id, "FINISHED", 100));
        }
        if (id.equals(scenario.updateRequestId) && updateSubmitted) {
            if (scenario.fault == Scenario.Fault.PATCH_RESPONSE_FINISHED) {
                trackerPolls.merge(id, 1, Integer::sum);
                return json(200, tracker(id, "FINISHED", 100));
            }
            int poll = trackerPolls.merge(id, 1, Integer::sum);
            String status = poll == 1 ? "INPROGRESS" : scenario.updateFails ? "FAILED" : "FINISHED";
            return json(200, tracker(id, status, status.equals("INPROGRESS") ? 55 : 100));
        }
        return json(404, map("message", "unknown tracker"));
    }

    private Response listVSphereCloudAccounts() {
        if (!trackerCollectionRead || !drained.containsAll(List.of(scenario.firstDrainId, scenario.secondDrainId))
                || accountCollectionRead || updateSubmitted) {
            return json(409, map("message", "account lookup occurred before drain or more than once"));
        }
        accountCollectionRead = true;
        if (scenario.fault == Scenario.Fault.MALFORMED_ACCOUNT_JSON) {
            return new Response(200, "[malformed-account-json");
        }
        Map<String, Object> target = new LinkedHashMap<>();
        target.put("id", scenario.targetAccountId);
        target.put("name", scenario.accountName);
        if (scenario.description != null) target.put("description", scenario.description);
        target.put("hostName", scenario.hostName);
        if (scenario.dcid != null) target.put("dcid", scenario.dcid);
        if (scenario.username != null) target.put("username", scenario.username);
        target.put("enabledRegions", readRegions());
        target.put("customProperties", map("fixtureReadOnly", "must-not-be-written"));
        target.put("environment", "fixture-read-only");
        if (scenario.fault == Scenario.Fault.MISSING_ACCOUNT_VALUE) target.remove("hostName");
        if (scenario.fault == Scenario.Fault.NULL_OPTIONAL_ACCOUNT_VALUE) {
            target.put("description", null);
        }

        String decoyId = scenario.fault == Scenario.Fault.DUPLICATE_ACCOUNT_ID
                ? scenario.targetAccountId : scenario.decoyAccountId;
        String decoyName = scenario.fault == Scenario.Fault.DUPLICATE_ACCOUNT_NAME
                ? scenario.accountName : scenario.accountName.toUpperCase(java.util.Locale.ROOT);
        Map<String, Object> decoy = map("id", decoyId, "name", decoyName,
                "hostName", "decoy.example.test", "username", "decoy@example.test",
                "enabledRegions", List.of(map("name", "decoy", "externalRegionId", "decoy")));
        List<Object> content = scenario.description == null ? List.of(target, decoy) : List.of(decoy, target);
        return json(200, map("content", content, "totalElements", 2L, "numberOfElements", 2L));
    }

    private Response updateAccount(String id, byte[] requestBody) {
        if (!accountCollectionRead || updateSubmitted || !scenario.targetAccountId.equals(id)) {
            return json(404, map("message", "unknown or premature account id"));
        }
        Object parsed;
        try {
            parsed = TestJson.parse(new String(requestBody, StandardCharsets.UTF_8));
        } catch (RuntimeException e) {
            return json(400, map("message", "invalid JSON"));
        }
        if (!scenario.expectedPatch().equals(parsed)) {
            return json(400, map("message", "unexpected update shape"));
        }
        updateSubmitted = true;
        if (scenario.fault == Scenario.Fault.REJECTED_PATCH_STATUS) {
            return json(200, tracker(scenario.updateRequestId, "INPROGRESS", 0));
        }
        if (scenario.fault == Scenario.Fault.MALFORMED_PATCH_JSON) {
            return new Response(202, "{malformed-patch-json");
        }
        String status = scenario.fault == Scenario.Fault.UNKNOWN_PATCH_STATUS ? "QUEUED"
                : scenario.fault == Scenario.Fault.PATCH_RESPONSE_FINISHED ? "FINISHED"
                : "INPROGRESS";
        return json(202, tracker(scenario.updateRequestId, status, 0));
    }

    private List<Object> readRegions() {
        List<Object> regions = new ArrayList<>();
        for (Object item : (List<?>) scenario.expectedPatch().get("regions")) {
            @SuppressWarnings("unchecked")
            Map<String, Object> write = (Map<String, Object>) item;
            LinkedHashMap<String, Object> read = new LinkedHashMap<>();
            read.put("id", "region-" + Scenario.suffix());
            read.put("createdAt", "2026-08-16");
            read.put("externalRegionId", write.get("externalRegionId"));
            read.put("name", write.get("name"));
            read.put("cloudAccountId", scenario.targetAccountId);
            regions.add(read);
        }
        return regions;
    }

    private static Map<String, Object> tracker(String id, String status, int progress) {
        return map("progress", (long) progress, "status", status, "id", id,
                "selfLink", "/iaas/api/request-tracker/" + id);
    }

    private static boolean single(Headers headers, String name, String expected) {
        List<String> values = headers.get(name);
        return values != null && values.size() == 1 && values.get(0).equals(expected);
    }

    private record Response(int status, String body) {
    }

    private static Response json(int status, Object body) {
        return new Response(status, TestJson.stringify(body));
    }

    @SuppressWarnings("unchecked")
    private static List<Route> loadRoutes(Path contractPath) throws IOException {
        Object rootValue = TestJson.parse(Files.readString(contractPath, StandardCharsets.UTF_8));
        Map<String, Object> root = (Map<String, Object>) rootValue;
        List<Object> operations = (List<Object>) root.get("operations");
        ArrayList<Route> routes = new ArrayList<>();
        for (Object value : operations) {
            Map<String, Object> operation = (Map<String, Object>) value;
            routes.add(Route.from((String) operation.get("operationId"),
                    (String) operation.get("method"), (String) operation.get("path")));
        }
        return List.copyOf(routes);
    }

    private static String decodePathSegment(String raw) {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream(raw.length());
        for (int i = 0; i < raw.length(); i++) {
            char c = raw.charAt(i);
            if (c == '%' && i + 2 < raw.length()) {
                int high = Character.digit(raw.charAt(++i), 16);
                int low = Character.digit(raw.charAt(++i), 16);
                if (high < 0 || low < 0) throw new IllegalArgumentException("bad path encoding");
                bytes.write((high << 4) | low);
            } else {
                byte[] encoded = String.valueOf(c).getBytes(StandardCharsets.UTF_8);
                bytes.writeBytes(encoded);
            }
        }
        return bytes.toString(StandardCharsets.UTF_8);
    }

    static Map<String, Object> map(Object... pairs) {
        LinkedHashMap<String, Object> result = new LinkedHashMap<>();
        for (int i = 0; i < pairs.length; i += 2) result.put((String) pairs[i], pairs[i + 1]);
        return result;
    }
}
