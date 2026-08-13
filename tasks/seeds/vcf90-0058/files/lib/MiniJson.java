import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A dependency-free JSON reader/writer good enough for this project.
 *
 * parse() maps JSON onto: LinkedHashMap&lt;String,Object&gt;, ArrayList&lt;Object&gt;,
 * String, Long (integral numbers), Double (fractional numbers), Boolean, null.
 *
 * write() is deliberately literal: it emits exactly the entries present in the
 * map you hand it, in insertion order. A key mapped to null is emitted as
 * {@code "key":null} -- it is not dropped. If a field should not appear on the
 * wire, do not put it in the map.
 */
public final class MiniJson {

    private MiniJson() {
    }

    public static Map<String, Object> obj() {
        return new LinkedHashMap<>();
    }

    public static List<Object> arr() {
        return new ArrayList<>();
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> asObject(Object v) {
        if (!(v instanceof Map)) {
            throw new IllegalArgumentException("expected a JSON object, got " + describe(v));
        }
        return (Map<String, Object>) v;
    }

    @SuppressWarnings("unchecked")
    public static List<Object> asArray(Object v) {
        if (!(v instanceof List)) {
            throw new IllegalArgumentException("expected a JSON array, got " + describe(v));
        }
        return (List<Object>) v;
    }

    public static String asString(Object v) {
        if (!(v instanceof String)) {
            throw new IllegalArgumentException("expected a JSON string, got " + describe(v));
        }
        return (String) v;
    }

    public static long asLong(Object v) {
        if (v instanceof Long l) {
            return l;
        }
        if (v instanceof Double d && d == Math.floor(d) && !d.isInfinite()) {
            return d.longValue();
        }
        throw new IllegalArgumentException("expected an integral JSON number, got " + describe(v));
    }

    private static String describe(Object v) {
        return v == null ? "null" : v.getClass().getSimpleName();
    }

    // ---------------------------------------------------------------- writing

    public static String write(Object value) {
        StringBuilder sb = new StringBuilder();
        writeValue(sb, value);
        return sb.toString();
    }

    public static String writeIndented(Object value) {
        StringBuilder sb = new StringBuilder();
        writeIndented(sb, value, 0);
        return sb.toString();
    }

    private static void writeValue(StringBuilder sb, Object v) {
        if (v == null) {
            sb.append("null");
        } else if (v instanceof String s) {
            writeString(sb, s);
        } else if (v instanceof Boolean || v instanceof Long || v instanceof Integer) {
            sb.append(v);
        } else if (v instanceof Double d) {
            if (d == Math.floor(d) && !d.isInfinite()) {
                sb.append(d.longValue());
            } else {
                sb.append(d);
            }
        } else if (v instanceof Number n) {
            sb.append(n);
        } else if (v instanceof Map<?, ?> m) {
            sb.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> e : m.entrySet()) {
                if (!first) {
                    sb.append(',');
                }
                first = false;
                writeString(sb, String.valueOf(e.getKey()));
                sb.append(':');
                writeValue(sb, e.getValue());
            }
            sb.append('}');
        } else if (v instanceof Iterable<?> it) {
            sb.append('[');
            boolean first = true;
            for (Object e : it) {
                if (!first) {
                    sb.append(',');
                }
                first = false;
                writeValue(sb, e);
            }
            sb.append(']');
        } else {
            throw new IllegalArgumentException("cannot serialise " + v.getClass().getName());
        }
    }

    private static void writeIndented(StringBuilder sb, Object v, int depth) {
        String pad = "  ".repeat(depth + 1);
        String closePad = "  ".repeat(depth);
        if (v instanceof Map<?, ?> m) {
            if (m.isEmpty()) {
                sb.append("{}");
                return;
            }
            sb.append("{\n");
            boolean first = true;
            for (Map.Entry<?, ?> e : m.entrySet()) {
                if (!first) {
                    sb.append(",\n");
                }
                first = false;
                sb.append(pad);
                writeString(sb, String.valueOf(e.getKey()));
                sb.append(": ");
                writeIndented(sb, e.getValue(), depth + 1);
            }
            sb.append('\n').append(closePad).append('}');
        } else if (v instanceof Iterable<?> it) {
            List<Object> items = new ArrayList<>();
            it.forEach(items::add);
            if (items.isEmpty()) {
                sb.append("[]");
                return;
            }
            sb.append("[\n");
            for (int i = 0; i < items.size(); i++) {
                if (i > 0) {
                    sb.append(",\n");
                }
                sb.append(pad);
                writeIndented(sb, items.get(i), depth + 1);
            }
            sb.append('\n').append(closePad).append(']');
        } else {
            writeValue(sb, v);
        }
    }

