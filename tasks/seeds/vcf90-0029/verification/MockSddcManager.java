import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.InputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Loopback stand-in for SDDC Manager 9.0, pinned to {@code docs/contract.json}.
 *
 * <p>The route table is built at startup from the contract itself: only the operations the
 * contract names are served, every other target answers 404. Every request that reaches the
 * server is appended to an in-memory request log that the test reads back verbatim.
 *
 * <p>No live VMware endpoint is contacted. The server binds an ephemeral port on 127.0.0.1.
 */
public final class MockSddcManager implements AutoCloseable {

    /** Which step of the capacity change the estate refuses. */
    public enum Scenario {
        /** Every step is accepted. */
        ALL_ACCEPTED,
        /** The IP pool addition against the VSAN network is refused with 400. */
        IP_POOL_REFUSED,
        /** Reading the newly created pool back is refused after creation has taken effect. */
        NETWORK_READ_REFUSED,
        /** Every step up to and including the IP pool additions is accepted; commissioning is refused with 400. */
        COMMISSION_REFUSED,
        /** Commissioning is refused, and the service's message repeats values the report must redact. */
        COMMISSION_REFUSED_WITH_SECRETS,
        /** Commissioning answers with a non-JSON body, which is not a contract Error response. */
        MALFORMED_COMMISSION_REFUSAL
    }

    public static final String ACCESS_TOKEN =
            "eyJhbGciOiJSUzI1NiJ9.bW9jay1zZGRjLW1hbmFnZXItOTAtYWNjZXNz.c2lnbmF0dXJl";
    public static final String REFRESH_TOKEN_ID = "0f19d3a4-6c85-4b1e-9d20-7a4c3f5be812";
    public static final String POOL_ID = "pool/Blue ü?x+y";
    public static final String TASK_ID = "1c8f7a20-52d6-4a93-bb14-9e07f2c4d5a6";
    public static final String ECHOED_SSO_PASSWORD = "VMw@re1!SsoSecret";
    public static final String ECHOED_ESX_PASSWORD = "VMw@re1!EsxSecret";
    public static final String SENSITIVE_API_KEY = "api-\"key\\ü";
    public static final String IP_POOL_ERROR_CODE = "NETWORK_POOL_IP_RANGE_OVERLAP";
    public static final String IP_POOL_REFERENCE_TOKEN = "K4X9QW";
    public static final String NETWORK_READ_ERROR_CODE = "NETWORK_POOL_READ_REFUSED";
    public static final String NETWORK_READ_REFERENCE_TOKEN = "R8D3NP";
    public static final String COMMISSION_ERROR_CODE = "HOST_ALREADY_COMMISSIONED";
    public static final String COMMISSION_REFERENCE_TOKEN = "P7M2ZB";

    private static final String NETWORK_ID_PREFIX = "3a9c50be-77d1-4e2b-8f6a-0c4b91d7ae";

    private final HttpServer server;
    private final Scenario scenario;
    private final List<Recorded> log = Collections.synchronizedList(new ArrayList<>());
    private final List<Route> routes;

    /** The pool this run created, or null before {@code createNetworkPool} is accepted. */
    private Map<String, Object> pool;
    private final Map<String, String> networkIdsByType = new LinkedHashMap<>();

    private MockSddcManager(Scenario scenario, Path contractPath) throws IOException {
        this.scenario = scenario;
        this.routes = loadRoutes(contractPath);
        this.server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        this.server.createContext("/", this::dispatch);
        this.server.setExecutor(null);
        this.server.start();
    }

    public static MockSddcManager start(Scenario scenario) throws IOException {
        return start(scenario, Path.of("docs", "contract.json"));
    }

    public static MockSddcManager start(Scenario scenario, Path contractPath) throws IOException {
        return new MockSddcManager(scenario, contractPath);
    }

    public String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    /** Every request the server saw, in arrival order. */
    public List<Recorded> requestLog() {
        synchronized (log) {
            return List.copyOf(log);
        }
    }

    /** The operationIds this mock is willing to serve, straight out of the contract. */
    public List<String> servedOperationIds() {
        List<String> ids = new ArrayList<>();
        for (Route route : routes) {
            ids.add(route.operationId);
        }
        return List.copyOf(ids);
    }

