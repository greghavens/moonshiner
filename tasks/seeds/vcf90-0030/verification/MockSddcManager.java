import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Loopback stand-in for SDDC Manager, pinned to docs/contract.json.
 *
 * <p>The routing table is built from the contract's {@code operations} array, so the mock serves
 * exactly the three operations the contract names and nothing else. It performs no gating of its
 * own: if a client sends {@code POST /v1/hosts} while the precheck has not passed, the mock happily
 * answers it. Detecting that mistake is the request log's job.
 *
 * <p>Every exchange - matched or not - is appended to a JSON Lines request log that the test reads
 * back. The server binds the loopback interface on an ephemeral port and never reaches a live
 * VMware endpoint.
 */
final class MockSddcManager implements AutoCloseable {

    /** Canned responses for one run. Nothing here is derived from what the client sends. */
    static final class Script {
        int validateStatus = 202;
        String validateBody = "{}";
        int pollStatus = 202;
        /** Successive getHostCommissionValidationByID bodies; the last one repeats. */
        final List<String> pollBodies = new ArrayList<>();
        int commissionStatus = 202;
        String commissionBody = "{}";
    }

    private static final class Route {
        final String operationId;
        final String method;
        final String pathTemplate;
        final Pattern pattern;
        final boolean bodyRequired;
        final int literalSegments;

        Route(String operationId, String method, String pathTemplate, boolean bodyRequired) {
            this.operationId = operationId;
            this.method = method;
            this.pathTemplate = pathTemplate;
            this.bodyRequired = bodyRequired;
            StringBuilder regex = new StringBuilder("^");
            int literals = 0;
            for (String segment : pathTemplate.substring(1).split("/", -1)) {
                regex.append('/');
                if (segment.startsWith("{") && segment.endsWith("}")) {
                    regex.append("([^/]+)");
                } else {
                    regex.append(Pattern.quote(segment));
                    literals++;
                }
            }
            this.pattern = Pattern.compile(regex.append('$').toString());
            this.literalSegments = literals;
        }
    }

    private final HttpServer server;
    private final Script script;
    private final Path requestLog;
    private final List<Route> routes;
    private final AtomicInteger sequence = new AtomicInteger();
    private final AtomicInteger pollCount = new AtomicInteger();
    private final Object logLock = new Object();

    private MockSddcManager(HttpServer server, Script script, Path requestLog, List<Route> routes) {
        this.server = server;
        this.script = script;
        this.requestLog = requestLog;
        this.routes = routes;
    }

    static MockSddcManager start(Path contractPath, Path requestLog, Script script) throws IOException {
        List<Route> routes = readRoutes(contractPath);
        Files.deleteIfExists(requestLog);
        Files.createFile(requestLog);
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.setExecutor(Executors.newSingleThreadExecutor(runnable -> {
            Thread thread = new Thread(runnable, "mock-sddc-manager");
            thread.setDaemon(true);
            return thread;
        }));
        MockSddcManager mock = new MockSddcManager(server, script, requestLog, routes);
        server.createContext("/", mock::handle);
        server.start();
        return mock;
    }

    private static List<Route> readRoutes(Path contractPath) throws IOException {
        Map<String, Object> contract = Json.obj(Json.parse(Files.readString(contractPath, StandardCharsets.UTF_8)));
        List<Route> routes = new ArrayList<>();
        for (Object entry : Json.arr(contract.get("operations"))) {
            Map<String, Object> operation = Json.obj(entry);
            Object requestBody = operation.get("requestBody");
            boolean bodyRequired = requestBody instanceof Map
                    && Boolean.TRUE.equals(Json.obj(requestBody).get("required"));
            routes.add(new Route(
                    Json.str(operation.get("operationId")),
                    Json.str(operation.get("method")),
                    Json.str(operation.get("pathTemplate")),
                    bodyRequired));
        }
        routes.sort((a, b) -> Integer.compare(b.literalSegments, a.literalSegments));
        return routes;
    }

    String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    Path requestLog() {
        return requestLog;
    }

    @Override
    public void close() {
        server.stop(0);
    }

