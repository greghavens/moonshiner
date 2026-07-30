import java.io.IOException;
import java.math.BigDecimal;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Minimal JDK-only client for the three vSphere Automation API operations in
 * docs/contract.json.
 *
 * <p>The supplied URI denotes the API root, for example
 * {@code https://vcenter.example/api} or {@code https://vcenter.example/api/}.
 */
public final class VcenterCredentialRotationClient implements AutoCloseable {
    private static final String SESSION_PATH = "session";
    private static final String CLUSTER_PATH = "vcenter/cluster";

    private final URI apiBase;
    private final String username;
    private final Duration requestTimeout;
    private final HttpClient http;
    private final Object stateMonitor = new Object();
    private final Object rotationGate = new Object();

    private Session currentSession;
    private boolean closed;

    public VcenterCredentialRotationClient(
            URI apiBase,
            String username,
            String password,
            Duration requestTimeout) throws IOException, InterruptedException {
        this(apiBase, username, password, requestTimeout,
                newHttpClient(requestTimeout));
    }

    public VcenterCredentialRotationClient(
            URI apiBase,
            String username,
            String password,
            Duration requestTimeout,
            HttpClient http) throws IOException, InterruptedException {
        this.apiBase = normalizeApiBase(apiBase);
        this.username = requireUsername(username);
        Objects.requireNonNull(password, "password");
        this.requestTimeout = requirePositiveTimeout(requestTimeout);
        this.http = Objects.requireNonNull(http, "http");
        this.currentSession = createSession(password);
    }

    /**
     * Lists every cluster visible to the current session. All contract filters
     * are unset for this focused client.
     */
    public List<ClusterSummary> listClusters()
            throws IOException, InterruptedException {
        Session selected;
        synchronized (stateMonitor) {
            ensureOpen();
            selected = currentSession;
        }

        // BUG: empty values are not the same wire representation as omitted
        // optional query members.
        String emptyFilters = "?clusters=&names=&folders=&datacenters=";
        return requestClusters(selected.id(), emptyFilters);
    }

    /**
     * Creates and publishes a session derived from the replacement credential,
     * then retires the previous session.
     */
    public void rotateCredentials(String replacementPassword)
            throws IOException, InterruptedException {
        Objects.requireNonNull(replacementPassword, "replacementPassword");
        synchronized (rotationGate) {
            synchronized (stateMonitor) {
                ensureOpen();
            }

            Session replacement = createSession(replacementPassword);
            Session previous;
            synchronized (stateMonitor) {
                ensureOpen();
                previous = currentSession;
                currentSession = replacement;
            }

            // BUG: a request that already selected previous may still be using it.
            deleteSession(previous.id());
        }
    }

    /**
     * Stops new work and retires the current session. Repeated calls are no-ops.
     */
    @Override
    public void close() throws IOException, InterruptedException {
        synchronized (rotationGate) {
            Session session;
            synchronized (stateMonitor) {
                if (closed) {
                    return;
                }
                closed = true;
                session = currentSession;
            }
            deleteSession(session.id());
        }
    }

    private Session createSession(String password)
            throws IOException, InterruptedException {
        String userPass = username + ":" + password;
        String authorization = "Basic " + Base64.getEncoder().encodeToString(
                userPass.getBytes(StandardCharsets.UTF_8));
        HttpRequest request = HttpRequest.newBuilder(apiBase.resolve(SESSION_PATH))
                .timeout(requestTimeout)
                .header("Accept", "application/json")
                .header("Authorization", authorization)
                .POST(HttpRequest.BodyPublishers.noBody())
                .build();
        HttpResponse<String> response = send(request, "Cis.Session_create");
        if (response.statusCode() != 201) {
            throw statusFailure("Cis.Session_create", response.statusCode());
        }
        requireJson(response, "Cis.Session_create");

        Object decoded;
        try {
            decoded = Json.parse(response.body());
        } catch (Json.SyntaxException exception) {
            throw new IOException("Cis.Session_create returned malformed JSON");
        }
        if (!(decoded instanceof String sessionId)
                || sessionId.isBlank()
                || !isHeaderSafe(sessionId)) {
            throw new IOException("Cis.Session_create returned an invalid session");
        }
        return new Session(sessionId);
    }

    private List<ClusterSummary> requestClusters(
            String sessionId, String querySuffix)
            throws IOException, InterruptedException {
        HttpRequest request = HttpRequest.newBuilder(
                        apiBase.resolve(CLUSTER_PATH + querySuffix))
                .timeout(requestTimeout)
                .header("Accept", "application/json")
                .header("vmware-api-session-id", sessionId)
                .GET()
                .build();
        HttpResponse<String> response = send(request, "Vcenter.Cluster_list");
        if (response.statusCode() != 200) {
            throw statusFailure("Vcenter.Cluster_list", response.statusCode());
        }
        requireJson(response, "Vcenter.Cluster_list");
        return parseClusters(response.body());
    }

    private void deleteSession(String sessionId)
            throws IOException, InterruptedException {
        HttpRequest request = HttpRequest.newBuilder(apiBase.resolve(SESSION_PATH))
                .timeout(requestTimeout)
                .header("Accept", "application/json")
                .header("vmware-api-session-id", sessionId)
                .DELETE()
                .build();
        HttpResponse<String> response = send(request, "Cis.Session_delete");
        if (response.statusCode() != 204) {
            throw statusFailure("Cis.Session_delete", response.statusCode());
        }
    }

    private HttpResponse<String> send(HttpRequest request, String operationId)
            throws IOException, InterruptedException {
        try {
            return http.send(
                    request,
                    HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        } catch (IOException exception) {
            throw new IOException(operationId + " transport failure");
        }
    }

    private static List<ClusterSummary> parseClusters(String json)
            throws IOException {
        final Object decoded;
        try {
            decoded = Json.parse(json);
        } catch (Json.SyntaxException exception) {
            throw new IOException("Vcenter.Cluster_list returned malformed JSON");
        }
        List<Object> raw = array(decoded, "cluster response");
        List<ClusterSummary> result = new ArrayList<>(raw.size());
        for (int index = 0; index < raw.size(); index++) {
            Map<String, Object> item = object(raw.get(index), "cluster item");
            result.add(new ClusterSummary(
                    string(item.get("cluster"), "cluster"),
                    string(item.get("name"), "name"),
                    bool(item.get("ha_enabled"), "ha_enabled"),
                    bool(item.get("drs_enabled"), "drs_enabled")));
        }
        return List.copyOf(result);
    }

    @SuppressWarnings("unchecked")
    private static List<Object> array(Object value, String label)
            throws IOException {
        if (!(value instanceof List<?>)) {
            throw new IOException(label + " must be an array");
        }
        return (List<Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String label)
            throws IOException {
        if (!(value instanceof Map<?, ?>)) {
            throw new IOException(label + " must be an object");
        }
        return (Map<String, Object>) value;
    }

    private static String string(Object value, String label) throws IOException {
        if (!(value instanceof String text)) {
            throw new IOException(label + " must be a string");
        }
        return text;
    }

    private static boolean bool(Object value, String label) throws IOException {
        if (!(value instanceof Boolean flag)) {
            throw new IOException(label + " must be a boolean");
        }
        return flag;
    }

    private static HttpClient newHttpClient(Duration timeout) {
        requirePositiveTimeout(timeout);
        return HttpClient.newBuilder().connectTimeout(timeout).build();
    }

    private static URI normalizeApiBase(URI value) {
        Objects.requireNonNull(value, "apiBase");
        String scheme = value.getScheme();
        if (scheme == null
                || !(scheme.equalsIgnoreCase("http")
                || scheme.equalsIgnoreCase("https"))
                || value.getHost() == null
                || value.getRawUserInfo() != null
                || value.getRawQuery() != null
                || value.getRawFragment() != null
                || !(value.getRawPath().equals("/api")
                || value.getRawPath().equals("/api/"))) {
            throw new IllegalArgumentException("apiBase must be an HTTP(S) /api URI");
        }
        String text = value.toASCIIString();
        return URI.create(text.endsWith("/") ? text : text + "/");
    }

    private static String requireUsername(String value) {
        Objects.requireNonNull(value, "username");
        if (value.isBlank() || value.indexOf(':') >= 0) {
            throw new IllegalArgumentException("username is invalid");
        }
        return value;
    }

    private static Duration requirePositiveTimeout(Duration value) {
        Objects.requireNonNull(value, "requestTimeout");
        if (value.isZero() || value.isNegative()) {
            throw new IllegalArgumentException("requestTimeout must be positive");
        }
        return value;
    }

    private static boolean isHeaderSafe(String value) {
        for (int index = 0; index < value.length(); index++) {
            char current = value.charAt(index);
            if (current <= 0x20 || current >= 0x7f) {
                return false;
            }
        }
        return true;
    }

    private static void requireJson(
            HttpResponse<?> response, String operationId) throws IOException {
        String contentType = response.headers()
                .firstValue("Content-Type")
                .orElse("");
        int semicolon = contentType.indexOf(';');
        String mediaType = (semicolon < 0
                ? contentType
                : contentType.substring(0, semicolon)).trim();
        if (!mediaType.equalsIgnoreCase("application/json")) {
            throw new IOException(operationId + " returned an unexpected media type");
        }
    }

    private static IOException statusFailure(String operationId, int status) {
        return new IOException(operationId + " failed with HTTP " + status);
    }

    private void ensureOpen() {
        if (closed) {
            throw new IllegalStateException("client is closed");
        }
    }

    public record ClusterSummary(
            String cluster,
            String name,
            boolean haEnabled,
            boolean drsEnabled) {
        public ClusterSummary {
            Objects.requireNonNull(cluster, "cluster");
            Objects.requireNonNull(name, "name");
        }
    }

    private record Session(String id) {
    }

    /**
     * Strict JSON reader kept in this source file so the exercise has no
     * third-party dependency or build-tool requirement.
     */
    private static final class Json {
        static Object parse(String text) {
            Parser parser = new Parser(text);
            Object value = parser.readValue();
            parser.skipWhitespace();
            if (!parser.atEnd()) {
                throw parser.error("trailing data");
            }
            return value;
        }

        private static final class Parser {
            private final String text;
            private int offset;

            Parser(String text) {
                this.text = Objects.requireNonNull(text, "text");
            }

            Object readValue() {
                skipWhitespace();
                if (atEnd()) {
                    throw error("expected a value");
                }
                return switch (text.charAt(offset)) {
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
                expect('{');
                Map<String, Object> result = new LinkedHashMap<>();
                skipWhitespace();
                if (consume('}')) {
                    return result;
                }
                while (true) {
                    skipWhitespace();
                    if (atEnd() || text.charAt(offset) != '"') {
                        throw error("expected an object key");
                    }
                    String key = readString();
                    skipWhitespace();
                    expect(':');
                    if (result.containsKey(key)) {
                        throw error("duplicate object key");
                    }
                    result.put(key, readValue());
                    skipWhitespace();
                    if (consume('}')) {
                        return result;
                    }
                    expect(',');
                }
            }

            private List<Object> readArray() {
                expect('[');
                List<Object> result = new ArrayList<>();
                skipWhitespace();
                if (consume(']')) {
                    return result;
                }
                while (true) {
                    result.add(readValue());
                    skipWhitespace();
                    if (consume(']')) {
                        return result;
                    }
                    expect(',');
                }
            }

            private String readString() {
                expect('"');
                StringBuilder result = new StringBuilder();
                while (!atEnd()) {
                    char current = text.charAt(offset++);
                    if (current == '"') {
                        return result.toString();
                    }
                    if (current == '\\') {
                        if (atEnd()) {
                            throw error("unterminated escape");
                        }
                        char escaped = text.charAt(offset++);
                        switch (escaped) {
                            case '"', '\\', '/' -> result.append(escaped);
                            case 'b' -> result.append('\b');
                            case 'f' -> result.append('\f');
                            case 'n' -> result.append('\n');
                            case 'r' -> result.append('\r');
                            case 't' -> result.append('\t');
                            case 'u' -> result.append(readUnicode());
                            default -> throw error("invalid escape");
                        }
                    } else {
                        if (current < 0x20) {
                            throw error("unescaped control character");
                        }
                        result.append(current);
                    }
                }
                throw error("unterminated string");
            }

            private char readUnicode() {
                if (offset + 4 > text.length()) {
                    throw error("short unicode escape");
                }
                int value = 0;
                for (int index = 0; index < 4; index++) {
                    int digit = Character.digit(text.charAt(offset++), 16);
                    if (digit < 0) {
                        throw error("invalid unicode escape");
                    }
                    value = value * 16 + digit;
                }
                return (char) value;
            }

            private BigDecimal readNumber() {
                int start = offset;
                consume('-');
                int integerStart = offset;
                if (consume('0')) {
                    if (!atEnd() && Character.isDigit(text.charAt(offset))) {
                        throw error("leading zero");
                    }
                } else {
                    readDigits();
                    if (offset == integerStart) {
                        throw error("expected a value");
                    }
                }
                if (consume('.')) {
                    int fractionStart = offset;
                    readDigits();
                    if (offset == fractionStart) {
                        throw error("missing fraction");
                    }
                }
                if (consume('e') || consume('E')) {
                    consume('+');
                    consume('-');
                    int exponentStart = offset;
                    readDigits();
                    if (offset == exponentStart) {
                        throw error("missing exponent");
                    }
                }
                try {
                    return new BigDecimal(text.substring(start, offset));
                } catch (NumberFormatException exception) {
                    throw error("invalid number");
                }
            }

            private Object readLiteral(String literal, Object value) {
                if (!text.startsWith(literal, offset)) {
                    throw error("invalid literal");
                }
                offset += literal.length();
                return value;
            }

            private void readDigits() {
                while (!atEnd() && Character.isDigit(text.charAt(offset))) {
                    offset++;
                }
            }

            void skipWhitespace() {
                while (!atEnd()) {
                    char current = text.charAt(offset);
                    if (current == ' '
                            || current == '\n'
                            || current == '\r'
                            || current == '\t') {
                        offset++;
                    } else {
                        return;
                    }
                }
            }

            private boolean consume(char expected) {
                if (!atEnd() && text.charAt(offset) == expected) {
                    offset++;
                    return true;
                }
                return false;
            }

            private void expect(char expected) {
                if (!consume(expected)) {
                    throw error("expected " + expected);
                }
            }

            boolean atEnd() {
                return offset == text.length();
            }

            SyntaxException error(String message) {
                return new SyntaxException(message + " at character " + offset);
            }
        }

        private static final class SyntaxException extends RuntimeException {
            SyntaxException(String message) {
                super(message);
            }
        }
    }
}
