import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A very small JSON reader/writer, sufficient for the SDDC Manager payloads used here.
 *
 * <p>Parsed values are {@link LinkedHashMap}, {@link ArrayList}, {@link String}, {@link Long},
 * {@link Double}, {@link Boolean} or {@code null}. The writer is deliberately literal: it emits
 * exactly the members present in the map, in insertion order, and it emits {@code null} for a null
 * value rather than dropping the member. Deciding what belongs on the wire is the caller's job.
 */
public final class Json {

    private Json() {
    }

    // ---------------------------------------------------------------- parsing

    public static Object parse(String text) {
        Parser parser = new Parser(text);
        parser.skipWhitespace();
        Object value = parser.readValue();
        parser.skipWhitespace();
        if (parser.index != text.length()) {
            throw new IllegalArgumentException("trailing content at offset " + parser.index);
        }
        return value;
    }

    private static final class Parser {
        private final String src;
        private int index;

        Parser(String src) {
            this.src = src;
        }

        void skipWhitespace() {
            while (index < src.length() && Character.isWhitespace(src.charAt(index))) {
                index++;
            }
        }

        Object readValue() {
            skipWhitespace();
            if (index >= src.length()) {
                throw new IllegalArgumentException("unexpected end of input");
            }
            char c = src.charAt(index);
            switch (c) {
                case '{':
                    return readObject();
                case '[':
                    return readArray();
                case '"':
                    return readString();
                case 't':
                    expect("true");
                    return Boolean.TRUE;
                case 'f':
                    expect("false");
                    return Boolean.FALSE;
                case 'n':
                    expect("null");
                    return null;
                default:
                    return readNumber();
            }
        }

        private void expect(String literal) {
            if (!src.startsWith(literal, index)) {
                throw new IllegalArgumentException("expected " + literal + " at offset " + index);
            }
            index += literal.length();
        }

        private Map<String, Object> readObject() {
            Map<String, Object> map = new LinkedHashMap<>();
            index++; // '{'
            skipWhitespace();
            if (index < src.length() && src.charAt(index) == '}') {
                index++;
                return map;
            }
            while (true) {
                skipWhitespace();
                String key = readString();
                skipWhitespace();
                if (index >= src.length() || src.charAt(index) != ':') {
                    throw new IllegalArgumentException("expected ':' at offset " + index);
                }
                index++;
                map.put(key, readValue());
                skipWhitespace();
                if (index >= src.length()) {
                    throw new IllegalArgumentException("unterminated object");
                }
                char c = src.charAt(index++);
                if (c == '}') {
                    return map;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("expected ',' or '}' at offset " + (index - 1));
                }
            }
        }

        private List<Object> readArray() {
            List<Object> list = new ArrayList<>();
            index++; // '['
            skipWhitespace();
            if (index < src.length() && src.charAt(index) == ']') {
                index++;
                return list;
            }
            while (true) {
                list.add(readValue());
                skipWhitespace();
                if (index >= src.length()) {
                    throw new IllegalArgumentException("unterminated array");
                }
                char c = src.charAt(index++);
                if (c == ']') {
                    return list;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("expected ',' or ']' at offset " + (index - 1));
                }
            }
        }

        private String readString() {
            if (index >= src.length() || src.charAt(index) != '"') {
                throw new IllegalArgumentException("expected string at offset " + index);
            }
            index++;
            StringBuilder out = new StringBuilder();
            while (true) {
                if (index >= src.length()) {
                    throw new IllegalArgumentException("unterminated string");
                }
                char c = src.charAt(index++);
                if (c == '"') {
                    return out.toString();
                }
                if (c != '\\') {
                    out.append(c);
                    continue;
                }
                char esc = src.charAt(index++);
                switch (esc) {
                    case '"': out.append('"'); break;
                    case '\\': out.append('\\'); break;
                    case '/': out.append('/'); break;
                    case 'b': out.append('\b'); break;
                    case 'f': out.append('\f'); break;
                    case 'n': out.append('\n'); break;
                    case 'r': out.append('\r'); break;
                    case 't': out.append('\t'); break;
                    case 'u':
                        out.append((char) Integer.parseInt(src.substring(index, index + 4), 16));
                        index += 4;
                        break;
                    default:
                        throw new IllegalArgumentException("bad escape \\" + esc);
                }
            }
        }

