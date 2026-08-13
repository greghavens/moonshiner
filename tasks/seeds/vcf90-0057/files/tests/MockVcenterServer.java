import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Loopback stand-in for a VCF 9.0 vCenter, pinned to {@code docs/contract.json}.
 *
 * <p>The route table is built from the contract's operation list, so the server answers only
 * {@code Vcenter.VM_list} and {@code Vcenter.VM_create}; every other method and path is refused
 * with HTTP 404 and still recorded, which lets a test prove the client stayed inside the contract.
 * Every request is appended to a log the test can read back.
 *
 * <p>This is a protected fixture. It listens on the loopback interface only and contacts nothing.
 */
public final class MockVcenterServer {

    private static final String LOOPBACK_HOST = "127.0.0.1";

    private final Map<String, String> routes = new LinkedHashMap<>();
    private final Set<String> listQueryParameters = new LinkedHashSet<>();
    private final String sessionId;

    private final Object lock = new Object();
    private final List<Recorded> log = new ArrayList<>();
    private final List<Vm> vms = new ArrayList<>();
    private final Set<String> hiddenOnce = new HashSet<>();
    private int nextVmNumber = 1001;

    private HttpServer server;
    private ExecutorService executor;
    private String baseUrl;

    /**
     * @param contractPath path to {@code docs/contract.json}
     * @param sessionId the value this server requires in the contract's authentication header
     */
    public MockVcenterServer(Path contractPath, String sessionId) throws IOException {
        this.sessionId = sessionId;
        Object document = Json.read(Files.readString(contractPath, StandardCharsets.UTF_8));
        Map<?, ?> contract = asObject(document, "contract");
        Map<?, ?> source = asObject(contract.get("source"), "contract.source");
        String basePath = String.valueOf(source.get("server_base_path"));

        for (Object element : asArray(contract.get("operations"), "contract.operations")) {
            Map<?, ?> operation = asObject(element, "contract operation");
            String operationId = String.valueOf(operation.get("operationId"));
            String method = String.valueOf(operation.get("method")).toUpperCase(Locale.ROOT);
            routes.put(method + " " + basePath + operation.get("path"), operationId);
            if ("Vcenter.VM_list".equals(operationId)) {
                for (Object parameter : asArray(operation.get("parameters"), "parameters")) {
                    Map<?, ?> entry = asObject(parameter, "parameter");
                    if ("query".equals(entry.get("in"))) {
                        listQueryParameters.add(String.valueOf(entry.get("name")));
                    }
                }
            }
        }
        if (routes.size() != 2 || listQueryParameters.isEmpty()) {
            throw new IOException("contract did not describe the two expected operations");
        }
    }

    /** Starts the server on an ephemeral loopback port. */
    public void start() throws IOException {
        server = HttpServer.create(new InetSocketAddress(LOOPBACK_HOST, 0), 0);
        executor = Executors.newSingleThreadExecutor(runnable -> {
            Thread thread = new Thread(runnable, "mock-vcenter");
            thread.setDaemon(true);
            return thread;
        });
        server.setExecutor(executor);
        server.createContext("/", this::handle);
        server.start();
        baseUrl = "http://" + LOOPBACK_HOST + ":" + server.getAddress().getPort() + "/api";
    }

    public void stop() {
        if (server != null) {
            server.stop(0);
        }
        if (executor != null) {
            executor.shutdownNow();
        }
    }

    /** Base URL including the {@code /api} server base path from the contract. */
    public String baseUrl() {
        return baseUrl;
    }

    /**
     * Seeds a virtual machine that exists in inventory but is withheld from the next
     * {@code Vcenter.VM_list} response that would otherwise report it.
     *
     * <p>This reproduces the window in which another caller has already created the machine but
     * this caller's lookup cannot see it yet.
     */
    public void seedHiddenVirtualMachine(String name, String folder, String vmId) {
        synchronized (lock) {
            seedVirtualMachine(name, folder, vmId);
            hiddenOnce.add(vmId);
        }
    }

    /** Adds a visible inventory entry without going through {@code Vcenter.VM_create}. */
    public void seedVirtualMachine(String name, String folder, String vmId) {
        synchronized (lock) {
            if (findVirtualMachine(name, folder) != null) {
                throw new IllegalArgumentException(
                        "a virtual machine named " + name + " already exists in " + folder);
            }
            vms.add(new Vm(vmId, name, folder));
        }
    }

    /** Every request this server received, oldest first. */
    public List<Recorded> requests() {
        synchronized (lock) {
            return List.copyOf(log);
        }
    }

