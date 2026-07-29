import java.io.ByteArrayInputStream;
import java.io.IOException;
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
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.zip.GZIPInputStream;

/**
 * Focused VCF 9.1 SDDC Manager client for evidence-based task diagnosis.
 *
 * <p>This file intentionally uses only Java 17 standard-library APIs.</p>
 */
public final class VcfFailureDiagnosticsClient {
    public record NotificationEvent(
            String id,
            String type,
            String severity,
            String message,
            List<String> resourceIds) {
        public NotificationEvent {
            resourceIds = List.copyOf(resourceIds);
        }
    }

    public record Diagnosis(
            String taskId,
            String cause,
            String eventId,
            String evidencePath,
            List<NotificationEvent> relevantEvents,
            String bundleId) {
        public Diagnosis {
            relevantEvents = List.copyOf(relevantEvents);
        }
    }

    private static final int MAX_COMPRESSED_BYTES = 2 * 1024 * 1024;
    private static final int MAX_ARCHIVE_ENTRIES = 64;
    private static final int MAX_FILE_BYTES = 128 * 1024;
    private static final int MAX_EXPANDED_BYTES = 512 * 1024;

    private final URI baseUri;
    private final String accessToken;
    private final int maxPollAttempts;
    private final Duration requestTimeout;
    private final HttpClient http;

    public VcfFailureDiagnosticsClient(
            URI baseUri,
            String accessToken,
            int maxPollAttempts,
            Duration requestTimeout) {
        this.baseUri = validateBaseUri(baseUri);
        if (accessToken == null
                || accessToken.isBlank()
                || !accessToken.equals(accessToken.trim())
                || accessToken.chars().anyMatch(Character::isWhitespace)) {
            throw new IllegalArgumentException("accessToken must be nonblank without whitespace");
        }
        if (maxPollAttempts < 1) {
            throw new IllegalArgumentException("maxPollAttempts must be positive");
        }
        if (requestTimeout == null || requestTimeout.isZero() || requestTimeout.isNegative()) {
            throw new IllegalArgumentException("requestTimeout must be positive");
        }
        this.accessToken = accessToken;
        this.maxPollAttempts = maxPollAttempts;
        this.requestTimeout = requestTimeout;
        this.http = HttpClient.newBuilder()
                .followRedirects(HttpClient.Redirect.NEVER)
                .connectTimeout(requestTimeout)
                .build();
    }

