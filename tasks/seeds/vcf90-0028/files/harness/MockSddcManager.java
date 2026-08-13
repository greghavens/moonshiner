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
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Loopback stand-in for an SDDC Manager appliance, pinned to {@code docs/contract.json}: its route
 * table and its request body validation are both built from that file at startup, so it serves
 * exactly the three operations the contract names and rejects anything else with 404.
 *
 * <p>Every request is appended to a log that is written to {@code out/requests.json} on shutdown,
 * together with the final appliance state in {@code out/state.json}.
 *
 * <p>The harness selects one deterministic create behavior for each scenario: commit then answer
 * 502, reject once or always with a retryable 503 without committing, or reject permanently with
 * 400.
 *
 * <p>Harness file. Do not modify.
 */
final class MockSddcManager {

    enum CreateBehavior {
        COMMIT_THEN_502,
        REJECT_503_ONCE,
        REJECT_503_ALWAYS,
        REJECT_400
    }

    private final Map<String, Object> contract;
    private final Map<String, Map<String, Object>> schemas = new LinkedHashMap<>();
    private final Map<String, Map<String, Object>> routes = new LinkedHashMap<>();
    private final Path outDir;
    private final CreateBehavior createBehavior;

    private final List<Object> requestLog = new ArrayList<>();
    private final List<Map<String, Object>> pools = new ArrayList<>();
    private final List<String> issuedTokens = new ArrayList<>();

    private int requestSeq;
    private int createAttempts;
    private int poolSeq;
    private HttpServer server;
    private ExecutorService executor;

    @SuppressWarnings("unchecked")
    MockSddcManager(Path contractPath, Path outDir) throws IOException {
        this(contractPath, outDir, CreateBehavior.COMMIT_THEN_502);
    }

    @SuppressWarnings("unchecked")
    MockSddcManager(Path contractPath, Path outDir, CreateBehavior createBehavior)
            throws IOException {
        this.outDir = outDir;
        this.createBehavior = createBehavior;
        this.contract = MiniJson.parseObject(Files.readString(contractPath, StandardCharsets.UTF_8));
        Map<String, Object> rawSchemas = (Map<String, Object>) contract.get("schemas");
        for (Map.Entry<String, Object> e : rawSchemas.entrySet()) {
            schemas.put(e.getKey(), (Map<String, Object>) e.getValue());
        }
        Map<String, Object> ops = (Map<String, Object>) contract.get("operations");
        for (Object value : ops.values()) {
            Map<String, Object> op = (Map<String, Object>) value;
            routes.put(op.get("method") + " " + op.get("path"), op);
        }
    }