    /** The id the mock assigned to the network of the given type, or null if no pool was created. */
    public String networkId(String type) {
        return networkIdsByType.get(type);
    }

    /** True once {@code createNetworkPool} was accepted; the mock never rolls that back. */
    public boolean poolExists() {
        return pool != null;
    }

    /** Dumps the request log as JSON Lines, for debugging a failing run. */
    public void writeLog(Path destination) throws IOException {
        StringBuilder out = new StringBuilder();
        for (Recorded recorded : requestLog()) {
            out.append(recorded.toJson()).append('\n');
        }
        Files.writeString(destination, out.toString(), StandardCharsets.UTF_8);
    }

    @Override
    public void close() {
        server.stop(0);
    }

    // ---------------------------------------------------------------- routing

    private record Route(String operationId, String method, Pattern pattern, List<String> parameterNames) {}

    private static List<Route> loadRoutes(Path contractPath) throws IOException {
        Object parsed = Json.parse(Files.readString(contractPath, StandardCharsets.UTF_8));
        List<Object> operations = Json.list(Json.object(parsed).get("operations"));
        List<Route> loaded = new ArrayList<>();
        for (Object entry : operations) {
            Map<String, Object> operation = Json.object(entry);
            String template = Json.string(operation.get("path"));
            List<String> parameterNames = new ArrayList<>();
            StringBuilder regex = new StringBuilder();
            Matcher matcher = Pattern.compile("\\{([^}]+)}").matcher(template);
            int cursor = 0;
            while (matcher.find()) {
                regex.append(Pattern.quote(template.substring(cursor, matcher.start())));
                regex.append("([^/]+)");
                parameterNames.add(matcher.group(1));
                cursor = matcher.end();
            }
            regex.append(Pattern.quote(template.substring(cursor)));
            loaded.add(new Route(
                    Json.string(operation.get("operationId")),
                    Json.string(operation.get("method")),
                    Pattern.compile("^" + regex + "$"),
                    List.copyOf(parameterNames)));
        }
        return List.copyOf(loaded);
    }

    private void dispatch(HttpExchange exchange) throws IOException {
        byte[] body;
        try (InputStream in = exchange.getRequestBody()) {
            body = in.readAllBytes();
        }
        String rawPath = exchange.getRequestURI().getRawPath();
        String rawQuery = exchange.getRequestURI().getRawQuery();
        String method = exchange.getRequestMethod();

        Map<String, List<String>> headers = new LinkedHashMap<>();
        exchange.getRequestHeaders().forEach((name, values) ->
                headers.put(name.toLowerCase(Locale.ROOT), List.copyOf(values)));

        Route matched = null;
        Map<String, String> parameters = new LinkedHashMap<>();
        boolean pathKnown = false;
        for (Route route : routes) {
            Matcher matcher = route.pattern().matcher(rawPath);
            if (!matcher.matches()) {
                continue;
            }
            pathKnown = true;
            if (!route.method().equalsIgnoreCase(method)) {
                continue;
            }
            matched = route;
            for (int i = 0; i < route.parameterNames().size(); i++) {
                parameters.put(route.parameterNames().get(i),
                        URLDecoder.decode(matcher.group(i + 1), StandardCharsets.UTF_8));
            }
            break;
        }

        Recorded recorded = new Recorded(
                method,
                rawQuery == null ? rawPath : rawPath + "?" + rawQuery,
                rawPath,
                rawQuery,
                Map.copyOf(headers),
                body,
                matched == null ? null : matched.operationId(),
                Map.copyOf(parameters));
        log.add(recorded);

        try {
            if (matched == null) {
                String code = pathKnown ? "METHOD_NOT_IN_CONTRACT" : "ROUTE_NOT_IN_CONTRACT";
                respond(exchange, pathKnown ? 405 : 404, error(code,
                        "The contract in docs/contract.json does not name " + method + " " + rawPath + ".",
                        "Call one of " + servedOperationIds() + " instead.", null));
                return;
            }
            handle(exchange, matched.operationId(), parameters, recorded);
        } catch (RuntimeException failure) {
            respond(exchange, 500, error("MOCK_INTERNAL_ERROR", String.valueOf(failure), null, null));
        }
    }

