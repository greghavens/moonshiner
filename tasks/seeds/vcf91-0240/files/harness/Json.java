import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal JSON reader/writer for the LCM harness.
 *
 * Objects parse to {@link LinkedHashMap} (insertion ordered), arrays to {@link List},
 * numbers to {@link Double}, strings to {@link String}, and JSON null to Java null.
 *
 * Writing a LinkedHashMap preserves key order, so a value that is absent from the map
 * is absent from the emitted JSON -- putting a key with a null or "" value is a
 * different wire shape from not putting the key at all.
 */
public final class Json {

    private Json() {
    }

    // ---------------------------------------------------------------- building

    /** A fresh insertion-ordered object. */
    public static Map<String, Object> obj() {
        return new LinkedHashMap<>();
    }

    /** A fresh array. */
    public static List<Object> arr() {
        return new ArrayList<>();
    }

    /** Reads a nested value, e.g. {@code path(root, "componentSpec", "software", "version")}. */
    @SuppressWarnings("unchecked")
    public static Object path(Object root, Object... keys) {
        Object cur = root;
        for (Object key : keys) {
            if (cur == null) {
                return null;
            }
            if (key instanceof Integer) {
                List<Object> list = (List<Object>) cur;
                int i = (Integer) key;
                cur = (i >= 0 && i < list.size()) ? list.get(i) : null;
            } else {
                cur = ((Map<String, Object>) cur).get(key);
            }
        }
        return cur;
    }

