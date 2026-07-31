import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.math.BigDecimal;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/**
 * Dependency-free client that correlates VKS Pod events with a previous log.
 *
 * Complete the TODOs in this file without adding another production source file.
 */
public final class VksFailureEvidenceClient {
    public static final String VCENTER_OPERATION =
            "Vcenter.Namespaces.User.Instances_list";
    public static final String EVENTS_OPERATION =
            "core/v1:namespaced-events:list";
    public static final String LOG_OPERATION =
            "core/v1:namespaced-pod-log:read";

    private static final int MAX_RESPONSE_BYTES = 64 * 1024;
    private static final int EVENT_LIMIT = 50;
    private static final int LOG_TAIL_LINES = 200;

    public enum Cause {
        UPSTREAM_DNS,
        INCONCLUSIVE
    }

    public record Config(
            URI vcenterApiBase,
            URI vksApiOrigin,
            String vcenterSessionId,
            String kubernetesBearerToken,
            Duration requestTimeout) {
    }

    public record EventEvidence(String reason, String message, long count) {
    }

    public record Diagnosis(
            Cause cause,
            String supervisorNamespace,
            String supervisorEndpoint,
            String workloadNamespace,
            String podName,
            String containerName,
            List<EventEvidence> warningEvents,
            String previousContainerLog) {
        public Diagnosis {
            Objects.requireNonNull(cause, "cause");
            Objects.requireNonNull(supervisorNamespace, "supervisorNamespace");
            Objects.requireNonNull(supervisorEndpoint, "supervisorEndpoint");
            Objects.requireNonNull(workloadNamespace, "workloadNamespace");
            Objects.requireNonNull(podName, "podName");
            Objects.requireNonNull(containerName, "containerName");
            warningEvents = List.copyOf(warningEvents);
            Objects.requireNonNull(previousContainerLog, "previousContainerLog");
        }
    }

    public static class ClientException extends RuntimeException {
        public ClientException(String message) {
            super(message);
        }
    }

    public static final class ApiException extends ClientException {
        private final String operation;
        private final int statusCode;

        public ApiException(String operation, int statusCode) {
            super(operation + " failed with HTTP " + statusCode);
            this.operation = operation;
            this.statusCode = statusCode;
        }

        public String operation() {
            return operation;
        }

        public int statusCode() {
            return statusCode;
        }
    }

    public static final class ProtocolException extends ClientException {
        private final String operation;

        public ProtocolException(String operation) {
            super(operation + " returned an invalid success response");
            this.operation = operation;
        }

        public String operation() {
            return operation;
        }
    }

    public static final class NamespaceNotAuthorizedException
            extends ClientException {
        public NamespaceNotAuthorizedException() {
            super("Supervisor namespace is not authorized");
        }
    }

    static record WireResponse(int status, byte[] body) {
    }

    interface Exchange {
        WireResponse send(String operation, HttpRequest request)
                throws InterruptedException;
    }

    private final Config config;
    private final Exchange exchange;
    private final String vcenterBase;
    private final String vksOrigin;

    public VksFailureEvidenceClient(Config config) {
        this(config, null);
    }

    VksFailureEvidenceClient(Config config, Exchange suppliedExchange) {
        this.config = Objects.requireNonNull(config, "config");
        this.vcenterBase = validateVcenterBase(config.vcenterApiBase());
        this.vksOrigin = validateOrigin(config.vksApiOrigin(), "vksApiOrigin");
        validateCredential(config.vcenterSessionId(), "vcenterSessionId");
        validateCredential(
                config.kubernetesBearerToken(), "kubernetesBearerToken");
        if (config.requestTimeout() == null
                || config.requestTimeout().isZero()
                || config.requestTimeout().isNegative()) {
            throw new IllegalArgumentException("requestTimeout must be positive");
        }
        if (suppliedExchange == null) {
            HttpClient httpClient = HttpClient.newBuilder()
                    .connectTimeout(config.requestTimeout())
                    .version(HttpClient.Version.HTTP_1_1)
                    .followRedirects(HttpClient.Redirect.NEVER)
                    .build();
            this.exchange = (operation, request) -> {
                HttpResponse<InputStream> response;
                try {
                    response = httpClient.send(
                            request, HttpResponse.BodyHandlers.ofInputStream());
                } catch (InterruptedException error) {
                    Thread.currentThread().interrupt();
                    throw error;
                } catch (IOException error) {
                    throw new ClientException(operation + " transport failed");
                }

                byte[] responseBody;
                try (InputStream stream = response.body()) {
                    responseBody = readLimited(stream, operation);
                } catch (IOException error) {
                    throw new ClientException(operation + " response read failed");
                }
                return new WireResponse(response.statusCode(), responseBody);
            };
        } else {
            this.exchange = suppliedExchange;
        }
    }

