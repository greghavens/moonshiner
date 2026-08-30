import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Loopback-only fixture for the two VCF Automation operations pinned in docs/contract.json:
 * "Query Virtual Centers" and "Attach Virtual Center". No other route exists on this server,
 * and it holds real state: a successful attach becomes visible to the next query, and a second
 * attach of an already attached vCenter URL is rejected exactly as a duplicate registration.
 */
public final class MockVcfAutomation implements AutoCloseable {
    public static final String VIRTUAL_CENTERS_PATH = "/cloudapi/1.0.0/virtualCenters";
    public static final String ACCEPT = "application/json;version=9.1.0";
    public static final String DEFAULT_TOKEN = "fixture-jwt-never-real";

    /** A vCenter as the fixture stores it. */
    public record VCenter(String vcId, String name, String url, String username,
                          String description, Boolean isEnabled) {
    }

    /** One entry of the request log. */
    public record RecordedRequest(String method, String path, String rawQuery,
                                  Map<String, List<String>> headers, String body) {
        public String header(String name) {
            for (Map.Entry<String, List<String>> entry : headers.entrySet()) {
                if (entry.getKey().equalsIgnoreCase(name)) {
                    return entry.getValue().size() == 1 ? entry.getValue().get(0) : null;
                }
            }
            return null;
        }
    }

    private final HttpServer server;
    private final String bearerToken;
    private final List<VCenter> inventory = new ArrayList<>();
    private final List<RecordedRequest> requestLog = new ArrayList<>();
    private int attachCount;
    private int[] forcedAttachFailure;
    private String forcedMinorErrorCode;
    private String forcedMessage;
    private int[] forcedQueryStatus;
    private String forcedQueryBody;
    private boolean omitNextAttachLocation;

    public MockVcfAutomation(List<VCenter> seeded) throws IOException {
        this(DEFAULT_TOKEN, seeded);
    }

    public MockVcfAutomation(String bearerToken, List<VCenter> seeded) throws IOException {
        verifyPinnedContract();
        this.bearerToken = bearerToken;
        this.inventory.addAll(seeded);
        this.server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        // Only the contracted collection route exists. Nothing else is served.
        this.server.createContext(VIRTUAL_CENTERS_PATH, this::handleVirtualCenters);
        this.server.createContext("/", this::handleUnknownRoute);
        this.server.start();
    }

    public String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    public synchronized List<RecordedRequest> requestLog() {
        return List.copyOf(requestLog);
    }

    public synchronized List<VCenter> inventory() {
        return List.copyOf(inventory);
    }

    public synchronized long countRequests(String method) {
        return requestLog.stream().filter(request -> request.method().equals(method)).count();
    }

    /** Makes the next Attach Virtual Center answer with the given documented error instead. */
    public synchronized void failNextAttach(int status, String minorErrorCode, String message) {
        forcedAttachFailure = new int[] {status};
        forcedMinorErrorCode = minorErrorCode;
        forcedMessage = message;
    }

    /** Makes the next Query Virtual Centers answer with the given raw status and body instead. */
    public synchronized void respondToNextQueryWith(int status, String body) {
        forcedQueryStatus = new int[] {status};
        forcedQueryBody = body;
    }

    /** Makes the next successful attach omit the required asynchronous task Location header. */
    public synchronized void omitLocationFromNextAcceptedAttach() {
        omitNextAttachLocation = true;
    }

    /** The deterministic task id this fixture hands out for the n-th accepted attach, 1-based. */
    public static String taskUuid(int ordinal) {
        return String.format(Locale.ROOT, "5a7d0000-0000-4000-8000-%012d", ordinal);
    }

    public String taskLocation(int ordinal) {
        return baseUrl() + "/api/task/" + taskUuid(ordinal);
    }

    private void handleUnknownRoute(HttpExchange exchange) throws IOException {
        byte[] requestBytes = exchange.getRequestBody().readAllBytes();
        Map<String, List<String>> headers = new LinkedHashMap<>();
        exchange.getRequestHeaders().forEach((key, value) -> headers.put(key, List.copyOf(value)));
        RecordedRequest recorded = new RecordedRequest(exchange.getRequestMethod(),
                exchange.getRequestURI().getPath(), exchange.getRequestURI().getRawQuery(),
                Map.copyOf(headers), new String(requestBytes, StandardCharsets.UTF_8));
        synchronized (this) {
            requestLog.add(recorded);
        }
        send(exchange, 404, error("RESOURCE_NOT_FOUND", "The specified resource was not found."));
    }

