package harness;

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
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Loopback mock of the VCF Operations for Networks API, pinned to {@code docs/contract.json}.
 *
 * <p>The route table is built from the contract at construction time: only the operations the
 * contract names are served, and every other method/path pair answers 404. Request bodies are
 * validated against the contract's declared properties, so a body that carries an unknown field,
 * omits a required field, or sends an unset optional field as null / "" / {} / [] is rejected
 * with 400 instead of being quietly accepted.
 *
 * <p>Binds to 127.0.0.1 on an ephemeral port. Every request is appended to a JSONL request log
 * that the tests and the verifier read back.
 *
 * <p>Harness file. Do not modify.
 */
public final class MockVcfOnServer {

    /** Token handed out by {@code create}; deliberately contains base64 padding and a '+'. */
    public static final String TOKEN = "Mgs2YX0ZSY+gHW6RYypeeA==";
    public static final long TOKEN_EXPIRY = 1605201960327L;

    public static final String API_USERNAME = "admin@local";
    public static final String API_PASSWORD = "Vcf9!Networks";

    /** Targets whose precheck is answered with an in-body failure code. */
    private static final Map<String, String> PRECHECK_FAILURES = Map.of(
            "vc-bad.rainpole.local",
            "Validation failed: the supplied credentials were rejected by the vCenter Server.");

    private final Map<String, Object> contract;
    private final String basePath;
    private final Map<String, Route> routes = new LinkedHashMap<>();
    private final List<Object> log = Collections.synchronizedList(new ArrayList<>());
    private final List<Object> created = Collections.synchronizedList(new ArrayList<>());
    private final Path logPath;
    private HttpServer server;
    private int seq = 0;
    private int nextEntityId = 993642895;

    private record Route(String operationId, boolean authenticated, Map<String, Object> spec) {}

    public MockVcfOnServer(Map<String, Object> contract, Path logPath) {
        this.contract = contract;
        this.logPath = logPath;
        this.basePath = Json.str(contract.get("base_path"));
        for (Object o : Json.arr(contract.get("operations"))) {
            Map<String, Object> op = Json.obj(o);
            String key = Json.str(op.get("method")) + " " + basePath + Json.str(op.get("path"));
            routes.put(key, new Route(Json.str(op.get("operationId")),
                    Boolean.TRUE.equals(op.get("authenticated")), op));
        }
    }