    /** Requests received since the last call to {@link #mark()}. */
    public List<Recorded> requestsSince(int mark) {
        synchronized (lock) {
            return List.copyOf(log.subList(mark, log.size()));
        }
    }

    /** Number of requests recorded so far, for use with {@link #requestsSince(int)}. */
    public int mark() {
        synchronized (lock) {
            return log.size();
        }
    }

    private void handle(HttpExchange exchange) throws IOException {
        String method = exchange.getRequestMethod().toUpperCase(Locale.ROOT);
        URI uri = exchange.getRequestURI();
        String rawQuery = uri.getRawQuery();
        String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        Map<String, List<String>> query = parseQuery(rawQuery);
        Map<String, String> headers = new LinkedHashMap<>();
        exchange.getRequestHeaders().forEach((name, values) ->
                headers.put(name.toLowerCase(Locale.ROOT), String.join(",", values)));

        String operationId = routes.get(method + " " + uri.getPath());
        Response response;
        if (operationId == null) {
            response = error(404, "NOT_FOUND", "no operation is bound to " + method + " " + uri.getPath());
        } else if (!sessionId.equals(headers.get("vmware-api-session-id"))) {
            response = error(401, "UNAUTHENTICATED", "missing or unusable vmware-api-session-id header");
        } else if ("Vcenter.VM_list".equals(operationId)) {
            response = list(query);
        } else {
            response = create(headers.get("content-type"), body);
        }

        synchronized (lock) {
            log.add(new Recorded(method, uri.getPath(), rawQuery, query, headers, body,
                    operationId, response.status));
        }
        byte[] payload = response.body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(response.status, payload.length);
        exchange.getResponseBody().write(payload);
        exchange.close();
    }

    private Response list(Map<String, List<String>> query) {
        for (String parameter : query.keySet()) {
            if (!listQueryParameters.contains(parameter)) {
                return error(400, "INVALID_ARGUMENT",
                        "Vcenter.VM.FilterSpec has no query parameter named " + parameter);
            }
        }
        List<String> names = query.getOrDefault("names", List.of());
        List<String> folders = query.getOrDefault("folders", List.of());

        StringBuilder out = new StringBuilder("[");
        synchronized (lock) {
            boolean first = true;
            for (Vm vm : List.copyOf(vms)) {
                if (!names.isEmpty() && !names.contains(vm.name)) {
                    continue;
                }
                if (!folders.isEmpty() && !folders.contains(vm.folder)) {
                    continue;
                }
                if (hiddenOnce.remove(vm.id)) {
                    continue;
                }
                if (!first) {
                    out.append(',');
                }
                first = false;
                out.append("{\"vm\":\"").append(vm.id)
                        .append("\",\"name\":\"").append(vm.name)
                        .append("\",\"power_state\":\"POWERED_OFF\"}");
            }
        }
        return new Response(200, out.append(']').toString());
    }

    private Response create(String contentType, String body) {
        if (contentType == null || !contentType.toLowerCase(Locale.ROOT).startsWith("application/json")) {
            return error(400, "INVALID_ARGUMENT", "Vcenter.VM_create requires application/json");
        }
        Object document;
        try {
            document = Json.read(body);
        } catch (RuntimeException malformed) {
            return error(400, "INVALID_ARGUMENT", "request body is not a JSON document");
        }
        if (!(document instanceof Map)) {
            return error(400, "INVALID_ARGUMENT", "Vcenter.VM.CreateSpec must be a JSON object");
        }
        Map<?, ?> spec = (Map<?, ?>) document;
        if (!(spec.get("guest_os") instanceof String)) {
            return error(400, "INVALID_ARGUMENT", "Vcenter.VM.CreateSpec.guest_os is required");
        }
        Object requestedName = spec.get("name");
        if (requestedName != null && !(requestedName instanceof String)) {
            return error(400, "INVALID_ARGUMENT", "Vcenter.VM.CreateSpec.name must be a string");
        }
        String folder = null;
        Object placement = spec.get("placement");
        if (placement instanceof Map && ((Map<?, ?>) placement).get("folder") instanceof String) {
            folder = (String) ((Map<?, ?>) placement).get("folder");
        }

        synchronized (lock) {
            String name = requestedName instanceof String
                    ? (String) requestedName
                    : "server-generated-" + nextVmNumber;
            if (findVirtualMachine(name, folder) != null) {
                return error(400, "ALREADY_EXISTS",
                        "A virtual machine named " + name + " already exists in " + folder + ".");
            }
            String id = "vm-" + nextVmNumber++;
            vms.add(new Vm(id, name, folder));
            return new Response(201, "\"" + id + "\"");
        }
    }