    private void handleVirtualCenters(HttpExchange exchange) throws IOException {
        byte[] requestBytes = exchange.getRequestBody().readAllBytes();
        String body = new String(requestBytes, StandardCharsets.UTF_8);
        String path = exchange.getRequestURI().getPath();
        String rawQuery = exchange.getRequestURI().getRawQuery();
        Map<String, List<String>> headers = new LinkedHashMap<>();
        exchange.getRequestHeaders().forEach((key, value) -> headers.put(key, List.copyOf(value)));
        RecordedRequest recorded = new RecordedRequest(
                exchange.getRequestMethod(), path, rawQuery, Map.copyOf(headers), body);
        synchronized (this) {
            requestLog.add(recorded);
        }

        // The context matches by prefix; item routes such as /virtualCenters/{vcUrn} are not contracted.
        if (!VIRTUAL_CENTERS_PATH.equals(path)) {
            send(exchange, 404, error("RESOURCE_NOT_FOUND", "The specified resource was not found."));
            return;
        }
        String authorization = recorded.header("Authorization");
        if (authorization == null || !authorization.equals("Bearer " + bearerToken)) {
            send(exchange, 401, null);
            return;
        }
        if (!ACCEPT.equals(recorded.header("Accept"))) {
            send(exchange, 400, error("BAD_REQUEST",
                    "The accept header must request the API version, for example " + ACCEPT + "."));
            return;
        }

        switch (recorded.method()) {
            case "GET" -> handleQuery(exchange, rawQuery);
            case "POST" -> handleAttach(exchange, body, recorded.header("Content-Type"));
            default -> send(exchange, 405, error("METHOD_NOT_ALLOWED",
                    "This fixture serves only Query Virtual Centers and Attach Virtual Center."));
        }
    }

    private void handleQuery(HttpExchange exchange, String rawQuery) throws IOException {
        int[] forced;
        String forcedBody;
        synchronized (this) {
            forced = forcedQueryStatus;
            forcedBody = forcedQueryBody;
            forcedQueryStatus = null;
            forcedQueryBody = null;
        }
        if (forced != null) {
            send(exchange, forced[0], forcedBody);
            return;
        }

        Map<String, String> parameters = new LinkedHashMap<>();
        if (rawQuery != null && !rawQuery.isEmpty()) {
            for (String pair : rawQuery.split("&", -1)) {
                int split = pair.indexOf('=');
                if (split < 0) {
                    send(exchange, 400, error("BAD_REQUEST", "Malformed query string."));
                    return;
                }
                String name = URLDecoder.decode(pair.substring(0, split), StandardCharsets.UTF_8);
                String value = URLDecoder.decode(pair.substring(split + 1), StandardCharsets.UTF_8);
                if (!List.of("filter", "sortAsc", "sortDesc", "page", "pageSize").contains(name)
                        || parameters.put(name, value) != null) {
                    send(exchange, 400, error("BAD_REQUEST", "Unsupported query parameter: " + name));
                    return;
                }
            }
        }

        Integer page = positiveInteger(parameters.get("page"));
        Integer pageSize = positiveInteger(parameters.get("pageSize"));
        if (page == null || page < 1) {
            send(exchange, 400, error("BAD_REQUEST", "page is required and must be at least 1."));
            return;
        }
        if (pageSize == null || pageSize > 128) {
            send(exchange, 400, error("BAD_REQUEST",
                    "pageSize is required and must be between 0 and 128."));
            return;
        }

        List<VCenter> matches = new ArrayList<>(inventory());
        String filter = parameters.get("filter");
        if (filter != null) {
            int operator = filter.indexOf("==");
            String field = operator < 0 ? "" : filter.substring(0, operator);
            String wanted = operator < 0 ? "" : filter.substring(operator + 2);
            if (!field.equals("url") && !field.equals("name")) {
                send(exchange, 400, error("BAD_REQUEST",
                        "Unsupported FIQL filter. Supported: url==<value>, name==<value>."));
                return;
            }
            matches.removeIf(candidate ->
                    !wanted.equals(field.equals("url") ? candidate.url() : candidate.name()));
        }

        int from = Math.min((page - 1) * pageSize, matches.size());
        int to = Math.min(from + pageSize, matches.size());
        List<VCenter> pageValues = matches.subList(from, to);
        StringBuilder json = new StringBuilder();
        json.append("{\"resultTotal\":").append(matches.size())
                .append(",\"pageCount\":").append(pageSize == 0 ? 0 : (matches.size() + pageSize - 1) / pageSize)
                .append(",\"page\":").append(page)
                .append(",\"pageSize\":").append(pageSize)
                .append(",\"values\":[");
        for (int index = 0; index < pageValues.size(); index++) {
            VCenter value = pageValues.get(index);
            if (index > 0) {
                json.append(',');
            }
            json.append("{\"vcId\":").append(quote(value.vcId()))
                    .append(",\"name\":").append(quote(value.name()));
            if (value.description() != null) {
                json.append(",\"description\":").append(quote(value.description()));
            }
            json.append(",\"username\":").append(quote(value.username()))
                    .append(",\"url\":").append(quote(value.url()))
                    .append(",\"isEnabled\":").append(value.isEnabled() == null || value.isEnabled())
                    .append(",\"isConnected\":true")
                    .append(",\"mode\":\"IAAS\"")
                    .append(",\"listenerState\":\"CONNECTED\"}");
        }
        json.append("]}");
        send(exchange, 200, json.toString());
    }

