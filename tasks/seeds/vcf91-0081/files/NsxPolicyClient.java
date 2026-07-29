import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
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
 * Small, dependency-free client for the NSX Policy operation in
 * docs/contract.json.
 */
public final class NsxPolicyClient {
    private static final String LIST_SEGMENTS_PATH =
            "/policy/api/v1/infra/segments";

    public record ListOptions(
            Integer pageSize,
            String segmentType,
            Boolean includeMarkedForDeleteObjects,
            String includedFields) {

        public static ListOptions unset() {
            return new ListOptions(null, null, null, null);
        }
    }

    public record Segment(String id, String displayName, String path) {}

    public static final class RepeatedCursorException extends IOException {
        public RepeatedCursorException(String cursor) {
            super("NSX Policy repeated pagination cursor: " + cursor);
        }
    }

    private record Page(List<Segment> results, String cursor) {}

    private final String baseUrl;
    private final Duration requestTimeout;
    private final HttpClient httpClient;
    private final String authorization;

    public NsxPolicyClient(
            URI baseUri,
            String username,
            String password,
            Duration requestTimeout) {
        Objects.requireNonNull(baseUri, "baseUri");
        Objects.requireNonNull(username, "username");
        Objects.requireNonNull(password, "password");
        this.requestTimeout =
                Objects.requireNonNull(requestTimeout, "requestTimeout");

        String scheme = baseUri.getScheme();
        if (!baseUri.isAbsolute()
                || baseUri.getHost() == null
                || (!"http".equalsIgnoreCase(scheme)
                        && !"https".equalsIgnoreCase(scheme))) {
            throw new IllegalArgumentException(
                    "baseUri must be an absolute HTTP(S) URI");
        }
        if (baseUri.getRawQuery() != null || baseUri.getRawFragment() != null) {
            throw new IllegalArgumentException(
                    "baseUri must not contain a query or fragment");
        }
        if (requestTimeout.isZero() || requestTimeout.isNegative()) {
            throw new IllegalArgumentException("requestTimeout must be positive");
        }

        this.baseUrl = stripTrailingSlashes(baseUri.toString());
        this.authorization =
                "Basic "
                        + Base64.getEncoder()
                                .encodeToString(
                                        (username + ":" + password)
                                                .getBytes(StandardCharsets.UTF_8));
        this.httpClient =
                HttpClient.newBuilder()
                        .connectTimeout(requestTimeout)
                        .build();
    }