    // --------------------------------------------------------------- handlers

    private void handle(HttpExchange exchange, String operationId, Map<String, String> parameters, Recorded request)
            throws IOException {
        if (!"createToken".equals(operationId) && !bearerTokenPresent(request)) {
            respond(exchange, 401, error("UNAUTHORIZED",
                    "No usable access token was presented.",
                    "Sign in with createToken and send Authorization: Bearer <accessToken>.", null));
            return;
        }
        switch (operationId) {
            case "createToken" -> createToken(exchange, request);
            case "createNetworkPool" -> createNetworkPool(exchange, request);
            case "getNetworksOfNetworkPool" -> getNetworksOfNetworkPool(exchange, parameters);
            case "addIpPoolToNetworkOfNetworkPool" -> addIpPool(exchange, parameters, request);
            case "commissionHosts" -> commissionHosts(exchange, request);
            default -> respond(exchange, 501, error("MOCK_OPERATION_NOT_IMPLEMENTED", operationId, null, null));
        }
    }

    private boolean bearerTokenPresent(Recorded request) {
        List<String> values = request.headers().get("authorization");
        return values != null && values.size() == 1 && values.get(0).equals("Bearer " + ACCESS_TOKEN);
    }

    private void createToken(HttpExchange exchange, Recorded request) throws IOException {
        Map<String, Object> spec;
        try {
            spec = Json.object(Json.parse(request.bodyText()));
        } catch (RuntimeException malformed) {
            respond(exchange, 400, error("BAD_REQUEST", "TokenCreationSpec was not a JSON object.", null, null));
            return;
        }
        Object username = spec.get("username");
        Object password = spec.get("password");
        if (!(username instanceof String u) || u.isBlank() || !(password instanceof String p) || p.isBlank()) {
            respond(exchange, 400, error("INVALID_CREDENTIALS",
                    "TokenCreationSpec must carry a username and a password.", null, null));
            return;
        }
        Map<String, Object> refresh = new LinkedHashMap<>();
        refresh.put("id", REFRESH_TOKEN_ID);
        Map<String, Object> pair = new LinkedHashMap<>();
        pair.put("accessToken", ACCESS_TOKEN);
        pair.put("refreshToken", refresh);
        respond(exchange, 201, pair);
    }

    private void createNetworkPool(HttpExchange exchange, Recorded request) throws IOException {
        Map<String, Object> requested;
        try {
            requested = Json.object(Json.parse(request.bodyText()));
        } catch (RuntimeException malformed) {
            respond(exchange, 400, error("BAD_REQUEST", "NetworkPool was not a JSON object.", null, null));
            return;
        }
        Object name = requested.get("name");
        Object networks = requested.get("networks");
        if (!(name instanceof String poolName) || poolName.isBlank() || !(networks instanceof List<?> requestedNetworks)
                || requestedNetworks.isEmpty()) {
            respond(exchange, 400, error("NETWORK_POOL_VALIDATION_FAILED",
                    "A network pool needs a name and at least one network.", null, null));
            return;
        }

        List<Object> stored = new ArrayList<>();
        int index = 0;
        for (Object element : requestedNetworks) {
            Map<String, Object> network = new LinkedHashMap<>(Json.object(element));
            for (String required : List.of("type", "vlanId", "mtu", "subnet", "mask", "gateway")) {
                if (network.get(required) == null) {
                    respond(exchange, 400, error("NETWORK_POOL_VALIDATION_FAILED",
                            "Network is missing the required member " + required + ".", null, null));
                    return;
                }
            }
            String networkId = String.format("%s%02d", NETWORK_ID_PREFIX, ++index);
            Map<String, Object> materialised = new LinkedHashMap<>();
            materialised.put("id", networkId);
            materialised.put("type", network.get("type"));
            materialised.put("vlanId", network.get("vlanId"));
            materialised.put("mtu", network.get("mtu"));
            materialised.put("subnet", network.get("subnet"));
            materialised.put("mask", network.get("mask"));
            materialised.put("gateway", network.get("gateway"));
            materialised.put("ipPools", network.get("ipPools") instanceof List<?> pools
                    ? new ArrayList<Object>(pools) : new ArrayList<>());
            stored.add(materialised);
            networkIdsByType.put(Json.string(network.get("type")), networkId);
        }

        Map<String, Object> created = new LinkedHashMap<>();
        created.put("id", POOL_ID);
        created.put("name", poolName);
        created.put("networks", stored);
        created.put("hostsCount", 0L);
        this.pool = created;
        respond(exchange, 201, created);
    }

