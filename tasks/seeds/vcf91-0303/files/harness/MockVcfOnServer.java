import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.ExecutorService;

/**
 * A loopback stand-in for a VCF Operations for Networks appliance, pinned to docs/contract.json.
 *
 * It binds 127.0.0.1 on an ephemeral port and serves exactly the three operations the contract
 * names -- 'create', 'listApplications' and 'getApplicationById'. Every other method+path pair is
 * answered 404 with an ApiError body. Contract violations are answered 4xx with a message naming
 * the rule that was broken, so a client under development gets an immediate, readable signal.
 *
 * Every request is appended to an in-memory log -- method, raw path, raw query, parsed query
 * parameters, the headers that matter, the body, and the status it was answered with -- and the
 * log is flushed to JSON Lines by writeRequestLog(). The verifier reads that file; it is the
 * record of what actually went over the wire.
 *
 * Requests are handled on a single thread, so log order is arrival order.
 *
 * DO NOT MODIFY.
 */
public final class MockVcfOnServer {

    private static final String BASE = "/api/ni";
    private static final String LIST_PATH = BASE + "/groups/applications";
    private static final String TOKEN_PATH = BASE + "/auth/token";
    private static final String DETAIL_PREFIX = LIST_PATH + "/";
    private static final String AUTH_SCHEME = "NetworkInsight ";

    private final List<Map<String, Object>> applications = new ArrayList<>();
    private final Map<String, Map<String, Object>> byId = new LinkedHashMap<>();
    private final Map<Integer, String> cursorByOffset = new LinkedHashMap<>();
    private final Map<String, Integer> offsetByCursor = new LinkedHashMap<>();
    private final String terminalCursorStyle;

    private final String expectedUsername;
    private final String expectedPassword;
    private final Map<String, Object> expectedDomain; // null when the client must omit 'domain'
    private final Long expectedModifiedAfter;          // null when the client must omit it

    private final List<Map<String, Object>> requestLog = new ArrayList<>();
    private final String issuedToken;

    private HttpServer server;
    private ExecutorService executor;