    /**
     * Retrieve every page and return a deterministic segment inventory.
     */
    public List<Segment> listAllSegments(ListOptions options)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }

    private Page listPage(String rawQuery)
            throws IOException, InterruptedException {
        String target =
                baseUrl
                        + LIST_SEGMENTS_PATH
                        + (rawQuery.isEmpty() ? "" : "?" + rawQuery);
        final URI uri;
        try {
            uri = URI.create(target);
        } catch (IllegalArgumentException error) {
            throw new IOException("invalid NSX Policy list URI", error);
        }

        HttpRequest request =
                HttpRequest.newBuilder(uri)
                        .timeout(requestTimeout)
                        .header("Accept", "application/json")
                        .header("Authorization", authorization)
                        .GET()
                        .build();
        HttpResponse<String> response =
                httpClient.send(
                        request,
                        HttpResponse.BodyHandlers.ofString(
                                StandardCharsets.UTF_8));
        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            String body = response.body();
            if (body.length() > 512) {
                body = body.substring(0, 512);
            }
            throw new IOException(
                    "ListAllInfraSegments returned HTTP "
                            + response.statusCode()
                            + ": "
                            + body.strip());
        }
        return decodePage(response.body());
    }

    private static Page decodePage(String body) throws IOException {
        Object root = Json.parse(body);
        if (!(root instanceof Map<?, ?> object)) {
            throw new IOException("segment page must be a JSON object");
        }
        Object rawResults = object.get("results");
        if (!(rawResults instanceof List<?> resultValues)) {
            throw new IOException(
                    "segment page is missing the required results array");
        }

        List<Segment> results = new ArrayList<>(resultValues.size());
        for (Object value : resultValues) {
            if (!(value instanceof Map<?, ?> segment)) {
                throw new IOException("segment result must be a JSON object");
            }
            results.add(
                    new Segment(
                            optionalString(segment, "id"),
                            optionalString(segment, "display_name"),
                            optionalString(segment, "path")));
        }
        return new Page(results, optionalString(object, "cursor"));
    }

    private static String optionalString(Map<?, ?> object, String name)
            throws IOException {
        Object value = object.get(name);
        if (value == null) {
            return "";
        }
        if (value instanceof String text) {
            return text;
        }
        throw new IOException("JSON property " + name + " must be a string");
    }

    private static String queryParameter(String name, Object value) {
        return name
                + "="
                + URLEncoder.encode(
                        String.valueOf(value), StandardCharsets.UTF_8);
    }

    private static String stripTrailingSlashes(String value) {
        int end = value.length();
        while (end > 0 && value.charAt(end - 1) == '/') {
            end--;
        }
        return value.substring(0, end);
    }

    /**
     * Deliberately small JSON reader used to keep this exercise dependency-free.
     */
    private static final class Json {
        static Object parse(String text) throws IOException {
            return new Parser(text).parseDocument();
        }

        private static final class Parser {
            private final String text;
            private int index;

            Parser(String text) {
                this.text = Objects.requireNonNull(text, "text");
            }

            Object parseDocument() throws IOException {
                Object value = parseValue();
                skipWhitespace();
                if (index != text.length()) {
                    throw error("unexpected trailing content");
                }
                return value;
            }

            private Object parseValue() throws IOException {
                skipWhitespace();
                if (index >= text.length()) {
                    throw error("unexpected end of JSON");
                }
                return switch (text.charAt(index)) {
                    case '{' -> parseObject();
                    case '[' -> parseArray();
                    case '"' -> parseString();
                    case 't' -> parseLiteral("true", Boolean.TRUE);
                    case 'f' -> parseLiteral("false", Boolean.FALSE);
                    case 'n' -> parseLiteral("null", null);
                    default -> parseNumber();
                };
            }

            private Map<String, Object> parseObject() throws IOException {
                expect('{');
                Map<String, Object> value = new LinkedHashMap<>();
                skipWhitespace();
                if (take('}')) {
                    return value;
                }
                while (true) {
                    skipWhitespace();
                    if (index >= text.length()
                            || text.charAt(index) != '"') {
                        throw error("object key must be a string");
                    }
                    String key = parseString();
                    skipWhitespace();
                    expect(':');
                    value.put(key, parseValue());
                    skipWhitespace();
                    if (take('}')) {
                        return value;
                    }
                    expect(',');
                }
            }

            private List<Object> parseArray() throws IOException {
                expect('[');
                List<Object> value = new ArrayList<>();
                skipWhitespace();
                if (take(']')) {
                    return value;
                }
                while (true) {
                    value.add(parseValue());
                    skipWhitespace();
                    if (take(']')) {
                        return value;
                    }
                    expect(',');
                }
            }

            private String parseString() throws IOException {
                expect('"');
                StringBuilder value = new StringBuilder();
                while (index < text.length()) {
                    char current = text.charAt(index++);
                    if (current == '"') {
                        return value.toString();
                    }
                    if (current == '\\') {
                        if (index >= text.length()) {
                            throw error("unterminated escape");
                        }
                        char escaped = text.charAt(index++);
                        switch (escaped) {
                            case '"' -> value.append('"');
                            case '\\' -> value.append('\\');
                            case '/' -> value.append('/');
                            case 'b' -> value.append('\b');
                            case 'f' -> value.append('\f');
                            case 'n' -> value.append('\n');
                            case 'r' -> value.append('\r');
                            case 't' -> value.append('\t');
                            case 'u' -> value.append(parseUnicodeEscape());
                            default -> throw error("invalid string escape");
                        }
                    } else {
                        if (current < 0x20) {
                            throw error("unescaped control character");
                        }
                        value.append(current);
                    }
                }
                throw error("unterminated string");
            }

            private char parseUnicodeEscape() throws IOException {
                if (index + 4 > text.length()) {
                    throw error("short unicode escape");
                }
                String digits = text.substring(index, index + 4);
                index += 4;
                try {
                    return (char) Integer.parseInt(digits, 16);
                } catch (NumberFormatException error) {
                    throw error("invalid unicode escape");
                }
            }

            private Object parseNumber() throws IOException {
                int start = index;
                if (take('-') && index >= text.length()) {
                    throw error("invalid number");
                }
                takeDigits();
                boolean decimal = false;
                if (take('.')) {
                    decimal = true;
                    if (!takeDigits()) {
                        throw error("invalid fraction");
                    }
                }
                if (take('e') || take('E')) {
                    decimal = true;
                    take('+');
                    take('-');
                    if (!takeDigits()) {
                        throw error("invalid exponent");
                    }
                }
                if (start == index) {
                    throw error("expected JSON value");
                }
                String number = text.substring(start, index);
                try {
                    return decimal
                            ? Double.valueOf(number)
                            : Long.valueOf(number);
                } catch (NumberFormatException error) {
                    throw error("invalid number");
                }
            }

            private boolean takeDigits() {
                int start = index;
                while (index < text.length()
                        && Character.isDigit(text.charAt(index))) {
                    index++;
                }
                return index > start;
            }

            private Object parseLiteral(String literal, Object value)
                    throws IOException {
                if (!text.startsWith(literal, index)) {
                    throw error("invalid literal");
                }
                index += literal.length();
                return value;
            }

            private void skipWhitespace() {
                while (index < text.length()) {
                    char current = text.charAt(index);
                    if (current != ' '
                            && current != '\n'
                            && current != '\r'
                            && current != '\t') {
                        return;
                    }
                    index++;
                }
            }

            private void expect(char expected) throws IOException {
                if (!take(expected)) {
                    throw error("expected '" + expected + "'");
                }
            }

            private boolean take(char expected) {
                if (index < text.length()
                        && text.charAt(index) == expected) {
                    index++;
                    return true;
                }
                return false;
            }

            private IOException error(String message) {
                return new IOException(
                        "malformed JSON at character "
                                + index
                                + ": "
                                + message);
            }
        }
    }
}
