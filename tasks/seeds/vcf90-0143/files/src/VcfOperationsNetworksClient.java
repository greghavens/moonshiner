import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class VcfOperationsNetworksClient {
    public record VmSnapshot(String entityId, String entityType, long time) {}

    private record Page(List<VmSnapshot> results, String cursor) {}

    private final URI baseUri;
    private final String token;
    private final HttpClient httpClient;

    public VcfOperationsNetworksClient(URI baseUri, String token) {
        this.baseUri = baseUri;
        this.token = token;
        this.httpClient = HttpClient.newHttpClient();
    }

    public List<VmSnapshot> listAllVms(Integer size, Long startTime, Long endTime)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("listAllVms is not implemented");
    }

    private static Page parsePage(String body) throws IOException {
        Object parsed = new JsonParser(body).parse();
        if (!(parsed instanceof Map<?, ?> root)) {
            throw new IOException("Expected a JSON object response");
        }

        Object rawResults = root.get("results");
        if (!(rawResults instanceof List<?> resultValues)) {
            throw new IOException("Response is missing results array");
        }

        List<VmSnapshot> results = new ArrayList<>();
        for (Object value : resultValues) {
            if (!(value instanceof Map<?, ?> item)) {
                throw new IOException("Result entry is not an object");
            }
            Object entityId = item.get("entity_id");
            Object entityType = item.get("entity_type");
            Object time = item.get("time");
            if (!(entityId instanceof String id)
                    || !(entityType instanceof String type)
                    || !(time instanceof Number number)) {
                throw new IOException("Result entry does not match EntityIdWithTime");
            }
            results.add(new VmSnapshot(id, type, number.longValue()));
        }

        Object cursorValue = root.get("cursor");
        if (cursorValue != null && !(cursorValue instanceof String)) {
            throw new IOException("Response cursor is not a string");
        }
        return new Page(results, (String) cursorValue);
    }

    private static final class JsonParser {
        private final String input;
        private int position;

        private JsonParser(String input) {
            this.input = input;
        }

        private Object parse() throws IOException {
            Object value = readValue();
            skipWhitespace();
            if (position != input.length()) {
                throw error("Trailing data");
            }
            return value;
        }

        private Object readValue() throws IOException {
            skipWhitespace();
            if (position >= input.length()) {
                throw error("Unexpected end of input");
            }
            return switch (input.charAt(position)) {
                case '{' -> readObject();
                case '[' -> readArray();
                case '"' -> readString();
                case 't' -> readLiteral("true", Boolean.TRUE);
                case 'f' -> readLiteral("false", Boolean.FALSE);
                case 'n' -> readLiteral("null", null);
                default -> readNumber();
            };
        }

        private Map<String, Object> readObject() throws IOException {
            expect('{');
            Map<String, Object> result = new LinkedHashMap<>();
            skipWhitespace();
            if (consume('}')) {
                return result;
            }
            while (true) {
                skipWhitespace();
                if (position >= input.length() || input.charAt(position) != '"') {
                    throw error("Expected object key");
                }
                String key = readString();
                skipWhitespace();
                expect(':');
                result.put(key, readValue());
                skipWhitespace();
                if (consume('}')) {
                    return result;
                }
                expect(',');
            }
        }

        private List<Object> readArray() throws IOException {
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

        private String readString() throws IOException {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (position < input.length()) {
                char ch = input.charAt(position++);
                if (ch == '"') {
                    return result.toString();
                }
                if (ch != '\\') {
                    if (ch < 0x20) {
                        throw error("Control character in string");
                    }
                    result.append(ch);
                    continue;
                }
                if (position >= input.length()) {
                    throw error("Unterminated escape");
                }
                char escaped = input.charAt(position++);
                switch (escaped) {
                    case '"', '\\', '/' -> result.append(escaped);
                    case 'b' -> result.append('\b');
                    case 'f' -> result.append('\f');
                    case 'n' -> result.append('\n');
                    case 'r' -> result.append('\r');
                    case 't' -> result.append('\t');
                    case 'u' -> result.append(readUnicodeEscape());
                    default -> throw error("Invalid escape");
                }
            }
            throw error("Unterminated string");
        }

        private char readUnicodeEscape() throws IOException {
            if (position + 4 > input.length()) {
                throw error("Short unicode escape");
            }
            int value = 0;
            for (int i = 0; i < 4; i++) {
                int digit = Character.digit(input.charAt(position++), 16);
                if (digit < 0) {
                    throw error("Invalid unicode escape");
                }
                value = (value << 4) | digit;
            }
            return (char) value;
        }

        private Object readLiteral(String text, Object value) throws IOException {
            if (!input.startsWith(text, position)) {
                throw error("Invalid literal");
            }
            position += text.length();
            return value;
        }

        private Number readNumber() throws IOException {
            int start = position;
            if (consume('-')) {
                // Sign consumed.
            }
            if (position >= input.length() || !Character.isDigit(input.charAt(position))) {
                throw error("Invalid number");
            }
            while (position < input.length() && Character.isDigit(input.charAt(position))) {
                position++;
            }
            boolean decimal = false;
            if (consume('.')) {
                decimal = true;
                while (position < input.length() && Character.isDigit(input.charAt(position))) {
                    position++;
                }
            }
            if (position < input.length()
                    && (input.charAt(position) == 'e' || input.charAt(position) == 'E')) {
                decimal = true;
                position++;
                if (position < input.length()
                        && (input.charAt(position) == '+' || input.charAt(position) == '-')) {
                    position++;
                }
                while (position < input.length() && Character.isDigit(input.charAt(position))) {
                    position++;
                }
            }
            String number = input.substring(start, position);
            try {
                if (decimal) {
                    return Double.valueOf(number);
                }
                return Long.valueOf(number);
            } catch (NumberFormatException exception) {
                throw error("Invalid number");
            }
        }

        private void skipWhitespace() {
            while (position < input.length() && Character.isWhitespace(input.charAt(position))) {
                position++;
            }
        }

        private boolean consume(char expected) {
            if (position < input.length() && input.charAt(position) == expected) {
                position++;
                return true;
            }
            return false;
        }

        private void expect(char expected) throws IOException {
            if (!consume(expected)) {
                throw error("Expected '" + expected + "'");
            }
        }

        private IOException error(String message) {
            return new IOException(message + " at JSON offset " + position);
        }
    }
}