    private static void writeString(StringBuilder sb, String s) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"' -> sb.append("\\\"");
                case '\\' -> sb.append("\\\\");
                case '\n' -> sb.append("\\n");
                case '\r' -> sb.append("\\r");
                case '\t' -> sb.append("\\t");
                case '\b' -> sb.append("\\b");
                case '\f' -> sb.append("\\f");
                default -> {
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
                }
            }
        }
        sb.append('"');
    }

    // ---------------------------------------------------------------- parsing

    public static Object parse(String text) {
        Parser p = new Parser(text);
        p.skipWhitespace();
        Object v = p.readValue();
        p.skipWhitespace();
        if (!p.atEnd()) {
            throw new IllegalArgumentException("trailing content at offset " + p.pos);
        }
        return v;
    }

    private static final class Parser {
        private final String s;
        private int pos;

        Parser(String s) {
            this.s = s;
        }

        boolean atEnd() {
            return pos >= s.length();
        }

        void skipWhitespace() {
            while (pos < s.length() && Character.isWhitespace(s.charAt(pos))) {
                pos++;
            }
        }

        private char peek() {
            if (pos >= s.length()) {
                throw new IllegalArgumentException("unexpected end of JSON input");
            }
            return s.charAt(pos);
        }

        private void expect(char c) {
            if (peek() != c) {
                throw new IllegalArgumentException("expected '" + c + "' at offset " + pos + " but found '" + peek() + "'");
            }
            pos++;
        }

        Object readValue() {
            skipWhitespace();
            char c = peek();
            return switch (c) {
                case '{' -> readObject();
                case '[' -> readArray();
                case '"' -> readString();
                case 't', 'f' -> readBoolean();
                case 'n' -> readNull();
                default -> readNumber();
            };
        }

        private Map<String, Object> readObject() {
            expect('{');
            Map<String, Object> m = new LinkedHashMap<>();
            skipWhitespace();
            if (peek() == '}') {
                pos++;
                return m;
            }
            while (true) {
                skipWhitespace();
                String key = readString();
                skipWhitespace();
                expect(':');
                m.put(key, readValue());
                skipWhitespace();
                char c = peek();
                if (c == ',') {
                    pos++;
                } else if (c == '}') {
                    pos++;
                    return m;
                } else {
                    throw new IllegalArgumentException("expected ',' or '}' at offset " + pos);
                }
            }
        }

        private List<Object> readArray() {
            expect('[');
            List<Object> list = new ArrayList<>();
            skipWhitespace();
            if (peek() == ']') {
                pos++;
                return list;
            }
            while (true) {
                list.add(readValue());
                skipWhitespace();
                char c = peek();
                if (c == ',') {
                    pos++;
                } else if (c == ']') {
                    pos++;
                    return list;
                } else {
                    throw new IllegalArgumentException("expected ',' or ']' at offset " + pos);
                }
            }
        }

        private String readString() {
            expect('"');
            StringBuilder sb = new StringBuilder();
            while (true) {
                char c = peek();
                pos++;
                if (c == '"') {
                    return sb.toString();
                }
                if (c != '\\') {
                    sb.append(c);
                    continue;
                }
                char esc = peek();
                pos++;
                switch (esc) {
                    case '"' -> sb.append('"');
                    case '\\' -> sb.append('\\');
                    case '/' -> sb.append('/');
                    case 'b' -> sb.append('\b');
                    case 'f' -> sb.append('\f');
                    case 'n' -> sb.append('\n');
                    case 'r' -> sb.append('\r');
                    case 't' -> sb.append('\t');
                    case 'u' -> {
                        sb.append((char) Integer.parseInt(s.substring(pos, pos + 4), 16));
                        pos += 4;
                    }
                    default -> throw new IllegalArgumentException("bad escape '\\" + esc + "' at offset " + pos);
                }
            }
        }

        private Boolean readBoolean() {
            if (s.startsWith("true", pos)) {
                pos += 4;
                return Boolean.TRUE;
            }
            if (s.startsWith("false", pos)) {
                pos += 5;
                return Boolean.FALSE;
            }
            throw new IllegalArgumentException("bad literal at offset " + pos);
        }

        private Object readNull() {
            if (s.startsWith("null", pos)) {
                pos += 4;
                return null;
            }
            throw new IllegalArgumentException("bad literal at offset " + pos);
        }

        private Object readNumber() {
            int start = pos;
            if (pos < s.length() && (s.charAt(pos) == '-' || s.charAt(pos) == '+')) {
                pos++;
            }
            boolean fractional = false;
            while (pos < s.length()) {
                char c = s.charAt(pos);
                if (c >= '0' && c <= '9') {
                    pos++;
                } else if (c == '.' || c == 'e' || c == 'E' || c == '+' || c == '-') {
                    fractional = true;
                    pos++;
                } else {
                    break;
                }
            }
            String raw = s.substring(start, pos);
            if (raw.isEmpty()) {
                throw new IllegalArgumentException("expected a number at offset " + start);
            }
            return fractional ? (Object) Double.valueOf(raw) : (Object) Long.valueOf(raw);
        }
    }
}
