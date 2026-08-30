package com.broadcom.vcf.lab.harness;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.Executors;

/**
 * A loopback-only stand-in for a vCenter Server appliance, wired directly to {@code
 * docs/contract.json}. The routing table, the accepted query parameter names and the accepted
 * request body properties are all read out of the contract at start-up, so the mock can only ever
 * serve the four operations the contract names; anything else answers 404.
 *
 * <p>The mock hands out session tokens whose lifetime is measured in authenticated requests. The
 * first token it issues is deliberately short lived, so a run that does more work than that token
 * covers will be interrupted by a 401 part way through and has to recover.
 *
 * <p>Every exchange is appended to a request log that the verifier reads afterwards.
 *
 * <p>Part of the protected harness: do not modify.
 */
public final class MockVcenter implements AutoCloseable {

    /** Number of authenticated requests the very first session token is good for. */
    public static final int FIRST_TOKEN_BUDGET = 3;

    public static final String USERNAME = "svc-fanout@vsphere.local";
    public static final String PASSWORD = "Ch4ng3Me!fanout";

    public static final String SOURCE_VM_NAME = "golden-rhel9-base";
    public static final String SOURCE_VM_ID = "vm-1001";

    private static final String SESSION_HEADER = "vmware-api-session-id";

    private final Map<String, Object> contract;
    private final String basePath;
    private final List<Route> routes = new ArrayList<>();
    private final Set<String> vmListParams;
    private final Set<String> cloneSpecProperties;
    private final Set<String> clonePlacementProperties;
    private final Set<String> cloneSpecRequired;

    private final HttpServer server;
    private final List<RequestRecord> log = Collections.synchronizedList(new ArrayList<>());

    /** token -> authenticated requests still allowed on it; a token drops out when it is deleted. */
    private final Map<String, Integer> tokens = new LinkedHashMap<>();
    private final List<String> issuedTokens = new ArrayList<>();
    private final Map<String, String> inventory = new LinkedHashMap<>(); // vm id -> name
    private int cloneCounter = 0;

    public MockVcenter(Path contractFile) throws IOException {
        this.contract = Json.parseObject(Files.readString(contractFile, StandardCharsets.UTF_8));
        this.basePath = str(map(contract, "server"), "base_path");

        for (Object o : list(contract, "operations")) {
            Map<String, Object> op = asMap(o);
            routes.add(new Route(
                    str(op, "operationId"),
                    str(op, "method"),
                    basePath + str(op, "path"),
                    (String) op.get("action_query")));
        }

        Set<String> params = new LinkedHashSet<>();
        for (Object o : list(operation("Vcenter.VM_list"), "query_parameters")) {
            params.add(str(asMap(o), "name"));
        }
        this.vmListParams = Collections.unmodifiableSet(params);

        Map<String, Object> schemas = map(contract, "schemas");
        Map<String, Object> cloneSpec = asMap(schemas.get("Vcenter.VM.CloneSpec"));
        this.cloneSpecProperties = Collections.unmodifiableSet(
                new LinkedHashSet<>(map(cloneSpec, "properties").keySet()));
        this.cloneSpecRequired = Collections.unmodifiableSet(new LinkedHashSet<>(strings(cloneSpec, "required")));
        this.clonePlacementProperties = Collections.unmodifiableSet(new LinkedHashSet<>(
                map(asMap(schemas.get("Vcenter.VM.ClonePlacementSpec")), "properties").keySet()));

        inventory.put(SOURCE_VM_ID, SOURCE_VM_NAME);
        inventory.put("vm-1042", "prod-web-01");
        inventory.put("vm-1043", "prod-db-01");
        inventory.put("vm-1044", "golden-rhel9-base-archive");

        this.server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        this.server.createContext("/", this::dispatch);
        this.server.setExecutor(Executors.newSingleThreadExecutor(r -> {
            Thread t = new Thread(r, "mock-vcenter");
            t.setDaemon(true);
            return t;
        }));
        this.server.start();
    }

