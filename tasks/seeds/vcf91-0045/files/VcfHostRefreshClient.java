import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Minimal Java 17 client for the VCF 9.1 SDDC Manager operations projected in
 * docs/contract.json.
 */
public final class VcfHostRefreshClient {
    private final URI baseUri;
    private final String accessToken;
    private final Duration requestTimeout;
    private final HttpClient http;

    public record RefreshRequest(List<String> hostIds, Boolean forceRefresh) {
    }

    public record Task(
            String id,
            String name,
            String status,
            String creationTimestamp,
            String completionTimestamp) {
    }

    public record Host(String id, String fqdn, String status) {
    }

    public record RefreshResult(Task task, List<Host> hosts) {
        public RefreshResult {
            Objects.requireNonNull(task, "task");
            hosts = List.copyOf(Objects.requireNonNull(hosts, "hosts"));
        }
    }

    public VcfHostRefreshClient(
            URI baseUri,
            String accessToken,
            Duration requestTimeout) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.accessToken = Objects.requireNonNull(accessToken, "accessToken");
        this.requestTimeout = Objects.requireNonNull(requestTimeout, "requestTimeout");
        this.http = HttpClient.newBuilder()
                .connectTimeout(requestTimeout)
                .build();
    }

    /**
     * Refreshes the selected host records, waits for the accepted Task to
     * terminate successfully, then returns a stable host collection view.
     */
    public RefreshResult refreshHostsAndWait(
            RefreshRequest request,
            int maxPolls,
            Duration pollInterval) throws IOException, InterruptedException {
        // TODO: implement updateHosts, terminal getTask polling, and sorted getHosts.
        throw new UnsupportedOperationException("Not implemented");
    }

    /**
     * Provided dependency-free JSON reader for the focused response models.
     */
    private static Object parseJson(String text) throws IOException {
        return new JsonReader(text).parse();
    }

    private static final class JsonReader {
        private final String text;
        private int index;

        private JsonReader(String text) {
            this.text = Objects.requireNonNull(text, "text");
        }

        private Object parse() throws IOException {
            Object value = value();
            whitespace();
            if (index != text.length()) {
                throw malformed();
            }
            return value;
        }

        private Object value() throws IOException {
            whitespace();
            if (index >= text.length()) {
                throw malformed();
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
            expect('{');
            LinkedHashMap<String, Object> result = new LinkedHashMap<>();
            whitespace();
            if (take('}')) {
                return result;
            }
            while (true) {
                whitespace();
                if (index >= text.length() || text.charAt(index) != '"') {
                    throw malformed();
                }
                String key = string();
                whitespace();
                expect(':');
                result.put(key, value());
                whitespace();
                if (take('}')) {
                    return result;
                }
                expect(',');
            }
        }

        private List<Object> array() throws IOException {
            expect('[');
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
                if (current < 0x20) {
                    throw malformed();
                }
                if (current != '\\') {
                    result.append(current);
                    continue;
                }
                if (index >= text.length()) {
                    throw malformed();
                }
                char escape = text.charAt(index++);
                switch (escape) {
                    case '"' -> result.append('"');
                    case '\\' -> result.append('\\');
                    case '/' -> result.append('/');
                    case 'b' -> result.append('\b');
                    case 'f' -> result.append('\f');
                    case 'n' -> result.append('\n');
                    case 'r' -> result.append('\r');
                    case 't' -> result.append('\t');
                    case 'u' -> result.append(unicode());
                    default -> throw malformed();
                }
            }
            throw malformed();
        }

        private char unicode() throws IOException {
            if (index + 4 > text.length()) {
                throw malformed();
            }
            int value = 0;
            for (int count = 0; count < 4; count++) {
                int digit = Character.digit(text.charAt(index++), 16);
                if (digit < 0) {
                    throw malformed();
                }
                value = value * 16 + digit;
            }
            return (char) value;
        }

        private Object number() throws IOException {
            int start = index;
            if (take('-') && index >= text.length()) {
                throw malformed();
            }
            if (take('0')) {
                // A leading zero is complete unless a fraction or exponent follows.
            } else {
                digits();
            }
            if (take('.')) {
                digits();
            }
            if (take('e') || take('E')) {
                take('+');
                take('-');
                digits();
            }
            if (start == index) {
                throw malformed();
            }
            String token = text.substring(start, index);
            try {
                if (token.indexOf('.') >= 0
                        || token.indexOf('e') >= 0
                        || token.indexOf('E') >= 0) {
                    return Double.valueOf(token);
                }
                return Long.valueOf(token);
            } catch (NumberFormatException invalid) {
                throw malformed();
            }
        }

        private void digits() throws IOException {
            int start = index;
            while (index < text.length()
                    && Character.isDigit(text.charAt(index))) {
                index++;
            }
            if (start == index) {
                throw malformed();
            }
        }

        private Object literal(String token, Object value) throws IOException {
            if (!text.startsWith(token, index)) {
                throw malformed();
            }
            index += token.length();
            return value;
        }

        private void whitespace() {
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

        private boolean take(char expected) {
            if (index < text.length() && text.charAt(index) == expected) {
                index++;
                return true;
            }
            return false;
        }

        private void expect(char expected) throws IOException {
            if (!take(expected)) {
                throw malformed();
            }
        }

        private IOException malformed() {
            return new IOException("Malformed JSON at character " + index);
        }
    }
}
