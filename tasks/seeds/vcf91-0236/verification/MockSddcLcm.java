import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.InputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Loopback SDDC LCM stand-in pinned to {@code docs/contract.json}.
 *
 * <p>The route table is built from the contract projection, so the server answers only the four
 * projected operationIds. Any other method or path is recorded as a contract violation and
 * answered with 404; it never reaches scripted state. Every request is appended to a log the
 * harness reads back to assert the exact wire shape the client produced.
 */
public final class MockSddcLcm implements AutoCloseable {

    /** One observed request, tagged with the contract operation whose route matched it. */
    public record Recorded(
            String operationId,
            String method,
            String path,
            String query,
            Map<String, String> headers,
            String body,
            Map<String, String> pathParams) {

        public String header(String name) {
            return headers.get(name.toLowerCase(Locale.ROOT));
        }

        public boolean hasHeader(String name) {
            return headers.containsKey(name.toLowerCase(Locale.ROOT));
        }

        @Override
        public String toString() {
            return (operationId == null ? "<off-contract>" : operationId)
                    + " "
                    + method
                    + " "
                    + path
                    + (query == null ? "" : "?" + query);
        }
    }

    /** A SupportBundle document as the listing operation returns it. */
    public record Bundle(String id, String name, long size, String createdTimestamp, String url) {}

    private record Route(String operationId, String method, Pattern pattern, List<String> names) {}

    private record TaskScript(List<String> statuses, String failedStage, String errorMessage) {}

    private record ApiError(int status, String code, String message) {}

    private final HttpServer server;
    private final String baseUrl;
    private final String serviceBasePath;
    private final List<Route> routes;

    private final List<Recorded> log = Collections.synchronizedList(new ArrayList<>());
    private final AtomicInteger offContract = new AtomicInteger();

    private final Map<String, String> generateTasks = new ConcurrentHashMap<>();
    private final Map<String, TaskScript> taskScripts = new ConcurrentHashMap<>();
    private final Map<String, AtomicInteger> taskPolls = new ConcurrentHashMap<>();
    private final Map<String, List<Bundle>> bundles = new ConcurrentHashMap<>();
    private final Map<String, String> deleteTasks = new ConcurrentHashMap<>();
    private final Map<String, ApiError> errors = new ConcurrentHashMap<>();

    private MockSddcLcm(HttpServer server, String serviceBasePath, List<Route> routes) {
        this.server = server;
        this.serviceBasePath = serviceBasePath;
        this.routes = routes;
        this.baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
    }

    /** Starts the server on an ephemeral loopback port using the pinned contract projection. */
    public static MockSddcLcm start() throws IOException {
        Path contractPath = locateContract();
        Object contract = Json.parse(Files.readString(contractPath, StandardCharsets.UTF_8));
        String basePath = Json.str(Json.get(contract, "source", "serviceBasePath"));
        if (basePath == null || basePath.isEmpty()) {
            throw new IllegalStateException("contract.json is missing source.serviceBasePath");
        }
        List<Route> routes = new ArrayList<>();
        for (Object op : Json.arr(Json.get(contract, "operations"))) {
            String operationId = Json.str(Json.get(op, "operationId"));
            String method = Json.str(Json.get(op, "method"));
            String template = Json.str(Json.get(op, "path"));
            routes.add(compile(operationId, method, basePath + template));
        }
        if (routes.isEmpty()) {
            throw new IllegalStateException("contract.json projects no operations");
        }
        HttpServer server =
                HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        MockSddcLcm mock = new MockSddcLcm(server, basePath, routes);
        server.createContext("/", mock::dispatch);
        server.setExecutor(null);
        server.start();
        return mock;
    }

    private static Path locateContract() {
        Path direct = Path.of("docs", "contract.json");
        if (Files.isReadable(direct)) {
            return direct;
        }
        throw new IllegalStateException(
                "docs/contract.json not found relative to " + Path.of("").toAbsolutePath());
    }

