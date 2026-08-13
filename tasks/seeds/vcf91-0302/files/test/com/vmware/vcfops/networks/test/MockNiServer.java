package com.vmware.vcfops.networks.test;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import com.vmware.vcfops.networks.Json;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * A loopback-only stand-in for a VCF Operations for Networks appliance, pinned to
 * {@code docs/contract.json}.
 *
 * <p>It serves exactly the four operations the contract names and 404s everything else, so a
 * client that strays outside the contract fails loudly rather than silently. Every request is
 * captured in an ordered request log that the test harness reads back.
 *
 * <p>Token lifetime is deliberately shorter than the sweep: the first token issued survives two
 * authenticated requests and the second survives three, after which they answer 401 exactly as
 * the specification describes for an expired token. Tokens issued after that do not expire, so
 * a correct client converges. Because the budget is counted in requests rather than wall-clock
 * seconds, the expiry points are identical on every run.
 *
 * <p>DO NOT MODIFY.
 */
public final class MockNiServer implements AutoCloseable {

    /** Credentials the mock appliance accepts. */
    public static final String USERNAME = "svc-inventory@lab.local";
    public static final String PASSWORD = "Hex-Fold-42-Marlin";
    public static final String DOMAIN_TYPE = "LOCAL";

    /** Page size the contract pins for listApplications. */
    public static final int PAGE_SIZE = 5;

    /** Authenticated requests each issued token will serve before it starts answering 401. */
    private static final int[] TOKEN_BUDGETS = {2, 3};

    /** Hard stop so a misbehaving client cannot spin forever. */
    private static final int MAX_REQUESTS = 200;

    private static final String BASE = "/api/ni";
    private static final String APPS = BASE + "/groups/applications";
    private static final String TOKEN = BASE + "/auth/token";

    private final HttpServer server;
    private final List<RecordedRequest> log = Collections.synchronizedList(new ArrayList<>());
    private final Map<String, int[]> tokenBudgets = Collections.synchronizedMap(new LinkedHashMap<>());
    private final List<String> issuedTokens = Collections.synchronizedList(new ArrayList<>());
    private final AtomicInteger sequence = new AtomicInteger();
    private final List<Map<String, Object>> applications = buildApplications();

    public MockNiServer() throws IOException {
        server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        server.createContext("/", this::dispatch);
        server.setExecutor(null);
    }

    public MockNiServer start() {
        server.start();
        return this;
    }

    @Override
    public void close() {
        server.stop(0);
    }

    /** Loopback base URL, e.g. {@code http://127.0.0.1:53124}. */
    public String baseUrl() {
        return "http://" + server.getAddress().getAddress().getHostAddress() + ":"
                + server.getAddress().getPort();
    }

    /** The ordered request log, oldest first. */
    public List<RecordedRequest> requests() {
        synchronized (log) {
            return List.copyOf(log);
        }
    }

    /** Tokens handed out by {@code create}, in issue order. */
    public List<String> issuedTokens() {
        synchronized (issuedTokens) {
            return List.copyOf(issuedTokens);
        }
    }

    /** The fixture inventory the client is expected to reproduce. */
    public List<Map<String, Object>> applications() {
        return applications;
    }

