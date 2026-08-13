import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal JSON reader supplied by the harness so the client does not need a
 * third-party dependency. Parsed values are Map&lt;String,Object&gt;,
 * List&lt;Object&gt;, String, Double, Boolean or null.
 *
 * PROTECTED FILE - part of the graded harness, do not modify.
 */
public final class Json {

    private final String src;
    private int pos;

    private Json(String src) {
        this.src = src;
    }

    /** Parse a JSON document. */
    public static Object parse(String text) {
        Json p = new Json(text);
        p.ws();
        Object value = p.value();
        p.ws();
        if (p.pos != text.length()) {
            throw new IllegalArgumentException("trailing content at offset " + p.pos);
        }
        return value;
    }

    /** Escape a string and wrap it in double quotes, ready to embed in a JSON document. */
    public static String quote(String s) {
        StringBuilder out = new StringBuilder(s.length() + 2);
        out.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"'  -> out.append("\\\"");
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
        return out.append('"').toString();
    }

    // --- typed accessors -----------------------------------------------------

    @SuppressWarnings("unchecked")
    public static Map<String, Object> asObject(Object o) {
        if (o instanceof Map) {
            return (Map<String, Object>) o;
        }
        throw new IllegalArgumentException("expected a JSON object, got " + describe(o));
    }

    @SuppressWarnings("unchecked")
    public static List<Object> asArray(Object o) {
        if (o instanceof List) {
            return (List<Object>) o;
        }
        throw new IllegalArgumentException("expected a JSON array, got " + describe(o));
    }

    /** Read a field, returning null when absent or JSON null. */
    public static Object get(Object obj, String field) {
        return asObject(obj).get(field);
    }

    public static String str(Object obj, String field) {
        Object v = get(obj, field);
        return v == null ? null : String.valueOf(v);
    }

    public static boolean bool(Object obj, String field) {
        Object v = get(obj, field);
        return v instanceof Boolean b && b;
    }

    public static int intOf(Object obj, String field) {
        Object v = get(obj, field);
        if (v instanceof Double d) {
            return (int) Math.round(d);
        }
        throw new IllegalArgumentException("field '" + field + "' is not a number: " + describe(v));
    }

    private static String describe(Object o) {
        return o == null ? "null" : o.getClass().getSimpleName();
    }

    // --- parser --------------------------------------------------------------

    private void ws() {
        while (pos < src.length() && Character.isWhitespace(src.charAt(pos))) {
            pos++;
        }
    }

    private char peek() {
        if (pos >= src.length()) {
            throw new IllegalArgumentException("unexpected end of JSON input");
        }
        return src.charAt(pos);
    }

    private void expect(char c) {
        if (peek() != c) {
            throw new IllegalArgumentException("expected '" + c + "' at offset " + pos);
        }
        pos++;
    }

    private Object value() {
        char c = peek();
        return switch (c) {
            case '{' -> object();
            case '[' -> array();
            case '"' -> string();
            case 't', 'f' -> literalBoolean();
            case 'n' -> literalNull();
            default -> number();
        };
    }

    private Map<String, Object> object() {
        expect('{');
        Map<String, Object> out = new LinkedHashMap<>();
        ws();
        if (peek() == '}') {
            pos++;
            return out;
        }
        while (true) {
            ws();
            String key = string();
            ws();
            expect(':');
            ws();
            out.put(key, value());
            ws();
            char c = peek();
            pos++;
            if (c == '}') {
                return out;
            }
            if (c != ',') {
                throw new IllegalArgumentException("expected ',' or '}' at offset " + (pos - 1));
            }
        }
    }

    private List<Object> array() {
        expect('[');
        List<Object> out = new ArrayList<>();
        ws();
        if (peek() == ']') {
            pos++;
            return out;
        }
        while (true) {
            ws();
            out.add(value());
            ws();
            char c = peek();
            pos++;
            if (c == ']') {
                return out;
            }
            if (c != ',') {
                throw new IllegalArgumentException("expected ',' or ']' at offset " + (pos - 1));
            }
        }
    }

    private String string() {
        expect('"');
        StringBuilder out = new StringBuilder();
        while (true) {
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
                case '"'  -> out.append('"');
                case '\\' -> out.append('\\');
                case '/'  -> out.append('/');
                case 'b'  -> out.append('\b');
                case 'f'  -> out.append('\f');
                case 'n'  -> out.append('\n');
                case 'r'  -> out.append('\r');
                case 't'  -> out.append('\t');
                case 'u'  -> {
                    out.append((char) Integer.parseInt(src.substring(pos, pos + 4), 16));
                    pos += 4;
                }
                default -> throw new IllegalArgumentException("bad escape '\\" + esc + "'");
            }
        }
    }

    private Object literalBoolean() {
        if (src.startsWith("true", pos)) {
            pos += 4;
            return Boolean.TRUE;
        }
        if (src.startsWith("false", pos)) {
            pos += 5;
            return Boolean.FALSE;
        }
        throw new IllegalArgumentException("bad literal at offset " + pos);
    }

    private Object literalNull() {
        if (src.startsWith("null", pos)) {
            pos += 4;
            return null;
        }
        throw new IllegalArgumentException("bad literal at offset " + pos);
    }

    private Double number() {
        int start = pos;
        while (pos < src.length() && "+-.eE0123456789".indexOf(src.charAt(pos)) >= 0) {
            pos++;
        }
        if (start == pos) {
            throw new IllegalArgumentException("expected a value at offset " + pos);
        }
        return Double.valueOf(src.substring(start, pos));
    }
}
