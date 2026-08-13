import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Minimal dependency-free JSON reader/escaper used by the protected test harness. */
final class Json {

    private final String text;
    private int cursor;

    private Json(String text) {
        this.text = text;
    }

    static Object parse(String text) {
        Json parser = new Json(text);
        parser.skipWhitespace();
        Object value = parser.readValue();
        parser.skipWhitespace();
        if (parser.cursor != text.length()) {
            throw new IllegalArgumentException("trailing content at offset " + parser.cursor);
        }
        return value;
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> asObject(Object value) {
        if (!(value instanceof Map)) {
            throw new IllegalArgumentException("expected a JSON object but found " + value);
        }
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    static List<Object> asArray(Object value) {
        if (!(value instanceof List)) {
            throw new IllegalArgumentException("expected a JSON array but found " + value);
        }
        return (List<Object>) value;
    }

    /** Renders {@code value} as a JSON string literal. */
    static String string(String value) {
        StringBuilder out = new StringBuilder("\"");
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            switch (character) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                default -> {
                    if (character < 0x20) {
                        out.append(String.format("\\u%04x", (int) character));
                    } else {
                        out.append(character);
                    }
                }
            }
        }
        return out.append('"').toString();
    }

    // ---------------------------------------------------------------- parsing

    private Object readValue() {
        char character = peek();
        return switch (character) {
            case '{' -> readObject();
            case '[' -> readArray();
            case '"' -> readString();
            case 't', 'f' -> readBoolean();
            case 'n' -> readNull();
            default -> readNumber();
        };
    }

    private Map<String, Object> readObject() {
        Map<String, Object> members = new LinkedHashMap<>();
        expect('{');
        skipWhitespace();
        if (peek() == '}') {
            cursor++;
            return members;
        }
        while (true) {
            skipWhitespace();
            String key = readString();
            skipWhitespace();
            expect(':');
            skipWhitespace();
            members.put(key, readValue());
            skipWhitespace();
            char next = text.charAt(cursor++);
            if (next == '}') {
                return members;
            }
            if (next != ',') {
                throw new IllegalArgumentException("expected ',' or '}' at offset " + (cursor - 1));
            }
        }
    }

    private List<Object> readArray() {
        List<Object> elements = new ArrayList<>();
        expect('[');
        skipWhitespace();
        if (peek() == ']') {
            cursor++;
            return elements;
        }
        while (true) {
            skipWhitespace();
            elements.add(readValue());
            skipWhitespace();
            char next = text.charAt(cursor++);
            if (next == ']') {
                return elements;
            }
            if (next != ',') {
                throw new IllegalArgumentException("expected ',' or ']' at offset " + (cursor - 1));
            }
        }
    }

    private String readString() {
        expect('"');
        StringBuilder out = new StringBuilder();
        while (true) {
            char character = text.charAt(cursor++);
            if (character == '"') {
                return out.toString();
            }
            if (character != '\\') {
                out.append(character);
                continue;
            }
            char escape = text.charAt(cursor++);
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
                    out.append((char) Integer.parseInt(text.substring(cursor, cursor + 4), 16));
                    cursor += 4;
                }
                default -> throw new IllegalArgumentException("bad escape at offset " + (cursor - 1));
            }
        }
    }

    private Boolean readBoolean() {
        if (text.startsWith("true", cursor)) {
            cursor += 4;
            return Boolean.TRUE;
        }
        if (text.startsWith("false", cursor)) {
            cursor += 5;
            return Boolean.FALSE;
        }
        throw new IllegalArgumentException("bad literal at offset " + cursor);
    }

    private Object readNull() {
        if (!text.startsWith("null", cursor)) {
            throw new IllegalArgumentException("bad literal at offset " + cursor);
        }
        cursor += 4;
        return null;
    }

    private Number readNumber() {
        int start = cursor;
        while (cursor < text.length() && "+-0123456789.eE".indexOf(text.charAt(cursor)) >= 0) {
            cursor++;
        }
        String literal = text.substring(start, cursor);
        if (literal.contains(".") || literal.contains("e") || literal.contains("E")) {
            return Double.valueOf(literal);
        }
        return Long.valueOf(literal);
    }

    private char peek() {
        if (cursor >= text.length()) {
            throw new IllegalArgumentException("unexpected end of input");
        }
        return text.charAt(cursor);
    }

    private void expect(char expected) {
        if (peek() != expected) {
            throw new IllegalArgumentException("expected '" + expected + "' at offset " + cursor);
        }
        cursor++;
    }

    private void skipWhitespace() {
        while (cursor < text.length() && Character.isWhitespace(text.charAt(cursor))) {
            cursor++;
        }
    }
}
