package harness;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal JSON reader/writer used by the test harness, the loopback mock and the verifier.
 *
 * <p>Objects are {@link LinkedHashMap} (insertion ordered so the raw wire key order survives),
 * arrays are {@link List}, numbers are {@link Long} when integral and {@link Double} otherwise.
 * A JSON {@code null} is represented by {@link #NULL} rather than a Java {@code null} so that
 * "key present with a null value" stays distinguishable from "key absent".
 *
 * <p>Harness file. Do not modify.
 */
public final class Json {

    /** Sentinel for a JSON null that was actually present on the wire. */
    public static final Object NULL = new Object() {
        @Override public String toString() { return "null"; }
    };

    private final String src;
    private int pos;

    private Json(String src) {
        this.src = src;
        this.pos = 0;
    }

    public static Object parse(String text) {
        Json p = new Json(text);
        p.skipWs();
        Object v = p.readValue();
        p.skipWs();
        if (p.pos != p.src.length()) {
            throw new IllegalArgumentException("trailing content at offset " + p.pos);
        }
        return v;
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> parseObject(String text) {
        Object v = parse(text);
        if (!(v instanceof Map)) {
            throw new IllegalArgumentException("expected a JSON object, got " + kindOf(v));
        }
        return (Map<String, Object>) v;
    }

    public static String kindOf(Object v) {
        if (v == NULL || v == null) return "null";
        if (v instanceof Map) return "object";
        if (v instanceof List) return "array";
        if (v instanceof String) return "string";
        if (v instanceof Boolean) return "boolean";
        return "number";
    }

    // ---------------------------------------------------------------- reading

    private Object readValue() {
        skipWs();
        if (pos >= src.length()) throw new IllegalArgumentException("unexpected end of input");
        char c = src.charAt(pos);
        switch (c) {
            case '{': return readObject();
            case '[': return readArray();
            case '"': return readString();
            case 't': expect("true"); return Boolean.TRUE;
            case 'f': expect("false"); return Boolean.FALSE;
            case 'n': expect("null"); return NULL;
            default: return readNumber();
        }
    }

    private Map<String, Object> readObject() {
        Map<String, Object> out = new LinkedHashMap<>();
        pos++; // {
        skipWs();
        if (peek() == '}') { pos++; return out; }
        while (true) {
            skipWs();
            if (peek() != '"') throw new IllegalArgumentException("expected key string at offset " + pos);
            String key = readString();
            skipWs();
            if (peek() != ':') throw new IllegalArgumentException("expected ':' at offset " + pos);
            pos++;
            Object value = readValue();
            if (out.containsKey(key)) throw new IllegalArgumentException("duplicate key: " + key);
            out.put(key, value);
            skipWs();
            char c = peek();
            if (c == ',') { pos++; continue; }
            if (c == '}') { pos++; return out; }
            throw new IllegalArgumentException("expected ',' or '}' at offset " + pos);
        }
    }

    private List<Object> readArray() {
        List<Object> out = new ArrayList<>();
        pos++; // [
        skipWs();
        if (peek() == ']') { pos++; return out; }
        while (true) {
            out.add(readValue());
            skipWs();
            char c = peek();
            if (c == ',') { pos++; continue; }
            if (c == ']') { pos++; return out; }
            throw new IllegalArgumentException("expected ',' or ']' at offset " + pos);
        }
    }

    private String readString() {
        pos++; // opening quote
        StringBuilder sb = new StringBuilder();
        while (true) {
            if (pos >= src.length()) throw new IllegalArgumentException("unterminated string");
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
                default: throw new IllegalArgumentException("bad escape \\" + e);
            }
        }
    }

    private Object readNumber() {
        int start = pos;
        while (pos < src.length() && "+-0123456789.eE".indexOf(src.charAt(pos)) >= 0) pos++;
        String text = src.substring(start, pos);
        if (text.isEmpty()) throw new IllegalArgumentException("expected a value at offset " + start);
        if (text.indexOf('.') < 0 && text.indexOf('e') < 0 && text.indexOf('E') < 0) {
            return Long.valueOf(text);
        }
        return Double.valueOf(text);
    }

    private char peek() {
        if (pos >= src.length()) throw new IllegalArgumentException("unexpected end of input");
        return src.charAt(pos);
    }

    private void expect(String literal) {
        if (!src.startsWith(literal, pos)) {
            throw new IllegalArgumentException("expected " + literal + " at offset " + pos);
        }
        pos += literal.length();
    }

    private void skipWs() {
        while (pos < src.length() && Character.isWhitespace(src.charAt(pos))) pos++;
    }

    // ---------------------------------------------------------------- writing

    public static String write(Object value) {
        StringBuilder sb = new StringBuilder();
        writeInto(sb, value);
        return sb.toString();
    }

    private static void writeInto(StringBuilder sb, Object value) {
        if (value == null || value == NULL) { sb.append("null"); return; }
        if (value instanceof Map<?, ?> map) {
            sb.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> e : map.entrySet()) {
                if (!first) sb.append(',');
                first = false;
                writeString(sb, String.valueOf(e.getKey()));
                sb.append(':');
                writeInto(sb, e.getValue());
            }
            sb.append('}');
            return;
        }
        if (value instanceof List<?> list) {
            sb.append('[');
            for (int i = 0; i < list.size(); i++) {
                if (i > 0) sb.append(',');
                writeInto(sb, list.get(i));
            }
            sb.append(']');
            return;
        }
        if (value instanceof String s) { writeString(sb, s); return; }
        if (value instanceof Boolean b) { sb.append(b.booleanValue()); return; }
        sb.append(value);
    }

    private static void writeString(StringBuilder sb, String s) {
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
                    if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
            }
        }
        sb.append('"');
    }

    // ---------------------------------------------------------------- helpers

    @SuppressWarnings("unchecked")
    public static Map<String, Object> obj(Object v) {
        return v instanceof Map ? (Map<String, Object>) v : null;
    }

    @SuppressWarnings("unchecked")
    public static List<Object> arr(Object v) {
        return v instanceof List ? (List<Object>) v : null;
    }

    public static String str(Object v) {
        return v instanceof String ? (String) v : null;
    }

    public static Map<String, Object> map(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) m.put(String.valueOf(kv[i]), kv[i + 1]);
        return m;
    }
}
