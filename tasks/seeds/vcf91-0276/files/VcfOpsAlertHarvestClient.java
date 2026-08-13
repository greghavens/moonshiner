import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
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
import java.util.Locale;
import java.util.Map;
import java.util.Objects;

/**
 * Focused VCF 9.1 VCF Operations client that harvests the critical and
 * immediate alerts raised against one resource kind.
 *
 * <p>This file intentionally uses only Java 17 standard-library APIs.</p>
 */
public final class VcfOpsAlertHarvestClient {
    public record MonitoredResource(String identifier, String name, String resourceKindKey) {}

    public record ActiveAlert(String alertId, String resourceId, String alertLevel, String status) {}

    public record Harvest(
            List<MonitoredResource> resources,
            List<ActiveAlert> alerts,
            int tokenAcquisitions) {
        public Harvest {
            resources = List.copyOf(resources);
            alerts = List.copyOf(alerts);
        }
    }

    private static final int MAX_RESPONSE_BYTES = 1024 * 1024;

    private final URI baseUri;
    private final String username;
    private final String password;
    private final String authSource;
    private final int pageSize;
    private final Duration requestTimeout;
    private final HttpClient http;

    private String token;
    private int tokenAcquisitions;

    /**
     * @param authSource optional VCF Operations auth source name; {@code null}
     *                   selects the deployment default and must not be sent.
     */
    public VcfOpsAlertHarvestClient(
            URI baseUri,
            String username,
            String password,
            String authSource,
            int pageSize,
            Duration requestTimeout) {
        this.baseUri = validateBaseUri(baseUri);
        this.username = validateCredential(username, "username");
        this.password = validateCredential(password, "password");
        if (authSource != null) {
            validateCredential(authSource, "authSource");
        }
        if (pageSize < 1) {
            throw new IllegalArgumentException("pageSize must be positive");
        }
        if (requestTimeout == null || requestTimeout.isZero() || requestTimeout.isNegative()) {
            throw new IllegalArgumentException("requestTimeout must be positive");
        }
        this.authSource = authSource;
        this.pageSize = pageSize;
        this.requestTimeout = requestTimeout;
        this.http = HttpClient.newBuilder()
                .followRedirects(HttpClient.Redirect.NEVER)
                .connectTimeout(requestTimeout)
                .build();
    }

    /**
     * Lists every monitored resource of {@code resourceKind}, queries the
     * critical and immediate alerts raised against those resources, and
     * releases the session token.
     *
     * <p>The lab token expires part way through the run; the harvest must
     * survive that without losing or repeating collected pages.</p>
     */
    public Harvest harvestCriticalAlerts(String resourceKind)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO: implement the alert harvest");
    }

    private Map<String, Object> callSecured(
            String operationId,
            String method,
            String target,
            String body)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }

    private void acquireToken() throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }

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

    private static String validateCredential(String value, String what) {
        if (value == null
                || value.isBlank()
                || !value.equals(value.trim())
                || value.indexOf('\r') >= 0
                || value.indexOf('\n') >= 0) {
            throw new IllegalArgumentException(what + " must be nonblank without control characters");
        }
        return value;
    }

    /** Percent-encodes one query parameter value. */
    private static String encodeQueryValue(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
    }

    /** Renders one JSON string literal, including the surrounding quotes. */
    private static String quote(String value) {
        StringBuilder out = new StringBuilder(value.length() + 2).append('"');
        for (int index = 0; index < value.length(); index++) {
            char symbol = value.charAt(index);
            switch (symbol) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\b' -> out.append("\\b");
                case '\f' -> out.append("\\f");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                default -> {
                    if (symbol < 0x20) {
                        out.append(String.format("\\u%04x", (int) symbol));
                    } else {
                        out.append(symbol);
                    }
                }
            }
        }
        return out.append('"').toString();
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

    private static long requiredNumber(Map<String, Object> value, String key, String what)
            throws IOException {
        Object raw = value.get(key);
        if (!(raw instanceof Number number)
                || number.doubleValue() != Math.rint(number.doubleValue())) {
            throw new IOException("malformed " + what);
        }
        return number.longValue();
    }

    private static String mediaType(HttpResponse<?> response) {
        return response.headers().firstValue("Content-Type")
                .orElse("")
                .split(";", 2)[0]
                .trim()
                .toLowerCase(Locale.ROOT);
    }

    private static String decodeUtf8(byte[] bytes, String what) throws IOException {
        if (bytes.length > MAX_RESPONSE_BYTES) {
            throw new IOException("oversized " + what);
        }
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

    private HttpRequest.Builder request(String target) {
        return HttpRequest.newBuilder(baseUri.resolve(target))
                .timeout(requestTimeout)
                .header("Accept", "application/json");
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
            List<Object> result = new ArrayList<>();
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
            StringBuilder out = new StringBuilder();
            while (true) {
                if (index >= text.length()) {
                    throw new IOException("malformed JSON response");
                }
                char symbol = text.charAt(index++);
                if (symbol == '"') {
                    return out.toString();
                }
                if (symbol != '\\') {
                    if (symbol < 0x20) {
                        throw new IOException("malformed JSON response");
                    }
                    out.append(symbol);
                    continue;
                }
                if (index >= text.length()) {
                    throw new IOException("malformed JSON response");
                }
                char escape = text.charAt(index++);
                switch (escape) {
                    case '"' -> out.append('"');
                    case '\\' -> out.append('\\');
                    case '/' -> out.append('/');
                    case 'b' -> out.append('\b');
                    case 'f' -> out.append('\f');
                    case 'n' -> out.append('\n');
                    case 'r' -> out.append('\r');
                    case 't' -> out.append('\t');
                    case 'u' -> {
                        if (index + 4 > text.length()) {
                            throw new IOException("malformed JSON response");
                        }
                        String digits = text.substring(index, index + 4);
                        index += 4;
                        try {
                            out.append((char) Integer.parseInt(digits, 16));
                        } catch (NumberFormatException error) {
                            throw new IOException("malformed JSON response");
                        }
                    }
                    default -> throw new IOException("malformed JSON response");
                }
            }
        }

        private Object number() throws IOException {
            int start = index;
            while (index < text.length() && "+-.eE0123456789".indexOf(text.charAt(index)) >= 0) {
                index++;
            }
            String literal = text.substring(start, index);
            try {
                if (literal.contains(".") || literal.contains("e") || literal.contains("E")) {
                    return Double.parseDouble(literal);
                }
                return Long.parseLong(literal);
            } catch (NumberFormatException error) {
                throw new IOException("malformed JSON response");
            }
        }

        private Object literal(String word, Object value) throws IOException {
            if (!text.startsWith(word, index)) {
                throw new IOException("malformed JSON response");
            }
            index += word.length();
            return value;
        }

        private void space() {
            while (index < text.length() && Character.isWhitespace(text.charAt(index))) {
                index++;
            }
        }

        private boolean take(char symbol) {
            if (index < text.length() && text.charAt(index) == symbol) {
                index++;
                return true;
            }
            return false;
        }

        private void expect(char symbol) throws IOException {
            if (!take(symbol)) {
                throw new IOException("malformed JSON response");
            }
        }
    }
}