    public Diagnosis diagnose(
            String supervisorNamespace,
            String workloadNamespace,
            String podName,
            String containerName,
            String upstreamHost)
            throws InterruptedException {
        throw new UnsupportedOperationException(
                "TODO: authorize namespace, collect events and log, then correlate");
    }

    private String findAuthorizedNamespace(
            byte[] body, String supervisorNamespace) {
        throw new UnsupportedOperationException(
                "TODO: validate the complete namespace list and select one match");
    }

    private List<EventEvidence> decodeEvents(
            byte[] body, String workloadNamespace, String podName) {
        throw new UnsupportedOperationException(
                "TODO: validate the focused core/v1 EventList");
    }

    private static Cause classify(
            List<EventEvidence> events,
            String previousLog,
            String upstreamHost) {
        throw new UnsupportedOperationException(
                "TODO: require correlated BackOff and UnknownHost evidence");
    }

    private WireResponse send(
            String operation,
            String uri,
            boolean vcenter,
            String accept)
            throws InterruptedException {
        HttpRequest request;
        try {
            HttpRequest.Builder builder = HttpRequest.newBuilder(URI.create(uri))
                    .timeout(config.requestTimeout())
                    .header("Accept", accept)
                    .GET();
            if (vcenter) {
                builder.header(
                        "vmware-api-session-id", config.vcenterSessionId());
            } else {
                builder.header(
                        "Authorization",
                        "Bearer " + config.kubernetesBearerToken());
            }
            request = builder.build();
        } catch (IllegalArgumentException error) {
            throw new ClientException(operation + " request construction failed");
        }

        return exchange.send(operation, request);
    }