    public String start() throws IOException {
        server = HttpServer.create(new InetSocketAddress(
                InetAddress.getByAddress(new byte[] {127, 0, 0, 1}), 0), 0);
        server.createContext("/", this::handle);
        server.setExecutor(null);
        server.start();
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    public void stop() throws IOException {
        if (server != null) server.stop(0);
        StringBuilder sb = new StringBuilder();
        for (Object entry : log) sb.append(Json.write(entry)).append('\n');
        Files.createDirectories(logPath.getParent());
        Files.writeString(logPath, sb.toString(), StandardCharsets.UTF_8);
    }

    /** Data sources this server actually created, in creation order. */
    public List<Object> createdDataSources() {
        return new ArrayList<>(created);
    }

    public List<Object> requestLog() {
        return new ArrayList<>(log);
    }

    // ------------------------------------------------------------- dispatching

    private void handle(HttpExchange ex) throws IOException {
        String method = ex.getRequestMethod();
        String path = ex.getRequestURI().getPath();
        String query = ex.getRequestURI().getRawQuery();
        String authorization = ex.getRequestHeaders().getFirst("Authorization");
        String contentType = ex.getRequestHeaders().getFirst("Content-Type");
        String rawBody;
        try (InputStream in = ex.getRequestBody()) {
            rawBody = new String(in.readAllBytes(), StandardCharsets.UTF_8);
        }

        Route route = routes.get(method + " " + path);
        Object parsedBody = Json.NULL;
        String parseError = null;
        if (!rawBody.isEmpty()) {
            try {
                parsedBody = Json.parse(rawBody);
            } catch (RuntimeException e) {
                parseError = e.getMessage();
            }
        }

        Response response;
        try {
            response = route == null
                    ? new Response(404, error(404, "No such operation in the pinned contract: "
                            + method + " " + path))
                    : serve(route, authorization, contentType, rawBody, parsedBody, parseError);
        } catch (RuntimeException e) {
            response = new Response(500, error(500, "mock failure: " + e));
        }

        Map<String, Object> entry = new LinkedHashMap<>();
        entry.put("seq", (long) (++seq));
        entry.put("operation_id", route == null ? Json.NULL : route.operationId());
        entry.put("method", method);
        entry.put("path", path);
        entry.put("query", query == null ? Json.NULL : query);
        entry.put("authorization", authorization == null ? Json.NULL : authorization);
        entry.put("content_type", contentType == null ? Json.NULL : contentType);
        entry.put("body_raw", rawBody);
        entry.put("body", parseError == null ? parsedBody : Json.NULL);
        entry.put("body_parse_error", parseError == null ? Json.NULL : parseError);
        entry.put("status", (long) response.status());
        entry.put("response_body", response.body());
        log.add(entry);

        byte[] out = Json.write(response.body()).getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().add("Content-Type", "application/json");
        ex.sendResponseHeaders(response.status(), out.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(out);
        }
    }

    private record Response(int status, Object body) {}

    private Response serve(Route route, String authorization, String contentType,
                           String rawBody, Object parsedBody, String parseError) {
        if (route.authenticated()) {
            if (authorization == null) {
                return new Response(401, error(401, "Missing Authorization header."));
            }
            if (!authorization.equals("NetworkInsight " + TOKEN)) {
                return new Response(401, error(401,
                        "Authorization header must be 'NetworkInsight {token}' with a valid token."));
            }
        } else if (authorization != null) {
            return new Response(400, error(400, "Operation '" + route.operationId()
                    + "' is declared with an empty security requirement and must be called"
                    + " without an Authorization header."));
        }

        Map<String, Object> requestProps = Json.obj(route.spec().get("request_properties"));
        if (requestProps == null) {
            if (!rawBody.isEmpty()) {
                return new Response(400, error(400, "Operation '" + route.operationId()
                        + "' does not accept a request body."));
            }
        } else {
            if (parseError != null) {
                return new Response(400, error(400, "Malformed JSON body: " + parseError));
            }
            if (rawBody.isEmpty()) {
                return new Response(400, error(400, "A request body is required."));
            }
            if (contentType == null || !contentType.toLowerCase().startsWith("application/json")) {
                return new Response(415, error(415,
                        "Content-Type must be application/json, got: " + contentType));
            }
            Map<String, Object> body = Json.obj(parsedBody);
            if (body == null) {
                return new Response(400, error(400, "Request body must be a JSON object."));
            }
            String problem = validate(route, body, requestProps, "");
            if (problem != null) return new Response(400, error(400, problem));
        }

        return switch (route.operationId()) {
            case "create" -> createToken(Json.obj(parsedBody));
            case "listExpandedNodes" -> new Response(200, nodes());
            case "validateVCenter" -> validateVcenter(Json.obj(parsedBody));
            case "addVcenterDatasource" -> addVcenter(Json.obj(parsedBody));
            default -> new Response(500, error(500, "unrouted operation " + route.operationId()));
        };
    }

    // ------------------------------------------------------- contract checking

    private String validate(Route route, Map<String, Object> body,
                            Map<String, Object> props, String prefix) {
        for (Map.Entry<String, Object> e : body.entrySet()) {
            String name = prefix + e.getKey();
            Map<String, Object> prop = Json.obj(props.get(e.getKey()));
            if (prop == null) {
                return "Unknown field '" + name + "' is not part of "
                        + route.spec().get("request_schema") + ".";
            }
            Object v = e.getValue();
            if (v == Json.NULL) {
                return "Field '" + name + "' was sent as null; an unset optional field must be"
                        + " omitted from the request body.";
            }
            if (v instanceof String s && s.isEmpty()) {
                return "Field '" + name + "' was sent as an empty string; an unset optional field"
                        + " must be omitted from the request body.";
            }
            if (v instanceof Map<?, ?> m && m.isEmpty()) {
                return "Field '" + name + "' was sent as an empty object; an unset optional field"
                        + " must be omitted from the request body.";
            }
            if (v instanceof List<?> l && l.isEmpty()) {
                return "Field '" + name + "' was sent as an empty array; an unset optional field"
                        + " must be omitted from the request body.";
            }
            String declared = Json.str(prop.get("type"));
            if (declared != null && !declared.equals(Json.kindOf(v))) {
                return "Field '" + name + "' must be a " + declared + ", got " + Json.kindOf(v) + ".";
            }
            List<Object> enumeration = Json.arr(prop.get("enum"));
            if (enumeration != null && !enumeration.contains(v)) {
                return "Field '" + name + "' must be one of " + Json.write(enumeration) + ".";
            }
            String nested = Json.str(prop.get("schema"));
            if (nested != null) {
                Map<String, Object> nestedProps =
                        Json.obj(Json.obj(route.spec().get("request_nested_schemas")).get(nested));
                String problem = validate(route, Json.obj(v), nestedProps, name + ".");
                if (problem != null) return problem;
            }
        }
        for (Map.Entry<String, Object> e : props.entrySet()) {
            if (Boolean.TRUE.equals(Json.obj(e.getValue()).get("required"))
                    && !body.containsKey(e.getKey())) {
                return "Required field '" + prefix + e.getKey() + "' is missing.";
            }
        }
        if (prefix.isEmpty()) {
            List<Object> groups = Json.arr(route.spec().get("request_exactly_one_of"));
            if (groups != null) {
                for (Object g : groups) {
                    List<Object> group = Json.arr(g);
                    List<String> present = new ArrayList<>();
                    for (Object k : group) if (body.containsKey(Json.str(k))) present.add(Json.str(k));
                    if (present.size() != 1) {
                        return "Exactly one of " + Json.write(group) + " must be present, found "
                                + present + ".";
                    }
                }
            }
        }
        return null;
    }

    // ------------------------------------------------------------- operations

    private Response createToken(Map<String, Object> body) {
        if (!API_USERNAME.equals(body.get("username")) || !API_PASSWORD.equals(body.get("password"))) {
            return new Response(401, error(401, "Invalid username or password."));
        }
        Map<String, Object> domain = Json.obj(body.get("domain"));
        if (domain != null && "LOCAL".equals(domain.get("domain_type")) && domain.containsKey("value")) {
            return new Response(400, error(400,
                    "domain.value is not applicable to the LOCAL domain type and must be omitted."));
        }
        return new Response(200, Json.map("token", TOKEN, "expiry", TOKEN_EXPIRY));
    }

    private Object nodes() {
        List<Object> results = new ArrayList<>();
        results.add(node("18230:901:1585583463", "PLATFORM_VM", "OZ4YB5TQ2XG1HGWJ0M1EQ3XKPA",
                "10.220.232.210", "Platform_10.220.232.210", false, 1667887284891L, 1668156689312L));
        results.add(node("18230:901:1706494033", "PROXY_VM", "I8T7CR167RJRVY1FAY74HYFDCZ",
                "10.220.232.214", "Collector_10.220.232.214", false, 1667887301442L, 1668156689318L));
        results.add(node("18230:901:1706494077", "PROXY_VM", "K2QW9LM4TTZ8B0V6RH3NCXPD1E",
                "10.220.232.219", "Collector_10.220.232.219 (\"DC2\", rack \\ 4)",
                true, 1667887399015L, 1668156689401L));
        return Json.map("results", results, "total_count", (long) results.size());
    }

    private Object node(String id, String nodeType, String nodeId, String ip, String name,
                        boolean physicalFlowCollector, long registered, long updated) {
        Map<String, Object> n = new LinkedHashMap<>();
        n.put("id", id);
        n.put("entity_type", "NODE");
        n.put("node_type", nodeType);
        n.put("node_id", nodeId);
        n.put("ip_address", ip);
        n.put("ipv6_address", Json.NULL);
        n.put("name", name);
        n.put("is_physical_flow_collector", physicalFlowCollector);
        n.put("version", "9.1.0.1668532499");
        n.put("health", Json.map("health_status", "HEALTHY", "health_details",
                List.of(Json.map("message", "SUCCEEDED", "code", "0"))));
        n.put("registered_timestamp", registered);
        n.put("last_updated_timestamp", updated);
        return n;
    }

    private Set<String> nodeIds() {
        Set<String> ids = new LinkedHashSet<>();
        for (Object o : Json.arr(Json.obj(nodes()).get("results"))) ids.add(Json.str(Json.obj(o).get("id")));
        return ids;
    }

    private Response validateVcenter(Map<String, Object> body) {
        String proxyId = Json.str(body.get("proxy_id"));
        if (!nodeIds().contains(proxyId)) {
            return new Response(200, Json.map("code", 400L,
                    "message", "Unknown proxy_id '" + proxyId + "'."));
        }
        String target = body.containsKey("ip") ? Json.str(body.get("ip")) : Json.str(body.get("fqdn"));
        String failure = PRECHECK_FAILURES.get(target);
        if (failure != null) {
            return new Response(200, Json.map("code", 400L, "message", failure));
        }
        return new Response(200, Json.map("code", 200L, "message", "Validation successful."));
    }

    private Response addVcenter(Map<String, Object> body) {
        String proxyId = Json.str(body.get("proxy_id"));
        if (!nodeIds().contains(proxyId)) {
            return new Response(400, error(400, "Unknown proxy_id '" + proxyId + "'."));
        }
        Map<String, Object> ds = new LinkedHashMap<>();
        ds.put("entity_id", "18230:902:" + (nextEntityId++));
        ds.put("entity_type", "VCenterDataSource");
        ds.put("ip", body.containsKey("ip") ? body.get("ip") : Json.NULL);
        ds.put("fqdn", body.containsKey("fqdn") ? body.get("fqdn") : Json.NULL);
        ds.put("proxy_id", proxyId);
        ds.put("nickname", body.get("nickname"));
        ds.put("enabled", body.containsKey("enabled") ? body.get("enabled") : Boolean.TRUE);
        ds.put("notes", body.containsKey("notes") ? body.get("notes") : Json.NULL);
        Map<String, Object> creds = Json.obj(body.get("credentials"));
        ds.put("credentials", creds == null ? Json.NULL
                : Json.map("username", creds.get("username"), "password", ""));
        created.add(ds);
        return new Response(201, ds);
    }

    private Object error(int code, String message) {
        return Json.map("code", (long) code, "message", message);
    }
}