    private void getNetworksOfNetworkPool(HttpExchange exchange, Map<String, String> parameters) throws IOException {
        if (pool == null || !POOL_ID.equals(parameters.get("id"))) {
            respond(exchange, 404, error("NETWORK_POOL_NOT_FOUND",
                    "No network pool with id " + parameters.get("id") + ".", null, null));
            return;
        }
        if (scenario == Scenario.NETWORK_READ_REFUSED) {
            respond(exchange, 404, error(NETWORK_READ_ERROR_CODE,
                    "The created pool cannot be read back yet.",
                    "Retry the change after the pool is readable.", null, NETWORK_READ_REFERENCE_TOKEN));
            return;
        }

        // Deliberately return the page in a different order than the create request. A client must
        // resolve ids by Network.type; correlating an addition with an array position is incorrect.
        List<Object> elements = new ArrayList<>(Json.list(pool.get("networks")));
        Collections.reverse(elements);
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("pageNumber", 0L);
        metadata.put("pageSize", (long) elements.size());
        metadata.put("totalElements", (long) elements.size());
        metadata.put("totalPages", 1L);
        Map<String, Object> page = new LinkedHashMap<>();
        page.put("elements", elements);
        page.put("pageMetadata", metadata);
        respond(exchange, 200, page);
    }

    private void addIpPool(HttpExchange exchange, Map<String, String> parameters, Recorded request) throws IOException {
        if (pool == null || !POOL_ID.equals(parameters.get("id"))) {
            respond(exchange, 404, error("NETWORK_POOL_NOT_FOUND",
                    "No network pool with id " + parameters.get("id") + ".", null, null));
            return;
        }
        Map<String, Object> network = null;
        for (Object element : Json.list(pool.get("networks"))) {
            Map<String, Object> candidate = Json.object(element);
            if (Json.string(candidate.get("id")).equals(parameters.get("networkId"))) {
                network = candidate;
                break;
            }
        }
        if (network == null) {
            respond(exchange, 404, error("NETWORK_NOT_FOUND",
                    "Network pool " + POOL_ID + " has no network " + parameters.get("networkId") + ".", null, null));
            return;
        }

        Map<String, Object> range;
        try {
            range = Json.object(Json.parse(request.bodyText()));
        } catch (RuntimeException malformed) {
            respond(exchange, 400, error("BAD_REQUEST", "IpPool was not a JSON object.", null, null));
            return;
        }
        if (!(range.get("start") instanceof String) || !(range.get("end") instanceof String)) {
            respond(exchange, 400, error("IP_RANGE_VALIDATION_FAILED",
                    "An IpPool needs a start and an end address.", null, null));
            return;
        }

        String type = Json.string(network.get("type"));
        if (scenario == Scenario.IP_POOL_REFUSED && "VSAN".equals(type)) {
            List<Object> causes = new ArrayList<>();
            Map<String, Object> cause = new LinkedHashMap<>();
            cause.put("type", "IpRangeOverlap");
            cause.put("message", "The range " + range.get("start") + "-" + range.get("end")
                    + " overlaps an IP pool already attached to the " + type + " network.");
            causes.add(cause);
            respond(exchange, 400, error(IP_POOL_ERROR_CODE,
                    "The requested IP pool range overlaps an existing range on network " + network.get("id") + ".",
                    "Choose a range outside the ranges already attached to this network.",
                    causes, IP_POOL_REFERENCE_TOKEN));
            return;
        }

        Map<String, Object> appended = new LinkedHashMap<>();
        appended.put("start", range.get("start"));
        appended.put("end", range.get("end"));
        Json.list(network.get("ipPools")).add(appended);
        respond(exchange, 200, network);
    }