    private void handle(HttpExchange exchange) throws IOException {
        String method = exchange.getRequestMethod();
        String path = exchange.getRequestURI().getRawPath();
        String rawQuery = exchange.getRequestURI().getRawQuery();
        String contentType = exchange.getRequestHeaders().getFirst("Content-Type");
        String accept = exchange.getRequestHeaders().getFirst("Accept");
        String body = readBody(exchange);

        Route matched = null;
        String pathParameter = null;
        boolean pathKnownForOtherMethod = false;
        for (Route route : routes) {
            Matcher matcher = route.pattern.matcher(path);
            if (!matcher.matches()) {
                continue;
            }
            if (!route.method.equals(method)) {
                pathKnownForOtherMethod = true;
                continue;
            }
            matched = route;
            pathParameter = matcher.groupCount() >= 1 ? matcher.group(1) : null;
            break;
        }

        String[] response = respond(matched, pathParameter, rawQuery, contentType, body, pathKnownForOtherMethod);
        int status = Integer.parseInt(response[0]);
        String payload = response[1];

        log(matched, method, path, rawQuery, contentType, accept, body, status);

        byte[] bytes = payload.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream out = exchange.getResponseBody()) {
            out.write(bytes);
        }
    }

    private String[] respond(Route matched, String pathParameter, String rawQuery, String contentType,
                             String body, boolean pathKnownForOtherMethod) {
        if (matched == null) {
            return new String[] {"404", error("ROUTE_NOT_SERVED", pathKnownForOtherMethod
                    ? "The contract does not declare this method for this path."
                    : "The contract does not declare this path.")};
        }
        if (rawQuery != null) {
            return new String[] {"400", error("UNEXPECTED_QUERY_PARAMETER",
                    "This operation declares no query parameter but the request carried a query string.")};
        }
        if (matched.bodyRequired) {
            if (body.isEmpty()) {
                return new String[] {"400", error("REQUEST_BODY_REQUIRED", "This operation requires a request body.")};
            }
            if (!"application/json".equals(contentType)) {
                return new String[] {"415", error("UNSUPPORTED_MEDIA_TYPE",
                        "Expected Content-Type application/json but received: " + contentType)};
            }
        } else if (!body.isEmpty()) {
            return new String[] {"400", error("UNEXPECTED_REQUEST_BODY",
                    "This operation declares no request body.")};
        }

        switch (matched.operationId) {
            case "validateHostCommissionSpec":
                return new String[] {String.valueOf(script.validateStatus), script.validateBody};
            case "getHostCommissionValidationByID": {
                String issued = issuedValidationId();
                if (issued != null && !issued.equals(pathParameter)) {
                    return new String[] {"400", error("NO_SUCH_VALIDATION",
                            "No validation with id " + pathParameter + " exists; this run issued " + issued)};
                }
                if (script.pollBodies.isEmpty()) {
                    return new String[] {"400", error("NO_SUCH_VALIDATION",
                            "No validation is scripted for id " + pathParameter)};
                }
                int index = Math.min(pollCount.getAndIncrement(), script.pollBodies.size() - 1);
                return new String[] {String.valueOf(script.pollStatus), script.pollBodies.get(index)};
            }
            case "commissionHosts":
                return new String[] {String.valueOf(script.commissionStatus), script.commissionBody};
            default:
                return new String[] {"404", error("ROUTE_NOT_SERVED", "Unhandled operation " + matched.operationId)};
        }
    }

    /** The validation id this run handed out, so a client cannot poll an id it invented. */
    private String issuedValidationId() {
        try {
            return Json.str(Json.obj(Json.parse(script.validateBody)).get("id"));
        } catch (RuntimeException notAValidation) {
            return null;
        }
    }

    private void log(Route matched, String method, String path, String rawQuery, String contentType,
                     String accept, String body, int status) throws IOException {
        Map<String, Object> record = new LinkedHashMap<>();
        record.put("seq", (long) sequence.incrementAndGet());
        record.put("operationId", matched == null ? null : matched.operationId);
        record.put("pathTemplate", matched == null ? null : matched.pathTemplate);
        record.put("method", method);
        record.put("path", path);
        record.put("rawQuery", rawQuery);
        record.put("contentType", contentType);
        record.put("accept", accept);
        record.put("bodyPresent", !body.isEmpty());
        record.put("body", body.isEmpty() ? null : body);
        record.put("responseStatus", (long) status);
        synchronized (logLock) {
            Files.writeString(requestLog, Json.write(record) + System.lineSeparator(),
                    StandardCharsets.UTF_8, StandardOpenOption.APPEND);
        }
    }

    private static String readBody(HttpExchange exchange) throws IOException {
        try (InputStream in = exchange.getRequestBody()) {
            return new String(in.readAllBytes(), StandardCharsets.UTF_8);
        }
    }

    private static String error(String code, String message) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("errorCode", code);
        payload.put("errorType", "VALIDATION_FAILED");
        payload.put("message", message);
        return Json.write(payload);
    }
}
