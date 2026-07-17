import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal JSON support for the Vault wire format: objects parse to
 * LinkedHashMap, arrays to ArrayList, numbers to Long (integral) or Double,
 * plus String / Boolean / null. Not a general-purpose library.
 */
final class Json {

    private final String src;
    private int pos;

    private Json(String src) {
        this.src = src;
    }

    static Object parse(String text) {
        Json p = new Json(text);
        Object v = p.value();
        p.ws();
        if (p.pos != text.length()) throw p.err("trailing data");
        return v;
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> parseObject(String text) {
        return (Map<String, Object>) parse(text);
    }

    private RuntimeException err(String msg) {
        return new IllegalArgumentException("json: " + msg + " at offset " + pos);
    }

    private void ws() {
        while (pos < src.length() && Character.isWhitespace(src.charAt(pos))) pos++;
    }

    private char peek() {
        if (pos >= src.length()) throw err("unexpected end of input");
        return src.charAt(pos);
    }

    private void expect(char c) {
        if (peek() != c) throw err("expected '" + c + "'");
        pos++;
    }

    private Object value() {
        ws();
        char c = peek();
        switch (c) {
            case '{': return object();
            case '[': return array();
            case '"': return string();
            case 't': literal("true"); return Boolean.TRUE;
            case 'f': literal("false"); return Boolean.FALSE;
            case 'n': literal("null"); return null;
            default: return number();
        }
    }

    private void literal(String lit) {
        if (!src.startsWith(lit, pos)) throw err("bad literal");
        pos += lit.length();
    }

    private Map<String, Object> object() {
        Map<String, Object> out = new LinkedHashMap<>();
        expect('{');
        ws();
        if (peek() == '}') { pos++; return out; }
        while (true) {
            ws();
            String key = string();
            ws();
            expect(':');
            out.put(key, value());
            ws();
            char c = peek();
            pos++;
            if (c == '}') return out;
            if (c != ',') throw err("expected ',' or '}'");
        }
    }

    private List<Object> array() {
        List<Object> out = new ArrayList<>();
        expect('[');
        ws();
        if (peek() == ']') { pos++; return out; }
        while (true) {
            out.add(value());
            ws();
            char c = peek();
            pos++;
            if (c == ']') return out;
            if (c != ',') throw err("expected ',' or ']'");
        }
    }

    private String string() {
        expect('"');
        StringBuilder sb = new StringBuilder();
        while (true) {
            char c = src.charAt(pos++);
            if (c == '"') return sb.toString();
            if (c != '\\') { sb.append(c); continue; }
            char e = src.charAt(pos++);
            switch (e) {
                case '"': sb.append('"'); break;
                case '\\': sb.append('\\'); break;
                case '/': sb.append('/'); break;
                case 'b': sb.append('\b'); break;
                case 'f': sb.append('\f'); break;
                case 'n': sb.append('\n'); break;
                case 'r': sb.append('\r'); break;
                case 't': sb.append('\t'); break;
                case 'u':
                    sb.append((char) Integer.parseInt(src.substring(pos, pos + 4), 16));
                    pos += 4;
                    break;
                default: throw err("bad escape '\\" + e + "'");
            }
        }
    }

    private Object number() {
        int start = pos;
        if (peek() == '-') pos++;
        boolean integral = true;
        while (pos < src.length()) {
            char c = src.charAt(pos);
            if (c >= '0' && c <= '9') { pos++; continue; }
            if (c == '.' || c == 'e' || c == 'E' || c == '+' || c == '-') { integral = false; pos++; continue; }
            break;
        }
        String tok = src.substring(start, pos);
        if (tok.isEmpty() || tok.equals("-")) throw err("bad number");
        return integral ? (Object) Long.parseLong(tok) : (Object) Double.parseDouble(tok);
    }

    // ------------------------------------------------------------- writing

    static String write(Object v) {
        StringBuilder sb = new StringBuilder();
        write(v, sb);
        return sb.toString();
    }

    private static void write(Object v, StringBuilder sb) {
        if (v == null) {
            sb.append("null");
        } else if (v instanceof String s) {
            writeString(s, sb);
        } else if (v instanceof Map<?, ?> m) {
            sb.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> e : m.entrySet()) {
                if (!first) sb.append(',');
                first = false;
                writeString(String.valueOf(e.getKey()), sb);
                sb.append(':');
                write(e.getValue(), sb);
            }
            sb.append('}');
        } else if (v instanceof List<?> l) {
            sb.append('[');
            for (int i = 0; i < l.size(); i++) {
                if (i > 0) sb.append(',');
                write(l.get(i), sb);
            }
            sb.append(']');
        } else if (v instanceof Boolean || v instanceof Long || v instanceof Integer) {
            sb.append(v);
        } else if (v instanceof Number) {
            sb.append(v);
        } else {
            throw new IllegalArgumentException("json: cannot write " + v.getClass());
        }
    }

    private static void writeString(String s, StringBuilder sb) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\b': sb.append("\\b"); break;
                case '\f': sb.append("\\f"); break;
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
}
