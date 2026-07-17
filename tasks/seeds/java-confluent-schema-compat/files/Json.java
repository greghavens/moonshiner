import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal JSON support: objects become LinkedHashMap&lt;String,Object&gt;,
 * arrays ArrayList&lt;Object&gt;, numbers Double. write() emits maps, lists,
 * strings, numbers, booleans and null. Enough for Schema Registry payloads;
 * not a general library.
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

    public static String write(Object value) {
        StringBuilder sb = new StringBuilder();
        emit(value, sb);
        return sb.toString();
    }

    private static void emit(Object value, StringBuilder sb) {
        if (value == null) {
            sb.append("null");
        } else if (value instanceof String s) {
            emitString(s, sb);
        } else if (value instanceof Boolean b) {
            sb.append(b.toString());
        } else if (value instanceof Double d && d == Math.floor(d) && !d.isInfinite()) {
            sb.append(Long.toString(d.longValue()));
        } else if (value instanceof Number n) {
            sb.append(n.toString());
        } else if (value instanceof Map<?, ?> m) {
            sb.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> e : m.entrySet()) {
                if (!first) sb.append(',');
                first = false;
                emitString(String.valueOf(e.getKey()), sb);
                sb.append(':');
                emit(e.getValue(), sb);
            }
            sb.append('}');
        } else if (value instanceof List<?> l) {
            sb.append('[');
            for (int i = 0; i < l.size(); i++) {
                if (i > 0) sb.append(',');
                emit(l.get(i), sb);
            }
            sb.append(']');
        } else {
            throw new IllegalArgumentException("cannot serialize " + value.getClass());
        }
    }

    private static void emitString(String s, StringBuilder sb) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
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
        if (c == '{') return object();
        if (c == '[') return array();
        if (c == '"') return string();
        if (c == 't') { literal("true"); return Boolean.TRUE; }
        if (c == 'f') { literal("false"); return Boolean.FALSE; }
        if (c == 'n') { literal("null"); return null; }
        return number();
    }

    private void literal(String lit) {
        if (!src.startsWith(lit, pos)) {
            throw new IllegalArgumentException("bad literal at offset " + pos);
        }
        pos += lit.length();
    }

    private Map<String, Object> object() {
        pos++; // consume '{'
        Map<String, Object> out = new LinkedHashMap<>();
        ws();
        if (peek() == '}') { pos++; return out; }
        while (true) {
            ws();
            String key = string();
            ws();
            if (peek() != ':') throw new IllegalArgumentException("expected ':' at offset " + pos);
            pos++;
            out.put(key, value());
            ws();
            char c = peek();
            pos++;
            if (c == '}') return out;
            if (c != ',') throw new IllegalArgumentException("expected ',' or '}' at offset " + (pos - 1));
        }
    }

    private List<Object> array() {
        pos++; // consume '['
        List<Object> out = new ArrayList<>();
        ws();
        if (peek() == ']') { pos++; return out; }
        while (true) {
            out.add(value());
            ws();
            char c = peek();
            pos++;
            if (c == ']') return out;
            if (c != ',') throw new IllegalArgumentException("expected ',' or ']' at offset " + (pos - 1));
        }
    }

    private String string() {
        ws();
        if (peek() != '"') throw new IllegalArgumentException("expected string at offset " + pos);
        pos++;
        StringBuilder sb = new StringBuilder();
        while (true) {
            char c = src.charAt(pos++);
            if (c == '"') return sb.toString();
            if (c == '\\') {
                char e = src.charAt(pos++);
                switch (e) {
                    case '"': sb.append('"'); break;
                    case '\\': sb.append('\\'); break;
                    case '/': sb.append('/'); break;
                    case 'n': sb.append('\n'); break;
                    case 't': sb.append('\t'); break;
                    case 'r': sb.append('\r'); break;
                    case 'b': sb.append('\b'); break;
                    case 'f': sb.append('\f'); break;
                    case 'u':
                        sb.append((char) Integer.parseInt(src.substring(pos, pos + 4), 16));
                        pos += 4;
                        break;
                    default:
                        throw new IllegalArgumentException("bad escape \\" + e);
                }
            } else {
                sb.append(c);
            }
        }
    }

    private Object number() {
        int start = pos;
        while (pos < src.length() && "+-0123456789.eE".indexOf(src.charAt(pos)) >= 0) {
            pos++;
        }
        return Double.parseDouble(src.substring(start, pos));
    }
}