    private void handleAttach(HttpExchange exchange, String body, String contentType) throws IOException {
        if (contentType == null || !contentType.toLowerCase(Locale.ROOT).startsWith("application/json")) {
            send(exchange, 400, error("BAD_REQUEST", "Content-Type must be application/json."));
            return;
        }
        Object parsed;
        try {
            parsed = Json.parse(body);
        } catch (RuntimeException ex) {
            send(exchange, 400, error("INVALID_CONFIGURATION", "The request body is not valid JSON."));
            return;
        }
        if (!(parsed instanceof Map<?, ?> object)) {
            send(exchange, 400, error("INVALID_CONFIGURATION", "The request body must be a JSON object."));
            return;
        }
        String name = text(object.get("name"));
        String username = text(object.get("username"));
        String url = text(object.get("url"));
        if (name == null || name.isBlank() || username == null || username.isBlank()
                || url == null || url.isBlank()) {
            send(exchange, 400, error("INVALID_CONFIGURATION",
                    "name, username and url are required to attach a vCenter server."));
            return;
        }

        int[] forced;
        String forcedCode;
        String forcedText;
        synchronized (this) {
            forced = forcedAttachFailure;
            forcedCode = forcedMinorErrorCode;
            forcedText = forcedMessage;
            forcedAttachFailure = null;
            forcedMinorErrorCode = null;
            forcedMessage = null;
        }
        if (forced != null) {
            send(exchange, forced[0], forced[0] == 401 ? null : error(forcedCode, forcedText));
            return;
        }

        String location;
        boolean omitLocation;
        synchronized (this) {
            for (VCenter attached : inventory) {
                if (attached.url().equals(url)) {
                    send(exchange, 400, error("DUPLICATE_VIM_SERVER_URL",
                            "A vCenter server with URL " + url + " is already attached."));
                    return;
                }
            }
            attachCount++;
            String vcId = String.format(Locale.ROOT,
                    "urn:vcloud:vimserver:0f1e0000-0000-4000-8000-%012d", attachCount);
            Object isEnabled = object.get("isEnabled");
            inventory.add(new VCenter(vcId, name, url, username, text(object.get("description")),
                    isEnabled instanceof Boolean flag ? flag : null));
            location = taskLocation(attachCount);
            omitLocation = omitNextAttachLocation;
            omitNextAttachLocation = false;
        }
        if (!omitLocation) {
            exchange.getResponseHeaders().set("Location", location);
        }
        send(exchange, 202, null);
    }

    private static String error(String minorErrorCode, String message) {
        return "{\"minorErrorCode\":" + quote(minorErrorCode) + ",\"message\":" + quote(message) + "}";
    }

    private static Integer positiveInteger(String raw) {
        if (raw == null) {
            return null;
        }
        try {
            int parsed = Integer.parseInt(raw.trim());
            return parsed < 0 ? null : parsed;
        } catch (NumberFormatException ex) {
            return null;
        }
    }

    private static String text(Object value) {
        return value instanceof String string ? string : null;
    }