        private Object readNumber() {
            int start = index;
            while (index < src.length() && "+-.eE0123456789".indexOf(src.charAt(index)) >= 0) {
                index++;
            }
            String literal = src.substring(start, index);
            if (literal.isEmpty()) {
                throw new IllegalArgumentException("expected value at offset " + start);
            }
            if (literal.indexOf('.') < 0 && literal.indexOf('e') < 0 && literal.indexOf('E') < 0) {
                return Long.valueOf(literal);
            }
            return Double.valueOf(literal);
        }
    }

    // ---------------------------------------------------------------- writing

    public static String write(Object value) {
        StringBuilder out = new StringBuilder();
        writeValue(value, out);
        return out.toString();
    }

    private static void writeValue(Object value, StringBuilder out) {
        if (value == null) {
            out.append("null");
        } else if (value instanceof Map<?, ?> map) {
            out.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                if (!first) {
                    out.append(',');
                }
                first = false;
                writeString(String.valueOf(entry.getKey()), out);
                out.append(':');
                writeValue(entry.getValue(), out);
            }
            out.append('}');
        } else if (value instanceof Iterable<?> iterable) {
            out.append('[');
            boolean first = true;
            for (Object element : iterable) {
                if (!first) {
                    out.append(',');
                }
                first = false;
                writeValue(element, out);
            }
            out.append(']');
        } else if (value instanceof String text) {
            writeString(text, out);
        } else if (value instanceof Number || value instanceof Boolean) {
            out.append(value);
        } else {
            writeString(String.valueOf(value), out);
        }
    }

    private static void writeString(String text, StringBuilder out) {
        out.append('"');
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            switch (c) {
                case '"': out.append("\\\""); break;
                case '\\': out.append("\\\\"); break;
                case '\b': out.append("\\b"); break;
                case '\f': out.append("\\f"); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                default:
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
            }
        }
        out.append('"');
    }

    // ------------------------------------------------------------- accessors

    @SuppressWarnings("unchecked")
    public static Map<String, Object> asObject(Object value) {
        if (!(value instanceof Map)) {
            throw new IllegalArgumentException("expected a JSON object but found " + describe(value));
        }
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    public static List<Object> asArray(Object value) {
        if (!(value instanceof List)) {
            throw new IllegalArgumentException("expected a JSON array but found " + describe(value));
        }
        return (List<Object>) value;
    }

    public static String asString(Object value) {
        if (!(value instanceof String)) {
            throw new IllegalArgumentException("expected a JSON string but found " + describe(value));
        }
        return (String) value;
    }

    /** Returns {@code root.get(key)} for an object, or {@code null} when absent. */
    public static Object get(Object root, String key) {
        return root instanceof Map<?, ?> map ? map.get(key) : null;
    }

    /** Returns a string member, or {@code null} when the member is absent or not a string. */
    public static String getString(Object root, String key) {
        Object value = get(root, key);
        return value instanceof String text ? text : null;
    }

    /** Builds a {@link LinkedHashMap} from alternating key/value arguments. */
    public static Map<String, Object> object(Object... keyValuePairs) {
        if (keyValuePairs.length % 2 != 0) {
            throw new IllegalArgumentException("expected an even number of arguments");
        }
        Map<String, Object> map = new LinkedHashMap<>();
        for (int i = 0; i < keyValuePairs.length; i += 2) {
            map.put(String.valueOf(keyValuePairs[i]), keyValuePairs[i + 1]);
        }
        return map;
    }

    public static String describe(Object value) {
        if (value == null) {
            return "null";
        }
        if (value instanceof Map) {
            return "an object";
        }
        if (value instanceof List) {
            return "an array";
        }
        if (value instanceof String) {
            return "a string";
        }
        return value.getClass().getSimpleName().toLowerCase();
    }
}
