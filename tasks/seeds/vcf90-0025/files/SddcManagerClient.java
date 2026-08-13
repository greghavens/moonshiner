import java.io.IOException;
import java.net.URI;
import java.net.URISyntaxException;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/**
 * Dependency-free client for the focused SDDC Manager 9.0 host-commissioning contract in
 * {@code docs/contract.json}.
 */
public final class SddcManagerClient {
    static final String CREATE_TOKEN_OPERATION = "createToken";
    static final String COMMISSION_HOSTS_OPERATION = "commissionHosts";
    static final String GET_TASK_OPERATION = "getTask";

    static final String TOKENS_PATH = "/v1/tokens";
    static final String HOSTS_PATH = "/v1/hosts";
    static final String TASKS_PATH = "/v1/tasks/";

    /** {@code HostCommissionSpec.storageType} values enumerated by the specification. */
    static final Set<String> STORAGE_TYPES = Set.of(
            "VSAN", "VSAN_ESA", "VSAN_REMOTE", "VSAN_MAX", "NFS", "VMFS_FC", "VVOL", "VMFS");

    /** {@code HostCommissionSpec.vvolStorageProtocolType} values enumerated by the specification. */
    static final Set<String> VVOL_STORAGE_PROTOCOL_TYPES = Set.of("ISCSI", "NFS", "FC");

    static final Set<String> NON_TERMINAL_STATUSES = Set.of("PENDING", "IN_PROGRESS");
    static final Set<String> TERMINAL_SUCCESS_STATUSES =
            Set.of("SUCCESSFUL", "COMPLETED_WITH_WARNING", "SKIPPED");
    static final Set<String> TERMINAL_FAILURE_STATUSES = Set.of("FAILED", "CANCELLED");

    private final String baseUrl;
    private final Credentials credentials;
    private final int pollLimit;
    private final long pollIntervalMillis;
    private final Sleeper sleeper;
    private final HttpClient httpClient;

    /** The specification's optional {@code TokenCreationSpec} members, in declaration order. */
    public record Credentials(String username, String password, String apiKey, String idToken) {
    }

    /** One {@code HostCommissionSpec}; null members are the ones the caller left unset. */
    public record HostCommission(
            String fqdn,
            String username,
            String password,
            String storageType,
            String vvolStorageProtocolType,
            String networkPoolId,
            String networkPoolName,
            String sshThumbprint,
            String sslThumbprint) {
    }

    /** The result of a commission that was polled through to a successful terminal state. */
    public record CommissionOutcome(
            String taskId,
            String taskName,
            String status,
            int pollCount,
            List<String> resourceIds) {
    }

    /** Injected wait so the acceptance harness can drive polling without real elapsed time. */
    @FunctionalInterface
    public interface Sleeper {
        void pause(long millis);
    }

    public static final class VcfApiException extends RuntimeException {
        private final String operationId;
        private final int statusCode;
        private final String errorCode;

        private VcfApiException(String operationId, int statusCode, String errorCode) {
            super(operationId + " failed with HTTP status " + statusCode);
            this.operationId = operationId;
            this.statusCode = statusCode;
            this.errorCode = errorCode;
        }

        public String operationId() {
            return operationId;
        }

        public int statusCode() {
            return statusCode;
        }

        public String errorCode() {
            return errorCode;
        }
    }

    public static final class ProtocolException extends RuntimeException {
        private final String operationId;

        private ProtocolException(String operationId, String problem) {
            super(operationId + " protocol error: " + problem);
            this.operationId = operationId;
        }

        public String operationId() {
            return operationId;
        }
    }

    public static final class TransportException extends RuntimeException {
        private final String operationId;

        private TransportException(String operationId) {
            super(operationId + " transport failure");
            this.operationId = operationId;
        }

        public String operationId() {
            return operationId;
        }
    }

    public static final class TaskFailedException extends RuntimeException {
        private final String taskId;
        private final String taskStatus;
        private final String errorCode;
        private final String referenceToken;

        private TaskFailedException(
                String taskId, String taskStatus, String errorCode, String referenceToken,
                String message) {
            super(message);
            this.taskId = taskId;
            this.taskStatus = taskStatus;
            this.errorCode = errorCode;
            this.referenceToken = referenceToken;
        }

        public String taskId() {
            return taskId;
        }

