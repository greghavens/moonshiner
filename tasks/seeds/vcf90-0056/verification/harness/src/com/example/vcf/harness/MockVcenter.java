package com.example.vcf.harness;

import com.example.vcf.support.Json;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Loopback stand-in for a vCenter Server, pinned to {@code docs/contract.json}.
 *
 * <p>It binds to 127.0.0.1 on an ephemeral port and serves exactly one operation, {@code
 * Vcenter.Authorization.Roles_list} ({@code GET /api/vcenter/authorization/roles}). Every other
 * method or path is a 404. Requests that violate the contract are rejected with the error schemas
 * the specification declares for the operation.
 *
 * <p>Each exchange is appended to a request log as one JSON object per line, so the test can read
 * back exactly what went over the wire.
 */
public final class MockVcenter implements AutoCloseable {

    static final String OPERATION_PATH = "/api/vcenter/authorization/roles";

    /** The only query keys the contract permits. */
    private static final Set<String> ALLOWED_QUERY_KEYS = Set.of("is_system", "page_size", "marker");

    private final HttpServer server;
    private final Path requestLog;
    private final String sessionId;
    private final long defaultPageSize;
    private final List<Map<String, Object>> roles;
    private final Map<String, List<Integer>> pagePlans;
    private final Set<String> nullTerminalMarkerPlans;

    private String scenario = "unset";
    private int sequence;

    public MockVcenter(Path fixtures, Path requestLog) throws IOException {
        this.requestLog = requestLog;
        Files.createDirectories(requestLog.toAbsolutePath().getParent());
        Files.writeString(requestLog, "", StandardCharsets.UTF_8);

        Map<String, Object> fixture = Json.parseObject(Files.readString(fixtures, StandardCharsets.UTF_8));
        this.sessionId = Json.optString(fixture, "session_id");
        this.defaultPageSize = ((Number) fixture.get("default_page_size")).longValue();

        List<Map<String, Object>> parsed = new ArrayList<>();
        for (Object element : Json.requireArray(fixture, "roles")) {
            @SuppressWarnings("unchecked")
            Map<String, Object> role = (Map<String, Object>) element;
            parsed.add(role);
        }
        // The specification states the collection is returned in lexicographical order.
        parsed.sort(Comparator.comparing(role -> (String) role.get("role")));
        this.roles = List.copyOf(parsed);

        Map<String, List<Integer>> plans = new LinkedHashMap<>();
        for (Map.Entry<String, Object> entry : Json.requireObject(fixture, "page_plans").entrySet()) {
            List<Integer> sizes = new ArrayList<>();
            for (Object size : (List<?>) entry.getValue()) {
                sizes.add(((Number) size).intValue());
            }
            plans.put(entry.getKey(), List.copyOf(sizes));
        }
        this.pagePlans = Map.copyOf(plans);

        Set<String> nullMarkerPlans = new LinkedHashSet<>();
        for (Object plan : Json.requireArray(fixture, "null_terminal_marker_plans")) {
            nullMarkerPlans.add((String) plan);
        }
        this.nullTerminalMarkerPlans = Set.copyOf(nullMarkerPlans);

        this.server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        this.server.createContext("/", this::handle);
        this.server.setExecutor(null);
    }

    public void start() {
        server.start();
    }

    @Override
    public void close() {
        server.stop(0);
    }

