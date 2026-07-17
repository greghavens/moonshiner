import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Tiny recursive-descent JSON parser: objects become LinkedHashMap, arrays
 * ArrayList, numbers Double, plus String/Boolean/null. Enough for Kubernetes
 * Status bodies and pod lists; we deliberately avoid a JSON dependency.
 */
public final class Json {

    private final String src;
    private int pos;

    private Json(String src) {
        this.src = src;
    }

    public static Object parse(String text) {
        Json p = new Json(text);
        p.ws();
        Object v = p.value();
        p.ws();
        if (p.pos != text.length()) {
            throw new IllegalArgumentException("trailing JSON content at " + p.pos);
        }
        return v;
    }

    private void ws() {
        while (pos < src.length() && Character.isWhitespace(src.charAt(pos))) pos++;
    }

    private char peek() {
        if (pos >= src.length()) throw new IllegalArgumentException("unexpected end of JSON");
        return src.charAt(pos);
    }

    private void expect(String literal) {
        if (!src.startsWith(literal, pos)) {
            throw new IllegalArgumentException("bad JSON literal at " + pos);
        }
        pos += literal.length();
    }

    private Object value() {
        char c = peek();
        switch (c) {
            case '{': return object();
            case '[': return array();
            case '"': return string();
            case 't': expect("true"); return Boolean.TRUE;
            case 'f': expect("false"); return Boolean.FALSE;
            case 'n': expect("null"); return null;
            default: return number();
        }
    }

    private Map<String, Object> object() {
        Map<String, Object> out = new LinkedHashMap<>();
        pos++; // '{'
        ws();
        if (peek() == '}') { pos++; return out; }
        while (true) {
            ws();
            String key = string();
            ws();
            if (peek() != ':') throw new IllegalArgumentException("expected ':' at " + pos);
            pos++;
            ws();
            out.put(key, value());
            ws();
            char c = peek();
            pos++;
            if (c == '}') return out;
            if (c != ',') throw new IllegalArgumentException("expected ',' or '}' at " + pos);
        }
    }

    private List<Object> array() {
        List<Object> out = new ArrayList<>();
        pos++; // '['
        ws();
        if (peek() == ']') { pos++; return out; }
        while (true) {
            ws();
            out.add(value());
            ws();
            char c = peek();
            pos++;
            if (c == ']') return out;
            if (c != ',') throw new IllegalArgumentException("expected ',' or ']' at " + pos);
        }
    }

    private String string() {
        if (peek() != '"') throw new IllegalArgumentException("expected string at " + pos);
        pos++;
        StringBuilder sb = new StringBuilder();
        while (true) {
            char c = src.charAt(pos++);
            if (c == '"') return sb.toString();
            if (c != '\\') { sb.append(c); continue; }
            char esc = src.charAt(pos++);
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
                    sb.append((char) Integer.parseInt(src.substring(pos, pos + 4), 16));
                    pos += 4;
                    break;
                default: throw new IllegalArgumentException("bad escape \\" + esc);
            }
        }
    }

    private Double number() {
        int start = pos;
        while (pos < src.length() && "+-0123456789.eE".indexOf(src.charAt(pos)) >= 0) pos++;
        return Double.valueOf(src.substring(start, pos));
    }

    // convenience getters -------------------------------------------------

    @SuppressWarnings("unchecked")
    public static Map<String, Object> asObject(Object v) {
        return (Map<String, Object>) v;
    }

    @SuppressWarnings("unchecked")
    public static List<Object> asArray(Object v) {
        return (List<Object>) v;
    }
}