        public String taskStatus() {
            return taskStatus;
        }

        public String errorCode() {
            return errorCode;
        }

        public String referenceToken() {
            return referenceToken;
        }
    }

    public static final class TaskTimeoutException extends RuntimeException {
        private final String taskId;
        private final int pollCount;

        private TaskTimeoutException(String taskId, int pollCount) {
            super("commission task was still non-terminal after " + pollCount + " polls");
            this.taskId = taskId;
            this.pollCount = pollCount;
        }

        public String taskId() {
            return taskId;
        }

        public int pollCount() {
            return pollCount;
        }
    }

    private record Task(
            String id,
            String name,
            String status,
            List<String> resourceIds,
            String errorCode,
            String errorMessage,
            String referenceToken) {
    }

    public SddcManagerClient(
            String baseUrl,
            Credentials credentials,
            int pollLimit,
            long pollIntervalMillis,
            Sleeper sleeper) {
        this.baseUrl = validateBaseUrl(baseUrl);
        this.credentials = validateCredentials(credentials);
        if (pollLimit < 1) {
            throw new IllegalArgumentException("pollLimit must be at least 1");
        }
        if (pollIntervalMillis < 0) {
            throw new IllegalArgumentException("pollIntervalMillis must not be negative");
        }
        this.pollLimit = pollLimit;
        this.pollIntervalMillis = pollIntervalMillis;
        this.sleeper = Objects.requireNonNull(sleeper, "sleeper");
        this.httpClient = HttpClient.newBuilder()
                .followRedirects(HttpClient.Redirect.NEVER)
                .build();
    }

    /**
     * Sign in, submit the commission, and poll the returned task until it reaches a terminal state.
     */
    public synchronized CommissionOutcome commissionHosts(List<HostCommission> hosts) {
        validateHosts(hosts);
        throw new UnsupportedOperationException(
                "TODO: sign in, submit the commission, and poll the task to a terminal state");
    }

    private String createToken() {
        throw new UnsupportedOperationException("TODO: implement the createToken request");
    }

    private Task submitCommission(List<HostCommission> hosts, String accessToken) {
        throw new UnsupportedOperationException("TODO: implement the commissionHosts request");
    }

    private Task pollTask(String taskId, String accessToken) {
        throw new UnsupportedOperationException("TODO: implement the getTask request");
    }

    /** Serialize {@code TokenCreationSpec}, emitting only the members the caller supplied. */
    static String tokenRequestBody(Credentials credentials) {
        throw new UnsupportedOperationException("TODO: serialize TokenCreationSpec");
    }

    /** Serialize the {@code HostCommissionSpec} array, emitting only supplied members. */
    static String commissionRequestBody(List<HostCommission> hosts) {
        throw new UnsupportedOperationException("TODO: serialize the HostCommissionSpec array");
    }

    /** Percent-encode one path segment, leaving only RFC 3986 unreserved characters intact. */
    static String encodePathSegment(String segment) {
        throw new UnsupportedOperationException("TODO: percent-encode one path segment");
    }

    /** Fold the specification's mixed-case status spellings onto one comparable form. */
    static String normalizeStatus(String status) {
        throw new UnsupportedOperationException("TODO: normalize a Task.status spelling");
    }

    /** Apply the specification-required members and the vVol protocol rule before any request. */
    static List<HostCommission> validateHosts(List<HostCommission> hosts) {
        Objects.requireNonNull(hosts, "hosts");
        throw new UnsupportedOperationException(
                "TODO: apply the required members and the vVol protocol rule before the wire");
    }

