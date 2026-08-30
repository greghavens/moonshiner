import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal dependency-free JSON reader/writer used by the mock server, the test driver and the
 * verifier. Objects are parsed into {@link LinkedHashMap} so that key insertion order (the order the
 * client put bytes on the wire) is preserved, and so that "absent key" can be distinguished from
 * "key present with a JSON null value".
 *
 * <p>Harness file. Do not modify.
 */
final class MiniJson {

    private MiniJson() {
    }

    // ---------------------------------------------------------------- parsing

    static Object parse(String text) {
        Parser p = new Parser(text);
        p.skipWhitespace();
        Object value = p.readValue();
        p.skipWhitespace();
        if (p.pos != text.length()) {
            throw new IllegalArgumentException("trailing content at offset " + p.pos);
        }
        return value;
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> parseObject(String text) {
        Object value = parse(text);
        if (!(value instanceof Map)) {
            throw new IllegalArgumentException("expected a JSON object, got " + typeName(value));
        }
        return (Map<String, Object>) value;
    }

    private static final class Parser {
        private final String s;
        private int pos;

        Parser(String s) {
            this.s = s;
        }

        void skipWhitespace() {
            while (pos < s.length()) {
                char c = s.charAt(pos);
                if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
                    pos++;
                } else {
                    break;
                }
            }
        }

        Object readValue() {
            if (pos >= s.length()) {
                throw new IllegalArgumentException("unexpected end of input");
            }
            char c = s.charAt(pos);
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
            if (!s.startsWith(literal, pos)) {
                throw new IllegalArgumentException("expected " + literal + " at offset " + pos);
            }
            pos += literal.length();
        }

        private Map<String, Object> readObject() {
            Map<String, Object> map = new LinkedHashMap<>();
            pos++; // '{'
            skipWhitespace();
            if (pos < s.length() && s.charAt(pos) == '}') {
                pos++;
                return map;
            }
            while (true) {
                skipWhitespace();
                String key = readString();
                skipWhitespace();
                if (pos >= s.length() || s.charAt(pos) != ':') {
                    throw new IllegalArgumentException("expected ':' at offset " + pos);
                }
                pos++;
                skipWhitespace();
                map.put(key, readValue());
                skipWhitespace();
                if (pos >= s.length()) {
                    throw new IllegalArgumentException("unterminated object");
                }
                char c = s.charAt(pos++);
                if (c == '}') {
                    return map;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("expected ',' or '}' at offset " + (pos - 1));
                }
            }
        }

        private List<Object> readArray() {
            List<Object> list = new ArrayList<>();
            pos++; // '['
            skipWhitespace();
            if (pos < s.length() && s.charAt(pos) == ']') {
                pos++;
                return list;
            }
            while (true) {
                skipWhitespace();
                list.add(readValue());
                skipWhitespace();
                if (pos >= s.length()) {
                    throw new IllegalArgumentException("unterminated array");
                }
                char c = s.charAt(pos++);
                if (c == ']') {
                    return list;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("expected ',' or ']' at offset " + (pos - 1));
                }
            }
        }

        private String readString() {
            if (pos >= s.length() || s.charAt(pos) != '"') {
                throw new IllegalArgumentException("expected '\"' at offset " + pos);
            }
            pos++;
            StringBuilder sb = new StringBuilder();
            while (true) {
                if (pos >= s.length()) {
                    throw new IllegalArgumentException("unterminated string");
                }
                char c = s.charAt(pos++);
                if (c == '"') {
                    return sb.toString();
                }
                if (c != '\\') {
                    sb.append(c);
                    continue;
                }
                char esc = s.charAt(pos++);
                switch (esc) {
                    case '"': sb.append('"'); break;
                    case '\\': sb.append('\\'); break;
                    case '/': sb.append('/'); break;
                    case 'b': sb.append('\b'); break;
                    case 'f': sb.append('\f'); break;
                    case 'n': sb.append('\n'); break;
                    case 'r': sb.append('\r'); break;
                    case 't': sb.append('\t'); break;
                    case 'u':
                        sb.append((char) Integer.parseInt(s.substring(pos, pos + 4), 16));
                        pos += 4;
                        break;
                    default:
                        throw new IllegalArgumentException("bad escape \\" + esc);
                }
            }
        }

        private Object readNumber() {
            int start = pos;
            while (pos < s.length() && "+-0123456789.eE".indexOf(s.charAt(pos)) >= 0) {
                pos++;
            }
            String raw = s.substring(start, pos);
            if (raw.isEmpty()) {
                throw new IllegalArgumentException("expected a value at offset " + start);
            }
            if (raw.indexOf('.') < 0 && raw.indexOf('e') < 0 && raw.indexOf('E') < 0) {
                return Long.valueOf(raw);
            }
            return Double.valueOf(raw);
        }
    }

    // ---------------------------------------------------------------- writing

    static String write(Object value) {
        StringBuilder sb = new StringBuilder();
        write(value, sb, false, 0);
        return sb.toString();
    }

    static String write(Object value, boolean pretty) {
        StringBuilder sb = new StringBuilder();
        write(value, sb, pretty, 0);
        return sb.toString();
    }

