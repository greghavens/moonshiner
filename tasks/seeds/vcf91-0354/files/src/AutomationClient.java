import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/** Minimal VCF Automation client for collecting failed-request evidence. */
public final class AutomationClient {
    private final URI baseUri;
    private final String bearerToken;
    private final HttpClient httpClient;

    public AutomationClient(URI baseUri, String bearerToken) {
        this(baseUri, bearerToken, HttpClient.newHttpClient());
    }

    AutomationClient(URI baseUri, String bearerToken, HttpClient httpClient) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.bearerToken = Objects.requireNonNull(bearerToken, "bearerToken");
        this.httpClient = Objects.requireNonNull(httpClient, "httpClient");
    }

    public FailureDiagnosis diagnoseLatestFailure(String deploymentId)
            throws IOException, InterruptedException {
        Objects.requireNonNull(deploymentId, "deploymentId");

        // Implement request lookup -> event lookup -> relevant log retrieval.
        throw new UnsupportedOperationException("TODO");
    }

    public record FailureDiagnosis(
            String requestId,
            String requestName,
            String requestDetails,
            List<EventEvidence> events) {
        public FailureDiagnosis {
            events = List.copyOf(events);
        }
    }

    public record EventEvidence(
            String eventId,
            String name,
            String details,
            boolean hasLogs,
            List<LogLine> logs) {
        public EventEvidence {
            logs = List.copyOf(logs);
        }
    }

    public record LogLine(long rowNumber, String timestamp, String message) {
    }

    /** Small JSON reader kept inside this source file to avoid external dependencies. */
    private static final class Json {
        static Object read(String source) throws IOException {
            Parser parser = new Parser(source);
            Object value = parser.value();
            parser.whitespace();
            if (!parser.atEnd()) {
                throw parser.error("unexpected trailing content");
            }
            return value;
        }

        private static final class Parser {
            private final String source;
            private int position;

            Parser(String source) {
                this.source = Objects.requireNonNull(source, "source");
            }

            Object value() throws IOException {
                whitespace();
                if (atEnd()) {
                    throw error("expected a JSON value");
                }
                return switch (source.charAt(position)) {
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
                position++;
                LinkedHashMap<String, Object> result = new LinkedHashMap<>();
                whitespace();
                if (consume('}')) {
                    return result;
                }
                while (true) {
                    whitespace();
                    if (atEnd() || source.charAt(position) != '"') {
                        throw error("expected a JSON object key");
                    }
                    String key = string();
                    whitespace();
                    expect(':');
                    result.put(key, value());
                    whitespace();
                    if (consume('}')) {
                        return result;
                    }
                    expect(',');
                }
            }

            private List<Object> array() throws IOException {
                position++;
                ArrayList<Object> result = new ArrayList<>();
                whitespace();
                if (consume(']')) {
                    return result;
                }
                while (true) {
                    result.add(value());
                    whitespace();
                    if (consume(']')) {
                        return result;
                    }
                    expect(',');
                }
            }

            private String string() throws IOException {
                expect('"');
                StringBuilder result = new StringBuilder();
                while (!atEnd()) {
                    char character = source.charAt(position++);
                    if (character == '"') {
                        return result.toString();
                    }
                    if (character == '\\') {
                        if (atEnd()) {
                            throw error("unterminated JSON escape");
                        }
                        char escaped = source.charAt(position++);
                        switch (escaped) {
                            case '"', '\\', '/' -> result.append(escaped);
                            case 'b' -> result.append('\b');
                            case 'f' -> result.append('\f');
                            case 'n' -> result.append('\n');
                            case 'r' -> result.append('\r');
                            case 't' -> result.append('\t');
                            case 'u' -> result.append(unicodeEscape());
                            default -> throw error("invalid JSON escape");
                        }
                    } else {
                        if (character < 0x20) {
                            throw error("unescaped control character in string");
                        }
                        result.append(character);
                    }
                }
                throw error("unterminated JSON string");
            }

            private char unicodeEscape() throws IOException {
                if (position + 4 > source.length()) {
                    throw error("incomplete unicode escape");
                }
                try {
                    char value = (char) Integer.parseInt(source.substring(position, position + 4), 16);
                    position += 4;
                    return value;
                } catch (NumberFormatException exception) {
                    throw error("invalid unicode escape");
                }
            }

            private Object number() throws IOException {
                int start = position;
                if (consume('-') && atEnd()) {
                    throw error("incomplete number");
                }
                if (consume('0')) {
                    // Leading zero completes the integer part.
                } else {
                    digits();
                }
                if (consume('.')) {
                    digits();
                }
                if (!atEnd() && (source.charAt(position) == 'e' || source.charAt(position) == 'E')) {
                    position++;
                    if (!atEnd() && (source.charAt(position) == '+' || source.charAt(position) == '-')) {
                        position++;
                    }
                    digits();
                }
                String token = source.substring(start, position);
                try {
                    return token.contains(".") || token.contains("e") || token.contains("E")
                            ? Double.parseDouble(token)
                            : Long.parseLong(token);
                } catch (NumberFormatException exception) {
                    throw error("invalid JSON number");
                }
            }

            private void digits() throws IOException {
                int start = position;
                while (!atEnd() && Character.isDigit(source.charAt(position))) {
                    position++;
                }
                if (start == position) {
                    throw error("expected digit");
                }
            }

            private Object literal(String expected, Object value) throws IOException {
                if (!source.startsWith(expected, position)) {
                    throw error("invalid JSON literal");
                }
                position += expected.length();
                return value;
            }

            void whitespace() {
                while (!atEnd() && Character.isWhitespace(source.charAt(position))) {
                    position++;
                }
            }

            private void expect(char expected) throws IOException {
                if (!consume(expected)) {
                    throw error("expected '" + expected + "'");
                }
            }

            private boolean consume(char expected) {
                if (!atEnd() && source.charAt(position) == expected) {
                    position++;
                    return true;
                }
                return false;
            }

            boolean atEnd() {
                return position >= source.length();
            }

            IOException error(String message) {
                return new IOException(message + " at JSON offset " + position);
            }
        }
    }
}
