import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Protected acceptance verifier with a loopback mock for the one operation
 * named by docs/contract.json. The mock exposes no control or data routes;
 * this test reads its in-memory request log directly.
 */
public final class TestMain {
    private static int checks;

    private static final String OPERATION_ID = "updateTroubleshootingIncident";
    private static final String METHOD = "PUT";
    private static final String SERVER_BASE_PATH = "/api/ni";
    private static final String OPERATION_PREFIX =
            SERVER_BASE_PATH + "/gnt/troubleshoot/incidents/";
    private static final String TOKEN = "fixture-token-7d3a";
    private static final String INCIDENT_ID = "incident/edge 01";
    private static final String RAW_INCIDENT_PATH =
            OPERATION_PREFIX + "incident%2Fedge%2001";
    private static final String NAME = "Edge uplink recovery";
    private static final long START_TIME = 1735689600000L;
    private static final String RICH_INCIDENT_ID = "incident/\u03b2 ?#%";
    private static final String RAW_RICH_INCIDENT_PATH =
            OPERATION_PREFIX + "incident%2F%CE%B2%20%3F%23%25";
    private static final String RICH_NAME =
            "Quote \" slash \\ newline\ncontrol\u0001 snowman \u2603";
    private static final long RICH_START_TIME = -17L;
    private static final long RICH_END_TIME = 1735689900123L;
    private static final String RICH_STATUS = "DONE/\u786e\u8ba4";
    private static final String RESPONSE =
            "{\"entity_id\":\"incident/edge 01\",\"name\":\"Edge uplink recovery\","
            + "\"status\":\"OPEN\",\"start_time\":1735689600000}";
    private static final Set<String> UPDATE_FIELDS =
            Set.of("name", "start_time", "end_time", "status");

    private static void check(boolean condition, String label) {
        checks++;
        if (!condition) {
            throw new AssertionError("FAIL: " + label);
        }
    }

    private static final class RequestEntry {
        final String method;
        final String rawPath;
        final String rawQuery;
        final String authorization;
        final String contentType;
        final String accept;
        final String body;

        RequestEntry(HttpExchange exchange, String body) {
            this.method = exchange.getRequestMethod();
            this.rawPath = exchange.getRequestURI().getRawPath();
            this.rawQuery = exchange.getRequestURI().getRawQuery();
            this.authorization = exchange.getRequestHeaders().getFirst("Authorization");
            this.contentType = exchange.getRequestHeaders().getFirst("Content-Type");
            this.accept = exchange.getRequestHeaders().getFirst("Accept");
            this.body = body;
        }
    }

    private static final class ContractMock implements AutoCloseable {
        final List<RequestEntry> requests =
                Collections.synchronizedList(new ArrayList<>());
        final HttpServer server;
        String currentName = "Original incident";
        long currentStartTime = 0L;
        int appliedEffects;

        ContractMock() throws IOException {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", this::handle);
            server.start();
        }

        String origin() {
            return "http://127.0.0.1:" + server.getAddress().getPort();
        }

        private void handle(HttpExchange exchange) throws IOException {
            String body = new String(exchange.getRequestBody().readAllBytes(),
                    StandardCharsets.UTF_8);
            RequestEntry entry = new RequestEntry(exchange, body);
            requests.add(entry);

            // Only the single contract operation is served. Everything else is
            // a plain 404 and no auxiliary HTTP route exposes the request log.
            boolean operationRoute = METHOD.equals(entry.method)
                    && entry.rawPath.startsWith(OPERATION_PREFIX)
                    && entry.rawPath.length() > OPERATION_PREFIX.length()
                    && entry.rawPath.indexOf('/', OPERATION_PREFIX.length()) < 0;
            if (!operationRoute) {
                respond(exchange, 404, "{\"error\":\"operation not served\"}");
                return;
            }
            Map<String, Object> update;
            try {
                update = parseUpdateBody(body);
            } catch (IllegalArgumentException invalidJson) {
                respond(exchange, 400, "{\"error\":\"bad update body\"}");
                return;
            }
            if (!isContractUpdate(update)) {
                respond(exchange, 400, "{\"error\":\"bad update body\"}");
                return;
            }

            if (entry.rawPath.equals(OPERATION_PREFIX + "force-302")) {
                respond(exchange, 302, "{\"error\":\"redirect\"}");
                return;
            }
            if (entry.rawPath.equals(OPERATION_PREFIX + "force-404")) {
                respond(exchange, 404, "{\"error\":\"not found\"}");
                return;
            }
            if (entry.rawPath.equals(OPERATION_PREFIX + "force-503")) {
                respond(exchange, 503, "{\"error\":\"unavailable\"}");
                return;
            }

            // PUT replaces the selected state. Repeating the same desired state
            // is observable in the log but does not apply a second effect.
            if (entry.rawPath.equals(RAW_INCIDENT_PATH)
                    && NAME.equals(update.get("name"))
                    && Long.valueOf(START_TIME).equals(update.get("start_time"))
                    && (!NAME.equals(currentName) || currentStartTime != START_TIME)) {
                currentName = NAME;
                currentStartTime = START_TIME;
                appliedEffects++;
            }
            respond(exchange, 200, RESPONSE);
        }

