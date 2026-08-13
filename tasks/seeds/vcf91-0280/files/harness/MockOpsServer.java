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
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;

/**
 * Loopback-only stand-in for a VCF Operations appliance.
 *
 * <p>The server is pinned to {@code docs/contract.json}: it reads the contract at startup and
 * registers a handler for each {@code operationId} the contract names. An operation named by the
 * contract that this class has no implementation for is a hard startup failure, and any request
 * that does not match one of those routes is answered with 404 and recorded as unmatched. Nothing
 * outside the contract is served.
 *
 * <p>Every request is appended to a JSON Lines request log so that a test or verifier can assert
 * the exact wire shape that a client produced.
 *
 * <p>This is test scaffolding. Do not modify it; the verifier checks its integrity.
 */
public final class MockOpsServer implements AutoCloseable {

    /** vCenter endpoints this fake appliance cannot reach, keyed by the VCURL resource identifier. */
    private static final List<String> UNREACHABLE_VCURLS =
            List.of("vcenter-down.lab.local", "vcenter-unreachable.lab.local");

    /** An adapter instance name that already exists, so creating it again is rejected. */
    private static final String CONFLICTING_ADAPTER_NAME = "Duplicate VC Adapter Instance";

    private final Path logPath;
    private final String basePath;
    private final Map<String, Route> routes = new LinkedHashMap<>();
    private HttpServer server;
    private int seq = 0;

    private record Route(String operationId, String method, String path) {}

    public MockOpsServer(Path contractPath, Path logPath) throws IOException {
        this.logPath = logPath;
        Object parsed = Json.parse(Files.readString(contractPath, StandardCharsets.UTF_8));
        Map<String, Object> contract = Json.asObject(parsed, "contract root");
        this.basePath = Json.asString(contract.get("serverBasePath"), "serverBasePath");

        List<Object> ops = Json.asArray(contract.get("operations"), "operations");
        for (Object o : ops) {
            Map<String, Object> op = Json.asObject(o, "operation");
            String operationId = Json.asString(op.get("operationId"), "operationId");
            String method = Json.asString(op.get("method"), "method").toUpperCase(Locale.ROOT);
            String path = Json.asString(op.get("path"), "path");
            if (!isImplemented(operationId)) {
                throw new IllegalStateException(
                        "contract names operationId '" + operationId + "' but MockOpsServer has no implementation for it");
            }
            routes.put(method + " " + basePath + path, new Route(operationId, method, basePath + path));
        }
        if (routes.isEmpty()) {
            throw new IllegalStateException("contract named no operations");
        }
        Files.createDirectories(logPath.toAbsolutePath().getParent());
        Files.writeString(logPath, "", StandardCharsets.UTF_8);
    }

    private static boolean isImplemented(String operationId) {
        return operationId.equals("testConnection") || operationId.equals("createAdapterInstance");
    }