    public MockVcfOnServer(Path fixture,
                           String expectedUsername,
                           String expectedPassword,
                           Map<String, Object> expectedDomain,
                           Long expectedModifiedAfter) throws IOException {
        Map<String, Object> data = MiniJson.parseObject(
                Files.readString(fixture, StandardCharsets.UTF_8));
        this.terminalCursorStyle = String.valueOf(data.get("terminal_cursor"));
        this.issuedToken = String.valueOf(data.get("token"));
        for (Map.Entry<String, Object> cursor : asMap(data.get("page_cursors")).entrySet()) {
            int offset = Integer.parseInt(cursor.getKey());
            String value = String.valueOf(cursor.getValue());
            cursorByOffset.put(offset, value);
            offsetByCursor.put(value, offset);
        }
        for (Object raw : asList(data.get("applications"))) {
            Map<String, Object> app = asMap(raw);
            applications.add(app);
            byId.put(String.valueOf(app.get("entity_id")), app);
        }
        this.expectedUsername = expectedUsername;
        this.expectedPassword = expectedPassword;
        this.expectedDomain = expectedDomain;
        this.expectedModifiedAfter = expectedModifiedAfter;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> asList(Object o) {
        return (List<Object>) o;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> asMap(Object o) {
        return (Map<String, Object>) o;
    }

    public String start() throws IOException {
        server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        executor = Executors.newSingleThreadExecutor();
        server.setExecutor(executor);
        server.createContext("/", this::dispatch);
        server.start();
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    public void stop() {
        if (server != null) {
            server.stop(0);
        }
        if (executor != null) {
            executor.shutdownNow();
        }
    }

    public void writeRequestLog(Path target) throws IOException {
        StringBuilder sb = new StringBuilder();
        for (Map<String, Object> entry : requestLog) {
            sb.append(MiniJson.write(entry)).append('\n');
        }
        Files.writeString(target, sb.toString(), StandardCharsets.UTF_8);
    }

    // ------------------------------------------------------------- dispatch

    private void dispatch(HttpExchange exchange) throws IOException {
        Map<String, Object> entry = new LinkedHashMap<>();
        try {
            String method = exchange.getRequestMethod();
            String rawPath = exchange.getRequestURI().getRawPath();
            String rawQuery = exchange.getRequestURI().getRawQuery();
            byte[] bodyBytes = readAll(exchange.getRequestBody());
            String body = new String(bodyBytes, StandardCharsets.UTF_8);

            entry.put("seq", (long) requestLog.size());
            entry.put("method", method);
            entry.put("raw_path", rawPath);
            entry.put("raw_query", rawQuery == null ? "" : rawQuery);
            entry.put("has_query_delimiter", rawQuery != null);

            QueryParse query;
            try {
                query = QueryParse.of(rawQuery);
            } catch (ContractViolation cv) {
                entry.put("query_params", new LinkedHashMap<String, Object>());
                entry.put("query_keys", new ArrayList<String>());
                recordHeaders(exchange, entry);
                entry.put("body", body);
                respond(exchange, entry, cv.status, cv.getMessage());
                return;
            }
            entry.put("query_params", query.single);
            entry.put("query_keys", new ArrayList<>(query.single.keySet()));
            recordHeaders(exchange, entry);
            entry.put("body", body);

            route(exchange, entry, method, rawPath, query, body);
        } catch (ContractViolation cv) {
            respond(exchange, entry, cv.status, cv.getMessage());
        } catch (RuntimeException ex) {
            respond(exchange, entry, 500, "mock failure: " + ex);
        } finally {
            if (!entry.isEmpty()) {
                requestLog.add(entry);
            }
            exchange.close();
        }
    }

    private void route(HttpExchange exchange,
                       Map<String, Object> entry,
                       String method,
                       String rawPath,
                       QueryParse query,
                       String body) throws IOException {
        if (TOKEN_PATH.equals(rawPath)) {
            requireMethod(method, "POST");
            entry.put("operation_id", "create");
            handleToken(exchange, entry, query, body);
            return;
        }
        if (LIST_PATH.equals(rawPath)) {
            requireMethod(method, "GET");
            entry.put("operation_id", "listApplications");
            handleList(exchange, entry, query);
            return;
        }
        if (rawPath.startsWith(DETAIL_PREFIX)) {
            requireMethod(method, "GET");
            entry.put("operation_id", "getApplicationById");
            handleDetail(exchange, entry, rawPath.substring(DETAIL_PREFIX.length()), query);
            return;
        }
        throw new ContractViolation(404,
                "no operation in docs/contract.json serves " + method + " " + rawPath);
    }

    private void requireMethod(String actual, String expected) {
        if (!expected.equals(actual)) {
            throw new ContractViolation(405, "expected method " + expected + ", got " + actual);
        }
    }

    // ---------------------------------------------------------------- create

    private void handleToken(HttpExchange exchange,
                             Map<String, Object> entry,
                             QueryParse query,
                             String body) throws IOException {
        if (!query.single.isEmpty()) {
            throw new ContractViolation(400, "operation 'create' takes no query parameters, got "
                    + query.single.keySet());
        }
        if (exchange.getRequestHeaders().getFirst("Authorization") != null) {
            throw new ContractViolation(400,
                    "wire_rules.no_auth_header_on_token_request: 'create' must not send Authorization");
        }
        requireJsonContentType(exchange);

        Map<String, Object> credential;
        try {
            credential = MiniJson.parseObject(body);
        } catch (RuntimeException ex) {
            throw new ContractViolation(400, "request body is not a JSON object: " + ex.getMessage());
        }
        requireKeysWithin(credential.keySet(), List.of("username", "password", "domain"),
                "UserCredential");
        requireNonEmptyString(credential, "username", "UserCredential");
        requireNonEmptyString(credential, "password", "UserCredential");

        if (!expectedUsername.equals(credential.get("username"))
                || !expectedPassword.equals(credential.get("password"))) {
            throw new ContractViolation(401, "invalid credentials");
        }

        boolean domainPresent = credential.containsKey("domain");
        if (expectedDomain == null) {
            if (domainPresent) {
                throw new ContractViolation(400, "wire_rules.omit_unset_optionals: no domain is "
                        + "configured for this run, so 'domain' must be omitted from the body "
                        + "entirely (got " + MiniJson.write(credential.get("domain")) + ")");
            }
        } else {
            if (!domainPresent) {
                throw new ContractViolation(400, "a domain is configured for this run but 'domain' "
                        + "is missing from the body");
            }
            Object domainValue = credential.get("domain");
            if (!(domainValue instanceof Map)) {
                throw new ContractViolation(400, "'domain' must be a JSON object, got "
                        + MiniJson.write(domainValue));
            }
            Map<String, Object> domain = asMap(domainValue);
            requireKeysWithin(domain.keySet(), List.of("domain_type", "value"), "Domain");
            if (!expectedDomain.equals(domain)) {
                throw new ContractViolation(400, "wire_rules.omit_unset_optionals: expected domain "
                        + MiniJson.write(expectedDomain) + " with exactly those keys, got "
                        + MiniJson.write(domain));
            }
        }

        Map<String, Object> token = new LinkedHashMap<>();
        token.put("token", issuedToken);
        token.put("expiry", 1605201960327L);
        respondJson(exchange, entry, 200, token);
    }

    private void requireJsonContentType(HttpExchange exchange) {
        String contentType = exchange.getRequestHeaders().getFirst("Content-Type");
        if (contentType == null) {
            throw new ContractViolation(415, "wire_rules.content_type: missing Content-Type");
        }
        String normalised = contentType.toLowerCase(Locale.ROOT).split(";")[0].trim();
        if (!"application/json".equals(normalised)) {
            throw new ContractViolation(415,
                    "wire_rules.content_type: expected application/json, got " + contentType);
        }
    }

    private void requireNonEmptyString(Map<String, Object> object, String key, String schema) {
        Object value = object.get(key);
        if (!(value instanceof String) || ((String) value).isEmpty()) {
            throw new ContractViolation(400,
                    schema + "." + key + " must be a non-empty string, got " + MiniJson.write(value));
        }
    }

    private void requireKeysWithin(Iterable<String> actual, List<String> allowed, String schema) {
        for (String key : actual) {
            if (!allowed.contains(key)) {
                throw new ContractViolation(400,
                        schema + " has no property '" + key + "'; allowed: " + allowed);
            }
        }
    }

    // ------------------------------------------------------- listApplications

    private void handleList(HttpExchange exchange, Map<String, Object> entry, QueryParse query)
            throws IOException {
        requireToken(exchange);
        requireNoBody(exchange);
        for (String key : query.single.keySet()) {
            if (!List.of("size", "cursor", "modifiedAfter").contains(key)) {
                throw new ContractViolation(400, "operation 'listApplications' has no query "
                        + "parameter '" + key + "'; allowed: [size, cursor, modifiedAfter]");
            }
        }

        String sizeRaw = query.single.get("size");
        if (sizeRaw == null) {
            throw new ContractViolation(400, "this client must always send 'size' explicitly");
        }
        int size = parsePositiveInt(sizeRaw, "size");

        if (query.single.containsKey("modifiedAfter")) {
            if (expectedModifiedAfter == null) {
                throw new ContractViolation(400, "wire_rules.omit_unset_optionals: no "
                        + "modifiedAfter is configured for this run, so the parameter must be "
                        + "omitted entirely (got 'modifiedAfter=" + query.single.get("modifiedAfter") + "')");
            }
            long actual = parseLong(query.single.get("modifiedAfter"), "modifiedAfter");
            if (actual != expectedModifiedAfter) {
                throw new ContractViolation(400, "expected modifiedAfter=" + expectedModifiedAfter
                        + ", got " + actual);
            }
        } else if (expectedModifiedAfter != null) {
            throw new ContractViolation(400, "modifiedAfter=" + expectedModifiedAfter
                    + " is configured for this run and must be sent on every listApplications "
                    + "request, including cursor-bearing ones");
        }

        int offset = 0;
        if (query.single.containsKey("cursor")) {
            String cursor = query.single.get("cursor");
            if (cursor.isEmpty()) {
                throw new ContractViolation(400, "wire_rules.omit_unset_optionals: 'cursor' was "
                        + "sent empty; omit the parameter instead of sending 'cursor='");
            }
            offset = decodeCursor(cursor);
        }

        // Apply the modifiedAfter filter the parameter describes, then page the survivors.
        List<Map<String, Object>> visible = new ArrayList<>();
        for (Map<String, Object> app : applications) {
            if (expectedModifiedAfter != null) {
                long modified = ((Number) app.get("last_modified_time")).longValue();
                if (modified < expectedModifiedAfter) {
                    continue;
                }
            }
            visible.add(app);
        }

        if (offset > visible.size()) {
            throw new ContractViolation(400, "cursor points past the end of the collection");
        }
        int end = Math.min(offset + size, visible.size());

        List<Object> results = new ArrayList<>();
        for (Map<String, Object> app : visible.subList(offset, end)) {
            // PagedListResponse.results carries EntityId references only. This deployment does not
            // populate entity_name, matching the spec's own example for listApplications.
            Map<String, Object> ref = new LinkedHashMap<>();
            ref.put("entity_id", app.get("entity_id"));
            ref.put("entity_type", "Application");
            results.add(ref);
        }

        Map<String, Object> page = new LinkedHashMap<>();
        page.put("results", results);
        if (end < visible.size()) {
            String next = cursorByOffset.get(end);
            if (next == null) {
                throw new IllegalStateException(
                        "fixture has no opaque page cursor for offset " + end);
            }
            page.put("cursor", next);
        } else if ("empty".equals(terminalCursorStyle)) {
            page.put("cursor", "");
        }
        page.put("total_count", (long) visible.size());
        respondJson(exchange, entry, 200, page);
    }

    private int decodeCursor(String cursor) {
        Integer offset = offsetByCursor.get(cursor);
        if (offset == null) {
            throw new ContractViolation(400, "cursor '" + cursor + "' was not one this server "
                    + "issued; copy the previous response's opaque cursor verbatim");
        }
        return offset;
    }

    // ----------------------------------------------------- getApplicationById

    private void handleDetail(HttpExchange exchange,
                              Map<String, Object> entry,
                              String rawId,
                              QueryParse query) throws IOException {
        requireToken(exchange);
        requireNoBody(exchange);
        if (!query.single.isEmpty()) {
            throw new ContractViolation(400, "wire_rules.omit_unset_optionals: neither "
                    + "fetch_member_counts nor fetch_update_status is set in this scenario, so "
                    + "getApplicationById must be sent with no query string at all (got "
                    + query.single.keySet() + ")");
        }
        if (rawId.contains("%")) {
            throw new ContractViolation(404, "wire_rules.path_segments_verbatim: entity ids are "
                    + "sent verbatim; ':' is a legal path character and must not be "
                    + "percent-encoded (got '" + rawId + "')");
        }
        Map<String, Object> app = byId.get(rawId);
        if (app == null) {
            throw new ContractViolation(404, "no application with entity_id '" + rawId + "'");
        }
        entry.put("entity_id", rawId);

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("entity_id", app.get("entity_id"));
        out.put("name", app.get("name"));
        out.put("entity_type", "Application");
        out.put("create_time", app.get("create_time"));
        out.put("created_by", app.get("created_by"));
        out.put("last_modified_time", app.get("last_modified_time"));
        out.put("tier_count", app.get("tier_count"));
        respondJson(exchange, entry, 200, out);
    }

    // ----------------------------------------------------------------- shared

    private void requireToken(HttpExchange exchange) {
        String header = exchange.getRequestHeaders().getFirst("Authorization");
        if (header == null) {
            throw new ContractViolation(401,
                    "wire_rules.auth_header: missing Authorization header");
        }
        if (!header.startsWith(AUTH_SCHEME)) {
            throw new ContractViolation(401, "wire_rules.auth_header: expected 'NetworkInsight "
                    + "<token>' with exactly one space, got '" + header + "'");
        }
        String token = header.substring(AUTH_SCHEME.length());
        if (!issuedToken.equals(token)) {
            throw new ContractViolation(401, "token '" + token + "' was not issued by 'create'");
        }
    }

    private void requireNoBody(HttpExchange exchange) {
        if (exchange.getRequestHeaders().getFirst("Content-Type") != null) {
            throw new ContractViolation(400, "wire_rules.content_type: this GET carries no body, "
                    + "so it must not send a Content-Type header");
        }
    }

    private static int parsePositiveInt(String raw, String name) {
        int value;
        try {
            value = Integer.parseInt(raw);
        } catch (NumberFormatException ex) {
            throw new ContractViolation(400, name + " must be an integer, got '" + raw + "'");
        }
        if (value <= 0) {
            throw new ContractViolation(400, name + " must be positive, got " + value);
        }
        return value;
    }

    private static long parseLong(String raw, String name) {
        try {
            return Long.parseLong(raw);
        } catch (NumberFormatException ex) {
            throw new ContractViolation(400, name + " must be an integer, got '" + raw + "'");
        }
    }

    private void recordHeaders(HttpExchange exchange, Map<String, Object> entry) {
        Map<String, Object> headers = new LinkedHashMap<>();
        for (String name : List.of("Authorization", "Content-Type", "Accept")) {
            String value = exchange.getRequestHeaders().getFirst(name);
            if (value != null) {
                headers.put(name.toLowerCase(Locale.ROOT), value);
            }
        }
        entry.put("headers", headers);
    }

    private void respondJson(HttpExchange exchange,
                             Map<String, Object> entry,
                             int status,
                             Map<String, Object> payload) throws IOException {
        send(exchange, entry, status, MiniJson.write(payload));
    }

    private void respond(HttpExchange exchange, Map<String, Object> entry, int status, String message)
            throws IOException {
        Map<String, Object> error = new LinkedHashMap<>();
        error.put("code", (long) status);
        error.put("message", message);
        error.put("details", new ArrayList<>());
        entry.put("rejected_because", message);
        send(exchange, entry, status, MiniJson.write(error));
    }

    private void send(HttpExchange exchange, Map<String, Object> entry, int status, String payload)
            throws IOException {
        entry.put("status", (long) status);
        byte[] bytes = payload.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream os = exchange.getResponseBody()) {
            os.write(bytes);
        }
    }

    private static byte[] readAll(InputStream in) throws IOException {
        return in.readAllBytes();
    }

    /** A rejection carrying the HTTP status the mock answers with. */
    private static final class ContractViolation extends RuntimeException {
        final int status;

        ContractViolation(int status, String message) {
            super(message);
            this.status = status;
        }
    }

    /** Raw query-string parsing that refuses duplicate keys. */
    private static final class QueryParse {
        final Map<String, String> single = new LinkedHashMap<>();

        static QueryParse of(String rawQuery) {
            QueryParse out = new QueryParse();
            if (rawQuery == null || rawQuery.isEmpty()) {
                return out;
            }
            for (String pair : rawQuery.split("&", -1)) {
                if (pair.isEmpty()) {
                    throw new ContractViolation(400, "empty segment in query string '" + rawQuery + "'");
                }
                int eq = pair.indexOf('=');
                String key = eq < 0 ? pair : pair.substring(0, eq);
                String value = eq < 0 ? "" : urlDecode(pair.substring(eq + 1));
                if (out.single.containsKey(key)) {
                    throw new ContractViolation(400, "wire_rules.no_duplicate_query_keys: '" + key
                            + "' appears more than once in '" + rawQuery + "'");
                }
                out.single.put(urlDecode(key), value);
            }
            return out;
        }

        private static String urlDecode(String s) {
            return java.net.URLDecoder.decode(s, StandardCharsets.UTF_8);
        }
    }
}