    private static Route compile(String operationId, String method, String template) {
        StringBuilder regex = new StringBuilder();
        List<String> names = new ArrayList<>();
        Matcher matcher = Pattern.compile("\\{([A-Za-z0-9_]+)}").matcher(template);
        int cursor = 0;
        while (matcher.find()) {
            regex.append(Pattern.quote(template.substring(cursor, matcher.start())));
            regex.append("([^/]+)");
            names.add(matcher.group(1));
            cursor = matcher.end();
        }
        regex.append(Pattern.quote(template.substring(cursor)));
        return new Route(operationId, method, Pattern.compile("^" + regex + "$"), names);
    }

    public String baseUrl() {
        return baseUrl;
    }

    public String serviceBasePath() {
        return serviceBasePath;
    }

    /** Snapshot of every request seen since the last {@link #reset()}, in arrival order. */
    public List<Recorded> log() {
        synchronized (log) {
            return List.copyOf(log);
        }
    }

    /** Requests that matched no contract route. Any non-zero count fails verification. */
    public int offContractRequests() {
        return offContract.get();
    }

    /** How many times {@code getTask} was served for this task id. */
    public int taskPolls(String taskId) {
        AtomicInteger counter = taskPolls.get(taskId);
        return counter == null ? 0 : counter.get();
    }

    public void reset() {
        log.clear();
        offContract.set(0);
        generateTasks.clear();
        taskScripts.clear();
        taskPolls.clear();
        bundles.clear();
        deleteTasks.clear();
        errors.clear();
    }

    @Override
    public void close() {
        server.stop(0);
    }

    // ---------------------------------------------------------------- scripting

    /** {@code generateComponentSupportBundle} on this component is accepted and returns taskId. */
    public void generateAccepts(String componentId, String taskId) {
        generateTasks.put(componentId, taskId);
    }

    /** {@code deleteComponentSupportBundle} for this bundle is accepted and returns taskId. */
    public void deleteAccepts(String componentId, String bundleId, String taskId) {
        deleteTasks.put(componentId + "/" + bundleId, taskId);
    }

    /** {@code getComponentSupportBundles} on this component returns these bundles, in order. */
    public void listReturns(String componentId, List<Bundle> reply) {
        bundles.put(componentId, List.copyOf(reply));
    }

    /**
     * Scripts the statuses {@code getTask} hands back for a task, one per poll. The final entry
     * repeats for every further poll, so a client that keeps polling past a terminal status is
     * still observable through {@link #taskPolls(String)}.
     */
    public void taskRunsThrough(String taskId, List<String> statuses) {
        taskScripts.put(taskId, new TaskScript(List.copyOf(statuses), null, null));
    }

    /** As {@link #taskRunsThrough} but the terminal document carries a failing TaskStage. */
    public void taskFailsAt(
            String taskId, List<String> statuses, String failedStage, String errorMessage) {
        taskScripts.put(taskId, new TaskScript(List.copyOf(statuses), failedStage, errorMessage));
    }

    /** Forces an ErrorResponse for the next and all later calls of an operation on a component. */
    public void failsWith(String operationId, String componentId, int status, String code, String message) {
        errors.put(operationId + "|" + componentId, new ApiError(status, code, message));
    }

    // ---------------------------------------------------------------- serving

    private void dispatch(HttpExchange exchange) throws IOException {
        String method = exchange.getRequestMethod();
        String path = exchange.getRequestURI().getRawPath();
        String query = exchange.getRequestURI().getRawQuery();
        byte[] raw = readAll(exchange.getRequestBody());
        String body = new String(raw, StandardCharsets.UTF_8);

        Map<String, String> headers = new LinkedHashMap<>();
        exchange.getRequestHeaders()
                .forEach(
                        (name, values) -> {
                            if (!values.isEmpty()) {
                                headers.put(name.toLowerCase(Locale.ROOT), values.get(0));
                            }
                        });

        Route route = null;
        Map<String, String> params = new LinkedHashMap<>();
        for (Route candidate : routes) {
            Matcher matcher = candidate.pattern().matcher(path);
            if (candidate.method().equalsIgnoreCase(method) && matcher.matches()) {
                route = candidate;
                for (int i = 0; i < candidate.names().size(); i++) {
                    params.put(candidate.names().get(i), matcher.group(i + 1));
                }
                break;
            }
        }

        log.add(
                new Recorded(
                        route == null ? null : route.operationId(),
                        method,
                        path,
                        query,
                        Map.copyOf(headers),
                        body,
                        Map.copyOf(params)));

        if (route == null) {
            offContract.incrementAndGet();
            send(exchange, 404, errorBody("NOT_ON_CONTRACT", method + " " + path + " is not a projected operation"));
            return;
        }

        String auth = headers.get("authorization");
        if (auth == null || auth.isBlank()) {
            send(exchange, 401, errorBody("UNAUTHORIZED", "missing bearer token"));
            return;
        }

        switch (route.operationId()) {
            case "generateComponentSupportBundle" -> serveGenerate(exchange, params);
            case "getTask" -> serveGetTask(exchange, params);
            case "getComponentSupportBundles" -> serveList(exchange, params);
            case "deleteComponentSupportBundle" -> serveDelete(exchange, params);
            default -> send(exchange, 500, errorBody("UNSCRIPTED", route.operationId()));
        }
    }