    private void commissionHosts(HttpExchange exchange, Recorded request) throws IOException {
        List<Object> specs;
        try {
            specs = Json.list(Json.parse(request.bodyText()));
        } catch (RuntimeException malformed) {
            respond(exchange, 400, error("BAD_REQUEST", "The body was not a JSON array of HostCommissionSpec.",
                    null, null));
            return;
        }
        if (specs.isEmpty()) {
            respond(exchange, 400, error("BAD_REQUEST", "At least one HostCommissionSpec is required.", null, null));
            return;
        }

        if (scenario == Scenario.MALFORMED_COMMISSION_REFUSAL) {
            respondRaw(exchange, 400, "text/plain", "not a contract Error body");
            return;
        }

        if (scenario != Scenario.ALL_ACCEPTED) {
            String offending = Json.string(Json.object(specs.get(specs.size() - 1)).get("fqdn"));
            List<Object> causes = new ArrayList<>();
            Map<String, Object> cause = new LinkedHashMap<>();
            cause.put("type", "HostAlreadyCommissioned");
            cause.put("message", "Host " + offending + " is already part of this VMware Cloud Foundation instance.");
            causes.add(cause);
            String message = "One of the hosts in the commission request is already commissioned: "
                    + offending + ".";
            if (scenario == Scenario.COMMISSION_REFUSED_WITH_SECRETS) {
                message += " Diagnostic echo: " + ECHOED_SSO_PASSWORD + " " + ECHOED_ESX_PASSWORD + " "
                        + SENSITIVE_API_KEY + " " + ACCESS_TOKEN + " " + REFRESH_TOKEN_ID;
            }
            respond(exchange, 400, error(COMMISSION_ERROR_CODE, message,
                    "Decommission the host, or drop it from the commission request, and submit the rest again.",
                    causes, COMMISSION_REFERENCE_TOKEN));
            return;
        }

        List<Object> resources = new ArrayList<>();
        for (Object element : specs) {
            Map<String, Object> spec = Json.object(element);
            Map<String, Object> resource = new LinkedHashMap<>();
            resource.put("resourceId", Json.string(spec.get("fqdn")));
            resource.put("type", "ESXI");
            resource.put("name", Json.string(spec.get("fqdn")));
            resource.put("fqdn", Json.string(spec.get("fqdn")));
            resources.add(resource);
        }
        Map<String, Object> task = new LinkedHashMap<>();
        task.put("id", TASK_ID);
        task.put("name", "Commissioning Hosts");
        task.put("type", "HOST_COMMISSION");
        task.put("status", "IN_PROGRESS");
        task.put("creationTimestamp", "2026-02-11T09:14:07.442Z");
        task.put("resources", resources);
        respond(exchange, 202, task);
    }

    // ---------------------------------------------------------------- plumbing

    private static Map<String, Object> error(String code, String message, String remediation, List<Object> causes) {
        return error(code, message, remediation, causes, null);
    }

