import java.io.IOException;
import java.math.BigDecimal;
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

/**
 * Minimal JDK-only client for Vcenter.Tagging.Categories_list.
 *
 * <p>The supplied base URI denotes the Automation API root, for example
 * {@code https://vcenter.example/api} or {@code https://vcenter.example/api/}.
 */
public final class VCenterCategoryClient {
    private static final String LIST_PATH = "vcenter/tagging/categories";

    private final URI apiBase;
    private final String sessionId;
    private final HttpClient http;

    public VCenterCategoryClient(URI apiBase, String sessionId) {
        this(apiBase, sessionId, HttpClient.newHttpClient());
    }

    public VCenterCategoryClient(URI apiBase, String sessionId, HttpClient http) {
        Objects.requireNonNull(apiBase, "apiBase");
        this.sessionId = Objects.requireNonNull(sessionId, "sessionId");
        this.http = Objects.requireNonNull(http, "http");
        String value = apiBase.toString();
        this.apiBase = URI.create(value.endsWith("/") ? value : value + "/");
    }

    /**
     * Fetches categories and returns them in their stable export order.
     */
    public List<Category> listAllCategories() throws IOException, InterruptedException {
        // BUG: an empty iteration marker is sent and only the first page is consumed.
        Page firstPage = requestPage("");
        return List.copyOf(firstPage.items());
    }

    /**
     * Writes one JSON object per line. Keys always appear in the documented order:
     * category_id, name, description, cardinality, associable_types, used_by.
     */
    public void writeAllCategories(Appendable output) throws IOException, InterruptedException {
        Objects.requireNonNull(output, "output");
        for (Category category : listAllCategories()) {
            output.append("{\"category_id\":");
            appendJsonString(output, category.categoryId());
            output.append(",\"name\":");
            appendJsonString(output, category.name());
            output.append(",\"description\":");
            appendJsonString(output, category.description());
            output.append(",\"cardinality\":");
            appendJsonString(output, category.cardinality());
            output.append(",\"associable_types\":");
            appendStringArray(output, category.associableTypes());
            output.append(",\"used_by\":");
            appendStringArray(output, category.usedBy());
            output.append("}\n");
        }
    }

    private Page requestPage(String marker) throws IOException, InterruptedException {
        // BUG: unset optional fields are serialized as empty query members.
        String query = "?names=&marker="
                + URLEncoder.encode(marker, StandardCharsets.UTF_8)
                + "&page_size=";
        URI uri = apiBase.resolve(LIST_PATH + query);
        HttpRequest request = HttpRequest.newBuilder(uri)
                .header("Accept", "application/json")
                .header("vmware-api-session-id", sessionId)
                .GET()
                .build();

        HttpResponse<String> response = http.send(
                request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new IOException("category list failed with HTTP " + response.statusCode());
        }
        return parsePage(response.body());
    }