    /** Reads a nested value as a String, or null. */
    public static String str(Object root, Object... keys) {
        Object v = path(root, keys);
        return v == null ? null : String.valueOf(v);
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> map(Object v) {
        return (Map<String, Object>) v;
    }

    @SuppressWarnings("unchecked")
    public static List<Object> list(Object v) {
        return (List<Object>) v;
    }

    // ----------------------------------------------------------------- writing

    public static String write(Object value) {
        StringBuilder sb = new StringBuilder();
        writeValue(sb, value, -1, 0);
        return sb.toString();
    }

    public static String writeIndented(Object value) {
        StringBuilder sb = new StringBuilder();
        writeValue(sb, value, 2, 0);
        return sb.toString();
    }

    private static void writeValue(StringBuilder sb, Object v, int indent, int depth) {
        if (v == null) {
            sb.append("null");
        } else if (v instanceof String) {
            writeString(sb, (String) v);
        } else if (v instanceof Boolean) {
            sb.append(v.toString());
        } else if (v instanceof Number) {
            double d = ((Number) v).doubleValue();
            if (d == Math.rint(d) && !Double.isInfinite(d) && Math.abs(d) < 1e15) {
                sb.append((long) d);
            } else {
                sb.append(v.toString());
            }
        } else if (v instanceof Map) {
            writeObject(sb, map(v), indent, depth);
        } else if (v instanceof List) {
            writeArray(sb, list(v), indent, depth);
        } else {
            writeString(sb, String.valueOf(v));
        }
    }

    private static void writeObject(StringBuilder sb, Map<String, Object> m, int indent, int depth) {
        if (m.isEmpty()) {
            sb.append("{}");
            return;
        }
        sb.append('{');
        boolean first = true;
        for (Map.Entry<String, Object> e : m.entrySet()) {
            if (!first) {
                sb.append(',');
            }
            first = false;
            newline(sb, indent, depth + 1);
            writeString(sb, e.getKey());
            sb.append(':');
            if (indent >= 0) {
                sb.append(' ');
            }
            writeValue(sb, e.getValue(), indent, depth + 1);
        }
        newline(sb, indent, depth);
        sb.append('}');
    }

    private static void writeArray(StringBuilder sb, List<Object> l, int indent, int depth) {
        if (l.isEmpty()) {
            sb.append("[]");
            return;
        }
        sb.append('[');
        boolean first = true;
        for (Object v : l) {
            if (!first) {
                sb.append(',');
            }
            first = false;
            newline(sb, indent, depth + 1);
            writeValue(sb, v, indent, depth + 1);
        }
        newline(sb, indent, depth);
        sb.append(']');
    }

    private static void newline(StringBuilder sb, int indent, int depth) {
        if (indent < 0) {
            return;
        }
        sb.append('\n');
        for (int i = 0; i < indent * depth; i++) {
            sb.append(' ');
        }
    }

    private static void writeString(StringBuilder sb, String s) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"':
                    sb.append("\\\"");
                    break;
                case '\\':
                    sb.append("\\\\");
                    break;
                case '\n':
                    sb.append("\\n");
                    break;
                case '\r':
                    sb.append("\\r");
                    break;
                case '\t':
                    sb.append("\\t");
                    break;
                case '\b':
                    sb.append("\\b");
                    break;
                case '\f':
                    sb.append("\\f");
                    break;
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

    // ----------------------------------------------------------------- parsing

    public static Object parse(String text) {
        Parser p = new Parser(text);
        p.skipWs();
        Object v = p.value();
        p.skipWs();
        if (p.pos != text.length()) {
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

        void skipWs() {
            while (pos < s.length() && Character.isWhitespace(s.charAt(pos))) {
                pos++;
            }
        }

        Object value() {
            if (pos >= s.length()) {
                throw new IllegalArgumentException("unexpected end of input");
            }
            char c = s.charAt(pos);
            switch (c) {
                case '{':
                    return object();
                case '[':
                    return array();
                case '"':
                    return string();
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
                    return number();
            }
        }

        private void expect(String lit) {
            if (!s.startsWith(lit, pos)) {
                throw new IllegalArgumentException("expected " + lit + " at offset " + pos);
            }
            pos += lit.length();
        }

        private Map<String, Object> object() {
            Map<String, Object> m = new LinkedHashMap<>();
            pos++; // {
            skipWs();
            if (pos < s.length() && s.charAt(pos) == '}') {
                pos++;
                return m;
            }
            while (true) {
                skipWs();
                String k = string();
                skipWs();
                if (s.charAt(pos) != ':') {
                    throw new IllegalArgumentException("expected ':' at offset " + pos);
                }
                pos++;
                skipWs();
                m.put(k, value());
                skipWs();
                char c = s.charAt(pos);
                pos++;
                if (c == '}') {
                    return m;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("expected ',' or '}' at offset " + (pos - 1));
                }
            }
        }

        private List<Object> array() {
            List<Object> l = new ArrayList<>();
            pos++; // [
            skipWs();
            if (pos < s.length() && s.charAt(pos) == ']') {
                pos++;
                return l;
            }
            while (true) {
                skipWs();
                l.add(value());
                skipWs();
                char c = s.charAt(pos);
                pos++;
                if (c == ']') {
                    return l;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("expected ',' or ']' at offset " + (pos - 1));
                }
            }
        }

        private String string() {
            if (s.charAt(pos) != '"') {
                throw new IllegalArgumentException("expected string at offset " + pos);
            }
            pos++;
            StringBuilder sb = new StringBuilder();
            while (true) {
                char c = s.charAt(pos++);
                if (c == '"') {
                    return sb.toString();
                }
                if (c != '\\') {
                    sb.append(c);
                    continue;
                }
                char e = s.charAt(pos++);
                switch (e) {
                    case '"':
                        sb.append('"');
                        break;
                    case '\\':
                        sb.append('\\');
                        break;
                    case '/':
                        sb.append('/');
                        break;
                    case 'b':
                        sb.append('\b');
                        break;
                    case 'f':
                        sb.append('\f');
                        break;
                    case 'n':
                        sb.append('\n');
                        break;
                    case 'r':
                        sb.append('\r');
                        break;
                    case 't':
                        sb.append('\t');
                        break;
                    case 'u':
                        sb.append((char) Integer.parseInt(s.substring(pos, pos + 4), 16));
                        pos += 4;
                        break;
                    default:
                        throw new IllegalArgumentException("bad escape \\" + e + " at offset " + (pos - 1));
                }
            }
        }

        private Double number() {
            int start = pos;
            while (pos < s.length() && "+-0123456789.eE".indexOf(s.charAt(pos)) >= 0) {
                pos++;
            }
            if (start == pos) {
                throw new IllegalArgumentException("unexpected character at offset " + pos);
            }
            return Double.valueOf(s.substring(start, pos));
        }
    }
}
