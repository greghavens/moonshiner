import java.io.IOException;
import java.net.ProxySelector;
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
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Dependency-free client for the focused VCF Operations Log Management
 * contract in docs/contract.json.
 *
 * <p>The public surface in this file is part of the exercise contract.</p>
 */
public final class VcfLogManagementClient {
    private static final String PATCH_AGENT_GROUP = "patchUpdateAgentGroupConfig";
    private static final String PATCH_FORWARDER = "patchLogForwarder";
    private static final String TEST_FORWARDER = "testLogForwarderConnection";

    private final URI origin;
    private final String jwtToken;
    private final Duration requestTimeout;
    private final HttpClient httpClient;

    public enum ChangeStatus {
        APPLIED,
        PARTIALLY_APPLIED,
        FAILED
    }

    public enum StepOutcome {
        APPLIED,
        VERIFIED,
        FAILED
    }

    public enum ForwarderProtocol {
        SYSLOG,
        RAW,
        RAWPLUS
    }

    public enum TransportProtocol {
        TCP,
        UDP
    }

    public record ForwarderProbe(
            String host,
            int port,
            ForwarderProtocol protocol,
            boolean sslEnabled,
            TransportProtocol transportProtocol) {
    }

    public record StepResult(
            String operationId,
            StepOutcome outcome,
            int statusCode,
            String errorCode,
            String errorMessage) {
    }

    public record ChangeReport(
            ChangeStatus status,
            boolean succeeded,
            List<StepResult> steps) {
        public ChangeReport {
            steps = List.copyOf(steps);
        }
    }

    public static final class ProtocolException extends IOException {
        private final String operationId;
        private final int statusCode;

        ProtocolException(String operationId, int statusCode, String message) {
            super(message);
            this.operationId = operationId;
            this.statusCode = statusCode;
        }

        public String operationId() {
            return operationId;
        }

        public int statusCode() {
            return statusCode;
        }
    }

    public VcfLogManagementClient(
            URI origin,
            String jwtToken,
            Duration requestTimeout) {
        this(origin, jwtToken, requestTimeout, null);
    }

    VcfLogManagementClient(
            URI origin,
            String jwtToken,
            Duration requestTimeout,
            HttpClient testHttpClient) {
        this.origin = requireOrigin(origin);
        this.jwtToken = requireToken(jwtToken);
        if (requestTimeout == null
                || requestTimeout.isZero()
                || requestTimeout.isNegative()) {
            throw new IllegalArgumentException("requestTimeout must be positive");
        }
        this.requestTimeout = requestTimeout;
        this.httpClient = testHttpClient != null
                ? testHttpClient
                : HttpClient.newBuilder()
                        .connectTimeout(requestTimeout)
                        .followRedirects(HttpClient.Redirect.NEVER)
                        .proxy(ProxySelector.of(null))
                        .build();
    }