    private static Page parsePage(String json) throws IOException {
        try {
            Map<String, Object> root = object(Json.parse(json), "response");
            if (!root.containsKey("items")) {
                throw new IOException("response is missing required items");
            }
            List<Object> rawItems = array(root.get("items"), "items");
            List<Category> items = new ArrayList<>(rawItems.size());
            for (int index = 0; index < rawItems.size(); index++) {
                Map<String, Object> item = object(rawItems.get(index), "items[" + index + "]");
                String id = string(item.get("category_id"), "category_id");
                Map<String, Object> info = object(item.get("info"), "info");
                items.add(new Category(
                        id,
                        string(info.get("name"), "name"),
                        string(info.get("description"), "description"),
                        string(info.get("cardinality"), "cardinality"),
                        strings(info.get("associable_types"), "associable_types"),
                        strings(info.get("used_by"), "used_by")));
            }

            Object rawMarker = root.get("marker");
            String marker = rawMarker == null ? null : string(rawMarker, "marker");
            return new Page(marker, List.copyOf(items));
        } catch (Json.SyntaxException | ClassCastException exception) {
            throw new IOException("malformed category list response", exception);
        }
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String field) throws IOException {
        if (!(value instanceof Map<?, ?>)) {
            throw new IOException(field + " must be an object");
        }
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> array(Object value, String field) throws IOException {
        if (!(value instanceof List<?>)) {
            throw new IOException(field + " must be an array");
        }
        return (List<Object>) value;
    }

    private static String string(Object value, String field) throws IOException {
        if (!(value instanceof String text)) {
            throw new IOException(field + " must be a string");
        }
        return text;
    }

    private static List<String> strings(Object value, String field) throws IOException {
        List<Object> raw = array(value, field);
        List<String> result = new ArrayList<>(raw.size());
        for (Object entry : raw) {
            result.add(string(entry, field + " item"));
        }
        return List.copyOf(result);
    }

    private static void appendStringArray(Appendable output, List<String> values)
            throws IOException {
        output.append('[');
        for (int index = 0; index < values.size(); index++) {
            if (index > 0) {
                output.append(',');
            }
            appendJsonString(output, values.get(index));
        }
        output.append(']');
    }

    private static void appendJsonString(Appendable output, String value) throws IOException {
        output.append('"');
        for (int index = 0; index < value.length(); index++) {
            char current = value.charAt(index);
            switch (current) {
                case '"' -> output.append("\\\"");
                case '\\' -> output.append("\\\\");
                case '\b' -> output.append("\\b");
                case '\f' -> output.append("\\f");
                case '\n' -> output.append("\\n");
                case '\r' -> output.append("\\r");
                case '\t' -> output.append("\\t");
                default -> {
                    if (current < 0x20) {
                        output.append(String.format("\\u%04x", (int) current));
                    } else {
                        output.append(current);
                    }
                }
            }
        }
        output.append('"');
    }

    public record Category(
            String categoryId,
            String name,
            String description,
            String cardinality,
            List<String> associableTypes,
            List<String> usedBy) {
        public Category {
            Objects.requireNonNull(categoryId, "categoryId");
            Objects.requireNonNull(name, "name");
            Objects.requireNonNull(description, "description");
            Objects.requireNonNull(cardinality, "cardinality");
            associableTypes = List.copyOf(associableTypes);
            usedBy = List.copyOf(usedBy);
        }
    }

    private record Page(String marker, List<Category> items) {
    }

    /**
     * Small strict JSON reader kept inside the client so the exercise has no
     * dependency or build-tool requirement.
     */
    private static final class Json {
        static Object parse(String text) {
            Parser parser = new Parser(text);
            Object value = parser.readValue();
            parser.skipWhitespace();
            if (!parser.atEnd()) {
                throw parser.error("trailing data");
            }
            return value;
        }

        private static final class Parser {
            private final String text;
            private int offset;

            Parser(String text) {
                this.text = Objects.requireNonNull(text, "text");
            }

            Object readValue() {
                skipWhitespace();
                if (atEnd()) {
                    throw error("expected a value");
                }
                return switch (text.charAt(offset)) {
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
                expect('{');
                Map<String, Object> result = new LinkedHashMap<>();
                skipWhitespace();
                if (consume('}')) {
                    return result;
                }
                while (true) {
                    skipWhitespace();
                    if (atEnd() || text.charAt(offset) != '"') {
                        throw error("expected an object key");
                    }
                    String key = readString();
                    skipWhitespace();
                    expect(':');
                    if (result.containsKey(key)) {
                        throw error("duplicate object key " + key);
                    }
                    result.put(key, readValue());
                    skipWhitespace();
                    if (consume('}')) {
                        return result;
                    }
                    expect(',');
                }
            }

            private List<Object> readArray() {
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

            private String readString() {
                expect('"');
                StringBuilder result = new StringBuilder();
                while (!atEnd()) {
                    char current = text.charAt(offset++);
                    if (current == '"') {
                        return result.toString();
                    }
                    if (current == '\\') {
                        if (atEnd()) {
                            throw error("unterminated escape");
                        }
                        char escaped = text.charAt(offset++);
                        switch (escaped) {
                            case '"', '\\', '/' -> result.append(escaped);
                            case 'b' -> result.append('\b');
                            case 'f' -> result.append('\f');
                            case 'n' -> result.append('\n');
                            case 'r' -> result.append('\r');
                            case 't' -> result.append('\t');
                            case 'u' -> result.append(readUnicode());
                            default -> throw error("invalid escape");
                        }
                    } else {
                        if (current < 0x20) {
                            throw error("unescaped control character");
                        }
                        result.append(current);
                    }
                }
                throw error("unterminated string");
            }

            private char readUnicode() {
                if (offset + 4 > text.length()) {
                    throw error("short unicode escape");
                }
                int value = 0;
                for (int index = 0; index < 4; index++) {
                    int digit = Character.digit(text.charAt(offset++), 16);
                    if (digit < 0) {
                        throw error("invalid unicode escape");
                    }
                    value = value * 16 + digit;
                }
                return (char) value;
            }

            private Object readNumber() {
                int start = offset;
                if (consume('-')) {
                    // Sign consumed.
                }
                readDigits();
                if (consume('.')) {
                    readDigits();
                }
                if (consume('e') || consume('E')) {
                    consume('+');
                    consume('-');
                    readDigits();
                }
                if (start == offset) {
                    throw error("expected a value");
                }
                try {
                    return new BigDecimal(text.substring(start, offset));
                } catch (NumberFormatException exception) {
                    throw error("invalid number");
                }
            }

            private void readDigits() {
                int start = offset;
                while (!atEnd() && Character.isDigit(text.charAt(offset))) {
                    offset++;
                }
                if (start == offset) {
                    throw error("expected a digit");
                }
            }

            private Object readLiteral(String literal, Object value) {
                if (!text.startsWith(literal, offset)) {
                    throw error("invalid literal");
                }
                offset += literal.length();
                return value;
            }

            void skipWhitespace() {
                while (!atEnd()) {
                    char current = text.charAt(offset);
                    if (current != ' ' && current != '\n' && current != '\r' && current != '\t') {
                        return;
                    }
                    offset++;
                }
            }

            private void expect(char expected) {
                if (!consume(expected)) {
                    throw error("expected '" + expected + "'");
                }
            }

            private boolean consume(char expected) {
                if (!atEnd() && text.charAt(offset) == expected) {
                    offset++;
                    return true;
                }
                return false;
            }

            boolean atEnd() {
                return offset >= text.length();
            }

            SyntaxException error(String message) {
                return new SyntaxException(message + " at offset " + offset);
            }
        }

        private static final class SyntaxException extends RuntimeException {
            SyntaxException(String message) {
                super(message);
            }
        }
    }
}
