import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal JSON parser: objects, arrays, strings, numbers, booleans, null.
 * Objects become LinkedHashMap&lt;String,Object&gt;, arrays ArrayList&lt;Object&gt;,
 * numbers Double. Enough for ServiceNow REST envelopes; not a general library.
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