    private void serveGenerate(HttpExchange exchange, Map<String, String> params) throws IOException {
        String componentId = params.get("componentId");
        ApiError forced = errors.get("generateComponentSupportBundle|" + componentId);
        if (forced != null) {
            send(exchange, forced.status(), errorBody(forced.code(), forced.message()));
            return;
        }
        String taskId = generateTasks.get(componentId);
        if (taskId == null) {
            send(exchange, 404, errorBody("NO_SUCH_COMPONENT", componentId));
            return;
        }
        send(exchange, 202, taskDocument(taskId, componentId, statusAt(taskId, 0), false));
    }

    private void serveGetTask(HttpExchange exchange, Map<String, String> params) throws IOException {
        String taskId = params.get("taskId");
        TaskScript script = taskScripts.get(taskId);
        if (script == null) {
            send(exchange, 404, errorBody("NO_SUCH_TASK", taskId));
            return;
        }
        int index = taskPolls.computeIfAbsent(taskId, unused -> new AtomicInteger()).getAndIncrement();
        String status = script.statuses().get(Math.min(index, script.statuses().size() - 1));
        boolean terminal = status.equals("SUCCEEDED") || status.equals("FAILED") || status.equals("CANCELED");
        send(exchange, 200, taskDocument(taskId, null, status, terminal));
    }

    private void serveList(HttpExchange exchange, Map<String, String> params) throws IOException {
        String componentId = params.get("componentId");
        ApiError forced = errors.get("getComponentSupportBundles|" + componentId);
        if (forced != null) {
            send(exchange, forced.status(), errorBody(forced.code(), forced.message()));
            return;
        }
        List<Bundle> reply = bundles.getOrDefault(componentId, List.of());
        StringBuilder out = new StringBuilder("[");
        for (int i = 0; i < reply.size(); i++) {
            Bundle bundle = reply.get(i);
            if (i > 0) {
                out.append(',');
            }
            out.append("{\"id\":").append(Json.quote(bundle.id()))
                    .append(",\"createdTimestamp\":").append(Json.quote(bundle.createdTimestamp()))
                    .append(",\"size\":").append(bundle.size())
                    .append(",\"name\":").append(Json.quote(bundle.name()))
                    .append(",\"url\":").append(Json.quote(bundle.url()))
                    .append('}');
        }
        send(exchange, 200, out.append(']').toString());
    }

    private void serveDelete(HttpExchange exchange, Map<String, String> params) throws IOException {
        String componentId = params.get("componentId");
        String bundleId = params.get("supportBundleId");
        ApiError forced = errors.get("deleteComponentSupportBundle|" + componentId);
        if (forced != null) {
            send(exchange, forced.status(), errorBody(forced.code(), forced.message()));
            return;
        }
        String taskId = deleteTasks.get(componentId + "/" + bundleId);
        if (taskId == null) {
            send(exchange, 404, errorBody("NO_SUCH_BUNDLE", bundleId));
            return;
        }
        send(exchange, 202, taskDocument(taskId, componentId, statusAt(taskId, 0), false));
    }

    private String statusAt(String taskId, int index) {
        TaskScript script = taskScripts.get(taskId);
        if (script == null) {
            return "PENDING";
        }
        return script.statuses().get(Math.min(index, script.statuses().size() - 1));
    }