    /**
     * Retrieves a failed task and its resource notifications, collects a focused
     * support bundle, and reports only a three-way-correlated log cause.
     */
    public Diagnosis diagnoseTaskFailure(String taskId)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO: implement evidence-based diagnosis");
    }

    private Map<String, Object> sendJson(
            String operationId,
            String method,
            String rawPath,
            String body,
            int expectedStatus)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }

    private byte[] sendBytes(
            String operationId,
            String rawPath,
            int expectedStatus)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }

    private Evidence findEvidence(
            byte[] archive,
            String taskId,
            Set<String> referenceTokens,
            Set<String> eventIds)
            throws IOException {
        throw new UnsupportedOperationException("TODO");
    }

    private record Evidence(String cause, String eventId, String path) {}

    private static URI validateBaseUri(URI value) {
        Objects.requireNonNull(value, "baseUri");
        String scheme = value.getScheme();
        if (scheme == null
                || !(scheme.equalsIgnoreCase("http") || scheme.equalsIgnoreCase("https"))
                || value.getRawAuthority() == null
                || value.getHost() == null
                || value.getUserInfo() != null
                || value.getRawQuery() != null
                || value.getRawFragment() != null
                || !(value.getRawPath() == null
                        || value.getRawPath().isEmpty()
                        || value.getRawPath().equals("/"))) {
            throw new IllegalArgumentException("baseUri must be an HTTP(S) origin");
        }
        return value;
    }

    private static Map<String, Object> object(Object value, String what) throws IOException {
        if (!(value instanceof Map<?, ?> raw)) {
            throw new IOException("malformed " + what);
        }
        Map<String, Object> result = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : raw.entrySet()) {
            if (!(entry.getKey() instanceof String key)) {
                throw new IOException("malformed " + what);
            }
            result.put(key, entry.getValue());
        }
        return result;
    }

    private static List<Object> array(Object value, String what) throws IOException {
        if (!(value instanceof List<?> raw)) {
            throw new IOException("malformed " + what);
        }
        return new ArrayList<>(raw);
    }

    private static String requiredString(Map<String, Object> value, String key, String what)
            throws IOException {
        Object raw = value.get(key);
        if (!(raw instanceof String text) || text.isBlank()) {
            throw new IOException("malformed " + what);
        }
        return text;
    }

    private static String optionalString(Map<String, Object> value, String key)
            throws IOException {
        Object raw = value.get(key);
        if (raw == null) {
            return null;
        }
        if (!(raw instanceof String text)) {
            throw new IOException("malformed JSON string");
        }
        return text;
    }

    private static String mediaType(HttpResponse<?> response) {
        return response.headers().firstValue("Content-Type")
                .orElse("")
                .split(";", 2)[0]
                .trim()
                .toLowerCase(java.util.Locale.ROOT);
    }

    private static String decodeUtf8(byte[] bytes, String what) throws IOException {
        try {
            return StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(bytes))
                    .toString();
        } catch (CharacterCodingException error) {
            throw new IOException("malformed UTF-8 " + what);
        }
    }

    /**
     * Small JSON parser sufficient for the spec-projected response models.
     */
    private static final class Json {
        private final String text;
        private int index;

        private Json(String text) {
            this.text = text;
        }

        static Object parse(String text) throws IOException {
            Json parser = new Json(text);
            Object value = parser.value();
            parser.space();
            if (parser.index != text.length()) {
                throw new IOException("malformed JSON response");
            }
            return value;
        }

        private Object value() throws IOException {
            space();
            if (index >= text.length()) {
                throw new IOException("malformed JSON response");
            }
            return switch (text.charAt(index)) {
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
            index++;
            LinkedHashMap<String, Object> result = new LinkedHashMap<>();
            space();
            if (take('}')) {
                return result;
            }
            while (true) {
                space();
                if (index >= text.length() || text.charAt(index) != '"') {
                    throw new IOException("malformed JSON response");
                }
                String key = string();
                space();
                expect(':');
                result.put(key, value());
                space();
                if (take('}')) {
                    return result;
                }
                expect(',');
            }
        }

        private List<Object> array() throws IOException {
            index++;
            ArrayList<Object> result = new ArrayList<>();
            space();
            if (take(']')) {
                return result;
            }
            while (true) {
                result.add(value());
                space();
                if (take(']')) {
                    return result;
                }
                expect(',');
            }
        }

        private String string() throws IOException {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (index < text.length()) {
                char current = text.charAt(index++);
                if (current == '"') {
                    return result.toString();
                }
                if (current == '\\') {
                    if (index >= text.length()) {
                        throw new IOException("malformed JSON response");
                    }
                    char escaped = text.charAt(index++);
                    switch (escaped) {
                        case '"', '\\', '/' -> result.append(escaped);
                        case 'b' -> result.append('\b');
                        case 'f' -> result.append('\f');
                        case 'n' -> result.append('\n');
                        case 'r' -> result.append('\r');
                        case 't' -> result.append('\t');
                        case 'u' -> {
                            if (index + 4 > text.length()) {
                                throw new IOException("malformed JSON response");
                            }
                            try {
                                result.append((char) Integer.parseInt(
                                        text.substring(index, index + 4), 16));
                            } catch (NumberFormatException error) {
                                throw new IOException("malformed JSON response");
                            }
                            index += 4;
                        }
                        default -> throw new IOException("malformed JSON response");
                    }
                } else {
                    if (current < 0x20) {
                        throw new IOException("malformed JSON response");
                    }
                    result.append(current);
                }
            }
            throw new IOException("malformed JSON response");
        }

        private Object literal(String literal, Object value) throws IOException {
            if (!text.startsWith(literal, index)) {
                throw new IOException("malformed JSON response");
            }
            index += literal.length();
            return value;
        }

        private Number number() throws IOException {
            int start = index;
            if (take('-')) {
                // sign consumed
            }
            while (index < text.length() && Character.isDigit(text.charAt(index))) {
                index++;
            }
            if (take('.')) {
                while (index < text.length() && Character.isDigit(text.charAt(index))) {
                    index++;
                }
            }
            if (index < text.length() && (text.charAt(index) == 'e' || text.charAt(index) == 'E')) {
                index++;
                if (index < text.length()
                        && (text.charAt(index) == '+' || text.charAt(index) == '-')) {
                    index++;
                }
                while (index < text.length() && Character.isDigit(text.charAt(index))) {
                    index++;
                }
            }
            if (start == index) {
                throw new IOException("malformed JSON response");
            }
            try {
                String token = text.substring(start, index);
                return token.contains(".") || token.contains("e") || token.contains("E")
                        ? Double.valueOf(token)
                        : Long.valueOf(token);
            } catch (NumberFormatException error) {
                throw new IOException("malformed JSON response");
            }
        }

        private void space() {
            while (index < text.length() && Character.isWhitespace(text.charAt(index))) {
                index++;
            }
        }

        private boolean take(char expected) {
            if (index < text.length() && text.charAt(index) == expected) {
                index++;
                return true;
            }
            return false;
        }

        private void expect(char expected) throws IOException {
            if (!take(expected)) {
                throw new IOException("malformed JSON response");
            }
        }
    }
}
