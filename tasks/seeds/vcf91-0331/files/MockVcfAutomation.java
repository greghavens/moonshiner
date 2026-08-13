import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Deque;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Loopback-only VCF Automation fixture. It binds 127.0.0.1 on an ephemeral port and contacts
 * nothing; no live VMware endpoint is involved anywhere in this project.
 *
 * <p>The served surface is built from {@code docs/contract.json} at construction time: a request
 * that matches no operation the contract names is answered 404 and counted as off contract, so a
 * client that invents a route cannot accidentally be served. Every request is recorded in order,
 * with the operationId it resolved to, its raw path, its raw query string, its headers and its
 * entity, and the log is what {@link WireVerifier} asserts against.
 *
 * <p>Reply payloads are fixture data staged by the test. They are deliberately not stable
 * constants of the API: the {@code latestApiVersion} handed back by {@code getAboutPage} differs
 * per scenario so that a client which hard-codes a version instead of discovering one is caught.
 */
public final class MockVcfAutomation implements AutoCloseable {

    /** One recorded request. */
    public record Recorded(
            String operationId,
            String method,
            String path,
            String query,
            Map<String, String> pathParameters,
            Map<String, List<String>> headers,
            String body) {

        public boolean hasHeader(String name) {
            return headers.containsKey(name.toLowerCase(Locale.ROOT));
        }

        public String header(String name) {
            List<String> values = headers.get(name.toLowerCase(Locale.ROOT));
            return values == null || values.isEmpty() ? null : String.join(",", values);
        }
    }

    /** A RequestTracker document the fixture hands back. */
    public record Tracker(String status, Integer progress, String message, List<String> resources) {
        public Tracker {
            resources = resources == null ? List.of() : List.copyOf(resources);
        }

        public static Tracker inProgress(int progress) {
            return new Tracker("INPROGRESS", progress, "Provisioning in progress", List.of());
        }

        public static Tracker finished(String... resources) {
            return new Tracker("FINISHED", 100, "Provisioning completed", List.of(resources));
        }

        public static Tracker failed(String message) {
            return new Tracker("FAILED", 100, message, List.of());
        }
    }

    /** A Machine document the fixture hands back from {@code getMachine}. */
    public record MachineDoc(
            String id,
            String name,
            String powerState,
            String address,
            String externalId,
            String projectId) {}

    /**
     * One staged provisioning run. {@code acceptance} is the RequestTracker returned with the 202
     * of {@code createMachine}; {@code polls} are the trackers returned by successive
     * {@code getRequestTracker} calls, the last one repeating for any further poll.
     */
    public record Provision(
            String requestId,
            String requestName,
            Tracker acceptance,
            List<Tracker> polls,
            MachineDoc machine) {

        public Provision {
            polls = List.copyOf(polls);
        }
    }

    /** A staged error reply. */
    public record ApiError(int status, String message, int errorCode, String serverErrorId) {}

    private record Route(String operationId, String method, Pattern pattern, List<String> names) {}

    private final HttpServer server;
    private final ExecutorService executor;
    private final List<Route> routes;
    private final String refreshToken;
    private final String bearerToken;
    private final String latestApiVersion;
    private final List<String> supportedApiVersions;

    private final Object lock = new Object();
    private final List<Recorded> log = new ArrayList<>();
    private final Deque<Provision> staged = new ArrayDeque<>();
    private final Map<String, Provision> byRequestId = new LinkedHashMap<>();
    private final Map<String, MachineDoc> machines = new LinkedHashMap<>();
    private final Map<String, Long> acceptedAtNanos = new LinkedHashMap<>();
    private final Map<String, List<Long>> pollTimesNanos = new LinkedHashMap<>();
    private final Map<String, AtomicInteger> polls = new ConcurrentHashMap<>();
    private final AtomicInteger offContract = new AtomicInteger();
    private ApiError loginError;
    private ApiError createError;
    private ApiError trackerError;