        @Override
        public void close() {
            server.stop(0);
        }
    }

    private static void respond(HttpExchange exchange, int status, String body)
            throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream output = exchange.getResponseBody()) {
            output.write(bytes);
        }
    }

    private static boolean isContractUpdate(Map<String, Object> update) {
        if (!UPDATE_FIELDS.containsAll(update.keySet())) {
            return false;
        }
        for (Map.Entry<String, Object> field : update.entrySet()) {
            boolean validType = switch (field.getKey()) {
                case "name", "status" -> field.getValue() instanceof String;
                case "start_time", "end_time" -> field.getValue() instanceof Long;
                default -> false;
            };
            if (!validType) {
                return false;
            }
        }
        return true;
    }

    /** Minimal strict parser for the flat string/integer JSON object in this contract. */
    private static Map<String, Object> parseUpdateBody(String body) {
        JsonCursor cursor = new JsonCursor(body);
        Map<String, Object> result = cursor.parseObject();
        cursor.skipWhitespace();
        if (!cursor.atEnd()) {
            throw new IllegalArgumentException("trailing JSON data");
        }
        return result;
    }

    private static final class JsonCursor {
        private final String input;
        private int position;

        JsonCursor(String input) {
            this.input = input;
        }

        boolean atEnd() {
            return position == input.length();
        }

        void skipWhitespace() {
            while (!atEnd()) {
                char c = input.charAt(position);
                if (c != ' ' && c != '\t' && c != '\n' && c != '\r') {
                    return;
                }
                position++;
            }
        }

        Map<String, Object> parseObject() {
            Map<String, Object> result = new LinkedHashMap<>();
            skipWhitespace();
            expect('{');
            skipWhitespace();
            if (take('}')) {
                return result;
            }
            while (true) {
                skipWhitespace();
                String key = parseString();
                if (result.containsKey(key)) {
                    throw new IllegalArgumentException("duplicate JSON member");
                }
                skipWhitespace();
                expect(':');
                skipWhitespace();
                Object value = peek() == '"' ? parseString() : parseInteger();
                result.put(key, value);
                skipWhitespace();
                if (take('}')) {
                    return result;
                }
                expect(',');
            }
        }

        private String parseString() {
            expect('"');
            StringBuilder decoded = new StringBuilder();
            while (!atEnd()) {
                char c = input.charAt(position++);
                if (c == '"') {
                    return decoded.toString();
                }
                if (c < 0x20) {
                    throw new IllegalArgumentException("unescaped control character");
                }
                if (c != '\\') {
                    decoded.append(c);
                    continue;
                }
                if (atEnd()) {
                    throw new IllegalArgumentException("unfinished JSON escape");
                }
                char escaped = input.charAt(position++);
                switch (escaped) {
                    case '"', '\\', '/' -> decoded.append(escaped);
                    case 'b' -> decoded.append('\b');
                    case 'f' -> decoded.append('\f');
                    case 'n' -> decoded.append('\n');
                    case 'r' -> decoded.append('\r');
                    case 't' -> decoded.append('\t');
                    case 'u' -> decoded.append(parseUnicodeEscape());
                    default -> throw new IllegalArgumentException("invalid JSON escape");
                }
            }
            throw new IllegalArgumentException("unfinished JSON string");
        }

        private char parseUnicodeEscape() {
            if (position + 4 > input.length()) {
                throw new IllegalArgumentException("short Unicode escape");
            }
            int value = 0;
            for (int i = 0; i < 4; i++) {
                int digit = Character.digit(input.charAt(position++), 16);
                if (digit < 0) {
                    throw new IllegalArgumentException("invalid Unicode escape");
                }
                value = (value << 4) | digit;
            }
            return (char) value;
        }

        private Long parseInteger() {
            int start = position;
            take('-');
            if (atEnd() || !Character.isDigit(input.charAt(position))) {
                throw new IllegalArgumentException("expected JSON integer");
            }
            if (input.charAt(position) == '0') {
                position++;
                if (!atEnd() && Character.isDigit(input.charAt(position))) {
                    throw new IllegalArgumentException("leading zero");
                }
            } else {
                while (!atEnd() && Character.isDigit(input.charAt(position))) {
                    position++;
                }
            }
            try {
                return Long.valueOf(input.substring(start, position));
            } catch (NumberFormatException outOfRange) {
                throw new IllegalArgumentException("invalid long", outOfRange);
            }
        }

        private char peek() {
            if (atEnd()) {
                throw new IllegalArgumentException("unexpected end of JSON");
            }
            return input.charAt(position);
        }

        private boolean take(char expected) {
            if (!atEnd() && input.charAt(position) == expected) {
                position++;
                return true;
            }
            return false;
        }

        private void expect(char expected) {
            if (!take(expected)) {
                throw new IllegalArgumentException("expected " + expected);
            }
        }
    }

    private static void verifyWire(RequestEntry request, String rawPath) {
        check(METHOD.equals(request.method), "wire method is PUT");
        check(rawPath.equals(request.rawPath),
                "server base, operation path, and path-segment encoding are exact");
        check(request.rawQuery == null, "update has no query string");
        check(("NetworkInsight " + TOKEN).equals(request.authorization),
                "Authorization uses documented NetworkInsight scheme");
        check(request.contentType != null
                        && request.contentType.equalsIgnoreCase("application/json"),
                "request Content-Type is application/json");
        check(request.accept != null
                        && request.accept.equalsIgnoreCase("application/json"),
                "request Accept is application/json");
    }

    private static void verifyPinnedDocuments() throws IOException {
        String contract = Files.readString(Path.of("docs/contract.json"));
        String sources = Files.readString(Path.of("docs/official_sources.json"));
        check(contract.contains("\"openapi\": \"3.0.1\""),
                "contract records OpenAPI version");
        check(contract.contains("\"serverBasePath\": \"/api/ni\""),
                "contract records server base path");
        check(contract.contains("\"operationId\": \"" + OPERATION_ID + "\""),
                "contract names the served operationId");
        check(contract.contains("\"method\": \"" + METHOD + "\""),
                "contract pins PUT method");
        check(contract.contains("\"path\": \"/gnt/troubleshoot/incidents/{id}\""),
                "contract pins incident update path");
        check(contract.contains("\"required\": []"),
                "contract records no required update properties");
        check(sources.contains("\"tag\": \"9.0.0.0\""),
                "provenance records tag");
        check(sources.contains(
                "\"commitSha\": \"85151f6b1bb58f13b6ac0304bfec53904bea085f\""),
                "provenance records revision commit");
        check(sources.contains(
                "\"specPath\": \"specifications/vcf-operations/"
                + "vcf-operations-for-networks-openapi.yaml\""),
                "provenance records specification path");
        check(sources.contains("\"updateTroubleshootingIncident\""),
                "provenance records each selected operationId");
    }

    private static void verifyPublicApi() throws ReflectiveOperationException {
        Class<?> clientType = Class.forName("OperationsForNetworksClient");
        check(Modifier.isPublic(clientType.getModifiers()),
                "client class is public in the default package");

        Constructor<?> constructor = clientType.getDeclaredConstructor(
                String.class, String.class);
        check(Modifier.isPublic(constructor.getModifiers()),
                "specified two-string constructor is public");

        Method update = clientType.getDeclaredMethod(
                "updateTroubleshootingIncident",
                String.class, String.class, Long.class, Long.class, String.class);
        check(Modifier.isPublic(update.getModifiers())
                        && !Modifier.isStatic(update.getModifiers()),
                "specified update method is a public instance method");
        check(update.getReturnType() == String.class,
                "specified update method returns String");
        check(Set.of(update.getExceptionTypes()).equals(
                        Set.of(IOException.class, InterruptedException.class)),
                "specified update method declares IOException and InterruptedException");
    }

    @FunctionalInterface
    private interface ThrowingCall {
        void run() throws IOException, InterruptedException;
    }

    private static boolean throwsIOException(ThrowingCall call)
            throws InterruptedException {
        try {
            call.run();
            return false;
        } catch (IOException expected) {
            return true;
        }
    }

    public static void main(String[] args) throws Exception {
        verifyPinnedDocuments();
        verifyPublicApi();

        try (ContractMock mock = new ContractMock()) {
            // A trailing slash verifies that the client does not introduce a
            // double slash before the specification's server base path.
            OperationsForNetworksClient client = new OperationsForNetworksClient(
                    mock.origin() + "/", TOKEN);

            String first = client.updateTroubleshootingIncident(
                    INCIDENT_ID, NAME, START_TIME, null, null);
            String repeated = client.updateTroubleshootingIncident(
                    INCIDENT_ID, NAME, START_TIME, null, null);

            check(RESPONSE.equals(first) && RESPONSE.equals(repeated),
                    "client returns both documented 200 response bodies");
            check(mock.requests.size() == 2,
                    "caller retry reaches the one served operation twice");

            RequestEntry one = mock.requests.get(0);
            RequestEntry two = mock.requests.get(1);
            for (RequestEntry request : List.of(one, two)) {
                verifyWire(request, RAW_INCIDENT_PATH);
                check(parseUpdateBody(request.body).equals(Map.<String, Object>of(
                                "name", NAME, "start_time", START_TIME)),
                        "body has exactly the two set fields and their values");
            }
            check(one.body.equals(two.body),
                    "caller retry sends a byte-identical mutation body");
            check(mock.appliedEffects == 1,
                    "repeated PUT produces one resulting state change");
            check(NAME.equals(mock.currentName) && mock.currentStartTime == START_TIME,
                    "mock state matches the requested replacement");

            // No trailing slash, all fields, reserved/non-ASCII path bytes, and
            // characters that require JSON escaping exercise the remaining shape.
            OperationsForNetworksClient plainOriginClient =
                    new OperationsForNetworksClient(mock.origin(), TOKEN);
            String richResponse = plainOriginClient.updateTroubleshootingIncident(
                    RICH_INCIDENT_ID, RICH_NAME, RICH_START_TIME,
                    RICH_END_TIME, RICH_STATUS);
            check(RESPONSE.equals(richResponse),
                    "200 response body is returned for a fully populated update");
            RequestEntry rich = mock.requests.get(2);
            verifyWire(rich, RAW_RICH_INCIDENT_PATH);
            check(parseUpdateBody(rich.body).equals(Map.<String, Object>of(
                            "name", RICH_NAME,
                            "start_time", RICH_START_TIME,
                            "end_time", RICH_END_TIME,
                            "status", RICH_STATUS)),
                    "all optional fields use exact names, types, and escaped values");

            String emptyResponse = plainOriginClient.updateTroubleshootingIncident(
                    "empty", null, null, null, null);
            check(RESPONSE.equals(emptyResponse),
                    "200 response body is returned for an empty optional update");
            RequestEntry empty = mock.requests.get(3);
            verifyWire(empty, OPERATION_PREFIX + "empty");
            check(parseUpdateBody(empty.body).isEmpty(),
                    "all null optional arguments are omitted from the JSON object");

            check(throwsIOException(() -> plainOriginClient.updateTroubleshootingIncident(
                            "force-302", null, null, null, null)),
                    "a non-200 redirect response throws IOException");
            check(throwsIOException(() -> plainOriginClient.updateTroubleshootingIncident(
                            "force-404", null, null, null, null)),
                    "a non-200 client-error response throws IOException");
            check(throwsIOException(() -> plainOriginClient.updateTroubleshootingIncident(
                            "force-503", null, null, null, null)),
                    "a non-200 server-error response throws IOException");
            check(mock.requests.size() == 7,
                    "all status probes reached the contract operation");
        }

        System.out.println("ALL CHECKS PASSED (" + checks + ")");
    }
}
