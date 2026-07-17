import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Tiny recursive-descent JSON parser; enough for ONTAP REST payloads. */
public final class Json {
    private final String src;
    private int pos;

    private Json(String src) {
        this.src = src;
    }

    public static Object parse(String text) {
        Json p = new Json(text);
        p.skipWs();
        Object value = p.value();
        p.skipWs();
        if (p.pos != p.src.length()) {
            throw new IllegalArgumentException("trailing data at offset " + p.pos);
        }
        return value;
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> object(Object value) {
        if (!(value instanceof Map)) {
            throw new IllegalArgumentException("expected JSON object, got " + describe(value));
        }
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    public static List<Object> array(Object value) {
        if (!(value instanceof List)) {
            throw new IllegalArgumentException("expected JSON array, got " + describe(value));
        }
        return (List<Object>) value;
    }

    private static String describe(Object value) {
        return value == null ? "null" : value.getClass().getSimpleName();
    }

    private Object value() {
        char c = peek();
        switch (c) {
            case '{': return objectValue();
            case '[': return arrayValue();
            case '"': return stringValue();
            case 't': expect("true"); return Boolean.TRUE;
            case 'f': expect("false"); return Boolean.FALSE;
            case 'n': expect("null"); return null;
            default: return numberValue();
        }
    }

    private Map<String, Object> objectValue() {
        Map<String, Object> out = new LinkedHashMap<>();
        pos++;
        skipWs();
        if (peek() == '}') { pos++; return out; }
        while (true) {
            skipWs();
            String key = stringValue();
            skipWs();
            if (src.charAt(pos++) != ':') throw new IllegalArgumentException("expected ':' at " + (pos - 1));
            skipWs();
            out.put(key, value());
            skipWs();
            char c = src.charAt(pos++);
            if (c == '}') return out;
            if (c != ',') throw new IllegalArgumentException("expected ',' or '}' at " + (pos - 1));
        }
    }

    private List<Object> arrayValue() {
        List<Object> out = new ArrayList<>();
        pos++;
        skipWs();
        if (peek() == ']') { pos++; return out; }
        while (true) {
            skipWs();
            out.add(value());
            skipWs();
            char c = src.charAt(pos++);
            if (c == ']') return out;
            if (c != ',') throw new IllegalArgumentException("expected ',' or ']' at " + (pos - 1));
        }
    }

    private String stringValue() {
        if (src.charAt(pos) != '"') throw new IllegalArgumentException("expected string at " + pos);
        pos++;
        StringBuilder sb = new StringBuilder();
        while (true) {
            char c = src.charAt(pos++);
            if (c == '"') return sb.toString();
            if (c == '\\') {
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
            } else {
                sb.append(c);
            }
        }
    }

    private Object numberValue() {
        int start = pos;
        while (pos < src.length() && "-+.eE0123456789".indexOf(src.charAt(pos)) >= 0) pos++;
        String text = src.substring(start, pos);
        if (text.isEmpty()) throw new IllegalArgumentException("unexpected character at " + start);
        if (text.indexOf('.') >= 0 || text.indexOf('e') >= 0 || text.indexOf('E') >= 0) {
            return Double.parseDouble(text);
        }
        return Long.parseLong(text);
    }

    private void expect(String literal) {
        if (!src.startsWith(literal, pos)) {
            throw new IllegalArgumentException("expected '" + literal + "' at " + pos);
        }
        pos += literal.length();
    }

    private char peek() {
        if (pos >= src.length()) throw new IllegalArgumentException("unexpected end of input");
        return src.charAt(pos);
    }

    private void skipWs() {
        while (pos < src.length() && Character.isWhitespace(src.charAt(pos))) pos++;
    }
}