    /** Writes the request log as JSON Lines for post-mortem inspection. */
    public void writeLog(Path target) throws IOException {
        StringBuilder sb = new StringBuilder();
        for (RecordedRequest r : requests()) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("sequence", r.sequence());
            row.put("operation_id", r.operationId());
            row.put("method", r.method());
            row.put("path", r.path());
            row.put("raw_query", r.rawQuery());
            row.put("authorization", r.authorizationHeader());
            row.put("content_type", r.contentTypeHeader());
            row.put("body", r.body());
            row.put("status", r.responseStatus());
            sb.append(Json.write(row)).append('\n');
        }
        Path parent = target.getParent();
        if (parent != null) {
            Files.createDirectories(parent);
        }
        Files.writeString(target, sb.toString(), StandardCharsets.UTF_8);
    }

    // ------------------------------------------------------------- dispatch

    private void dispatch(HttpExchange exchange) throws IOException {
        int seq = sequence.incrementAndGet();
        String method = exchange.getRequestMethod();
        String path = exchange.getRequestURI().getPath();
        String rawQuery = exchange.getRequestURI().getRawQuery();
        String auth = exchange.getRequestHeaders().getFirst("Authorization");
        String contentType = exchange.getRequestHeaders().getFirst("Content-Type");
        String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);

        Response response;
        String operationId;
        try {
            if (seq > MAX_REQUESTS) {
                operationId = "unknown";
                response = error(429, "Request budget exhausted; the client is not converging.");
            } else {
                Routed routed = route(method, path);
                operationId = routed.operationId();
                response = routed.handler() == null
                        ? error(404, "No operation in the pinned contract matches " + method + " " + path)
                        : routed.handler().handle(rawQuery, auth, body, routed.pathParam());
            }
        } catch (RuntimeException e) {
            operationId = "unknown";
            response = error(500, "Mock failure: " + e);
        }

        log.add(new RecordedRequest(seq, method, path, rawQuery, auth, contentType, body,
                response.status(), operationId));

        byte[] payload = response.body() == null
                ? new byte[0]
                : response.body().getBytes(StandardCharsets.UTF_8);
        if (payload.length > 0) {
            exchange.getResponseHeaders().set("Content-Type", "application/json");
        }
        exchange.sendResponseHeaders(response.status(), payload.length == 0 ? -1 : payload.length);
        if (payload.length > 0) {
            try (OutputStream out = exchange.getResponseBody()) {
                out.write(payload);
            }
        } else {
            exchange.close();
        }
    }

    private Routed route(String method, String path) {
        if (TOKEN.equals(path) && "POST".equals(method)) {
            return new Routed("create", null, (q, a, b, p) -> createToken(b));
        }
        if (TOKEN.equals(path) && "DELETE".equals(method)) {
            return new Routed("delete", null, (q, a, b, p) -> deleteToken(a));
        }
        if (APPS.equals(path) && "GET".equals(method)) {
            return new Routed("listApplications", null, (q, a, b, p) -> listApplications(q, a));
        }
        if (path.startsWith(APPS + "/") && "GET".equals(method)) {
            // HttpExchange#getRequestURI already percent-decodes the path.
            String id = path.substring((APPS + "/").length());
            return new Routed("getApplicationById", id, (q, a, b, p) -> getApplication(p, a));
        }
        return new Routed("unknown", null, null);
    }

    // ----------------------------------------------------------- operations

    /** operationId: create -- POST /api/ni/auth/token */
    private Response createToken(String body) {
        Map<String, Object> parsed;
        try {
            parsed = Json.parseObject(body);
        } catch (RuntimeException e) {
            return error(400, "Malformed UserCredential body: " + e.getMessage());
        }
        String username = Json.stringAt(parsed, "username");
        String password = Json.stringAt(parsed, "password");
        Map<String, Object> domain = Json.objectAt(parsed, "domain");
        String domainType = domain == null ? null : Json.stringAt(domain, "domain_type");

        if (!USERNAME.equals(username) || !PASSWORD.equals(password)
                || !DOMAIN_TYPE.equals(domainType)) {
            return error(401, "Invalid credentials.");
        }

        String token = "tok-" + (issuedTokens.size() + 1) + "-"
                + Base64.getEncoder().withoutPadding()
                        .encodeToString(("ni" + (issuedTokens.size() + 1)).getBytes(StandardCharsets.UTF_8));
        int budget = issuedTokens.size() < TOKEN_BUDGETS.length
                ? TOKEN_BUDGETS[issuedTokens.size()]
                : Integer.MAX_VALUE;
        issuedTokens.add(token);
        tokenBudgets.put(token, new int[] {budget});

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("token", token);
        // Advisory only: the appliance revokes on its own schedule, so 401 is the real signal.
        out.put("expiry", 4102444800000L);
        return new Response(200, Json.write(out));
    }

    /** operationId: delete -- DELETE /api/ni/auth/token */
    private Response deleteToken(String auth) {
        Response denial = authorize(auth);
        if (denial != null) {
            return denial;
        }
        tokenBudgets.remove(tokenOf(auth));
        return new Response(204, null);
    }

    /** operationId: listApplications -- GET /api/ni/groups/applications */
    private Response listApplications(String rawQuery, String auth) {
        Response denial = authorize(auth);
        if (denial != null) {
            return denial;
        }
        Map<String, String> params = parseQuery(rawQuery);

        int size = PAGE_SIZE;
        if (params.containsKey("size")) {
            try {
                size = (int) Double.parseDouble(params.get("size"));
            } catch (RuntimeException e) {
                return error(400, "Invalid 'size' parameter: " + params.get("size"));
            }
        }
        if (size <= 0) {
            return error(400, "'size' must be positive.");
        }

        int offset = 0;
        if (params.containsKey("cursor")) {
            String cursor = params.get("cursor");
            if (cursor == null || cursor.isEmpty()) {
                return error(400, "'cursor' was sent with no value; omit the parameter instead.");
            }
            try {
                offset = Integer.parseInt(
                        new String(Base64.getDecoder().decode(cursor), StandardCharsets.UTF_8));
            } catch (RuntimeException e) {
                return error(400, "Unrecognised cursor: " + cursor);
            }
        }
        if (offset < 0 || offset > applications.size()) {
            return error(400, "Cursor is out of range.");
        }

        int end = Math.min(offset + size, applications.size());
        List<Object> results = new ArrayList<>();
        for (Map<String, Object> app : applications.subList(offset, end)) {
            Map<String, Object> entity = new LinkedHashMap<>();
            entity.put("entity_id", app.get("entity_id"));
            entity.put("entity_type", app.get("entity_type"));
            entity.put("entity_name", app.get("name"));
            results.add(entity);
        }

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("results", results);
        if (end < applications.size()) {
            out.put("cursor", Base64.getEncoder()
                    .encodeToString(String.valueOf(end).getBytes(StandardCharsets.UTF_8)));
        }
        out.put("total_count", applications.size());
        return new Response(200, Json.write(out));
    }

    /** operationId: getApplicationById -- GET /api/ni/groups/applications/{id} */
    private Response getApplication(String id, String auth) {
        Response denial = authorize(auth);
        if (denial != null) {
            return denial;
        }
        for (Map<String, Object> app : applications) {
            if (app.get("entity_id").equals(id)) {
                return new Response(200, Json.write(app));
            }
        }
        return error(404, "No application with entity_id " + id);
    }

    // -------------------------------------------------------------- helpers

    /** Returns a 401 response when the Authorization header is missing, malformed or spent. */
    private Response authorize(String auth) {
        if (auth == null || !auth.startsWith("NetworkInsight ")) {
            return error(401, "Missing or malformed Authorization header; expected "
                    + "'NetworkInsight {token}'.");
        }
        String token = tokenOf(auth);
        int[] budget = tokenBudgets.get(token);
        if (budget == null) {
            return error(401, "Unknown or already deleted token.");
        }
        if (budget[0] <= 0) {
            return error(401, "The auth token has expired. Acquire a new one.");
        }
        if (budget[0] != Integer.MAX_VALUE) {
            budget[0]--;
        }
        return null;
    }

    private static String tokenOf(String auth) {
        return auth.substring("NetworkInsight ".length());
    }

    private static Map<String, String> parseQuery(String rawQuery) {
        Map<String, String> out = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return out;
        }
        for (String pair : rawQuery.split("&", -1)) {
            if (pair.isEmpty()) {
                continue;
            }
            int eq = pair.indexOf('=');
            if (eq < 0) {
                out.put(urlDecode(pair), null);
            } else {
                out.put(urlDecode(pair.substring(0, eq)), urlDecode(pair.substring(eq + 1)));
            }
        }
        return out;
    }

    private static String urlDecode(String s) {
        return java.net.URLDecoder.decode(s, StandardCharsets.UTF_8);
    }

    private static Response error(int status, String message) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("code", status);
        out.put("message", message);
        return new Response(status, Json.write(out));
    }

    private static List<Map<String, Object>> buildApplications() {
        String[][] fixture = {
            {"18230:561:271275765", "edge-billing", "4", "112"},
            {"18230:561:271275766", "hr-workday-proxy", "2", "18"},
            {"18230:561:271275767", "payments-core", "6", "240"},
            {"18230:561:271275768", "claims-intake", "3", "57"},
            {"18230:561:271275769", "fraud-scoring", "5", "88"},
            {"18230:561:271275770", "customer-portal", "4", "196"},
            {"18230:561:271275771", "batch-reconcile", "2", "31"},
            {"18230:561:271275772", "telemetry-sink", "3", "74"},
            {"18230:561:271275773", "identity-broker", "5", "129"},
            {"18230:561:271275774", "partner-gateway", "2", "45"},
            {"18230:561:271275775", "reporting-warehouse", "7", "310"},
            {"18230:561:271275776", "legacy-mainframe-bridge", "1", "9"},
        };
        List<Map<String, Object>> out = new ArrayList<>();
        for (String[] row : fixture) {
            Map<String, Object> app = new LinkedHashMap<>();
            app.put("entity_id", row[0]);
            app.put("name", row[1]);
            app.put("entity_type", "Application");
            app.put("tier_count", Integer.parseInt(row[2]));
            app.put("member_count", Integer.parseInt(row[3]));
            out.add(Collections.unmodifiableMap(app));
        }
        return Collections.unmodifiableList(out);
    }

    private record Response(int status, String body) {}

    private record Routed(String operationId, String pathParam, Handler handler) {}

    @FunctionalInterface
    private interface Handler {
        Response handle(String rawQuery, String auth, String body, String pathParam);
    }
}