    private static void write(Object value, StringBuilder sb, boolean pretty, int depth) {
        if (value == null) {
            sb.append("null");
        } else if (value instanceof String) {
            writeString((String) value, sb);
        } else if (value instanceof Boolean) {
            sb.append(value.toString());
        } else if (value instanceof Number) {
            sb.append(numberToString((Number) value));
        } else if (value instanceof Map<?, ?> map) {
            if (map.isEmpty()) {
                sb.append("{}");
                return;
            }
            sb.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> e : map.entrySet()) {
                if (!first) {
                    sb.append(',');
                }
                first = false;
                newline(sb, pretty, depth + 1);
                writeString(String.valueOf(e.getKey()), sb);
                sb.append(':');
                if (pretty) {
                    sb.append(' ');
                }
                write(e.getValue(), sb, pretty, depth + 1);
            }
            newline(sb, pretty, depth);
            sb.append('}');
        } else if (value instanceof List<?> list) {
            if (list.isEmpty()) {
                sb.append("[]");
                return;
            }
            sb.append('[');
            boolean first = true;
            for (Object o : list) {
                if (!first) {
                    sb.append(',');
                }
                first = false;
                newline(sb, pretty, depth + 1);
                write(o, sb, pretty, depth + 1);
            }
            newline(sb, pretty, depth);
            sb.append(']');
        } else {
            throw new IllegalArgumentException("cannot serialize " + value.getClass().getName());
        }
    }

    private static void newline(StringBuilder sb, boolean pretty, int depth) {
        if (!pretty) {
            return;
        }
        sb.append('\n');
        sb.append("  ".repeat(depth));
    }

    private static String numberToString(Number n) {
        if (n instanceof Integer || n instanceof Long || n instanceof Short || n instanceof Byte) {
            return String.valueOf(n.longValue());
        }
        double d = n.doubleValue();
        if (d == Math.rint(d) && !Double.isInfinite(d)) {
            return String.valueOf((long) d);
        }
        return String.valueOf(d);
    }

    private static void writeString(String s, StringBuilder sb) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                case '\b': sb.append("\\b"); break;
                case '\f': sb.append("\\f"); break;
                default:
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
            }
        }
        sb.append('"');
    }

    // ------------------------------------------------------------ comparison

    /** Structural equality; JSON numbers compare by value, object key order is irrelevant. */
    static boolean deepEquals(Object a, Object b) {
        return firstDifference(a, b, "$") == null;
    }

    /**
     * Returns a human readable description of the first structural difference between {@code
     * expected} and {@code actual}, or {@code null} when they are structurally equal. Object key
     * order is not significant, but the key sets must match exactly, so a key that should have been
     * omitted (or one that is missing) is reported.
     */
    static String firstDifference(Object expected, Object actual, String path) {
        if (expected == null || actual == null) {
            if (expected == actual) {
                return null;
            }
            return path + ": expected " + render(expected) + " but found " + render(actual);
        }
        if (expected instanceof Map<?, ?> em) {
            if (!(actual instanceof Map<?, ?> am)) {
                return path + ": expected a JSON object but found " + typeName(actual);
            }
            for (Object key : em.keySet()) {
                if (!am.containsKey(key)) {
                    return path + "." + key + ": missing, expected " + render(em.get(key));
                }
            }
            for (Object key : am.keySet()) {
                if (!em.containsKey(key)) {
                    return path + "." + key + ": unexpected key with value "
                            + render(am.get(key)) + " (it must be omitted from the request)";
                }
            }
            for (Object key : em.keySet()) {
                String diff = firstDifference(em.get(key), am.get(key), path + "." + key);
                if (diff != null) {
                    return diff;
                }
            }
            return null;
        }
        if (expected instanceof List<?> el) {
            if (!(actual instanceof List<?> al)) {
                return path + ": expected a JSON array but found " + typeName(actual);
            }
            if (el.size() != al.size()) {
                return path + ": expected " + el.size() + " element(s) but found " + al.size();
            }
            for (int i = 0; i < el.size(); i++) {
                String diff = firstDifference(el.get(i), al.get(i), path + "[" + i + "]");
                if (diff != null) {
                    return diff;
                }
            }
            return null;
        }
        if (expected instanceof Number en) {
            if (!(actual instanceof Number an)) {
                return path + ": expected the JSON number " + numberToString(en) + " but found "
                        + typeName(actual) + " " + render(actual);
            }
            if (en.doubleValue() != an.doubleValue()) {
                return path + ": expected " + numberToString(en) + " but found "
                        + numberToString(an);
            }
            return null;
        }
        if (!expected.equals(actual)) {
            return path + ": expected " + render(expected) + " but found " + render(actual);
        }
        return null;
    }

    static String typeName(Object o) {
        if (o == null) {
            return "null";
        }
        if (o instanceof Map) {
            return "object";
        }
        if (o instanceof List) {
            return "array";
        }
        if (o instanceof String) {
            return "string";
        }
        if (o instanceof Boolean) {
            return "boolean";
        }
        if (o instanceof Number) {
            return "number";
        }
        return o.getClass().getSimpleName();
    }

    static String render(Object o) {
        String s = write(o);
        return s.length() > 200 ? s.substring(0, 200) + "..." : s;
    }
}
