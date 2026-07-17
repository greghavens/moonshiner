import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal JSON support: objects become LinkedHashMap&lt;String,Object&gt;,
 * arrays ArrayList&lt;Object&gt;, numbers Double. Enough for Microsoft Graph
 * envelopes; not a general library.
 */
public final class Json {
    private final String src;
    private int pos;

    private Json(String src) {
        this.src = src;
    }

    public static Object parse(String text) {
        Json p = new Json(text);
        Object v = p.value();
        p.ws();
        if (p.pos != text.length()) {
            throw new IllegalArgumentException("trailing data at offset " + p.pos);
        }
        return v;
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> object(Object v) {
        if (!(v instanceof Map)) {
            throw new IllegalArgumentException("expected JSON object, got " + v);
        }
        return (Map<String, Object>) v;
    }

    @SuppressWarnings("unchecked")
    public static List<Object> array(Object v) {
        if (!(v instanceof List)) {
            throw new IllegalArgumentException("expected JSON array, got " + v);
        }
        return (List<Object>) v;
    }

    public static String write(Object v) {
        StringBuilder sb = new StringBuilder();
        writeValue(sb, v);
        return sb.toString();
    }

    private static void writeValue(StringBuilder sb, Object v) {
        if (v == null) {
            sb.append("null");
        } else if (v instanceof String s) {
            writeString(sb, s);
        } else if (v instanceof Boolean || v instanceof Integer || v instanceof Long) {
            sb.append(v);
        } else if (v instanceof Number n) {
            double d = n.doubleValue();
            if (d == Math.floor(d) && !Double.isInfinite(d) && Math.abs(d) < 1e15) {
                sb.append((long) d);
            } else {
                sb.append(d);
            }
        } else if (v instanceof Map<?, ?> m) {
            sb.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> e : m.entrySet()) {
                if (!first) sb.append(',');
                first = false;
                writeString(sb, String.valueOf(e.getKey()));
                sb.append(':');
                writeValue(sb, e.getValue());
            }
            sb.append('}');
        } else if (v instanceof List<?> l) {
            sb.append('[');
            for (int i = 0; i < l.size(); i++) {
                if (i > 0) sb.append(',');
                writeValue(sb, l.get(i));
            }
            sb.append(']');
        } else {
            throw new IllegalArgumentException("cannot serialize " + v.getClass());
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

    private void ws() {
        while (pos < src.length() && Character.isWhitespace(src.charAt(pos))) {
            pos++;
        }
    }

    private char peek() {
        if (pos >= src.length()) {
            throw new IllegalArgumentException("unexpected end of input");
        }
        return src.charAt(pos);
    }

    private Object value() {
        ws();
        char c = peek();
        if (c == '{') return objectValue();
        if (c == '[') return arrayValue();
        if (c == '"') return stringValue();
        if (c == 't') { literal("true"); return Boolean.TRUE; }
        if (c == 'f') { literal("false"); return Boolean.FALSE; }
        if (c == 'n') { literal("null"); return null; }
        return numberValue();
    }

    private void literal(String lit) {
        if (!src.startsWith(lit, pos)) {
            throw new IllegalArgumentException("bad literal at offset " + pos);
        }
        pos += lit.length();
    }

    private Map<String, Object> objectValue() {
        Map<String, Object> out = new LinkedHashMap<>();
        pos++; // '{'
        ws();
        if (peek() == '}') { pos++; return out; }
        while (true) {
            ws();
            String key = stringValue();
            ws();
            if (peek() != ':') throw new IllegalArgumentException("expected ':' at offset " + pos);
            pos++;
            out.put(key, value());
            ws();
            char c = peek();
            if (c == ',') { pos++; continue; }
            if (c == '}') { pos++; return out; }
            throw new IllegalArgumentException("expected ',' or '}' at offset " + pos);
        }
    }

    private List<Object> arrayValue() {
        List<Object> out = new ArrayList<>();
        pos++; // '['
        ws();
        if (peek() == ']') { pos++; return out; }
        while (true) {
            out.add(value());
            ws();
            char c = peek();
            if (c == ',') { pos++; continue; }
            if (c == ']') { pos++; return out; }
            throw new IllegalArgumentException("expected ',' or ']' at offset " + pos);
        }
    }

    private String stringValue() {
        if (peek() != '"') throw new IllegalArgumentException("expected string at offset " + pos);
        pos++;
        StringBuilder sb = new StringBuilder();
        while (true) {
            if (pos >= src.length()) throw new IllegalArgumentException("unterminated string");
            char c = src.charAt(pos++);
            if (c == '"') return sb.toString();
            if (c == '\\') {
                char e = src.charAt(pos++);
                switch (e) {
                    case '"' -> sb.append('"');
                    case '\\' -> sb.append('\\');
                    case '/' -> sb.append('/');
                    case 'b' -> sb.append('\b');
                    case 'f' -> sb.append('\f');
                    case 'n' -> sb.append('\n');
                    case 'r' -> sb.append('\r');
                    case 't' -> sb.append('\t');
                    case 'u' -> {
                        sb.append((char) Integer.parseInt(src.substring(pos, pos + 4), 16));
                        pos += 4;
                    }
                    default -> throw new IllegalArgumentException("bad escape \\" + e);
                }
            } else {
                sb.append(c);
            }
        }
    }

    private Double numberValue() {
        int start = pos;
        if (peek() == '-') pos++;
        while (pos < src.length() && "0123456789.eE+-".indexOf(src.charAt(pos)) >= 0) {
            pos++;
        }
        return Double.parseDouble(src.substring(start, pos));
    }
}