    public String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort() + "/api";
    }

    public String sessionId() {
        return sessionId;
    }

    public List<Map<String, Object>> roles() {
        return roles;
    }

    /** Tags every subsequent log entry, so the verifier can group requests per scenario. */
    public void beginScenario(String name) {
        this.scenario = name;
        this.sequence = 0;
    }

    // ---------------------------------------------------------------- routing

    private void handle(HttpExchange exchange) throws IOException {
        Map<String, Object> record = new LinkedHashMap<>();
        record.put("scenario", scenario);
        record.put("seq", sequence++);
        record.put("method", exchange.getRequestMethod());
        record.put("path", exchange.getRequestURI().getPath());

        String rawQuery = exchange.getRequestURI().getRawQuery();
        record.put("rawQuery", rawQuery);

        Map<String, Object> headers = new LinkedHashMap<>();
        headers.put("vmware-api-session-id", exchange.getRequestHeaders().getFirst("vmware-api-session-id"));
        headers.put("accept", exchange.getRequestHeaders().getFirst("Accept"));
        headers.put("authorization", exchange.getRequestHeaders().getFirst("Authorization"));
        record.put("headers", headers);

        Response response;
        Map<String, List<String>> query = null;
        try {
            query = parseQuery(rawQuery);
            response = route(exchange, query);
        } catch (ContractViolation violation) {
            response = error(400, "com.vmware.vapi.std.errors.invalid_argument", violation.getMessage());
        }

        Map<String, Object> queryRecord = new LinkedHashMap<>();
        if (query != null) {
            queryRecord.putAll(query);
        }
        record.put("query", queryRecord);
        record.put("queryKeyCount", queryRecord.size());
        record.put("responseStatus", response.status);
        record.put("responseMarker", response.marker);
        record.put("responseItemCount", response.itemCount);
        appendLog(record);

        byte[] body = response.body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().add("Content-Type", "application/json");
        exchange.sendResponseHeaders(response.status, body.length);
        try (OutputStream out = exchange.getResponseBody()) {
            out.write(body);
        }
    }

    private Response route(HttpExchange exchange, Map<String, List<String>> query) {
        String path = exchange.getRequestURI().getPath();
        if (!OPERATION_PATH.equals(path)) {
            return error(404, "com.vmware.vapi.std.errors.not_found",
                    "no operation is served at " + path);
        }
        if (!"GET".equals(exchange.getRequestMethod())) {
            return error(404, "com.vmware.vapi.std.errors.not_found",
                    exchange.getRequestMethod() + " is not served at " + path);
        }
        String presented = exchange.getRequestHeaders().getFirst("vmware-api-session-id");
        if (presented == null || !presented.equals(sessionId)) {
            return error(401, "com.vmware.vapi.std.errors.unauthenticated",
                    "the vmware-api-session-id header is missing or does not identify a session");
        }
        return listRoles(query);
    }

    // ------------------------------------------------------- the one operation

    private Response listRoles(Map<String, List<String>> query) {
        Boolean isSystem = readBoolean(query, "is_system");
        Long pageSize = readLong(query, "page_size");
        String marker = readSingle(query, "marker");

        List<Map<String, Object>> matching = new ArrayList<>();
        for (Map<String, Object> role : roles) {
            @SuppressWarnings("unchecked")
            Map<String, Object> info = (Map<String, Object>) role.get("info");
            if (isSystem == null || isSystem.equals(info.get("system"))) {
                matching.add(role);
            }
        }

        String planKey = isSystem == null ? "all" : (isSystem ? "system" : "custom");
        List<Integer> plan;
        if (pageSize == null) {
            // The service default is an upper bound, not a promise: it may still return a short
            // page and a marker. The fixed default plan makes that contract rule observable even
            // when the caller leaves page_size unset.
            plan = pagePlans.get("default");
        } else {
            if (pageSize <= 0) {
                throw new ContractViolation("page_size must be a positive integer, got " + pageSize);
            }
            plan = pagePlans.get(planKey);
            if (plan == null) {
                throw new ContractViolation("no page plan is configured for filter '" + planKey + "'");
            }
        }

        int offset = decodeMarker(marker, planKey, plan, matching.size());
        int pageIndex = pageIndexForOffset(offset, plan);
        int count = pageIndex < plan.size() ? plan.get(pageIndex) : 0;
        int end = Math.min(offset + count, matching.size());

        List<Object> items = new ArrayList<>(matching.subList(offset, end));

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("items", items);
        String nextMarker = null;
        if (end < matching.size()) {
            nextMarker = encodeMarker(planKey, end);
            body.put("marker", nextMarker);
        } else if (nullTerminalMarkerPlans.contains(planKey)) {
            body.put("marker", null);
        }
        return new Response(200, Json.write(body), nextMarker, items.size());
    }

    private int pageIndexForOffset(int offset, List<Integer> plan) {
        int cursor = 0;
        for (int i = 0; i < plan.size(); i++) {
            if (cursor == offset) {
                return i;
            }
            cursor += plan.get(i);
        }
        return plan.size();
    }

    private String encodeMarker(String planKey, int offset) {
        // Deliberately contains reserved query characters. A client must treat the marker as an
        // opaque value and percent-encode it rather than splicing it into the URI.
        return "roles/" + planKey + "?offset=" + offset + "&opaque=+/%";
    }

    private int decodeMarker(String marker, String planKey, List<Integer> plan, int total) {
        if (marker == null) {
            return 0;
        }
        String expectedPrefix = "roles/" + planKey + "?offset=";
        String expectedSuffix = "&opaque=+/%";
        if (!marker.startsWith(expectedPrefix) || !marker.endsWith(expectedSuffix)) {
            throw new ContractViolation("marker '" + marker + "' does not belong to this result set");
        }
        int offset;
        try {
            offset = Integer.parseInt(marker.substring(
                    expectedPrefix.length(), marker.length() - expectedSuffix.length()));
        } catch (NumberFormatException e) {
            throw new ContractViolation("marker '" + marker + "' is malformed");
        }
        // A marker is only ever handed out at a page boundary strictly inside the collection.
        int cursor = 0;
        for (Integer size : plan) {
            cursor += size;
            if (cursor >= total) {
                break;
            }
            if (cursor == offset) {
                return offset;
            }
        }
        throw new ContractViolation("marker '" + marker + "' does not point at a page boundary");
    }

    // ------------------------------------------------------ query enforcement

    private Map<String, List<String>> parseQuery(String rawQuery) {
        Map<String, List<String>> parsed = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return parsed;
        }
        for (String pair : rawQuery.split("&", -1)) {
            if (pair.isEmpty()) {
                throw new ContractViolation("the query string contains an empty parameter");
            }
            int eq = pair.indexOf('=');
            String rawKey = eq < 0 ? pair : pair.substring(0, eq);
            String rawValue = eq < 0 ? null : pair.substring(eq + 1);
            String key = URLDecoder.decode(rawKey, StandardCharsets.UTF_8);
            if (!ALLOWED_QUERY_KEYS.contains(key)) {
                throw new ContractViolation("unexpected query parameter '" + key + "'");
            }
            if (rawValue == null) {
                throw new ContractViolation("query parameter '" + key + "' was sent without a value; "
                        + "unset optional properties must be omitted entirely");
            }
            String value = URLDecoder.decode(rawValue, StandardCharsets.UTF_8);
            if (value.isEmpty()) {
                throw new ContractViolation("query parameter '" + key + "' was sent with an empty value; "
                        + "unset optional properties must be omitted entirely");
            }
            if (parsed.containsKey(key)) {
                throw new ContractViolation("query parameter '" + key + "' was sent more than once");
            }
            parsed.put(key, List.of(value));
        }
        return parsed;
    }

    private String readSingle(Map<String, List<String>> query, String key) {
        List<String> values = query.get(key);
        return values == null ? null : values.get(0);
    }

    private Boolean readBoolean(Map<String, List<String>> query, String key) {
        String value = readSingle(query, key);
        if (value == null) {
            return null;
        }
        if ("true".equals(value)) {
            return Boolean.TRUE;
        }
        if ("false".equals(value)) {
            return Boolean.FALSE;
        }
        throw new ContractViolation("query parameter '" + key + "' must be 'true' or 'false', got '" + value + "'");
    }

    private Long readLong(Map<String, List<String>> query, String key) {
        String value = readSingle(query, key);
        if (value == null) {
            return null;
        }
        try {
            return Long.valueOf(value);
        } catch (NumberFormatException e) {
            throw new ContractViolation("query parameter '" + key + "' must be an integer, got '" + value + "'");
        }
    }

    // ------------------------------------------------------------- plumbing

    private Response error(int status, String type, String message) {
        Map<String, Object> localizable = new LinkedHashMap<>();
        localizable.put("id", type);
        localizable.put("default_message", message);
        localizable.put("args", List.of());

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("error_type", type.substring(type.lastIndexOf('.') + 1).toUpperCase(java.util.Locale.ROOT));
        body.put("messages", List.of(localizable));
        return new Response(status, Json.write(body), null, 0);
    }

    private void appendLog(Map<String, Object> record) throws IOException {
        try (BufferedWriter writer = Files.newBufferedWriter(requestLog, StandardCharsets.UTF_8,
                StandardOpenOption.CREATE, StandardOpenOption.APPEND)) {
            writer.write(Json.write(record));
            writer.newLine();
        }
    }

    /** Raised when a request breaches the contract; surfaced as 400 InvalidArgument. */
    private static final class ContractViolation extends RuntimeException {
        ContractViolation(String message) {
            super(message);
        }
    }

    private record Response(int status, String body, String marker, int itemCount) {
    }

    /** Distinct query keys seen across the log; used only for diagnostics. */
    static Set<String> keysOf(Map<String, Object> queryRecord) {
        return new LinkedHashSet<>(queryRecord.keySet());
    }
}
