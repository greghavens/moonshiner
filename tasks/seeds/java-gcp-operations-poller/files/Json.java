import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal JSON parser for the API payloads this tool handles: objects,
 * arrays, strings, longs/doubles, booleans, null. Integral numbers come back
 * as Long, everything else as Double.
 */
final class Json {
    private final String src;
    private int pos;

    private Json(String src) {
        this.src = src;
    }

    static Object parse(String text) {
        Json p = new Json(text);
        p.ws();
        Object v = p.value();
        p.ws();
        if (p.pos != p.src.length()) {
            throw new IllegalArgumentException("trailing JSON content at offset " + p.pos);
        }
        return v;
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> object(Object v) {
        if (!(v instanceof Map)) {
            throw new IllegalArgumentException("expected a JSON object, got: " + v);
        }
        return (Map<String, Object>) v;
    }

    @SuppressWarnings("unchecked")
    static List<Object> array(Object v) {
        if (!(v instanceof List)) {
            throw new IllegalArgumentException("expected a JSON array, got: " + v);
        }
        return (List<Object>) v;
    }

    private void ws() {
        while (pos < src.length() && Character.isWhitespace(src.charAt(pos))) {
            pos++;
        }
    }

    private char peek() {
        if (pos >= src.length()) {
            throw new IllegalArgumentException("unexpected end of JSON");
        }
        return src.charAt(pos);
    }

    private void expect(String literal) {
        if (!src.startsWith(literal, pos)) {
            throw new IllegalArgumentException("malformed JSON literal at offset " + pos);
        }
        pos += literal.length();
    }

    private Object value() {
        char c = peek();
        return switch (c) {
            case '{' -> obj();
            case '[' -> arr();
            case '"' -> str();
            case 't' -> {
                expect("true");
                yield Boolean.TRUE;
            }
            case 'f' -> {
                expect("false");
                yield Boolean.FALSE;
            }
            case 'n' -> {
                expect("null");
                yield null;
            }
            default -> num();
        };
    }

    private Map<String, Object> obj() {
        Map<String, Object> out = new LinkedHashMap<>();
        pos++;
        ws();
        if (peek() == '}') {
            pos++;
            return out;
        }
        while (true) {
            String key = str();
            ws();
            if (peek() != ':') {
                throw new IllegalArgumentException("expected ':' at offset " + pos);
            }
            pos++;
            ws();
            out.put(key, value());
            ws();
            char c = peek();
            pos++;
            if (c == '}') {
                return out;
            }
            if (c != ',') {
                throw new IllegalArgumentException("expected ',' or '}' at offset " + pos);
            }
            ws();
        }
    }

    private List<Object> arr() {
        List<Object> out = new ArrayList<>();
        pos++;
        ws();
        if (peek() == ']') {
            pos++;
            return out;
        }
        while (true) {
            out.add(value());
            ws();
            char c = peek();
            pos++;
            if (c == ']') {
                return out;
            }
            if (c != ',') {
                throw new IllegalArgumentException("expected ',' or ']' at offset " + pos);
            }
            ws();
        }
    }

    private String str() {
        if (peek() != '"') {
            throw new IllegalArgumentException("expected a string at offset " + pos);
        }
        pos++;
        StringBuilder sb = new StringBuilder();
        while (true) {
            if (pos >= src.length()) {
                throw new IllegalArgumentException("unterminated string");
            }
            char c = src.charAt(pos++);
            if (c == '"') {
                return sb.toString();
            }
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

    private Object num() {
        int start = pos;
        while (pos < src.length() && "-+.eE0123456789".indexOf(src.charAt(pos)) >= 0) {
            pos++;
        }
        String text = src.substring(start, pos);
        if (text.isEmpty()) {
            throw new IllegalArgumentException("unexpected character at offset " + pos);
        }
        if (text.indexOf('.') < 0 && text.indexOf('e') < 0 && text.indexOf('E') < 0) {
            return Long.parseLong(text);
        }
        return Double.parseDouble(text);
    }
}