    public String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort() + basePath;
    }

    public List<RequestRecord> requestLog() {
        synchronized (log) {
            return List.copyOf(log);
        }
    }

    /** Session tokens the mock handed out, in the order it issued them. */
    public List<String> issuedTokens() {
        synchronized (issuedTokens) {
            return List.copyOf(issuedTokens);
        }
    }

    /** Virtual machines that exist on the mock now, as an id -> name map in creation order. */
    public Map<String, String> inventory() {
        return new LinkedHashMap<>(inventory);
    }

    @Override
    public void close() {
        server.stop(0);
    }

    // ------------------------------------------------------------------ dispatch

    private void dispatch(HttpExchange exchange) throws IOException {
        String path = exchange.getRequestURI().getPath();
        String rawQuery = exchange.getRequestURI().getRawQuery();
        Map<String, List<String>> query = parseQuery(rawQuery);
        Map<String, String> headers = new LinkedHashMap<>();
        exchange.getRequestHeaders().forEach((k, v) -> {
            if (!v.isEmpty()) headers.put(k.toLowerCase(Locale.ROOT), v.get(0));
        });
        String body = readBody(exchange);
        String method = exchange.getRequestMethod().toUpperCase(Locale.ROOT);

        Route route = match(method, path, query);
        Reply reply;
        try {
            reply = route == null ? notFound() : handle(route, headers, query, body);
        } catch (RuntimeException e) {
            reply = error(500, "ERROR", "mock failure: " + e);
        }

        synchronized (log) {
            log.add(new RequestRecord(log.size() + 1, method, path, rawQuery, query, headers, body,
                    route == null ? null : route.operationId, reply.status));
        }
        send(exchange, reply);
    }

    private Reply handle(Route route, Map<String, String> headers,
                         Map<String, List<String>> query, String body) {
        switch (route.operationId) {
            case "Cis.Session_create": return sessionCreate(headers, body);
            case "Cis.Session_delete": return sessionDelete(headers);
            case "Vcenter.VM_list": return vmList(headers, query);
            case "Vcenter.VM_clone": return vmClone(headers, body);
            default: return notFound();
        }
    }

    private Reply sessionCreate(Map<String, String> headers, String body) {
        if (!body.isEmpty()) {
            return error(400, "INVALID_ARGUMENT", "Cis.Session_create declares no request body.");
        }
        String auth = headers.get("authorization");
        if (auth == null || !auth.regionMatches(true, 0, "Basic ", 0, 6)) {
            return unauthenticated("Basic credentials are required to create a session.");
        }
        String decoded;
        try {
            decoded = new String(Base64.getDecoder().decode(auth.substring(6).trim()), StandardCharsets.UTF_8);
        } catch (IllegalArgumentException e) {
            return unauthenticated("The authorization header is not valid base64.");
        }
        int split = decoded.indexOf(':');
        if (split < 0 || !decoded.substring(0, split).equals(USERNAME)
                || !decoded.substring(split + 1).equals(PASSWORD)) {
            return unauthenticated("Cannot authenticate user.");
        }
        String token;
        synchronized (issuedTokens) {
            token = String.format("vmw-sess-%04d-a1b2c3", issuedTokens.size() + 1);
            issuedTokens.add(token);
            tokens.put(token, issuedTokens.size() == 1 ? FIRST_TOKEN_BUDGET : Integer.MAX_VALUE);
        }
        return json(201, token);
    }

    private Reply sessionDelete(Map<String, String> headers) {
        Reply denied = authenticate(headers);
        if (denied != null) return denied;
        tokens.remove(headers.get(SESSION_HEADER));
        return new Reply(204, null, null);
    }

    private Reply vmList(Map<String, String> headers, Map<String, List<String>> query) {
        Reply denied = authenticate(headers);
        if (denied != null) return denied;
        for (String key : query.keySet()) {
            if (!vmListParams.contains(key)) {
                return error(400, "INVALID_ARGUMENT",
                        "Unexpected query parameter '" + key + "' for Vcenter.VM_list.");
            }
        }
        List<String> ids = query.get("vms");
        List<String> names = query.get("names");
        List<Object> out = new ArrayList<>();
        for (Map.Entry<String, String> vm : inventory.entrySet()) {
            if (ids != null && !ids.isEmpty() && !ids.contains(vm.getKey())) continue;
            if (names != null && !names.isEmpty() && !names.contains(vm.getValue())) continue;
            Map<String, Object> summary = new LinkedHashMap<>();
            summary.put("vm", vm.getKey());
            summary.put("name", vm.getValue());
            summary.put("power_state", "POWERED_OFF");
            summary.put("cpu_count", 4L);
            summary.put("memory_size_mib", 8192L);
            out.add(summary);
        }
        return json(200, out);
    }

    private Reply vmClone(Map<String, String> headers, String body) {
        Reply denied = authenticate(headers);
        if (denied != null) return denied;

        Map<String, Object> spec;
        try {
            spec = Json.parseObject(body);
        } catch (RuntimeException e) {
            return error(400, "INVALID_ARGUMENT", "Request body is not a JSON object: " + e.getMessage());
        }
        for (String key : spec.keySet()) {
            if (!cloneSpecProperties.contains(key)) {
                return error(400, "INVALID_ARGUMENT",
                        "'" + key + "' is not a property of Vcenter.VM.CloneSpec.");
            }
        }
        for (String required : cloneSpecRequired) {
            Object v = spec.get(required);
            if (!(v instanceof String) || ((String) v).isEmpty()) {
                return error(400, "INVALID_ARGUMENT",
                        "Vcenter.VM.CloneSpec.'" + required + "' is required and must be a non-empty string.");
            }
        }
        Object placement = spec.get("placement");
        if (placement != null) {
            if (!(placement instanceof Map)) {
                return error(400, "INVALID_ARGUMENT",
                        "Vcenter.VM.CloneSpec.placement must be a Vcenter.VM.ClonePlacementSpec object.");
            }
            for (Object key : ((Map<?, ?>) placement).keySet()) {
                if (!clonePlacementProperties.contains(String.valueOf(key))) {
                    return error(400, "INVALID_ARGUMENT",
                            "'" + key + "' is not a property of Vcenter.VM.ClonePlacementSpec.");
                }
            }
        }
        Object powerOn = spec.get("power_on");
        if (spec.containsKey("power_on") && powerOn != null && !(powerOn instanceof Boolean)) {
            return error(400, "INVALID_ARGUMENT", "Vcenter.VM.CloneSpec.power_on must be a boolean.");
        }

        String source = (String) spec.get("source");
        if (!inventory.containsKey(source)) {
            return error(404, "NOT_FOUND", "No virtual machine with identifier '" + source + "'.");
        }
        String name = (String) spec.get("name");
        if (inventory.containsValue(name)) {
            return error(400, "ALREADY_EXISTS", "A virtual machine named '" + name + "' already exists.");
        }
        String id = "vm-" + (2001 + cloneCounter++);
        inventory.put(id, name);
        return json(200, id);
    }

    private Reply authenticate(Map<String, String> headers) {
        String token = headers.get(SESSION_HEADER);
        if (token == null || token.isEmpty()) {
            return unauthenticated("The session id is missing from the request.");
        }
        Integer remaining = tokens.get(token);
        if (remaining == null) {
            return unauthenticated("The session id does not identify a known session.");
        }
        if (remaining <= 0) {
            return unauthenticated("The session identified by the request has expired.");
        }
        if (remaining != Integer.MAX_VALUE) {
            tokens.put(token, remaining - 1);
        }
        return null;
    }

    // ------------------------------------------------------------------ routing

    private Route match(String method, String path, Map<String, List<String>> query) {
        List<String> action = query.get("action");
        String actionValue = (action == null || action.size() != 1) ? null : action.get(0);
        for (Route r : routes) {
            if (!r.method.equals(method) || !r.path.equals(path)) continue;
            if (r.action == null) {
                if (actionValue == null) return r;
            } else if (r.action.equals(actionValue)) {
                return r;
            }
        }
        return null;
    }

    private static final class Route {
        final String operationId;
        final String method;
        final String path;
        final String action;

        Route(String operationId, String method, String path, String action) {
            this.operationId = operationId;
            this.method = method;
            this.path = path;
            this.action = action;
        }
    }

    // ------------------------------------------------------------------ replies

    private static final class Reply {
        final int status;
        final String contentType;
        final byte[] body;

        Reply(int status, String contentType, byte[] body) {
            this.status = status;
            this.contentType = contentType;
            this.body = body;
        }
    }

    private static Reply json(int status, Object value) {
        return new Reply(status, "application/json", Json.write(value).getBytes(StandardCharsets.UTF_8));
    }

    private static Reply error(int status, String errorType, String message) {
        Map<String, Object> msg = new LinkedHashMap<>();
        msg.put("id", "com.vmware.vapi.std.errors." + errorType.toLowerCase(Locale.ROOT));
        msg.put("default_message", message);
        msg.put("args", List.of());
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("error_type", errorType);
        payload.put("messages", List.of(msg));
        return json(status, payload);
    }

    private static Reply unauthenticated(String message) {
        return error(401, "UNAUTHENTICATED", message);
    }

    private static Reply notFound() {
        return error(404, "NOT_FOUND", "This mock serves only the operations named in docs/contract.json.");
    }

    private static void send(HttpExchange exchange, Reply reply) throws IOException {
        if (reply.body == null) {
            exchange.sendResponseHeaders(reply.status, -1);
            exchange.close();
            return;
        }
        exchange.getResponseHeaders().set("Content-Type", reply.contentType);
        exchange.sendResponseHeaders(reply.status, reply.body.length);
        try (OutputStream out = exchange.getResponseBody()) {
            out.write(reply.body);
        }
    }

    // ------------------------------------------------------------------ small helpers

    private static String readBody(HttpExchange exchange) throws IOException {
        try (InputStream in = exchange.getRequestBody()) {
            return new String(in.readAllBytes(), StandardCharsets.UTF_8);
        }
    }

    private static Map<String, List<String>> parseQuery(String rawQuery) {
        Map<String, List<String>> out = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) return out;
        for (String pair : rawQuery.split("&", -1)) {
            if (pair.isEmpty()) continue;
            int eq = pair.indexOf('=');
            String key = eq < 0 ? pair : pair.substring(0, eq);
            String value = eq < 0 ? "" : pair.substring(eq + 1);
            out.computeIfAbsent(decode(key), k -> new ArrayList<>()).add(decode(value));
        }
        return out;
    }

    private static String decode(String s) {
        return URLDecoder.decode(s, StandardCharsets.UTF_8);
    }

    private Map<String, Object> operation(String operationId) {
        for (Object o : list(contract, "operations")) {
            Map<String, Object> op = asMap(o);
            if (operationId.equals(op.get("operationId"))) return op;
        }
        throw new IllegalStateException("docs/contract.json does not describe " + operationId);
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> asMap(Object o) {
        if (!(o instanceof Map)) throw new IllegalStateException("expected an object, got " + Json.typeName(o));
        return (Map<String, Object>) o;
    }

    static Map<String, Object> map(Map<String, Object> parent, String key) {
        return asMap(parent.get(key));
    }

    static List<Object> list(Map<String, Object> parent, String key) {
        Object o = parent.get(key);
        if (!(o instanceof List)) throw new IllegalStateException("expected an array at '" + key + "'");
        @SuppressWarnings("unchecked") List<Object> l = (List<Object>) o;
        return l;
    }

    static List<String> strings(Map<String, Object> parent, String key) {
        List<String> out = new ArrayList<>();
        for (Object o : list(parent, key)) out.add(String.valueOf(o));
        return out;
    }

    static String str(Map<String, Object> parent, String key) {
        Object o = parent.get(key);
        if (!(o instanceof String)) throw new IllegalStateException("expected a string at '" + key + "'");
        return (String) o;
    }
}