    private static byte[] readLimited(InputStream stream, String operation)
            throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[4096];
        int total = 0;
        while (true) {
            int read = stream.read(buffer);
            if (read == -1) {
                return output.toByteArray();
            }
            total += read;
            if (total > MAX_RESPONSE_BYTES) {
                throw new ClientException(operation + " response exceeded limit");
            }
            output.write(buffer, 0, read);
        }
    }

    private static void requireStatus(
            WireResponse response, String operation, int expected) {
        if (response.status() != expected) {
            throw new ApiException(operation, response.status());
        }
    }

    private static String decodeUtf8(byte[] bytes, String operation) {
        try {
            return StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(bytes))
                    .toString();
        } catch (CharacterCodingException error) {
            throw new ProtocolException(operation);
        }
    }

    private static String validateVcenterBase(URI uri) {
        String origin = validateOriginParts(uri, "vcenterApiBase", "/api");
        return origin + "/api";
    }

    private static String validateOrigin(URI uri, String name) {
        return validateOriginParts(uri, name, "/");
    }

    private static String validateOriginParts(
            URI uri, String name, String requiredPath) {
        if (uri == null || !uri.isAbsolute()) {
            throw new IllegalArgumentException(name + " must be absolute");
        }
        String scheme = uri.getScheme();
        String rawPath = uri.getRawPath();
        boolean pathMatches;
        if ("/".equals(requiredPath)) {
            pathMatches = rawPath == null
                    || rawPath.isEmpty()
                    || rawPath.equals("/");
        } else {
            pathMatches = requiredPath.equals(rawPath);
        }
        if ((!"http".equalsIgnoreCase(scheme)
                        && !"https".equalsIgnoreCase(scheme))
                || uri.getHost() == null
                || uri.getRawAuthority() == null
                || uri.getRawAuthority().isBlank()
                || uri.getRawUserInfo() != null
                || !pathMatches
                || uri.getRawQuery() != null
                || uri.getRawFragment() != null
                || uri.getPort() > 65535) {
            throw new IllegalArgumentException(name + " has invalid URI shape");
        }
        return scheme.toLowerCase() + "://" + uri.getRawAuthority();
    }

    private static String normalizeMasterHost(String value) {
        validateRequired(value, "master_host");
        if (value.indexOf('\r') >= 0 || value.indexOf('\n') >= 0) {
            throw new ProtocolException(VCENTER_OPERATION);
        }
        URI candidate;
        try {
            boolean explicitHttp = value.regionMatches(
                    true, 0, "http://", 0, "http://".length());
            boolean explicitHttps = value.regionMatches(
                    true, 0, "https://", 0, "https://".length());
            candidate = URI.create(
                    explicitHttp || explicitHttps
                            ? value
                            : "https://" + value);
            return validateOrigin(candidate, "master_host");
        } catch (IllegalArgumentException error) {
            throw new ProtocolException(VCENTER_OPERATION);
        }
    }

    private static void validateCredential(String value, String name) {
        if (value == null
                || value.isBlank()
                || value.indexOf('\r') >= 0
                || value.indexOf('\n') >= 0) {
            throw new IllegalArgumentException(name + " is invalid");
        }
    }

    private static void validateRequired(String value, String name) {
        if (value == null
                || value.isBlank()
                || value.indexOf('\r') >= 0
                || value.indexOf('\n') >= 0) {
            throw new IllegalArgumentException(name + " is required");
        }
    }

    private static String encodeComponent(String value) {
        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
        StringBuilder encoded = new StringBuilder(bytes.length);
        char[] hex = "0123456789ABCDEF".toCharArray();
        for (byte item : bytes) {
            int unsigned = item & 0xff;
            if ((unsigned >= 'a' && unsigned <= 'z')
                    || (unsigned >= 'A' && unsigned <= 'Z')
                    || (unsigned >= '0' && unsigned <= '9')
                    || unsigned == '-'
                    || unsigned == '.'
                    || unsigned == '_'
                    || unsigned == '~') {
                encoded.append((char) unsigned);
            } else {
                encoded.append('%')
                        .append(hex[unsigned >>> 4])
                        .append(hex[unsigned & 0x0f]);
            }
        }
        return encoded.toString();
    }

    private static Object parseJson(byte[] bytes, String operation) {
        try {
            return new JsonParser(
                            decodeUtf8(bytes, operation))
                    .parse();
        } catch (RuntimeException error) {
            if (error instanceof ProtocolException) {
                throw error;
            }
            throw new ProtocolException(operation);
        }
    }

    private static Map<String, Object> requiredObject(
            Map<String, Object> object, String key, String operation) {
        Object value = object.get(key);
        if (!(value instanceof Map<?, ?> map)) {
            throw new ProtocolException(operation);
        }
        return stringMap(map, operation);
    }

    private static Map<String, Object> stringMap(
            Map<?, ?> input, String operation) {
        Map<String, Object> result = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : input.entrySet()) {
            if (!(entry.getKey() instanceof String key)) {
                throw new ProtocolException(operation);
            }
            result.put(key, entry.getValue());
        }
        return result;
    }

    private static List<?> requiredList(
            Map<String, Object> object, String key, String operation) {
        Object value = object.get(key);
        if (!(value instanceof List<?> list)) {
            throw new ProtocolException(operation);
        }
        return list;
    }

    private static String requiredString(
            Map<String, Object> object, String key, String operation) {
        Object value = object.get(key);
        if (!(value instanceof String text)) {
            throw new ProtocolException(operation);
        }
        return text;
    }

    private static String requiredNonblankString(
            Map<String, Object> object, String key, String operation) {
        String value = requiredString(object, key, operation);
        if (value.isBlank()) {
            throw new ProtocolException(operation);
        }
        return value;
    }

    private static long requiredLong(
            Map<String, Object> object, String key, String operation) {
        Object value = object.get(key);
        if (!(value instanceof BigDecimal number)) {
            throw new ProtocolException(operation);
        }
        try {
            return number.longValueExact();
        } catch (ArithmeticException error) {
            throw new ProtocolException(operation);
        }
    }

    private static final class JsonParser {
        private final String input;
        private int offset;
        private int depth;

        private JsonParser(String input) {
            this.input = input;
        }

        private Object parse() {
            skipSpace();
            Object result = value();
            skipSpace();
            if (offset != input.length()) {
                throw new IllegalArgumentException("trailing JSON");
            }
            return result;
        }

        private Object value() {
            if (offset >= input.length()) {
                throw new IllegalArgumentException("missing JSON value");
            }
            return switch (input.charAt(offset)) {
                case '{' -> object();
                case '[' -> array();
                case '"' -> string();
                case 't' -> literal("true", Boolean.TRUE);
                case 'f' -> literal("false", Boolean.FALSE);
                case 'n' -> literal("null", null);
                default -> number();
            };
        }

        private Map<String, Object> object() {
            enter();
            try {
                expect('{');
                skipSpace();
                Map<String, Object> result = new LinkedHashMap<>();
                if (consume('}')) {
                    return result;
                }
                while (true) {
                    skipSpace();
                    String key = string();
                    skipSpace();
                    expect(':');
                    skipSpace();
                    if (result.containsKey(key)) {
                        throw new IllegalArgumentException("duplicate key");
                    }
                    result.put(key, value());
                    skipSpace();
                    if (consume('}')) {
                        return result;
                    }
                    expect(',');
                    skipSpace();
                }
            } finally {
                depth--;
            }
        }

        private List<Object> array() {
            enter();
            try {
                expect('[');
                skipSpace();
                List<Object> result = new ArrayList<>();
                if (consume(']')) {
                    return result;
                }
                while (true) {
                    result.add(value());
                    skipSpace();
                    if (consume(']')) {
                        return result;
                    }
                    expect(',');
                    skipSpace();
                }
            } finally {
                depth--;
            }
        }

        private void enter() {
            depth++;
            if (depth > 64) {
                throw new IllegalArgumentException("JSON nesting too deep");
            }
        }

        private String string() {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (offset < input.length()) {
                char item = input.charAt(offset++);
                if (item == '"') {
                    return result.toString();
                }
                if (item == '\\') {
                    if (offset >= input.length()) {
                        throw new IllegalArgumentException("bad escape");
                    }
                    char escaped = input.charAt(offset++);
                    switch (escaped) {
                        case '"', '\\', '/' -> result.append(escaped);
                        case 'b' -> result.append('\b');
                        case 'f' -> result.append('\f');
                        case 'n' -> result.append('\n');
                        case 'r' -> result.append('\r');
                        case 't' -> result.append('\t');
                        case 'u' -> result.append(unicodeEscape());
                        default -> throw new IllegalArgumentException("bad escape");
                    }
                } else {
                    if (item < 0x20) {
                        throw new IllegalArgumentException("control in string");
                    }
                    result.append(item);
                }
            }
            throw new IllegalArgumentException("unterminated string");
        }

        private char unicodeEscape() {
            if (offset + 4 > input.length()) {
                throw new IllegalArgumentException("short unicode escape");
            }
            int value = 0;
            for (int count = 0; count < 4; count++) {
                int digit = Character.digit(input.charAt(offset++), 16);
                if (digit < 0) {
                    throw new IllegalArgumentException("bad unicode escape");
                }
                value = (value << 4) | digit;
            }
            return (char) value;
        }

        private Object literal(String text, Object value) {
            if (!input.startsWith(text, offset)) {
                throw new IllegalArgumentException("bad literal");
            }
            offset += text.length();
            return value;
        }

        private BigDecimal number() {
            int start = offset;
            consume('-');
            if (!consume('0')) {
                digits();
            } else if (offset < input.length()
                    && Character.isDigit(input.charAt(offset))) {
                throw new IllegalArgumentException("leading zero");
            }
            if (consume('.')) {
                digits();
            }
            if (consume('e') || consume('E')) {
                if (!consume('+')) {
                    consume('-');
                }
                digits();
            }
            if (start == offset) {
                throw new IllegalArgumentException("bad number");
            }
            return new BigDecimal(input.substring(start, offset));
        }

        private void digits() {
            int start = offset;
            while (offset < input.length()
                    && Character.isDigit(input.charAt(offset))) {
                offset++;
            }
            if (start == offset) {
                throw new IllegalArgumentException("digits required");
            }
        }

        private void skipSpace() {
            while (offset < input.length()) {
                char item = input.charAt(offset);
                if (item == ' '
                        || item == '\n'
                        || item == '\r'
                        || item == '\t') {
                    offset++;
                } else {
                    return;
                }
            }
        }

        private boolean consume(char expected) {
            if (offset < input.length() && input.charAt(offset) == expected) {
                offset++;
                return true;
            }
            return false;
        }

        private void expect(char expected) {
            if (!consume(expected)) {
                throw new IllegalArgumentException("unexpected JSON token");
            }
        }
    }
}