    private static void requirePresent(String value, String member) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(
                    "HostCommissionSpec." + member + " is required and must be nonblank");
        }
    }

    private static Credentials validateCredentials(Credentials credentials) {
        return Objects.requireNonNull(credentials, "credentials");
    }

    private static String validateBaseUrl(String baseUrl) {
        if (baseUrl == null || baseUrl.isBlank()) {
            throw new IllegalArgumentException("baseUrl must be nonblank");
        }
        final URI uri;
        try {
            uri = new URI(baseUrl);
        } catch (URISyntaxException exception) {
            throw new IllegalArgumentException("baseUrl must be a valid URI");
        }
        String scheme = uri.getScheme();
        if (scheme == null
                || !(scheme.equalsIgnoreCase("http") || scheme.equalsIgnoreCase("https"))) {
            throw new IllegalArgumentException("baseUrl must be an http or https URL");
        }
        if (uri.getHost() == null || uri.getUserInfo() != null
                || uri.getRawQuery() != null || uri.getRawFragment() != null) {
            throw new IllegalArgumentException(
                    "baseUrl must be a bare service root with a host and no userinfo");
        }
        String path = uri.getRawPath() == null ? "" : uri.getRawPath();
        if (!path.isEmpty() && !path.equals("/")) {
            throw new IllegalArgumentException("baseUrl must not carry a path");
        }
        String text = baseUrl.trim();
        return text.endsWith("/") ? text.substring(0, text.length() - 1) : text;
    }

    private HttpResponse<String> send(HttpRequest request, String operationId) {
        try {
            return httpClient.send(
                    request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw new TransportException(operationId);
        } catch (IOException exception) {
            throw new TransportException(operationId);
        }
    }

    private static VcfApiException apiError(String operationId, HttpResponse<String> response) {
        String errorCode = null;
        try {
            Object parsed = MiniJson.parse(response.body());
            if (parsed instanceof Map<?, ?> error && error.get("errorCode") instanceof String code) {
                errorCode = code;
            }
        } catch (RuntimeException ignored) {
            errorCode = null;
        }
        return new VcfApiException(operationId, response.statusCode(), errorCode);
    }

    private static void requireJson(HttpResponse<String> response, String operationId) {
        String contentType = response.headers().firstValue("Content-Type").orElse("");
        int parameter = contentType.indexOf(';');
        String mediaType = (parameter < 0 ? contentType : contentType.substring(0, parameter))
                .trim();
        if (!mediaType.equalsIgnoreCase("application/json")) {
            throw new ProtocolException(operationId, "successful response must be JSON");
        }
    }

    private static Task decodeTask(String body, String operationId) {
        Map<?, ?> root = parseObject(body, operationId, "Task");
        String id = requiredText(root, "id", operationId);
        String name = requiredText(root, "name", operationId);
        String status = requiredText(root, "status", operationId);
        requiredText(root, "creationTimestamp", operationId);

        List<String> resourceIds = new ArrayList<>();
        Object resources = root.get("resources");
        if (resources != null) {
            if (!(resources instanceof List<?> list)) {
                throw new ProtocolException(operationId, "Task.resources must be an array");
            }
            for (Object element : list) {
                if (!(element instanceof Map<?, ?> resource)) {
                    throw new ProtocolException(operationId, "Resource must be an object");
                }
                resourceIds.add(requiredText(resource, "resourceId", operationId));
            }
        }

        String errorCode = null;
        String errorMessage = null;
        String referenceToken = null;
        Object errors = root.get("errors");
        if (errors != null) {
            if (!(errors instanceof List<?> list)) {
                throw new ProtocolException(operationId, "Task.errors must be an array");
            }
            if (!list.isEmpty()) {
                if (!(list.get(0) instanceof Map<?, ?> first)) {
                    throw new ProtocolException(operationId, "Error must be an object");
                }
                errorCode = textOrNull(first, "errorCode", operationId);
                errorMessage = textOrNull(first, "message", operationId);
                referenceToken = textOrNull(first, "referenceToken", operationId);
            }
        }
        return new Task(
                id, name, status, List.copyOf(resourceIds), errorCode, errorMessage,
                referenceToken);
    }

    private static Map<?, ?> parseObject(String body, String operationId, String schema) {
        final Object parsed;
        try {
            parsed = MiniJson.parse(body);
        } catch (RuntimeException exception) {
            throw new ProtocolException(operationId, "malformed " + schema + " JSON");
        }
        if (!(parsed instanceof Map<?, ?> object)) {
            throw new ProtocolException(operationId, schema + " must be a JSON object");
        }
        return object;
    }

    private static String requiredText(Map<?, ?> object, String member, String operationId) {
        Object value = object.get(member);
        if (!(value instanceof String text) || text.isBlank()) {
            throw new ProtocolException(operationId,
                    "required member " + member + " must be a nonblank string");
        }
        return text;
    }

    private static String textOrNull(Map<?, ?> object, String member, String operationId) {
        Object value = object.get(member);
        if (value == null) {
            return null;
        }
        if (!(value instanceof String text)) {
            throw new ProtocolException(operationId,
                    "optional member " + member + " must be a string when present");
        }
        return text;
    }

    private static boolean headerSafeText(String value) {
        if (value == null || value.isBlank()) {
            return false;
        }
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            if (character < 0x20 || character == 0x7F) {
                return false;
            }
        }
        return true;
    }

    /** Minimal JSON reader and string writer; the contract needs nothing broader. */
    static final class MiniJson {
        private final String text;
        private int at;

        private MiniJson(String text) {
            this.text = text;
        }

        static Object parse(String text) {
            MiniJson reader = new MiniJson(Objects.requireNonNull(text, "text"));
            reader.skipWhitespace();
            Object value = reader.readValue();
            reader.skipWhitespace();
            if (reader.at != reader.text.length()) {
                throw new IllegalArgumentException("trailing JSON content");
            }
            return value;
        }

        static String quote(String value) {
            StringBuilder quoted = new StringBuilder("\"");
            for (int index = 0; index < value.length(); index++) {
                char character = value.charAt(index);
                switch (character) {
                    case '"' -> quoted.append("\\\"");
                    case '\\' -> quoted.append("\\\\");
                    case '\b' -> quoted.append("\\b");
                    case '\f' -> quoted.append("\\f");
                    case '\n' -> quoted.append("\\n");
                    case '\r' -> quoted.append("\\r");
                    case '\t' -> quoted.append("\\t");
                    default -> {
                        if (character < 0x20) {
                            quoted.append(String.format("\\u%04x", (int) character));
                        } else {
                            quoted.append(character);
                        }
                    }
                }
            }
            return quoted.append('"').toString();
        }

        private Object readValue() {
            if (at >= text.length()) {
                throw new IllegalArgumentException("unexpected end of JSON");
            }
            char character = text.charAt(at);
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
            at++;
            skipWhitespace();
            if (peek() == '}') {
                at++;
                return object;
            }
            while (true) {
                skipWhitespace();
                String key = readString();
                skipWhitespace();
                expect(':');
                skipWhitespace();
                object.put(key, readValue());
                skipWhitespace();
                char next = peek();
                at++;
                if (next == '}') {
                    return object;
                }
                if (next != ',') {
                    throw new IllegalArgumentException("expected , or } in object");
                }
            }
        }

        private List<Object> readArray() {
            List<Object> array = new ArrayList<>();
            at++;
            skipWhitespace();
            if (peek() == ']') {
                at++;
                return array;
            }
            while (true) {
                skipWhitespace();
                array.add(readValue());
                skipWhitespace();
                char next = peek();
                at++;
                if (next == ']') {
                    return array;
                }
                if (next != ',') {
                    throw new IllegalArgumentException("expected , or ] in array");
                }
            }
        }

        private String readString() {
            expect('"');
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
                    case '"' -> value.append('"');
                    case '\\' -> value.append('\\');
                    case '/' -> value.append('/');
                    case 'b' -> value.append('\b');
                    case 'f' -> value.append('\f');
                    case 'n' -> value.append('\n');
                    case 'r' -> value.append('\r');
                    case 't' -> value.append('\t');
                    case 'u' -> {
                        value.append((char) Integer.parseInt(text.substring(at, at + 4), 16));
                        at += 4;
                    }
                    default -> throw new IllegalArgumentException("invalid JSON escape");
                }
            }
        }

        private Object readLiteral(String literal, Object value) {
            if (!text.startsWith(literal, at)) {
                throw new IllegalArgumentException("invalid JSON literal");
            }
            at += literal.length();
            return value;
        }

        private Object readNumber() {
            int start = at;
            while (at < text.length() && "+-.eE0123456789".indexOf(text.charAt(at)) >= 0) {
                at++;
            }
            String number = text.substring(start, at);
            if (number.isEmpty()) {
                throw new IllegalArgumentException("invalid JSON value");
            }
            if (number.indexOf('.') < 0 && number.indexOf('e') < 0 && number.indexOf('E') < 0) {
                return Long.parseLong(number);
            }
            return Double.parseDouble(number);
        }

        private void skipWhitespace() {
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

        private void expect(char expected) {
            if (peek() != expected) {
                throw new IllegalArgumentException("expected " + expected);
            }
            at++;
        }
    }
}