    private static Map<String, Object> error(String code, String message, String remediation, List<Object> causes,
            String referenceToken) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("errorCode", code);
        body.put("errorType", "VALIDATION_FAILED");
        body.put("message", message);
        if (remediation != null) {
            body.put("remediationMessage", remediation);
        }
        if (causes != null) {
            body.put("causes", causes);
        }
        if (referenceToken != null) {
            body.put("referenceToken", referenceToken);
        }
        return body;
    }

    private static void respond(HttpExchange exchange, int status, Map<String, Object> body) throws IOException {
        byte[] payload = Json.write(body).getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, payload.length);
        exchange.getResponseBody().write(payload);
        exchange.close();
    }

    private static void respondRaw(HttpExchange exchange, int status, String contentType, String body)
            throws IOException {
        byte[] payload = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", contentType);
        exchange.sendResponseHeaders(status, payload.length);
        exchange.getResponseBody().write(payload);
        exchange.close();
    }

    /** One request exactly as it arrived on the wire. */
    public record Recorded(
            String method,
            String rawTarget,
            String rawPath,
            String rawQuery,
            Map<String, List<String>> headers,
            byte[] body,
            String operationId,
            Map<String, String> pathParameters) {

        public String bodyText() {
            return new String(body, StandardCharsets.UTF_8);
        }

        public List<String> header(String name) {
            List<String> values = headers.get(name.toLowerCase(Locale.ROOT));
            return values == null ? List.of() : values;
        }

        public String toJson() {
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("method", method);
            out.put("rawTarget", rawTarget);
            out.put("operationId", operationId);
            Map<String, Object> headerView = new LinkedHashMap<>();
            headers.forEach((name, values) -> headerView.put(name, new ArrayList<Object>(values)));
            out.put("headers", headerView);
            out.put("bodyLength", (long) body.length);
            out.put("body", bodyText());
            return Json.write(out);
        }
    }

    /**
     * The smallest JSON reader and writer that serves this mock and its test. Objects keep
     * insertion order, so a parsed body still reports the order the client put on the wire.
     */
    public static final class Json {

        private Json() {}

        @SuppressWarnings("unchecked")
        public static Map<String, Object> object(Object value) {
            if (!(value instanceof Map)) {
                throw new IllegalArgumentException("expected a JSON object, saw " + describe(value));
            }
            return (Map<String, Object>) value;
        }

        @SuppressWarnings("unchecked")
        public static List<Object> list(Object value) {
            if (!(value instanceof List)) {
                throw new IllegalArgumentException("expected a JSON array, saw " + describe(value));
            }
            return (List<Object>) value;
        }

        public static String string(Object value) {
            if (!(value instanceof String text)) {
                throw new IllegalArgumentException("expected a JSON string, saw " + describe(value));
            }
            return text;
        }

        private static String describe(Object value) {
            return value == null ? "null" : value.getClass().getSimpleName();
        }

        public static Object parse(String text) {
            Reader reader = new Reader(text);
            reader.skipWhitespace();
            Object value = reader.readValue();
            reader.skipWhitespace();
            if (!reader.atEnd()) {
                throw new IllegalArgumentException("trailing content at offset " + reader.cursor);
            }
            return value;
        }

        public static String write(Object value) {
            StringBuilder out = new StringBuilder();
            writeValue(value, out);
            return out.toString();
        }

        private static void writeValue(Object value, StringBuilder out) {
            switch (value) {
                case null -> out.append("null");
                case String text -> writeString(text, out);
                case Boolean flag -> out.append(flag);
                case Long number -> out.append(number.longValue());
                case Integer number -> out.append(number.intValue());
                case Double number -> out.append(number == Math.rint(number) && !number.isInfinite()
                        ? String.valueOf(number.longValue()) : String.valueOf(number));
                case Map<?, ?> map -> {
                    out.append('{');
                    boolean first = true;
                    for (Map.Entry<?, ?> entry : map.entrySet()) {
                        if (!first) {
                            out.append(',');
                        }
                        first = false;
                        writeString(String.valueOf(entry.getKey()), out);
                        out.append(':');
                        writeValue(entry.getValue(), out);
                    }
                    out.append('}');
                }
                case List<?> items -> {
                    out.append('[');
                    for (int i = 0; i < items.size(); i++) {
                        if (i > 0) {
                            out.append(',');
                        }
                        writeValue(items.get(i), out);
                    }
                    out.append(']');
                }
                default -> throw new IllegalArgumentException("cannot serialize " + value.getClass());
            }
        }

        private static void writeString(String text, StringBuilder out) {
            out.append('"');
            for (int i = 0; i < text.length(); i++) {
                char c = text.charAt(i);
                switch (c) {
                    case '"' -> out.append("\\\"");
                    case '\\' -> out.append("\\\\");
                    case '\n' -> out.append("\\n");
                    case '\r' -> out.append("\\r");
                    case '\t' -> out.append("\\t");
                    case '\b' -> out.append("\\b");
                    case '\f' -> out.append("\\f");
                    default -> {
                        if (c < 0x20) {
                            out.append(String.format("\\u%04x", (int) c));
                        } else {
                            out.append(c);
                        }
                    }
                }
            }
            out.append('"');
        }

        private static final class Reader {
            private final String text;
            private int cursor;

            Reader(String text) {
                this.text = text;
            }

            boolean atEnd() {
                return cursor >= text.length();
            }

            void skipWhitespace() {
                while (cursor < text.length() && Character.isWhitespace(text.charAt(cursor))) {
                    cursor++;
                }
            }

            Object readValue() {
                if (atEnd()) {
                    throw new IllegalArgumentException("unexpected end of JSON input");
                }
                char c = text.charAt(cursor);
                return switch (c) {
                    case '{' -> readObject();
                    case '[' -> readArray();
                    case '"' -> readString();
                    case 't' -> readLiteral("true", Boolean.TRUE);
                    case 'f' -> readLiteral("false", Boolean.FALSE);
                    case 'n' -> readLiteral("null", null);
                    default -> readNumber();
                };
            }

            private Object readLiteral(String literal, Object value) {
                if (!text.startsWith(literal, cursor)) {
                    throw new IllegalArgumentException("bad literal at offset " + cursor);
                }
                cursor += literal.length();
                return value;
            }

            private Map<String, Object> readObject() {
                Map<String, Object> map = new LinkedHashMap<>();
                cursor++;
                skipWhitespace();
                if (!atEnd() && text.charAt(cursor) == '}') {
                    cursor++;
                    return map;
                }
                while (true) {
                    skipWhitespace();
                    String key = readString();
                    skipWhitespace();
                    expect(':');
                    skipWhitespace();
                    map.put(key, readValue());
                    skipWhitespace();
                    char next = next();
                    if (next == '}') {
                        return map;
                    }
                    if (next != ',') {
                        throw new IllegalArgumentException("expected , or } at offset " + (cursor - 1));
                    }
                }
            }

            private List<Object> readArray() {
                List<Object> items = new ArrayList<>();
                cursor++;
                skipWhitespace();
                if (!atEnd() && text.charAt(cursor) == ']') {
                    cursor++;
                    return items;
                }
                while (true) {
                    skipWhitespace();
                    items.add(readValue());
                    skipWhitespace();
                    char next = next();
                    if (next == ']') {
                        return items;
                    }
                    if (next != ',') {
                        throw new IllegalArgumentException("expected , or ] at offset " + (cursor - 1));
                    }
                }
            }

            private String readString() {
                expect('"');
                StringBuilder out = new StringBuilder();
                while (true) {
                    char c = next();
                    if (c == '"') {
                        return out.toString();
                    }
                    if (c != '\\') {
                        out.append(c);
                        continue;
                    }
                    char escape = next();
                    switch (escape) {
                        case '"' -> out.append('"');
                        case '\\' -> out.append('\\');
                        case '/' -> out.append('/');
                        case 'b' -> out.append('\b');
                        case 'f' -> out.append('\f');
                        case 'n' -> out.append('\n');
                        case 'r' -> out.append('\r');
                        case 't' -> out.append('\t');
                        case 'u' -> {
                            out.append((char) Integer.parseInt(text.substring(cursor, cursor + 4), 16));
                            cursor += 4;
                        }
                        default -> throw new IllegalArgumentException("bad escape \\" + escape);
                    }
                }
            }

            private Object readNumber() {
                int start = cursor;
                while (cursor < text.length() && "+-0123456789.eE".indexOf(text.charAt(cursor)) >= 0) {
                    cursor++;
                }
                String literal = text.substring(start, cursor);
                if (literal.isEmpty()) {
                    throw new IllegalArgumentException("expected a value at offset " + start);
                }
                if (literal.indexOf('.') < 0 && literal.indexOf('e') < 0 && literal.indexOf('E') < 0) {
                    return Long.parseLong(literal);
                }
                return Double.parseDouble(literal);
            }

            private void expect(char expected) {
                char actual = next();
                if (actual != expected) {
                    throw new IllegalArgumentException("expected " + expected + " at offset " + (cursor - 1));
                }
            }

            private char next() {
                if (atEnd()) {
                    throw new IllegalArgumentException("unexpected end of JSON input");
                }
                return text.charAt(cursor++);
            }
        }
    }
}
