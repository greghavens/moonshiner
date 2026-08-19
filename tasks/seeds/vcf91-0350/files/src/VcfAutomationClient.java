import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

public final class VcfAutomationClient {
    private static final String API_VERSION = "2021-07-15";

    private final URI baseUri;
    private final String bearerToken;
    private final HttpClient http;

    public VcfAutomationClient(URI baseUri, String bearerToken) {
        String base = Objects.requireNonNull(baseUri, "baseUri").toString();
        this.baseUri = URI.create(base.endsWith("/") ? base : base + "/");
        this.bearerToken = Objects.requireNonNull(bearerToken, "bearerToken");
        this.http = HttpClient.newHttpClient();
    }

    public String ensureProjectIntegration(String projectName, String integrationName)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO: implement the VCF Automation client flow");
    }

    private HttpResponse<String> send(String method, String relativeTarget, String jsonBody)
            throws IOException, InterruptedException {
        HttpRequest.Builder request = HttpRequest.newBuilder(baseUri.resolve(relativeTarget))
                .header("Authorization", "Bearer " + bearerToken)
                .header("Accept", "application/json");
        if (jsonBody == null) {
            request.method(method, HttpRequest.BodyPublishers.noBody());
        } else {
            request.header("Content-Type", "application/json")
                    .method(method, HttpRequest.BodyPublishers.ofString(jsonBody, StandardCharsets.UTF_8));
        }
        return http.send(request.build(), HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
    }

    private static String queryEncode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
    }

    private static String pathEncode(String value) {
        return queryEncode(value).replace("+", "%20");
    }

    private static IOException httpError(String operation, HttpResponse<String> response) {
        return new IOException(operation + " failed with HTTP " + response.statusCode());
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String label) throws IOException {
        if (!(value instanceof Map<?, ?>)) {
            throw new IOException(label + " must be a JSON object");
        }
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> array(Object value, String label) throws IOException {
        if (!(value instanceof List<?>)) {
            throw new IOException(label + " must be a JSON array");
        }
        return (List<Object>) value;
    }

    private static String string(Object value, String label) throws IOException {
        if (!(value instanceof String result) || result.isEmpty()) {
            throw new IOException(label + " must be a non-empty JSON string");
        }
        return result;
    }

    /* Small dependency-free JSON codec used by the single-file client. */
    private static final class Json {
        static Object parse(String source) throws IOException {
            Parser parser = new Parser(source);
            Object value = parser.value();
            parser.space();
            if (!parser.end()) {
                throw parser.error("trailing data");
            }
            return value;
        }

        static String write(Object value) {
            StringBuilder out = new StringBuilder();
            append(out, value);
            return out.toString();
        }

        private static void append(StringBuilder out, Object value) {
            if (value == null) {
                out.append("null");
            } else if (value instanceof String text) {
                out.append('"');
                for (int i = 0; i < text.length(); i++) {
                    char c = text.charAt(i);
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
            } else if (value instanceof Boolean || value instanceof Number) {
                out.append(value);
            } else if (value instanceof Map<?, ?> map) {
                out.append('{');
                boolean first = true;
                for (Map.Entry<?, ?> entry : map.entrySet()) {
                    if (!first) out.append(',');
                    first = false;
                    append(out, String.valueOf(entry.getKey()));
                    out.append(':');
                    append(out, entry.getValue());
                }
                out.append('}');
            } else if (value instanceof Iterable<?> values) {
                out.append('[');
                boolean first = true;
                for (Object item : values) {
                    if (!first) out.append(',');
                    first = false;
                    append(out, item);
                }
                out.append(']');
            } else {
                throw new IllegalArgumentException("unsupported JSON value: " + value.getClass());
            }
        }

        private static final class Parser {
            private final String source;
            private int at;

            Parser(String source) {
                this.source = source;
            }

            boolean end() {
                return at == source.length();
            }

            void space() {
                while (!end() && Character.isWhitespace(source.charAt(at))) at++;
            }

            IOException error(String message) {
                return new IOException("invalid JSON at character " + at + ": " + message);
            }

            Object value() throws IOException {
                space();
                if (end()) throw error("expected value");
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

            Object literal(String text, Object value) throws IOException {
                if (!source.startsWith(text, at)) throw error("expected " + text);
                at += text.length();
                return value;
            }

            Map<String, Object> object() throws IOException {
                LinkedHashMap<String, Object> result = new LinkedHashMap<>();
                at++;
                space();
                if (!end() && source.charAt(at) == '}') {
                    at++;
                    return result;
                }
                while (true) {
                    space();
                    if (end() || source.charAt(at) != '"') throw error("expected object key");
                    String key = string();
                    space();
                    if (end() || source.charAt(at++) != ':') throw error("expected ':'");
                    result.put(key, value());
                    space();
                    if (end()) throw error("unterminated object");
                    char delimiter = source.charAt(at++);
                    if (delimiter == '}') return result;
                    if (delimiter != ',') throw error("expected ',' or '}'");
                }
            }

            List<Object> array() throws IOException {
                ArrayList<Object> result = new ArrayList<>();
                at++;
                space();
                if (!end() && source.charAt(at) == ']') {
                    at++;
                    return result;
                }
                while (true) {
                    result.add(value());
                    space();
                    if (end()) throw error("unterminated array");
                    char delimiter = source.charAt(at++);
                    if (delimiter == ']') return result;
                    if (delimiter != ',') throw error("expected ',' or ']'");
                }
            }

            String string() throws IOException {
                if (source.charAt(at++) != '"') throw error("expected string");
                StringBuilder result = new StringBuilder();
                while (!end()) {
                    char c = source.charAt(at++);
                    if (c == '"') return result.toString();
                    if (c != '\\') {
                        if (c < 0x20) throw error("control character in string");
                        result.append(c);
                        continue;
                    }
                    if (end()) throw error("unterminated escape");
                    char escaped = source.charAt(at++);
                    switch (escaped) {
                        case '"', '\\', '/' -> result.append(escaped);
                        case 'b' -> result.append('\b');
                        case 'f' -> result.append('\f');
                        case 'n' -> result.append('\n');
                        case 'r' -> result.append('\r');
                        case 't' -> result.append('\t');
                        case 'u' -> {
                            if (at + 4 > source.length()) throw error("short unicode escape");
                            try {
                                result.append((char) Integer.parseInt(source.substring(at, at + 4), 16));
                            } catch (NumberFormatException e) {
                                throw error("bad unicode escape");
                            }
                            at += 4;
                        }
                        default -> throw error("bad escape");
                    }
                }
                throw error("unterminated string");
            }

            Number number() throws IOException {
                int start = at;
                if (!end() && source.charAt(at) == '-') at++;
                while (!end() && Character.isDigit(source.charAt(at))) at++;
                if (!end() && source.charAt(at) == '.') {
                    at++;
                    while (!end() && Character.isDigit(source.charAt(at))) at++;
                }
                if (!end() && (source.charAt(at) == 'e' || source.charAt(at) == 'E')) {
                    at++;
                    if (!end() && (source.charAt(at) == '+' || source.charAt(at) == '-')) at++;
                    while (!end() && Character.isDigit(source.charAt(at))) at++;
                }
                if (start == at) throw error("expected value");
                String token = source.substring(start, at);
                try {
                    return token.indexOf('.') >= 0 || token.indexOf('e') >= 0 || token.indexOf('E') >= 0
                            ? Double.valueOf(token) : Long.valueOf(token);
                } catch (NumberFormatException e) {
                    throw error("bad number");
                }
            }
        }
    }
}