    public MockVcfAutomation(
            Path contractPath,
            String refreshToken,
            String bearerToken,
            String latestApiVersion,
            List<String> supportedApiVersions)
            throws IOException {
        this.routes = loadRoutes(contractPath);
        this.refreshToken = refreshToken;
        this.bearerToken = bearerToken;
        this.latestApiVersion = latestApiVersion;
        this.supportedApiVersions = List.copyOf(supportedApiVersions);
        this.server =
                HttpServer.create(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0), 0);
        this.executor =
                Executors.newCachedThreadPool(
                        runnable -> {
                            Thread thread = new Thread(runnable, "vcf-automation-mock");
                            thread.setDaemon(true);
                            return thread;
                        });
        server.setExecutor(executor);
        server.createContext("/", this::handle);
        server.start();
    }

    public String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    public String bearerToken() {
        return bearerToken;
    }

    public String latestApiVersion() {
        return latestApiVersion;
    }

    /** Stages the next {@code createMachine} run. Runs are consumed in staging order. */
    public MockVcfAutomation stage(Provision provision) {
        synchronized (lock) {
            staged.addLast(provision);
            if (provision.machine() != null) {
                machines.put(provision.machine().id(), provision.machine());
            }
        }
        return this;
    }

    /**
     * Makes one more machine readable through {@code getMachine} without staging a provisioning
     * run for it, so a client that reads the wrong machine is answered rather than 404'd.
     */
    public MockVcfAutomation stageMachine(MachineDoc machine) {
        synchronized (lock) {
            machines.put(machine.id(), machine);
        }
        return this;
    }

    /** Makes {@code retrieveAuthToken} answer with an error instead of a token. */
    public MockVcfAutomation failLogin(ApiError error) {
        synchronized (lock) {
            loginError = error;
        }
        return this;
    }

    /** Makes {@code createMachine} answer with an error instead of accepting the request. */
    public MockVcfAutomation failCreate(ApiError error) {
        synchronized (lock) {
            createError = error;
        }
        return this;
    }

    /** Makes {@code getRequestTracker} answer with an error instead of a tracker. */
    public MockVcfAutomation failTracker(ApiError error) {
        synchronized (lock) {
            trackerError = error;
        }
        return this;
    }

    public List<Recorded> log() {
        synchronized (lock) {
            return List.copyOf(log);
        }
    }

    /** Number of requests that matched no operation named by the contract. */
    public int offContractRequests() {
        return offContract.get();
    }

    /** How many times {@code getRequestTracker} was called for one request id. */
    public int trackerPolls(String requestId) {
        AtomicInteger counter = polls.get(requestId);
        return counter == null ? 0 : counter.get();
    }

    /** Monotonic time at which the fixture accepted a create request, or zero if it did not. */
    public long acceptedAtNanos(String requestId) {
        synchronized (lock) {
            return acceptedAtNanos.getOrDefault(requestId, 0L);
        }
    }

    /** Monotonic receipt times of successive tracker reads. */
    public List<Long> trackerPollTimesNanos(String requestId) {
        synchronized (lock) {
            List<Long> times = pollTimesNanos.get(requestId);
            return times == null ? List.of() : List.copyOf(times);
        }
    }

    public void clearLog() {
        synchronized (lock) {
            log.clear();
        }
        offContract.set(0);
    }

    @Override
    public void close() {
        server.stop(0);
        executor.shutdownNow();
    }

    // ---------------------------------------------------------------- routing

    private static List<Route> loadRoutes(Path contractPath) throws IOException {
        Map<String, Object> contract =
                Json.obj(Json.parse(Files.readString(contractPath, StandardCharsets.UTF_8)));
        Map<String, Object> source = Json.obj(contract.get("source"));
        if (!"reference-documentation".equals(source.get("kind"))) {
            throw new IOException(
                    "docs/contract.json must declare its source kind as reference-documentation");
        }
        List<Object> operations = Json.arr(contract.get("operations"));
        if (operations.isEmpty()) {
            throw new IOException("docs/contract.json names no operation to serve");
        }
        List<Route> routes = new ArrayList<>();
        for (Object entry : operations) {
            Map<String, Object> operation = Json.obj(entry);
            String operationId = Json.str(operation, "operationId");
            String method = Json.str(operation, "method");
            String path = Json.str(operation, "path");
            String documentation = Json.str(operation, "documentation_url");
            if (operationId == null || method == null || path == null) {
                throw new IOException("contract operation is missing operationId, method or path");
            }
            if (documentation == null
                    || !documentation.startsWith("https://developer.broadcom.com/xapis/")) {
                throw new IOException(
                        "contract operation "
                                + operationId
                                + " does not record the Broadcom reference page it came from");
            }
            routes.add(compile(operationId, method, path));
        }
        return List.copyOf(routes);
    }

    private static Route compile(String operationId, String method, String template) {
        StringBuilder regex = new StringBuilder();
        List<String> names = new ArrayList<>();
        for (String segment : template.split("/", -1)) {
            if (segment.isEmpty()) {
                continue;
            }
            regex.append('/');
            if (segment.startsWith("{") && segment.endsWith("}")) {
                names.add(segment.substring(1, segment.length() - 1));
                regex.append("([^/]+)");
            } else {
                regex.append(Pattern.quote(segment));
            }
        }
        return new Route(
                operationId,
                method.toUpperCase(Locale.ROOT),
                Pattern.compile(regex.toString()),
                List.copyOf(names));
    }

    // ---------------------------------------------------------------- serving

    private void handle(HttpExchange exchange) throws IOException {
        String body =
                new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        String method = exchange.getRequestMethod();
        String path = exchange.getRequestURI().getRawPath();
        String query = exchange.getRequestURI().getRawQuery();

        Route matched = null;
        Map<String, String> pathParameters = Map.of();
        for (Route route : routes) {
            Matcher matcher = route.pattern().matcher(path);
            if (matcher.matches() && route.method().equals(method)) {
                matched = route;
                Map<String, String> captured = new LinkedHashMap<>();
                for (int i = 0; i < route.names().size(); i++) {
                    captured.put(route.names().get(i), matcher.group(i + 1));
                }
                pathParameters = Map.copyOf(captured);
                break;
            }
        }

        Recorded recorded =
                new Recorded(
                        matched == null ? null : matched.operationId(),
                        method,
                        path,
                        query,
                        pathParameters,
                        headers(exchange),
                        body);
        synchronized (lock) {
            log.add(recorded);
        }

        if (matched == null) {
            offContract.incrementAndGet();
            respond(
                    exchange,
                    404,
                    error(404, "no operation in docs/contract.json serves " + method + " " + path,
                            404, "off-contract"));
            return;
        }

        switch (matched.operationId()) {
            case "retrieveAuthToken" -> retrieveAuthToken(exchange, body);
            case "getAboutPage" -> getAboutPage(exchange);
            case "createMachine" -> createMachine(exchange, body);
            case "getRequestTracker" -> getRequestTracker(exchange, pathParameters.get("id"));
            case "getMachine" -> getMachine(exchange, pathParameters.get("id"));
            default ->
                    respond(
                            exchange,
                            501,
                            error(501,
                                    "the contract names operation "
                                            + matched.operationId()
                                            + ", which this fixture does not implement",
                                    501,
                                    "unimplemented"));
        }
    }

    private void retrieveAuthToken(HttpExchange exchange, String body) throws IOException {
        ApiError staged;
        synchronized (lock) {
            staged = loginError;
        }
        if (staged != null) {
            respond(exchange, staged.status(), error(staged));
            return;
        }
        Object parsed;
        try {
            parsed = Json.parse(body);
        } catch (RuntimeException malformed) {
            respond(exchange, 400, error(400, "request body is not JSON", 400, "bad-json"));
            return;
        }
        Object presented = Json.obj(parsed).get("refreshToken");
        if (!refreshToken.equals(presented)) {
            respond(exchange, 403, error(403, "refresh token was rejected", 403, "bad-token"));
            return;
        }
        Map<String, Object> reply = new LinkedHashMap<>();
        reply.put("tokenType", "Bearer");
        reply.put("token", bearerToken);
        respond(exchange, 200, reply);
    }

    private void getAboutPage(HttpExchange exchange) throws IOException {
        if (!authorized(exchange)) {
            respond(exchange, 403, error(403, "missing or invalid bearer token", 403, "forbidden"));
            return;
        }
        List<Object> supported = new ArrayList<>();
        for (String version : supportedApiVersions) {
            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("apiVersion", version);
            entry.put(
                    "documentationLink",
                    "https://developer.broadcom.com/xapis/vm-apps-org-provisioning-service/latest/");
            supported.add(entry);
        }
        Map<String, Object> reply = new LinkedHashMap<>();
        reply.put("supportedApis", supported);
        reply.put("latestApiVersion", latestApiVersion);
        respond(exchange, 200, reply);
    }

    private void createMachine(HttpExchange exchange, String body) throws IOException {
        if (!authorized(exchange)) {
            respond(exchange, 403, error(403, "missing or invalid bearer token", 403, "forbidden"));
            return;
        }
        ApiError failure;
        Provision provision;
        synchronized (lock) {
            failure = createError;
            provision = failure == null ? staged.pollFirst() : null;
            if (provision != null) {
                byRequestId.put(provision.requestId(), provision);
            }
        }
        if (failure != null) {
            respond(exchange, failure.status(), error(failure));
            return;
        }
        if (provision == null) {
            respond(
                    exchange,
                    500,
                    error(500, "the fixture has no provisioning run staged", 500, "unstaged"));
            return;
        }
        try {
            Json.obj(Json.parse(body));
        } catch (RuntimeException malformed) {
            respond(exchange, 400, error(400, "request body is not a JSON object", 400, "bad-json"));
            return;
        }
        synchronized (lock) {
            acceptedAtNanos.put(provision.requestId(), System.nanoTime());
        }
        respond(exchange, 202, tracker(provision, provision.acceptance()));
    }

    private void getRequestTracker(HttpExchange exchange, String requestId) throws IOException {
        if (!authorized(exchange)) {
            respond(exchange, 403, error(403, "missing or invalid bearer token", 403, "forbidden"));
            return;
        }
        Provision provision;
        ApiError failure;
        synchronized (lock) {
            pollTimesNanos.computeIfAbsent(requestId, ignored -> new ArrayList<>()).add(System.nanoTime());
            provision = byRequestId.get(requestId);
            failure = trackerError;
        }
        if (provision == null) {
            respond(exchange, 404, error(404, "unknown request id", 404, "not-found"));
            return;
        }
        if (failure != null) {
            respond(exchange, failure.status(), error(failure));
            return;
        }
        int attempt =
                polls.computeIfAbsent(requestId, ignored -> new AtomicInteger())
                        .getAndIncrement();
        List<Tracker> sequence = provision.polls();
        Tracker current = sequence.get(Math.min(attempt, sequence.size() - 1));
        respond(exchange, 200, tracker(provision, current));
    }

    private void getMachine(HttpExchange exchange, String machineId) throws IOException {
        if (!authorized(exchange)) {
            respond(exchange, 403, error(403, "missing or invalid bearer token", 403, "forbidden"));
            return;
        }
        MachineDoc machine;
        synchronized (lock) {
            machine = machines.get(machineId);
        }
        if (machine == null) {
            respond(exchange, 404, error(404, "unknown machine id " + machineId, 404, "not-found"));
            return;
        }
        Map<String, Object> links = new LinkedHashMap<>();
        Map<String, Object> self = new LinkedHashMap<>();
        self.put("href", "/iaas/api/machines/" + machine.id());
        links.put("self", self);

        Map<String, Object> reply = new LinkedHashMap<>();
        reply.put("id", machine.id());
        reply.put("name", machine.name());
        reply.put("powerState", machine.powerState());
        reply.put("address", machine.address());
        reply.put("externalId", machine.externalId());
        reply.put("externalRegionId", "sddc-region-a");
        reply.put("externalZoneId", "sddc-zone-a1");
        reply.put("projectId", machine.projectId());
        reply.put("provisioningStatus", "READY");
        reply.put("orgId", "org-4dcb");
        reply.put("createdAt", "2026-08-11T09:14:22.318Z");
        reply.put("_links", links);
        respond(exchange, 200, reply);
    }

    // ---------------------------------------------------------------- helpers

    private boolean authorized(HttpExchange exchange) {
        String presented = exchange.getRequestHeaders().getFirst("Authorization");
        return ("Bearer " + bearerToken).equals(presented);
    }

    private Map<String, Object> tracker(Provision provision, Tracker state) {
        Map<String, Object> reply = new LinkedHashMap<>();
        reply.put("progress", state.progress());
        if (state.message() != null) {
            reply.put("message", state.message());
        }
        reply.put("status", state.status());
        reply.put("resources", state.resources());
        reply.put("name", provision.requestName());
        reply.put("id", provision.requestId());
        reply.put("selfLink", "/iaas/api/request-tracker/" + provision.requestId());
        return reply;
    }

    private static Map<String, Object> error(ApiError error) {
        return error(error.status(), error.message(), error.errorCode(), error.serverErrorId());
    }

    private static Map<String, Object> error(
            int status, String message, int errorCode, String serverErrorId) {
        Map<String, Object> reply = new LinkedHashMap<>();
        reply.put("message", message);
        reply.put("statusCode", status);
        reply.put("errorCode", errorCode);
        reply.put("serverErrorId", serverErrorId);
        reply.put("documentKind", "com:vmware:provisioning:ServiceErrorResponse");
        return reply;
    }

    private static Map<String, List<String>> headers(HttpExchange exchange) {
        Map<String, List<String>> copy = new LinkedHashMap<>();
        exchange.getRequestHeaders()
                .forEach((name, values) -> copy.put(name.toLowerCase(Locale.ROOT), List.copyOf(values)));
        return Collections.unmodifiableMap(copy);
    }

    private static void respond(HttpExchange exchange, int status, Map<String, Object> body)
            throws IOException {
        byte[] bytes = Json.stringify(body).getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    /** Minimal JSON reader and writer, shared by the fixture and the wire verifier. */
    public static final class Json {

        private final String text;
        private int at;

        private Json(String text) {
            this.text = text;
        }

        public static Object parse(String text) {
            Json parser = new Json(text);
            parser.skip();
            Object value = parser.value();
            parser.skip();
            if (parser.at != text.length()) {
                throw new IllegalArgumentException("trailing content in JSON at offset " + parser.at);
            }
            return value;
        }

        @SuppressWarnings("unchecked")
        public static Map<String, Object> obj(Object value) {
            if (!(value instanceof Map)) {
                throw new IllegalArgumentException("expected a JSON object but found " + value);
            }
            return (Map<String, Object>) value;
        }

        @SuppressWarnings("unchecked")
        public static List<Object> arr(Object value) {
            if (!(value instanceof List)) {
                throw new IllegalArgumentException("expected a JSON array but found " + value);
            }
            return (List<Object>) value;
        }

        public static String str(Map<String, Object> object, String member) {
            Object value = object.get(member);
            return value instanceof String text ? text : null;
        }

        public static String stringify(Object value) {
            StringBuilder out = new StringBuilder();
            write(value, out);
            return out.toString();
        }

        private static void write(Object value, StringBuilder out) {
            switch (value) {
                case null -> out.append("null");
                case String text -> quote(text, out);
                case Boolean flag -> out.append(flag);
                case Integer number -> out.append(number);
                case Long number -> out.append(number);
                case Double number ->
                        out.append(number == Math.rint(number) && !number.isInfinite()
                                ? String.valueOf(number.longValue())
                                : String.valueOf(number));
                case Map<?, ?> map -> {
                    out.append('{');
                    boolean first = true;
                    for (Map.Entry<?, ?> entry : map.entrySet()) {
                        if (!first) {
                            out.append(',');
                        }
                        first = false;
                        quote(String.valueOf(entry.getKey()), out);
                        out.append(':');
                        write(entry.getValue(), out);
                    }
                    out.append('}');
                }
                case List<?> list -> {
                    out.append('[');
                    for (int i = 0; i < list.size(); i++) {
                        if (i > 0) {
                            out.append(',');
                        }
                        write(list.get(i), out);
                    }
                    out.append(']');
                }
                default -> throw new IllegalArgumentException("cannot serialise " + value);
            }
        }

        private static void quote(String text, StringBuilder out) {
            out.append('"');
            for (int i = 0; i < text.length(); i++) {
                char c = text.charAt(i);
                switch (c) {
                    case '"' -> out.append("\\\"");
                    case '\\' -> out.append("\\\\");
                    case '\n' -> out.append("\\n");
                    case '\r' -> out.append("\\r");
                    case '\t' -> out.append("\\t");
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

        private Object value() {
            char c = peek();
            return switch (c) {
                case '{' -> object();
                case '[' -> array();
                case '"' -> string();
                case 't', 'f' -> bool();
                case 'n' -> nul();
                default -> number();
            };
        }

        private Map<String, Object> object() {
            Map<String, Object> members = new LinkedHashMap<>();
            expect('{');
            skip();
            if (peek() == '}') {
                at++;
                return members;
            }
            while (true) {
                skip();
                String key = string();
                skip();
                expect(':');
                skip();
                members.put(key, value());
                skip();
                char c = next();
                if (c == '}') {
                    return members;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("expected ',' or '}' at offset " + at);
                }
            }
        }

        private List<Object> array() {
            List<Object> items = new ArrayList<>();
            expect('[');
            skip();
            if (peek() == ']') {
                at++;
                return items;
            }
            while (true) {
                skip();
                items.add(value());
                skip();
                char c = next();
                if (c == ']') {
                    return items;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("expected ',' or ']' at offset " + at);
                }
            }
        }

        private String string() {
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
                        out.append((char) Integer.parseInt(text.substring(at, at + 4), 16));
                        at += 4;
                    }
                    default -> throw new IllegalArgumentException("bad escape at offset " + at);
                }
            }
        }

        private Object bool() {
            if (text.startsWith("true", at)) {
                at += 4;
                return Boolean.TRUE;
            }
            if (text.startsWith("false", at)) {
                at += 5;
                return Boolean.FALSE;
            }
            throw new IllegalArgumentException("bad literal at offset " + at);
        }

        private Object nul() {
            if (!text.startsWith("null", at)) {
                throw new IllegalArgumentException("bad literal at offset " + at);
            }
            at += 4;
            return null;
        }

        private Object number() {
            int start = at;
            while (at < text.length() && "+-.eE0123456789".indexOf(text.charAt(at)) >= 0) {
                at++;
            }
            String literal = text.substring(start, at);
            if (literal.isEmpty()) {
                throw new IllegalArgumentException("expected a value at offset " + start);
            }
            if (literal.indexOf('.') < 0 && literal.indexOf('e') < 0 && literal.indexOf('E') < 0) {
                return Long.parseLong(literal);
            }
            return Double.parseDouble(literal);
        }

        private void skip() {
            while (at < text.length() && Character.isWhitespace(text.charAt(at))) {
                at++;
            }
        }

        private char peek() {
            if (at >= text.length()) {
                throw new IllegalArgumentException("unexpected end of JSON");
            }
            return text.charAt(at);
        }

        private char next() {
            char c = peek();
            at++;
            return c;
        }

        private void expect(char expected) {
            if (next() != expected) {
                throw new IllegalArgumentException("expected '" + expected + "' at offset " + (at - 1));
            }
        }
    }
}