    private Vm findVirtualMachine(String name, String folder) {
        for (Vm vm : vms) {
            if (vm.name.equals(name) && java.util.Objects.equals(vm.folder, folder)) {
                return vm;
            }
        }
        return null;
    }

    private static Response error(int status, String errorType, String message) {
        return new Response(status, "{\"error_type\":\"" + errorType + "\",\"messages\":[{"
                + "\"id\":\"com.vmware.api.vcenter.vm.mock\","
                + "\"default_message\":\"" + message + "\","
                + "\"args\":[]}]}");
    }

    private static Map<String, List<String>> parseQuery(String rawQuery) {
        Map<String, List<String>> parsed = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return parsed;
        }
        for (String pair : rawQuery.split("&", -1)) {
            if (pair.isEmpty()) {
                continue;
            }
            int split = pair.indexOf('=');
            String name = split < 0 ? pair : pair.substring(0, split);
            String value = split < 0 ? "" : pair.substring(split + 1);
            parsed.computeIfAbsent(decode(name), key -> new ArrayList<>()).add(decode(value));
        }
        parsed.replaceAll((key, values) -> Collections.unmodifiableList(values));
        return parsed;
    }

    private static String decode(String value) {
        return URLDecoder.decode(value, StandardCharsets.UTF_8);
    }

    private static Map<?, ?> asObject(Object value, String what) throws IOException {
        if (!(value instanceof Map)) {
            throw new IOException(what + " is not a JSON object");
        }
        return (Map<?, ?>) value;
    }

    private static List<?> asArray(Object value, String what) throws IOException {
        if (!(value instanceof List)) {
            throw new IOException(what + " is not a JSON array");
        }
        return (List<?>) value;
    }

    private static final class Vm {
        final String id;
        final String name;
        final String folder;

        Vm(String id, String name, String folder) {
            this.id = id;
            this.name = name;
            this.folder = folder;
        }
    }

    private static final class Response {
        final int status;
        final String body;

        Response(int status, String body) {
            this.status = status;
            this.body = body;
        }
    }

    /** One request as the server received it. */
    public static final class Recorded {
        private final String method;
        private final String path;
        private final String rawQuery;
        private final Map<String, List<String>> query;
        private final Map<String, String> headers;
        private final String body;
        private final String operationId;
        private final int status;

        Recorded(String method, String path, String rawQuery, Map<String, List<String>> query,
                 Map<String, String> headers, String body, String operationId, int status) {
            this.method = method;
            this.path = path;
            this.rawQuery = rawQuery;
            this.query = Collections.unmodifiableMap(query);
            this.headers = Collections.unmodifiableMap(headers);
            this.body = body;
            this.operationId = operationId;
            this.status = status;
        }

        public String method() {
            return method;
        }

        public String path() {
            return path;
        }

        /** The query string exactly as it arrived, before decoding. Null when there was none. */
        public String rawQuery() {
            return rawQuery;
        }

        /** Decoded query parameters, each mapped to its repeated values in arrival order. */
        public Map<String, List<String>> query() {
            return query;
        }

        /** Request header value by lower-case name, or null. */
        public String header(String name) {
            return headers.get(name.toLowerCase(Locale.ROOT));
        }

        public String body() {
            return body;
        }

        /** The contract operationId this request was routed to, or null when it matched none. */
        public String operationId() {
            return operationId;
        }

        public int status() {
            return status;
        }

        @Override
        public String toString() {
            return method + " " + path + (rawQuery == null ? "" : "?" + rawQuery)
                    + " -> " + status + " [" + operationId + "]";
        }
    }

    /**
     * Minimal JSON reader used by this fixture and by the test.
     *
     * <p>Numbers are kept as {@link Num} so a test can assert on the literal that was transmitted,
     * and JSON null is kept as {@link #NULL} so a test can tell an explicitly transmitted null
     * apart from an absent property.
     */
    public static final class Json {

        /** Sentinel for a JSON null that was actually present in the document. */
        public static final Object NULL = new Object() {
            @Override
            public String toString() {
                return "null";
            }
        };

        private final String text;
        private int at;

        private Json(String text) {
            this.text = text;
        }

        public static Object read(String text) {
            Json parser = new Json(text);
            parser.skipWhitespace();
            Object value = parser.readValue();
            parser.skipWhitespace();
            if (parser.at < parser.text.length()) {
                throw new IllegalArgumentException("trailing content in JSON document");
            }
            return value;
        }

        private void skipWhitespace() {
            while (at < text.length() && Character.isWhitespace(text.charAt(at))) {
                at++;
            }
        }

        private Object readValue() {
            skipWhitespace();
            if (at >= text.length()) {
                throw new IllegalArgumentException("empty JSON document");
            }
            switch (text.charAt(at)) {
                case '{':
                    return readObject();
                case '[':
                    return readArray();
                case '"':
                    return readString();
                case 't':
                    expect("true");
                    return Boolean.TRUE;
                case 'f':
                    expect("false");
                    return Boolean.FALSE;
                case 'n':
                    expect("null");
                    return NULL;
                default:
                    return readNumber();
            }
        }

        private Map<String, Object> readObject() {
            Map<String, Object> object = new LinkedHashMap<>();
            at++;
            skipWhitespace();
            if (at < text.length() && text.charAt(at) == '}') {
                at++;
                return object;
            }
            while (true) {
                skipWhitespace();
                String key = readString();
                skipWhitespace();
                require(':');
                object.put(key, readValue());
                skipWhitespace();
                if (at >= text.length()) {
                    throw new IllegalArgumentException("unterminated JSON object");
                }
                char character = text.charAt(at++);
                if (character == '}') {
                    return object;
                }
                if (character != ',') {
                    throw new IllegalArgumentException("malformed JSON object");
                }
            }
        }

        private List<Object> readArray() {
            List<Object> array = new ArrayList<>();
            at++;
            skipWhitespace();
            if (at < text.length() && text.charAt(at) == ']') {
                at++;
                return array;
            }
            while (true) {
                array.add(readValue());
                skipWhitespace();
                if (at >= text.length()) {
                    throw new IllegalArgumentException("unterminated JSON array");
                }
                char character = text.charAt(at++);
                if (character == ']') {
                    return array;
                }
                if (character != ',') {
                    throw new IllegalArgumentException("malformed JSON array");
                }
            }
        }

        private String readString() {
            require('"');
            StringBuilder value = new StringBuilder();
            while (true) {
                if (at >= text.length()) {
                    throw new IllegalArgumentException("unterminated JSON string");
                }
                char character = text.charAt(at++);
                if (character == '"') {
                    return value.toString();
                }
                if (character != '\\') {
                    value.append(character);
                    continue;
                }
                char escape = text.charAt(at++);
                switch (escape) {
                    case '"':
                    case '\\':
                    case '/':
                        value.append(escape);
                        break;
                    case 'b':
                        value.append('\b');
                        break;
                    case 'f':
                        value.append('\f');
                        break;
                    case 'n':
                        value.append('\n');
                        break;
                    case 'r':
                        value.append('\r');
                        break;
                    case 't':
                        value.append('\t');
                        break;
                    case 'u':
                        value.append((char) Integer.parseInt(text.substring(at, at + 4), 16));
                        at += 4;
                        break;
                    default:
                        throw new IllegalArgumentException("unsupported JSON escape");
                }
            }
        }

        private Num readNumber() {
            int start = at;
            while (at < text.length() && "+-.eE0123456789".indexOf(text.charAt(at)) >= 0) {
                at++;
            }
            if (at == start) {
                throw new IllegalArgumentException("malformed JSON value at offset " + start);
            }
            return new Num(text.substring(start, at));
        }

        private void require(char expected) {
            if (at >= text.length() || text.charAt(at) != expected) {
                throw new IllegalArgumentException("expected '" + expected + "' at offset " + at);
            }
            at++;
        }

        private void expect(String literal) {
            if (!text.startsWith(literal, at)) {
                throw new IllegalArgumentException("expected '" + literal + "'");
            }
            at += literal.length();
        }
    }

    /** A JSON number, preserving the literal exactly as it was transmitted. */
    public static final class Num {
        private final String literal;

        Num(String literal) {
            this.literal = literal;
        }

        public String literal() {
            return literal;
        }

        public boolean isIntegerLiteral() {
            return literal.matches("-?(0|[1-9][0-9]*)");
        }

        @Override
        public boolean equals(Object other) {
            return other instanceof Num && literal.equals(((Num) other).literal);
        }

        @Override
        public int hashCode() {
            return literal.hashCode();
        }

        @Override
        public String toString() {
            return literal;
        }
    }
}