    public MockOpsServer start() throws IOException {
        server = HttpServer.create(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0), 0);
        server.createContext("/", this::dispatch);
        server.setExecutor(null);
        server.start();
        return this;
    }

    /** Origin only, with no base path: the client is expected to append the contract's base path. */
    public String origin() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    /** Writes a scenario boundary into the request log so assertions can be grouped. */
    public synchronized void mark(String scenario) {
        Map<String, Object> entry = new LinkedHashMap<>();
        entry.put("type", "marker");
        entry.put("scenario", scenario);
        append(entry);
    }

    @Override
    public void close() {
        if (server != null) {
            server.stop(0);
        }
    }

    private void dispatch(HttpExchange exchange) throws IOException {
        String method = exchange.getRequestMethod().toUpperCase(Locale.ROOT);
        String path = exchange.getRequestURI().getPath();
        String rawQuery = exchange.getRequestURI().getRawQuery();
        byte[] bodyBytes;
        try (InputStream in = exchange.getRequestBody()) {
            bodyBytes = in.readAllBytes();
        }
        String body = new String(bodyBytes, StandardCharsets.UTF_8);
        Route route = routes.get(method + " " + path);

        Map<String, Object> entry = new LinkedHashMap<>();
        synchronized (this) {
            entry.put("type", "request");
            entry.put("seq", ++seq);
            entry.put("operationId", route == null ? null : route.operationId());
            entry.put("method", method);
            entry.put("path", path);
            entry.put("rawQuery", rawQuery);
            entry.put("query", parseQuery(rawQuery));
            entry.put("headers", capturedHeaders(exchange));
            entry.put("bodyLength", bodyBytes.length);
            entry.put("body", body);
        }

        int status;
        String response;
        if (route == null) {
            status = 404;
            response = errorBody("No operation is served at " + method + " " + path);
        } else if (route.operationId().equals("testConnection")) {
            String[] r = handleTestConnection(body);
            status = Integer.parseInt(r[0]);
            response = r[1];
        } else {
            String[] r = handleCreateAdapterInstance(body);
            status = Integer.parseInt(r[0]);
            response = r[1];
        }

        synchronized (this) {
            entry.put("responseStatus", status);
            append(entry);
        }

        byte[] out = response.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().add("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, out.length);
        try (OutputStream os = exchange.getResponseBody()) {
            os.write(out);
        }
    }

    private String[] handleTestConnection(String body) {
        Map<String, Object> req;
        try {
            req = Json.asObject(Json.parse(body), "request body");
        } catch (RuntimeException e) {
            return new String[] {"400", errorBody("Request body is not valid JSON: " + e.getMessage())};
        }
        String missing = missingRequired(req);
        if (missing != null) {
            return new String[] {"400", errorBody(missing)};
        }
        String vcurl = resourceIdentifier(req, "VCURL");
        if (vcurl != null && UNREACHABLE_VCURLS.contains(vcurl)) {
            return new String[] {
                "400",
                errorBody("Unable to establish a connection to the data source at " + vcurl
                        + ": connection timed out")
            };
        }
        return new String[] {"201", adapterInstanceBody(req, false)};
    }

    private String[] handleCreateAdapterInstance(String body) {
        Map<String, Object> req;
        try {
            req = Json.asObject(Json.parse(body), "request body");
        } catch (RuntimeException e) {
            return new String[] {"400", errorBody("Request body is not valid JSON: " + e.getMessage())};
        }
        String missing = missingRequired(req);
        if (missing != null) {
            return new String[] {"400", errorBody(missing)};
        }
        String name = String.valueOf(req.get("name"));
        if (CONFLICTING_ADAPTER_NAME.equals(name)) {
            return new String[] {
                "400", errorBody("An adapter instance named '" + name + "' already exists")
            };
        }
        return new String[] {"201", adapterInstanceBody(req, true)};
    }

    private static String missingRequired(Map<String, Object> req) {
        for (String required : new String[] {"name", "adapterKindKey"}) {
            Object v = req.get(required);
            if (!(v instanceof String s) || s.isEmpty()) {
                return "Required property '" + required + "' is missing or empty";
            }
        }
        return null;
    }

    @SuppressWarnings("unchecked")
    private static String resourceIdentifier(Map<String, Object> req, String name) {
        Object ids = req.get("resourceIdentifiers");
        if (!(ids instanceof List<?> list)) {
            return null;
        }
        for (Object o : list) {
            if (o instanceof Map<?, ?> m && name.equals(m.get("name"))) {
                Object v = m.get("value");
                return v == null ? null : String.valueOf(v);
            }
        }
        return null;
    }

    /** Shapes a response after the spec's adapter-instance example. */
    private static String adapterInstanceBody(Map<String, Object> req, boolean persisted) {
        String name = String.valueOf(req.get("name"));
        String adapterKindKey = String.valueOf(req.get("adapterKindKey"));
        String id = UUID.nameUUIDFromBytes((persisted ? "created:" : "tested:").concat(name)
                .getBytes(StandardCharsets.UTF_8)).toString();
        StringBuilder sb = new StringBuilder();
        sb.append("{\"resourceKey\":{\"name\":").append(Json.quote(name));
        sb.append(",\"adapterKindKey\":").append(Json.quote(adapterKindKey));
        sb.append(",\"resourceKindKey\":").append(Json.quote(adapterKindKey + " Adapter Instance"));
        sb.append(",\"resourceIdentifiers\":[]}");
        Object description = req.get("description");
        if (description instanceof String s) {
            sb.append(",\"description\":").append(Json.quote(s));
        }
        sb.append(",\"id\":").append(Json.quote(id)).append("}");
        return sb.toString();
    }

    private static String errorBody(String message) {
        return "{\"message\":" + Json.quote(message) + ",\"httpStatusCode\":400}";
    }

    private static Map<String, Object> capturedHeaders(HttpExchange exchange) {
        Map<String, Object> headers = new LinkedHashMap<>();
        for (String name : new String[] {"Authorization", "Content-Type", "Accept"}) {
            List<String> values = exchange.getRequestHeaders().get(name);
            headers.put(name, values == null || values.isEmpty() ? null : String.join(", ", values));
        }
        return headers;
    }

    /** Parses a raw query string into name -> value; a repeated name keeps the last value. */
    private static Map<String, Object> parseQuery(String rawQuery) {
        Map<String, Object> out = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return out;
        }
        for (String pair : rawQuery.split("&")) {
            if (pair.isEmpty()) {
                continue;
            }
            int eq = pair.indexOf('=');
            String k = eq < 0 ? pair : pair.substring(0, eq);
            String v = eq < 0 ? "" : pair.substring(eq + 1);
            out.put(java.net.URLDecoder.decode(k, StandardCharsets.UTF_8),
                    java.net.URLDecoder.decode(v, StandardCharsets.UTF_8));
        }
        return out;
    }

    private void append(Map<String, Object> entry) {
        try {
            Files.writeString(logPath, Json.write(entry) + System.lineSeparator(), StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        } catch (IOException e) {
            throw new UncheckedIOExceptionWrapper(e);
        }
    }

    private static final class UncheckedIOExceptionWrapper extends RuntimeException {
        UncheckedIOExceptionWrapper(IOException cause) {
            super(cause);
        }
    }

    /** Minimal dependency-free JSON reader/writer, sufficient for the contract and the request log. */
    static final class Json {

        private final String src;
        private int pos;

        private Json(String src) {
            this.src = src;
        }

        static Object parse(String text) {
            Json p = new Json(text);
            p.skipWs();
            Object value = p.readValue();
            p.skipWs();
            if (p.pos != p.src.length()) {
                throw new IllegalArgumentException("trailing content at offset " + p.pos);
            }
            return value;
        }

        @SuppressWarnings("unchecked")
        static Map<String, Object> asObject(Object o, String what) {
            if (!(o instanceof Map)) {
                throw new IllegalArgumentException(what + " is not a JSON object");
            }
            return (Map<String, Object>) o;
        }

        @SuppressWarnings("unchecked")
        static List<Object> asArray(Object o, String what) {
            if (!(o instanceof List)) {
                throw new IllegalArgumentException(what + " is not a JSON array");
            }
            return (List<Object>) o;
        }

        static String asString(Object o, String what) {
            if (!(o instanceof String s)) {
                throw new IllegalArgumentException(what + " is not a JSON string");
            }
            return s;
        }

        private Object readValue() {
            char c = peek();
            return switch (c) {
                case '{' -> readObject();
                case '[' -> readArray();
                case '"' -> readString();
                case 't', 'f' -> readBoolean();
                case 'n' -> readNull();
                default -> readNumber();
            };
        }

        private Map<String, Object> readObject() {
            expect('{');
            Map<String, Object> out = new LinkedHashMap<>();
            skipWs();
            if (peek() == '}') {
                pos++;
                return out;
            }
            while (true) {
                skipWs();
                String key = readString();
                skipWs();
                expect(':');
                skipWs();
                out.put(key, readValue());
                skipWs();
                char c = next();
                if (c == '}') {
                    return out;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("expected ',' or '}' at offset " + (pos - 1));
                }
            }
        }

        private List<Object> readArray() {
            expect('[');
            List<Object> out = new ArrayList<>();
            skipWs();
            if (peek() == ']') {
                pos++;
                return out;
            }
            while (true) {
                skipWs();
                out.add(readValue());
                skipWs();
                char c = next();
                if (c == ']') {
                    return out;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("expected ',' or ']' at offset " + (pos - 1));
                }
            }
        }

        private String readString() {
            expect('"');
            StringBuilder sb = new StringBuilder();
            while (true) {
                char c = next();
                if (c == '"') {
                    return sb.toString();
                }
                if (c != '\\') {
                    sb.append(c);
                    continue;
                }
                char esc = next();
                switch (esc) {
                    case '"', '\\', '/' -> sb.append(esc);
                    case 'b' -> sb.append('\b');
                    case 'f' -> sb.append('\f');
                    case 'n' -> sb.append('\n');
                    case 'r' -> sb.append('\r');
                    case 't' -> sb.append('\t');
                    case 'u' -> {
                        sb.append((char) Integer.parseInt(src.substring(pos, pos + 4), 16));
                        pos += 4;
                    }
                    default -> throw new IllegalArgumentException("bad escape \\" + esc);
                }
            }
        }

        private Boolean readBoolean() {
            if (src.startsWith("true", pos)) {
                pos += 4;
                return Boolean.TRUE;
            }
            if (src.startsWith("false", pos)) {
                pos += 5;
                return Boolean.FALSE;
            }
            throw new IllegalArgumentException("bad literal at offset " + pos);
        }

        private Object readNull() {
            if (!src.startsWith("null", pos)) {
                throw new IllegalArgumentException("bad literal at offset " + pos);
            }
            pos += 4;
            return null;
        }

        private Object readNumber() {
            int start = pos;
            while (pos < src.length() && "+-0123456789.eE".indexOf(src.charAt(pos)) >= 0) {
                pos++;
            }
            String text = src.substring(start, pos);
            if (text.isEmpty()) {
                throw new IllegalArgumentException("expected a value at offset " + start);
            }
            if (text.contains(".") || text.contains("e") || text.contains("E")) {
                return Double.valueOf(text);
            }
            return Long.valueOf(text);
        }

        private void skipWs() {
            while (pos < src.length() && Character.isWhitespace(src.charAt(pos))) {
                pos++;
            }
        }

        private char peek() {
            if (pos >= src.length()) {
                throw new IllegalArgumentException("unexpected end of input");
            }
            return src.charAt(pos);
        }

        private char next() {
            char c = peek();
            pos++;
            return c;
        }

        private void expect(char c) {
            if (next() != c) {
                throw new IllegalArgumentException("expected '" + c + "' at offset " + (pos - 1));
            }
        }

        static String write(Object value) {
            StringBuilder sb = new StringBuilder();
            writeInto(sb, value);
            return sb.toString();
        }

        private static void writeInto(StringBuilder sb, Object value) {
            if (value == null) {
                sb.append("null");
            } else if (value instanceof String s) {
                sb.append(quote(s));
            } else if (value instanceof Boolean || value instanceof Number) {
                sb.append(value);
            } else if (value instanceof Map<?, ?> m) {
                sb.append('{');
                boolean first = true;
                for (Map.Entry<?, ?> e : m.entrySet()) {
                    if (!first) {
                        sb.append(',');
                    }
                    first = false;
                    sb.append(quote(String.valueOf(e.getKey()))).append(':');
                    writeInto(sb, e.getValue());
                }
                sb.append('}');
            } else if (value instanceof List<?> l) {
                sb.append('[');
                boolean first = true;
                for (Object o : l) {
                    if (!first) {
                        sb.append(',');
                    }
                    first = false;
                    writeInto(sb, o);
                }
                sb.append(']');
            } else {
                sb.append(quote(String.valueOf(value)));
            }
        }

        static String quote(String s) {
            StringBuilder sb = new StringBuilder(s.length() + 2);
            sb.append('"');
            for (int i = 0; i < s.length(); i++) {
                char c = s.charAt(i);
                switch (c) {
                    case '"' -> sb.append("\\\"");
                    case '\\' -> sb.append("\\\\");
                    case '\b' -> sb.append("\\b");
                    case '\f' -> sb.append("\\f");
                    case '\n' -> sb.append("\\n");
                    case '\r' -> sb.append("\\r");
                    case '\t' -> sb.append("\\t");
                    default -> {
                        if (c < 0x20) {
                            sb.append(String.format("\\u%04x", (int) c));
                        } else {
                            sb.append(c);
                        }
                    }
                }
            }
            return sb.append('"').toString();
        }
    }
}