    private String taskDocument(String taskId, String componentId, String status, boolean terminal) {
        TaskScript script = taskScripts.get(taskId);
        StringBuilder out = new StringBuilder("{");
        out.append("\"id\":").append(Json.quote(taskId));
        out.append(",\"name\":").append(Json.quote("sddc_lcm_support_bundle"));
        out.append(",\"status\":").append(Json.quote(status));
        out.append(",\"type\":").append(Json.quote("support-bundle"));
        out.append(",\"createdBy\":").append(Json.quote("admin"));
        if (componentId != null) {
            out.append(",\"resourceId\":").append(Json.quote(componentId));
            out.append(",\"resourceType\":").append(Json.quote("COMPONENT"));
        }
        out.append(",\"createTime\":").append(Json.quote("2026-05-13T11:27:00.000Z"));
        out.append(",\"startTime\":").append(Json.quote("2026-05-13T11:27:01.000Z"));
        out.append(",\"cancellable\":").append(!terminal);
        out.append(",\"retriable\":").append(terminal && !status.equals("SUCCEEDED"));
        out.append(",\"taskSummary\":{\"totalSubTasks\":0,\"totalSteps\":3}");
        if (terminal && script != null && script.failedStage() != null) {
            out.append(",\"stages\":[");
            out.append(stage("stage-collect", "support-bundle-collect", "SUCCEEDED",
                    "INFO", "Collected component logs"));
            out.append(',');
            out.append(stage("stage-upload", script.failedStage(), "FAILED",
                    "ERROR", script.errorMessage()));
            out.append(',');
            out.append(stage("stage-publish", "support-bundle-publish", "SKIPPED",
                    "INFO", "Publication skipped"));
            out.append(']');
        } else if (terminal) {
            out.append(",\"stages\":[");
            out.append(stage("stage-collect", "support-bundle-collect", "SUCCEEDED",
                    "INFO", "Collected component logs"));
            out.append(',');
            out.append(stage("stage-upload", "support-bundle-upload", "SUCCEEDED",
                    "INFO", "Uploaded bundle"));
            out.append(']');
        }
        if (terminal) {
            out.append(",\"endTime\":").append(Json.quote("2026-05-13T11:29:00.000Z"));
        }
        return out.append('}').toString();
    }

    private static String stage(String id, String name, String status, String level, String message) {
        return "{\"id\":" + Json.quote(id)
                + ",\"name\":" + Json.quote(name)
                + ",\"status\":" + Json.quote(status)
                + ",\"messages\":[{\"level\":" + Json.quote(level)
                + ",\"stageId\":" + Json.quote(id)
                + ",\"message\":{\"id\":\"com.broadcom.lcm.ops.supportbundle\",\"defaultMessage\":"
                + Json.quote(message)
                + ",\"localizedMessage\":" + Json.quote(message) + "}}]}";
    }

    private static String errorBody(String code, String message) {
        return "{\"code\":" + Json.quote(code)
                + ",\"message\":{\"id\":\"com.broadcom.lcm.error\",\"defaultMessage\":" + Json.quote(message)
                + ",\"localizedMessage\":" + Json.quote(message) + "}"
                + ",\"resolution\":{\"id\":\"com.broadcom.lcm.resolution\","
                + "\"defaultMessage\":\"Retry the operation.\",\"localizedMessage\":\"Retry the operation.\"}"
                + ",\"referenceId\":\"ref-0001\""
                + ",\"timestamp\":\"2026-05-13T11:27:00.000Z\"}";
    }