    int start() throws IOException {
        server = HttpServer.create(
                new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        server.createContext("/", this::handle);
        // Single threaded so that the request log order is the order the client made its calls.
        executor = Executors.newSingleThreadExecutor();
        server.setExecutor(executor);
        server.start();
        return server.getAddress().getPort();
    }

    void stopAndDump() throws IOException {
        if (server != null) {
            server.stop(0);
        }
        if (executor != null) {
            executor.shutdownNow();
        }
        Files.createDirectories(outDir);
        Files.writeString(outDir.resolve("requests.json"), MiniJson.write(requestLog, true),
                StandardCharsets.UTF_8);
        Map<String, Object> state = new LinkedHashMap<>();
        state.put("networkPools", pools);
        state.put("createNetworkPoolAttempts", (long) createAttempts);
        state.put("accessTokensIssued", issuedTokens);
        Files.writeString(outDir.resolve("state.json"), MiniJson.write(state, true),
                StandardCharsets.UTF_8);
    }

    // ---------------------------------------------------------------- routing

    private void handle(HttpExchange ex) throws IOException {
        String method = ex.getRequestMethod();
        String path = ex.getRequestURI().getRawPath();
        byte[] bodyBytes;
        try (InputStream in = ex.getRequestBody()) {
            bodyBytes = in.readAllBytes();
        }
        String body = bodyBytes.length == 0 ? null : new String(bodyBytes, StandardCharsets.UTF_8);

        Map<String, Object> entry = new LinkedHashMap<>();
        entry.put("seq", (long) (++requestSeq));
        entry.put("method", method);
        entry.put("path", path);
        entry.put("query", ex.getRequestURI().getRawQuery());
        Map<String, Object> headers = new LinkedHashMap<>();
        headers.put("content-type", ex.getRequestHeaders().getFirst("Content-Type"));
        headers.put("accept", ex.getRequestHeaders().getFirst("Accept"));
        headers.put("authorization", ex.getRequestHeaders().getFirst("Authorization"));
        entry.put("headers", headers);
        entry.put("body", body);

        Map<String, Object> op = routes.get(method + " " + path);
        entry.put("matchedOperationId", op == null ? null : op.get("operationId"));
        requestLog.add(entry);

        try {
            if (op == null) {
                respond(ex, entry, 404, error("NOT_FOUND", "SDDC_MANAGER",
                        "No operation is served at " + method + " " + path
                                + "; this mock serves only the operations named in the contract."));
                return;
            }
            switch (String.valueOf(op.get("operationId"))) {
                case "createToken" -> createToken(ex, entry, body);
                case "getNetworkPool" -> getNetworkPool(ex, entry);
                case "createNetworkPool" -> createNetworkPool(ex, entry, body);
                default -> respond(ex, entry, 500, error("NOT_IMPLEMENTED", "SDDC_MANAGER",
                        "Unhandled operation " + op.get("operationId")));
            }
        } catch (RuntimeException t) {
            respond(ex, entry, 500, error("INTERNAL_SERVER_ERROR", "SDDC_MANAGER",
                    "Mock failure: " + t));
        }
    }

    // ------------------------------------------------------------ operations

    private void createToken(HttpExchange ex, Map<String, Object> entry, String body)
            throws IOException {
        Map<String, Object> parsed = parseBody(ex, entry, body, "TokenCreationSpec");
        if (parsed == null) {
            return;
        }
        Object user = parsed.get("username");
        Object pass = parsed.get("password");
        if (!(user instanceof String) || !(pass instanceof String)
                || ((String) user).isEmpty() || ((String) pass).isEmpty()) {
            respond(ex, entry, 400, error("INVALID_CREDENTIALS", "TOKEN",
                    "A username and a password are required to create a token pair."));
            return;
        }
        if (!Fixture.USERNAME.equals(user) || !Fixture.PASSWORD.equals(pass)) {
            respond(ex, entry, 400, error("INVALID_CREDENTIALS", "TOKEN",
                    "The supplied credentials were rejected by the appliance."));
            return;
        }
        String token = "mock-access-token-" + (issuedTokens.size() + 1);
        issuedTokens.add(token);
        entry.put("issuedAccessToken", token);
        Map<String, Object> refresh = new LinkedHashMap<>();
        refresh.put("id", "mock-refresh-token-" + issuedTokens.size());
        Map<String, Object> pair = new LinkedHashMap<>();
        pair.put("accessToken", token);
        pair.put("refreshToken", refresh);
        respond(ex, entry, 201, pair);
    }

    private void getNetworkPool(HttpExchange ex, Map<String, Object> entry) throws IOException {
        if (!authorized(ex, entry)) {
            return;
        }
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("pageNumber", 0L);
        metadata.put("pageSize", (long) pools.size());
        metadata.put("totalElements", (long) pools.size());
        metadata.put("totalPages", 1L);
        Map<String, Object> page = new LinkedHashMap<>();
        page.put("elements", new ArrayList<>(pools));
        page.put("pageMetadata", metadata);
        respond(ex, entry, 200, page);
    }

    private void createNetworkPool(HttpExchange ex, Map<String, Object> entry, String body)
            throws IOException {
        if (!authorized(ex, entry)) {
            return;
        }
        Map<String, Object> parsed = parseBody(ex, entry, body, "NetworkPool");
        if (parsed == null) {
            return;
        }
        createAttempts++;
        if (createBehavior == CreateBehavior.REJECT_400) {
            respond(ex, entry, 400, error("NETWORK_POOL_REJECTED", "SDDC_MANAGER",
                    "The requested network pool was permanently rejected."));
            return;
        }
        if (createBehavior == CreateBehavior.REJECT_503_ALWAYS
                || (createBehavior == CreateBehavior.REJECT_503_ONCE && createAttempts == 1)) {
            respond(ex, entry, 503, error("SERVICE_UNAVAILABLE", "SDDC_MANAGER",
                    "The appliance is temporarily unavailable; no pool was committed."));
            return;
        }
        Map<String, Object> created = materialize(parsed);
        pools.add(created);
        if (createBehavior == CreateBehavior.COMMIT_THEN_502 && createAttempts == 1) {
            // The pool is committed, but the response never reaches the caller.
            respond(ex, entry, 502, error("GATEWAY_TIMEOUT", "SDDC_MANAGER",
                    "The gateway timed out while waiting for the appliance response."));
            return;
        }
        respond(ex, entry, 201, created);
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> materialize(Map<String, Object> requested) {
        String id = String.format("np-%04d", ++poolSeq);
        Map<String, Object> pool = new LinkedHashMap<>();
        pool.put("id", id);
        pool.put("name", requested.get("name"));
        List<Object> networks = new ArrayList<>();
        int n = 0;
        for (Object raw : (List<Object>) requested.get("networks")) {
            Map<String, Object> in = (Map<String, Object>) raw;
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("id", id + "-net-" + (++n));
            out.put("type", in.get("type"));
            out.put("vlanId", in.get("vlanId"));
            out.put("mtu", in.get("mtu"));
            out.put("subnet", in.get("subnet"));
            out.put("mask", in.get("mask"));
            out.put("gateway", in.get("gateway"));
            if (in.containsKey("ipPools")) {
                out.put("ipPools", in.get("ipPools"));
            }
            out.put("freeIps", List.of());
            out.put("usedIps", List.of());
            networks.add(out);
        }
        pool.put("networks", networks);
        pool.put("hostsCount", 0L);
        return pool;
    }

    // -------------------------------------------------- contract enforcement

    private boolean authorized(HttpExchange ex, Map<String, Object> entry) throws IOException {
        String header = ex.getRequestHeaders().getFirst("Authorization");
        if (header != null && header.startsWith("Bearer ")
                && issuedTokens.contains(header.substring("Bearer ".length()))) {
            return true;
        }
        respond(ex, entry, 401, error("UNAUTHORIZED", "SDDC_MANAGER",
                "A valid 'Authorization: Bearer <accessToken>' header is required; obtain the "
                        + "access token from the createToken operation."));
        return false;
    }

    /** Parses and contract-validates a request body, answering 400 and returning null on failure. */
    private Map<String, Object> parseBody(HttpExchange ex, Map<String, Object> entry, String body,
                                          String schemaName) throws IOException {
        if (body == null || body.isBlank()) {
            respond(ex, entry, 400, error("REQUEST_BODY_MISSING", "SDDC_MANAGER",
                    "A JSON request body conforming to " + schemaName + " is required."));
            return null;
        }
        Object parsed;
        try {
            parsed = MiniJson.parse(body);
        } catch (RuntimeException e) {
            respond(ex, entry, 400, error("MALFORMED_REQUEST_BODY", "SDDC_MANAGER",
                    "The request body is not valid JSON: " + e.getMessage()));
            return null;
        }
        List<String> problems = new ArrayList<>();
        validate(schemaName, parsed, "$", problems);
        if (!problems.isEmpty()) {
            Map<String, Object> err = error("INVALID_REQUEST_BODY", "SDDC_MANAGER",
                    "The request body violates the " + schemaName + " schema.");
            List<Object> causes = new ArrayList<>();
            for (String problem : problems) {
                Map<String, Object> cause = new LinkedHashMap<>();
                cause.put("type", "SCHEMA_VIOLATION");
                cause.put("message", problem);
                causes.add(cause);
            }
            err.put("causes", causes);
            respond(ex, entry, 400, err);
            return null;
        }
        @SuppressWarnings("unchecked")
        Map<String, Object> map = (Map<String, Object>) parsed;
        return map;
    }

    @SuppressWarnings("unchecked")
    private void validate(String schemaName, Object value, String path, List<String> problems) {
        Map<String, Object> schema = schemas.get(schemaName);
        if (schema == null) {
            problems.add(path + ": unknown schema " + schemaName);
            return;
        }
        if (!(value instanceof Map)) {
            problems.add(path + ": expected a JSON object (" + schemaName + ") but found "
                    + MiniJson.typeName(value));
            return;
        }
        Map<String, Object> object = (Map<String, Object>) value;
        Map<String, Object> properties = (Map<String, Object>) schema.get("properties");
        List<Object> required = (List<Object>) schema.get("required");
        List<Object> readOnly = (List<Object>) schema.get("readOnlyProperties");

        for (Object name : required) {
            if (!object.containsKey(name) || object.get(name) == null) {
                problems.add(path + "." + name + ": required property of " + schemaName
                        + " is missing or null");
            }
        }
        for (Map.Entry<String, Object> e : object.entrySet()) {
            String key = e.getKey();
            if (!properties.containsKey(key)) {
                problems.add(path + "." + key + ": " + schemaName
                        + " has no such property in this specification revision");
                continue;
            }
            if (readOnly.contains(key)) {
                problems.add(path + "." + key + ": property is read-only and is owned by the "
                        + "appliance; it must not be sent in a request");
                continue;
            }
            if (e.getValue() == null) {
                continue;
            }
            checkType(properties.get(key), e.getValue(), path + "." + key, problems);
        }
    }

    @SuppressWarnings("unchecked")
    private void checkType(Object propertySchema, Object value, String path,
                           List<String> problems) {
        Map<String, Object> ps = (Map<String, Object>) propertySchema;
        String type = String.valueOf(ps.get("type"));
        switch (type) {
            case "string" -> {
                if (!(value instanceof String)) {
                    problems.add(path + ": expected a JSON string but found "
                            + MiniJson.typeName(value));
                } else if (ps.get("pattern") instanceof String pattern
                        && !((String) value).matches(pattern)) {
                    problems.add(path + ": '" + value + "' does not match " + pattern);
                }
            }
            case "integer" -> {
                if (!(value instanceof Number)) {
                    problems.add(path + ": expected a JSON number but found "
                            + MiniJson.typeName(value)
                            + "; integer properties must not be quoted");
                }
            }
            case "boolean" -> {
                if (!(value instanceof Boolean)) {
                    problems.add(path + ": expected a JSON boolean but found "
                            + MiniJson.typeName(value));
                }
            }
            case "array" -> {
                if (!(value instanceof List)) {
                    problems.add(path + ": expected a JSON array but found "
                            + MiniJson.typeName(value));
                    return;
                }
                Map<String, Object> items = (Map<String, Object>) ps.get("items");
                List<Object> list = (List<Object>) value;
                for (int i = 0; i < list.size(); i++) {
                    if (items != null && items.get("schema") != null) {
                        validate(String.valueOf(items.get("schema")), list.get(i),
                                path + "[" + i + "]", problems);
                    } else if (items != null) {
                        checkType(items, list.get(i), path + "[" + i + "]", problems);
                    }
                }
            }
            case "object" -> {
                if (ps.get("schema") != null) {
                    validate(String.valueOf(ps.get("schema")), value, path, problems);
                } else if (!(value instanceof Map)) {
                    problems.add(path + ": expected a JSON object but found "
                            + MiniJson.typeName(value));
                }
            }
            default -> {
                // No further constraint expressed by the contract.
            }
        }
    }

    // ------------------------------------------------------------- responding

    private static Map<String, Object> error(String errorCode, String component, String message) {
        Map<String, Object> context = new LinkedHashMap<>();
        context.put("component", component);
        Map<String, Object> err = new LinkedHashMap<>();
        err.put("errorCode", errorCode);
        err.put("errorType", "MOCK");
        err.put("context", context);
        err.put("message", message);
        err.put("referenceToken", "MOCK-REF");
        return err;
    }

    private void respond(HttpExchange ex, Map<String, Object> entry, int status, Object payload)
            throws IOException {
        entry.put("responseStatus", (long) status);
        String rendered = MiniJson.write(payload);
        entry.put("responseBody", rendered);
        byte[] bytes = rendered.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().set("Content-Type", "application/json");
        ex.sendResponseHeaders(status, bytes.length);
        try (OutputStream out = ex.getResponseBody()) {
            out.write(bytes);
        }
    }
}