    /**
     * Applies the agent-group patch, then the forwarder patch, then tests the
     * target. Accepted earlier work remains in the returned report if a later
     * HTTP step fails.
     */
    public ChangeReport applyRoutingChange(
            String agentGroupId,
            boolean agentAutoUpdate,
            String forwarderId,
            boolean forwarderEnabled,
            ForwarderProbe probe) throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO: apply routing change");
    }

    private record WireResponse(
            int statusCode,
            byte[] body,
            String contentType) {
        WireResponse {
            body = body.clone();
        }

        @Override
        public byte[] body() {
            return body.clone();
        }
    }

    private WireResponse send(
            String operationId,
            String method,
            String rawPath,
            byte[] body) throws IOException, InterruptedException {
        HttpRequest request = HttpRequest.newBuilder(
                        URI.create(origin.toASCIIString() + rawPath))
                .timeout(requestTimeout)
                .header("Accept", "application/json")
                .header("Content-Type", "application/json")
                .header("X-JWT-Token", jwtToken)
                .method(method, HttpRequest.BodyPublishers.ofByteArray(body))
                .build();
        final HttpResponse<byte[]> response;
        try {
            response = httpClient.send(
                    request, HttpResponse.BodyHandlers.ofByteArray());
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw interrupted;
        } catch (IOException failure) {
            throw new IOException(operationId + " transport failed", failure);
        }
        if (response.body().length > 1024 * 1024) {
            throw protocol(operationId, response.statusCode(),
                    "response body exceeds the client limit");
        }
        return new WireResponse(
                response.statusCode(),
                response.body(),
                response.headers().firstValue("Content-Type").orElse(null));
    }

    private Map<String, Object> successObject(
            String operationId,
            WireResponse response) throws ProtocolException {
        if (!isJson(response.contentType())) {
            throw protocol(operationId, response.statusCode(),
                    "successful response is not application/json");
        }
        final Object value;
        try {
            value = Json.decode(response.body());
        } catch (IOException malformed) {
            throw protocol(operationId, response.statusCode(),
                    "successful response body is not valid JSON");
        }
        if (!(value instanceof Map<?, ?> object)) {
            throw protocol(operationId, response.statusCode(),
                    "successful response body must be a JSON object");
        }
        return stringObject(object, operationId, response.statusCode());
    }

    private StepResult failedStep(String operationId, WireResponse response) {
        String errorCode = null;
        String errorMessage = null;
        if (isJson(response.contentType())) {
            try {
                Object value = Json.decode(response.body());
                if (value instanceof Map<?, ?> object) {
                    if (object.get("errorCode") instanceof String text) {
                        errorCode = text;
                    }
                    if (object.get("errorMessage") instanceof String text) {
                        errorMessage = text;
                    }
                }
            } catch (IOException ignored) {
                // The status still identifies the failed step; raw text stays private.
            }
        }
        return new StepResult(
                operationId,
                StepOutcome.FAILED,
                response.statusCode(),
                errorCode,
                errorMessage);
    }

    private static StepResult applied(String operationId) {
        return new StepResult(
                operationId, StepOutcome.APPLIED, 200, null, null);
    }

    private static ChangeReport failedReport(List<StepResult> steps) {
        boolean hasAcceptedStep = steps.stream()
                .anyMatch(step -> step.outcome() == StepOutcome.APPLIED
                        || step.outcome() == StepOutcome.VERIFIED);
        return new ChangeReport(
                hasAcceptedStep
                        ? ChangeStatus.PARTIALLY_APPLIED
                        : ChangeStatus.FAILED,
                false,
                steps);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> stringObject(
            Map<?, ?> value,
            String operationId,
            int status) throws ProtocolException {
        for (Object key : value.keySet()) {
            if (!(key instanceof String)) {
                throw protocol(operationId, status,
                        "response object contains a non-string key");
            }
        }
        return (Map<String, Object>) value;
    }

    private static boolean isJson(String contentType) {
        if (contentType == null) {
            return false;
        }
        int separator = contentType.indexOf(';');
        String mediaType = separator < 0
                ? contentType
                : contentType.substring(0, separator);
        return mediaType.trim().equalsIgnoreCase("application/json");
    }

    private static ProtocolException protocol(
            String operationId, int status, String message) {
        return new ProtocolException(
                operationId,
                status,
                operationId + " protocol failure: " + message);
    }

    private static URI requireOrigin(URI value) {
        if (value == null
                || value.getScheme() == null
                || !(value.getScheme().equalsIgnoreCase("http")
                        || value.getScheme().equalsIgnoreCase("https"))
                || value.getHost() == null
                || value.getRawUserInfo() != null
                || value.getRawQuery() != null
                || value.getRawFragment() != null
                || !(value.getRawPath() == null
                        || value.getRawPath().isEmpty()
                        || value.getRawPath().equals("/"))) {
            throw new IllegalArgumentException("origin must be an HTTP(S) origin");
        }
        String text = value.toASCIIString();
        return URI.create(text.endsWith("/")
                ? text.substring(0, text.length() - 1)
                : text);
    }

    private static String requireToken(String value) {
        if (value == null
                || value.isBlank()
                || !value.equals(value.strip())
                || value.indexOf('\r') >= 0
                || value.indexOf('\n') >= 0) {
            throw new IllegalArgumentException("JWT token must be nonblank and header-safe");
        }
        return value;
    }

    private static void requireResourceId(String value, String name) {
        if (value == null
                || value.isBlank()
                || !value.equals(value.strip())
                || !StandardCharsets.UTF_8.newEncoder().canEncode(value)) {
            throw new IllegalArgumentException(name + " must be nonblank and valid Unicode");
        }
    }

    private static void requireProbe(ForwarderProbe probe) {
        if (probe == null
                || probe.host() == null
                || probe.host().isBlank()
                || !probe.host().equals(probe.host().strip())
                || !StandardCharsets.UTF_8.newEncoder().canEncode(probe.host())
                || probe.port() < 1
                || probe.port() > 65535
                || probe.protocol() == null
                || probe.transportProtocol() == null) {
            throw new IllegalArgumentException("ForwarderProbe is invalid");
        }
    }

    private static String encodeSegment(String value) {
        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
        char[] hex = "0123456789ABCDEF".toCharArray();
        StringBuilder encoded = new StringBuilder(bytes.length);
        for (byte signed : bytes) {
            int b = signed & 0xff;
            if ((b >= 'a' && b <= 'z')
                    || (b >= 'A' && b <= 'Z')
                    || (b >= '0' && b <= '9')
                    || b == '-' || b == '.' || b == '_' || b == '~') {
                encoded.append((char) b);
            } else {
                encoded.append('%')
                        .append(hex[b >>> 4])
                        .append(hex[b & 15]);
            }
        }
        return encoded.toString();
    }

    /*
     * Small strict JSON codec supplied so the exercise stays focused on the
     * VCF REST contract and truthful partial-result semantics.
     */
    private static final class Json {
        static byte[] encode(Object value) throws IOException {
            StringBuilder out = new StringBuilder();
            write(value, out);
            return out.toString().getBytes(StandardCharsets.UTF_8);
        }

        static Object decode(byte[] bytes) throws IOException {
            final String text;
            try {
                text = StandardCharsets.UTF_8.newDecoder()
                        .onMalformedInput(CodingErrorAction.REPORT)
                        .onUnmappableCharacter(CodingErrorAction.REPORT)
                        .decode(ByteBuffer.wrap(bytes))
                        .toString();
            } catch (CharacterCodingException malformed) {
                throw new IOException("response body is not valid UTF-8");
            }
            return new Parser(text).document();
        }

        private static void write(Object value, StringBuilder out) throws IOException {
            if (value == null) {
                out.append("null");
            } else if (value instanceof String text) {
                quote(text, out);
            } else if (value instanceof Boolean) {
                out.append(value);
            } else if (value instanceof Byte
                    || value instanceof Short
                    || value instanceof Integer
                    || value instanceof Long) {
                out.append(value);
            } else if (value instanceof Float number) {
                if (!Float.isFinite(number)) {
                    throw new IOException("JSON number must be finite");
                }
                out.append(number);
            } else if (value instanceof Double number) {
                if (!Double.isFinite(number)) {
                    throw new IOException("JSON number must be finite");
                }
                out.append(number);
            } else if (value instanceof Map<?, ?> object) {
                out.append('{');
                boolean first = true;
                for (Map.Entry<?, ?> entry : object.entrySet()) {
                    if (!(entry.getKey() instanceof String key)) {
                        throw new IOException("JSON object keys must be strings");
                    }
                    if (!first) {
                        out.append(',');
                    }
                    first = false;
                    quote(key, out);
                    out.append(':');
                    write(entry.getValue(), out);
                }
                out.append('}');
            } else if (value instanceof Iterable<?> array) {
                out.append('[');
                boolean first = true;
                for (Object item : array) {
                    if (!first) {
                        out.append(',');
                    }
                    first = false;
                    write(item, out);
                }
                out.append(']');
            } else {
                throw new IOException("value is not JSON serializable");
            }
        }

        private static void quote(String text, StringBuilder out) {
            out.append('"');
            for (int index = 0; index < text.length(); index++) {
                char c = text.charAt(index);
                switch (c) {
                    case '"' -> out.append("\\\"");
                    case '\\' -> out.append("\\\\");
                    case '\b' -> out.append("\\b");
                    case '\f' -> out.append("\\f");
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

        private static final class Parser {
            private final String source;
            private int at;

            Parser(String source) {
                this.source = source;
            }

            Object document() throws IOException {
                Object value = value();
                whitespace();
                if (at != source.length()) {
                    throw malformed();
                }
                return value;
            }

            private Object value() throws IOException {
                whitespace();
                if (at >= source.length()) {
                    throw malformed();
                }
                return switch (source.charAt(at)) {
                    case '{' -> object();
                    case '[' -> array();
                    case '"' -> string();
                    case 't' -> literal("true", Boolean.TRUE);
                    case 'f' -> literal("false", Boolean.FALSE);
                    case 'n' -> literal("null", null);
                    default -> number();
                };
            }

            private Map<String, Object> object() throws IOException {
                at++;
                LinkedHashMap<String, Object> result = new LinkedHashMap<>();
                whitespace();
                if (take('}')) {
                    return result;
                }
                while (true) {
                    whitespace();
                    if (at >= source.length() || source.charAt(at) != '"') {
                        throw malformed();
                    }
                    String key = string();
                    whitespace();
                    require(':');
                    if (result.containsKey(key)) {
                        throw malformed();
                    }
                    result.put(key, value());
                    whitespace();
                    if (take('}')) {
                        return result;
                    }
                    require(',');
                }
            }

            private List<Object> array() throws IOException {
                at++;
                ArrayList<Object> result = new ArrayList<>();
                whitespace();
                if (take(']')) {
                    return result;
                }
                while (true) {
                    result.add(value());
                    whitespace();
                    if (take(']')) {
                        return result;
                    }
                    require(',');
                }
            }

            private String string() throws IOException {
                require('"');
                StringBuilder result = new StringBuilder();
                while (at < source.length()) {
                    char c = source.charAt(at++);
                    if (c == '"') {
                        return result.toString();
                    }
                    if (c == '\\') {
                        if (at >= source.length()) {
                            throw malformed();
                        }
                        char escaped = source.charAt(at++);
                        switch (escaped) {
                            case '"', '\\', '/' -> result.append(escaped);
                            case 'b' -> result.append('\b');
                            case 'f' -> result.append('\f');
                            case 'n' -> result.append('\n');
                            case 'r' -> result.append('\r');
                            case 't' -> result.append('\t');
                            case 'u' -> result.append(unicode());
                            default -> throw malformed();
                        }
                    } else {
                        if (c < 0x20) {
                            throw malformed();
                        }
                        result.append(c);
                    }
                }
                throw malformed();
            }

            private char unicode() throws IOException {
                if (at + 4 > source.length()) {
                    throw malformed();
                }
                try {
                    char result = (char) Integer.parseInt(source.substring(at, at + 4), 16);
                    at += 4;
                    return result;
                } catch (NumberFormatException badHex) {
                    throw malformed();
                }
            }

            private Object number() throws IOException {
                int start = at;
                take('-');
                if (take('0')) {
                    // zero consumed
                } else {
                    digits();
                }
                boolean decimal = false;
                if (take('.')) {
                    decimal = true;
                    digits();
                }
                if (at < source.length()
                        && (source.charAt(at) == 'e' || source.charAt(at) == 'E')) {
                    decimal = true;
                    at++;
                    if (!take('+')) {
                        take('-');
                    }
                    digits();
                }
                if (start == at) {
                    throw malformed();
                }
                String token = source.substring(start, at);
                try {
                    if (decimal) {
                        return Double.valueOf(token);
                    }
                    return Long.valueOf(token);
                } catch (NumberFormatException badNumber) {
                    throw malformed();
                }
            }

            private void digits() throws IOException {
                int start = at;
                while (at < source.length() && Character.isDigit(source.charAt(at))) {
                    at++;
                }
                if (start == at) {
                    throw malformed();
                }
            }

            private Object literal(String text, Object value) throws IOException {
                if (!source.startsWith(text, at)) {
                    throw malformed();
                }
                at += text.length();
                return value;
            }

            private boolean take(char expected) {
                if (at < source.length() && source.charAt(at) == expected) {
                    at++;
                    return true;
                }
                return false;
            }

            private void require(char expected) throws IOException {
                if (!take(expected)) {
                    throw malformed();
                }
            }

            private void whitespace() {
                while (at < source.length()
                        && (source.charAt(at) == ' '
                                || source.charAt(at) == '\n'
                                || source.charAt(at) == '\r'
                                || source.charAt(at) == '\t')) {
                    at++;
                }
            }

            private IOException malformed() {
                return new IOException("response body is not valid JSON");
            }
        }
    }
}