    private static void send(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().add("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    private static byte[] readAll(InputStream in) throws IOException {
        try (in) {
            return in.readAllBytes();
        }
    }

    // ---------------------------------------------------------------- minimal JSON

    /** Dependency-free JSON reader used by the mock and the wire verifier. */
    public static final class Json {

        private final String text;
        private int at;

        private Json(String text) {
            this.text = text;
        }

        public static Object parse(String text) {
            Json json = new Json(text);
            json.ws();
            Object value = json.value();
            json.ws();
            if (json.at != text.length()) {
                throw new IllegalArgumentException("trailing content at offset " + json.at);
            }
            return value;
        }

        /** Walks object keys; an integer step indexes an array. Returns null when absent. */
        public static Object get(Object node, Object... path) {
            Object current = node;
            for (Object step : path) {
                if (step instanceof Integer index) {
                    if (!(current instanceof List<?> list) || index < 0 || index >= list.size()) {
                        return null;
                    }
                    current = list.get(index);
                } else {
                    if (!(current instanceof Map<?, ?> map)) {
                        return null;
                    }
                    current = map.get(step);
                }
            }
            return current;
        }

        public static String str(Object node) {
            return node instanceof String s ? s : null;
        }

        @SuppressWarnings("unchecked")
        public static List<Object> arr(Object node) {
            return node instanceof List<?> list ? (List<Object>) list : List.of();
        }

        @SuppressWarnings("unchecked")
        public static Map<String, Object> obj(Object node) {
            return node instanceof Map<?, ?> map ? (Map<String, Object>) map : Map.of();
        }

        public static String quote(String raw) {
            if (raw == null) {
                return "null";
            }
            StringBuilder out = new StringBuilder("\"");
            for (int i = 0; i < raw.length(); i++) {
                char c = raw.charAt(i);
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
            return out.append('"').toString();
        }

        private void ws() {
            while (at < text.length() && Character.isWhitespace(text.charAt(at))) {
                at++;
            }
        }

        private Object value() {
            if (at >= text.length()) {
                throw new IllegalArgumentException("unexpected end of input");
            }
            char c = text.charAt(at);
            return switch (c) {
                case '{' -> object();
                case '[' -> array();
                case '"' -> string();
                case 't' -> literal("true", Boolean.TRUE);
                case 'f' -> literal("false", Boolean.FALSE);
                case 'n' -> literal("null", null);
                default -> number();
            };
        }

        private Object literal(String token, Object result) {
            if (!text.startsWith(token, at)) {
                throw new IllegalArgumentException("bad literal at offset " + at);
            }
            at += token.length();
            return result;
        }

        private Map<String, Object> object() {
            Map<String, Object> out = new LinkedHashMap<>();
            at++;
            ws();
            if (at < text.length() && text.charAt(at) == '}') {
                at++;
                return out;
            }
            while (true) {
                ws();
                String key = string();
                ws();
                expect(':');
                ws();
                out.put(key, value());
                ws();
                if (at < text.length() && text.charAt(at) == ',') {
                    at++;
                    continue;
                }
                expect('}');
                return out;
            }
        }

        private List<Object> array() {
            List<Object> out = new ArrayList<>();
            at++;
            ws();
            if (at < text.length() && text.charAt(at) == ']') {
                at++;
                return out;
            }
            while (true) {
                ws();
                out.add(value());
                ws();
                if (at < text.length() && text.charAt(at) == ',') {
                    at++;
                    continue;
                }
                expect(']');
                return out;
            }
        }

        private String string() {
            expect('"');
            StringBuilder out = new StringBuilder();
            while (true) {
                if (at >= text.length()) {
                    throw new IllegalArgumentException("unterminated string");
                }
                char c = text.charAt(at++);
                if (c == '"') {
                    return out.toString();
                }
                if (c != '\\') {
                    out.append(c);
                    continue;
                }
                char esc = text.charAt(at++);
                switch (esc) {
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
                    default -> throw new IllegalArgumentException("bad escape \\" + esc);
                }
            }
        }

        private Object number() {
            int start = at;
            while (at < text.length() && "+-.eE0123456789".indexOf(text.charAt(at)) >= 0) {
                at++;
            }
            String token = text.substring(start, at);
            if (token.isEmpty()) {
                throw new IllegalArgumentException("bad value at offset " + start);
            }
            if (token.contains(".") || token.contains("e") || token.contains("E")) {
                return Double.parseDouble(token);
            }
            return Long.parseLong(token);
        }

        private void expect(char c) {
            if (at >= text.length() || text.charAt(at) != c) {
                throw new IllegalArgumentException("expected '" + c + "' at offset " + at);
            }
            at++;
        }
    }
}
