import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal dependency-free JSON reader/writer shared by the mock, the harness and the client.
 *
 * Objects parse to LinkedHashMap&lt;String,Object&gt; (insertion order preserved, which is what the
 * wire verifier inspects), arrays to ArrayList&lt;Object&gt;, strings to String, integral numbers to
 * Long, fractional numbers to Double, and true/false/null to Boolean/null.
 */
public final class Json {

    private Json() {
    }

    /* ------------------------------------------------------------------ reading */

    public static Object parse(String text) {
        Parser p = new Parser(text);
        p.skipWs();
        Object value = p.value();
        p.skipWs();
        if (p.pos < p.src.length()) {
            throw new IllegalArgumentException("trailing content at offset " + p.pos);
        }
        return value;
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> object(Object value) {
        if (!(value instanceof Map)) {
            throw new IllegalArgumentException("expected a JSON object but got " + describe(value));
        }
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    public static List<Object> array(Object value) {
        if (!(value instanceof List)) {
            throw new IllegalArgumentException("expected a JSON array but got " + describe(value));
        }
        return (List<Object>) value;
    }

    /** Reads a string member, or null when absent or not a string. */
    public static String str(Map<String, Object> object, String field) {
        Object value = object.get(field);
        return (value instanceof String) ? (String) value : null;
    }

    /** Reads an object member, or null when absent. */
    public static Map<String, Object> obj(Map<String, Object> object, String field) {
        Object value = object.get(field);
        return (value instanceof Map) ? object(value) : null;
    }

    /** Reads an array member, or an empty list when absent. */
    public static List<Object> arr(Map<String, Object> object, String field) {
        Object value = object.get(field);
        return (value instanceof List) ? array(value) : new ArrayList<>();
    }

    public static String describe(Object value) {
        if (value == null) {
            return "null";
        }
        if (value instanceof Map) {
            return "object";
        }
        if (value instanceof List) {
            return "array";
        }
        return value.getClass().getSimpleName().toLowerCase();
    }

    /* ------------------------------------------------------------------ writing */

    public static String write(Object value) {
        StringBuilder out = new StringBuilder();
        writeValue(value, out);
        return out.toString();
    }

    private static void writeValue(Object value, StringBuilder out) {
        if (value == null) {
            out.append("null");
        } else if (value instanceof String) {
            writeString((String) value, out);
        } else if (value instanceof Boolean || value instanceof Long || value instanceof Integer) {
            out.append(value);
        } else if (value instanceof Double || value instanceof Float) {
            double d = ((Number) value).doubleValue();
            if (d == Math.rint(d) && !Double.isInfinite(d)) {
                out.append((long) d);
            } else {
                out.append(d);
            }
        } else if (value instanceof Map) {
            out.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                if (!first) {
                    out.append(',');
                }
                first = false;
                writeString(String.valueOf(entry.getKey()), out);
                out.append(':');
                writeValue(entry.getValue(), out);
            }
            out.append('}');
        } else if (value instanceof Iterable) {
            out.append('[');
            boolean first = true;
            for (Object element : (Iterable<?>) value) {
                if (!first) {
                    out.append(',');
                }
                first = false;
                writeValue(element, out);
            }
            out.append(']');
        } else {
            throw new IllegalArgumentException("cannot serialize " + value.getClass());
        }
    }

    private static void writeString(String s, StringBuilder out) {
        out.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                case '\b' -> out.append("\\b");
                case '\f' -> out.append("\\f");
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
    }

    /* ------------------------------------------------------------------ helpers */

    /** Builds a LinkedHashMap from alternating key/value arguments. */
    public static Map<String, Object> map(Object... keyValuePairs) {
        if (keyValuePairs.length % 2 != 0) {
            throw new IllegalArgumentException("expected alternating key/value arguments");
        }
        Map<String, Object> result = new LinkedHashMap<>();
        for (int i = 0; i < keyValuePairs.length; i += 2) {
            result.put(String.valueOf(keyValuePairs[i]), keyValuePairs[i + 1]);
        }
        return result;
    }

    public static List<Object> list(Object... elements) {
        List<Object> result = new ArrayList<>();
        for (Object element : elements) {
            result.add(element);
        }
        return result;
    }

    /* ------------------------------------------------------------------ parser */

    private static final class Parser {
        private final String src;
        private int pos;

        Parser(String src) {
            this.src = src;
        }

        void skipWs() {
            while (pos < src.length() && Character.isWhitespace(src.charAt(pos))) {
                pos++;
            }
        }

        Object value() {
            skipWs();
            if (pos >= src.length()) {
                throw new IllegalArgumentException("unexpected end of input");
            }
            char c = src.charAt(pos);
            switch (c) {
                case '{':
                    return objectValue();
                case '[':
                    return arrayValue();
                case '"':
                    return stringValue();
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
                    return numberValue();
            }
        }

        private void expect(String literal) {
            if (!src.startsWith(literal, pos)) {
                throw new IllegalArgumentException("expected " + literal + " at offset " + pos);
            }
            pos += literal.length();
        }

        private Map<String, Object> objectValue() {
            Map<String, Object> result = new LinkedHashMap<>();
            pos++; // {
            skipWs();
            if (pos < src.length() && src.charAt(pos) == '}') {
                pos++;
                return result;
            }
            while (true) {
                skipWs();
                String key = stringValue();
                skipWs();
                if (pos >= src.length() || src.charAt(pos) != ':') {
                    throw new IllegalArgumentException("expected ':' at offset " + pos);
                }
                pos++;
                result.put(key, value());
                skipWs();
                if (pos >= src.length()) {
                    throw new IllegalArgumentException("unterminated object");
                }
                char c = src.charAt(pos++);
                if (c == '}') {
                    return result;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("expected ',' or '}' at offset " + (pos - 1));
                }
            }
        }

        private List<Object> arrayValue() {
            List<Object> result = new ArrayList<>();
            pos++; // [
            skipWs();
            if (pos < src.length() && src.charAt(pos) == ']') {
                pos++;
                return result;
            }
            while (true) {
                result.add(value());
                skipWs();
                if (pos >= src.length()) {
                    throw new IllegalArgumentException("unterminated array");
                }
                char c = src.charAt(pos++);
                if (c == ']') {
                    return result;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("expected ',' or ']' at offset " + (pos - 1));
                }
            }
        }

        private String stringValue() {
            if (pos >= src.length() || src.charAt(pos) != '"') {
                throw new IllegalArgumentException("expected a string at offset " + pos);
            }
            pos++;
            StringBuilder out = new StringBuilder();
            while (true) {
                if (pos >= src.length()) {
                    throw new IllegalArgumentException("unterminated string");
                }
                char c = src.charAt(pos++);
                if (c == '"') {
                    return out.toString();
                }
                if (c != '\\') {
                    out.append(c);
                    continue;
                }
                char esc = src.charAt(pos++);
                switch (esc) {
                    case '"' -> out.append('"');
                    case '\\' -> out.append('\\');
                    case '/' -> out.append('/');
                    case 'b' -> out.append('\b');
                    case 'f' -> out.append('\f');
                    case 'n' -> out.append('\n');
                    case 'r' -> out.append('\r');
                    case 't' -> out.append('\t');
                    case 'u' -> {
                        out.append((char) Integer.parseInt(src.substring(pos, pos + 4), 16));
                        pos += 4;
                    }
                    default -> throw new IllegalArgumentException("bad escape \\" + esc);
                }
            }
        }

        private Object numberValue() {
            int start = pos;
            while (pos < src.length() && "+-0123456789.eE".indexOf(src.charAt(pos)) >= 0) {
                pos++;
            }
            String token = src.substring(start, pos);
            if (token.isEmpty()) {
                throw new IllegalArgumentException("unexpected character '" + src.charAt(pos) + "' at offset " + pos);
            }
            if (token.indexOf('.') < 0 && token.indexOf('e') < 0 && token.indexOf('E') < 0) {
                return Long.parseLong(token);
            }
            return Double.parseDouble(token);
        }
    }
}