    private static String quote(String value) {
        StringBuilder out = new StringBuilder("\"");
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            switch (character) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                default -> {
                    if (character < 0x20) {
                        out.append(String.format(Locale.ROOT, "\\u%04x", (int) character));
                    } else {
                        out.append(character);
                    }
                }
            }
        }
        return out.append('"').toString();
    }

    private static void send(HttpExchange exchange, int status, String body) throws IOException {
        if (body == null) {
            exchange.sendResponseHeaders(status, -1);
            exchange.close();
            return;
        }
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", ACCEPT);
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    private static void verifyPinnedContract() throws IOException {
        String contract = Files.readString(Path.of("docs/contract.json"), StandardCharsets.UTF_8);
        requireContains(contract, "\"operation\": \"Query Virtual Centers\"");
        requireContains(contract, "\"operation\": \"Attach Virtual Center\"");
        requireContains(contract, "\"path\": \"" + VIRTUAL_CENTERS_PATH + "\"");
        requireContains(contract, "\"kind\": \"reference-documentation\"");
        int operations = contract.split("\"operation\":", -1).length - 1;
        if (operations != 2) {
            throw new IllegalStateException(
                    "the fixture serves exactly the two contracted operations, found " + operations);
        }
    }

    private static void requireContains(String source, String expected) {
        if (!source.contains(expected)) {
            throw new IllegalStateException("docs/contract.json is not pinned to " + expected);
        }
    }

    @Override
    public void close() {
        server.stop(0);
    }

    /** Minimal JSON reader, sufficient for the fixture to inspect an attach body. */
    static final class Json {
        private final String source;
        private int cursor;

        private Json(String source) {
            this.source = source;
        }

        static Object parse(String source) {
            Json parser = new Json(source);
            parser.skipWhitespace();
            Object value = parser.readValue();
            parser.skipWhitespace();
            if (parser.cursor != source.length()) {
                throw new IllegalArgumentException("trailing content in JSON document");
            }
            return value;
        }

        private Object readValue() {
            skipWhitespace();
            if (cursor >= source.length()) {
                throw new IllegalArgumentException("unexpected end of JSON document");
            }
            char character = source.charAt(cursor);
            return switch (character) {
                case '{' -> readObject();
                case '[' -> readArray();
                case '"' -> readString();
                case 't' -> readLiteral("true", Boolean.TRUE);
                case 'f' -> readLiteral("false", Boolean.FALSE);
                case 'n' -> readLiteral("null", null);
                default -> readNumber();
            };
        }

        private Map<String, Object> readObject() {
            Map<String, Object> object = new LinkedHashMap<>();
            cursor++;
            skipWhitespace();
            if (cursor < source.length() && source.charAt(cursor) == '}') {
                cursor++;
                return object;
            }
            while (true) {
                skipWhitespace();
                String key = readString();
                skipWhitespace();
                expect(':');
                object.put(key, readValue());
                skipWhitespace();
                char next = next();
                if (next == '}') {
                    return object;
                }
                if (next != ',') {
                    throw new IllegalArgumentException("expected , or } in JSON object");
                }
            }
        }

        private List<Object> readArray() {
            List<Object> values = new ArrayList<>();
            cursor++;
            skipWhitespace();
            if (cursor < source.length() && source.charAt(cursor) == ']') {
                cursor++;
                return values;
            }
            while (true) {
                values.add(readValue());
                skipWhitespace();
                char next = next();
                if (next == ']') {
                    return values;
                }
                if (next != ',') {
                    throw new IllegalArgumentException("expected , or ] in JSON array");
                }
            }
        }

        private String readString() {
            expect('"');
            StringBuilder out = new StringBuilder();
            while (true) {
                char character = next();
                if (character == '"') {
                    return out.toString();
                }
                if (character != '\\') {
                    out.append(character);
                    continue;
                }
                char escape = next();
                switch (escape) {
                    case '"', '\\', '/' -> out.append(escape);
                    case 'b' -> out.append('\b');
                    case 'f' -> out.append('\f');
                    case 'n' -> out.append('\n');
                    case 'r' -> out.append('\r');
                    case 't' -> out.append('\t');
                    case 'u' -> {
                        out.append((char) Integer.parseInt(source.substring(cursor, cursor + 4), 16));
                        cursor += 4;
                    }
                    default -> throw new IllegalArgumentException("invalid escape \\" + escape);
                }
            }
        }

        private Object readLiteral(String literal, Object value) {
            if (!source.startsWith(literal, cursor)) {
                throw new IllegalArgumentException("invalid JSON literal");
            }
            cursor += literal.length();
            return value;
        }

        private Object readNumber() {
            int start = cursor;
            while (cursor < source.length() && "+-.eE0123456789".indexOf(source.charAt(cursor)) >= 0) {
                cursor++;
            }
            if (start == cursor) {
                throw new IllegalArgumentException("invalid JSON value");
            }
            return Double.parseDouble(source.substring(start, cursor));
        }

        private void skipWhitespace() {
            while (cursor < source.length() && Character.isWhitespace(source.charAt(cursor))) {
                cursor++;
            }
        }

        private void expect(char expected) {
            if (next() != expected) {
                throw new IllegalArgumentException("expected " + expected + " in JSON document");
            }
        }

        private char next() {
            if (cursor >= source.length()) {
                throw new IllegalArgumentException("unexpected end of JSON document");
            }
            return source.charAt(cursor++);
        }
    }
}
