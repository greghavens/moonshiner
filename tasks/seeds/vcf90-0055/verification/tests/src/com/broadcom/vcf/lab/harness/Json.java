package com.broadcom.vcf.lab.harness;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Dependency-free JSON reader/writer used by the harness. Objects become {@link LinkedHashMap}
 * so that key order is preserved for diagnostics, arrays become {@link List}, numbers become
 * {@link Long} when integral and {@link Double} otherwise, and JSON null becomes Java null.
 *
 * <p>Part of the protected harness: do not modify.
 */
public final class Json {

    private final String src;
    private int pos;

    private Json(String src) {
        this.src = src;
    }

    public static Object parse(String text) {
        Json p = new Json(text);
        p.skipWs();
        Object value = p.readValue();
        p.skipWs();
        if (p.pos != p.src.length()) {
            throw p.fail("trailing content after top-level value");
        }
        return value;
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> parseObject(String text) {
        Object v = parse(text);
        if (!(v instanceof Map)) {
            throw new IllegalArgumentException("expected a JSON object, got " + typeName(v));
        }
        return (Map<String, Object>) v;
    }

    public static String typeName(Object v) {
        if (v == null) return "null";
        if (v instanceof Map) return "object";
        if (v instanceof List) return "array";
        if (v instanceof String) return "string";
        if (v instanceof Boolean) return "boolean";
        if (v instanceof Long || v instanceof Double) return "number";
        return v.getClass().getName();
    }

    // ---------- reading ----------

    private Object readValue() {
        if (pos >= src.length()) throw fail("unexpected end of input");
        char c = src.charAt(pos);
        switch (c) {
            case '{': return readObject();
            case '[': return readArray();
            case '"': return readString();
            case 't': expect("true"); return Boolean.TRUE;
            case 'f': expect("false"); return Boolean.FALSE;
            case 'n': expect("null"); return null;
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
            if (peek() != '"') throw fail("expected a string key");
            String key = readString();
            skipWs();
            if (peek() != ':') throw fail("expected ':' after key " + key);
            pos++;
            skipWs();
            if (out.containsKey(key)) throw fail("duplicate key " + key);
            out.put(key, readValue());
            skipWs();
            char c = peek();
            if (c == ',') { pos++; continue; }
            if (c == '}') { pos++; return out; }
            throw fail("expected ',' or '}'");
        }
    }

    private List<Object> readArray() {
        List<Object> out = new ArrayList<>();
        pos++; // [
        skipWs();
        if (peek() == ']') { pos++; return out; }
        while (true) {
            skipWs();
            out.add(readValue());
            skipWs();
            char c = peek();
            if (c == ',') { pos++; continue; }
            if (c == ']') { pos++; return out; }
            throw fail("expected ',' or ']'");
        }
    }

    private String readString() {
        pos++; // opening quote
        StringBuilder sb = new StringBuilder();
        while (true) {
            if (pos >= src.length()) throw fail("unterminated string");
            char c = src.charAt(pos++);
            if (c == '"') return sb.toString();
            if (c != '\\') { sb.append(c); continue; }
            if (pos >= src.length()) throw fail("unterminated escape");
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
                    if (pos + 4 > src.length()) throw fail("truncated \\u escape");
                    sb.append((char) Integer.parseInt(src.substring(pos, pos + 4), 16));
                    pos += 4;
                    break;
                default: throw fail("invalid escape \\" + e);
            }
        }
    }

    private Object readNumber() {
        int start = pos;
        if (peek() == '-') pos++;
        while (pos < src.length() && isNumberChar(src.charAt(pos))) pos++;
        String text = src.substring(start, pos);
        if (text.isEmpty() || text.equals("-")) throw fail("expected a value");
        if (text.indexOf('.') < 0 && text.indexOf('e') < 0 && text.indexOf('E') < 0) {
            return Long.parseLong(text);
        }
        return Double.parseDouble(text);
    }

    private static boolean isNumberChar(char c) {
        return (c >= '0' && c <= '9') || c == '.' || c == 'e' || c == 'E' || c == '+' || c == '-';
    }

    private char peek() {
        if (pos >= src.length()) throw fail("unexpected end of input");
        return src.charAt(pos);
    }

    private void expect(String literal) {
        if (!src.startsWith(literal, pos)) throw fail("expected " + literal);
        pos += literal.length();
    }

    private void skipWs() {
        while (pos < src.length()) {
            char c = src.charAt(pos);
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r') pos++;
            else break;
        }
    }

    private IllegalArgumentException fail(String message) {
        int from = Math.max(0, pos - 30);
        int to = Math.min(src.length(), pos + 30);
        return new IllegalArgumentException(
                "invalid JSON at offset " + pos + ": " + message + " near ..." + src.substring(from, to) + "...");
    }

    // ---------- writing ----------

    public static String write(Object value) {
        StringBuilder sb = new StringBuilder();
        writeTo(sb, value);
        return sb.toString();
    }

    private static void writeTo(StringBuilder sb, Object value) {
        if (value == null) { sb.append("null"); return; }
        if (value instanceof String) { writeString(sb, (String) value); return; }
        if (value instanceof Boolean || value instanceof Long || value instanceof Integer) {
            sb.append(value.toString());
            return;
        }
        if (value instanceof Double) {
            double d = (Double) value;
            if (d == Math.rint(d) && !Double.isInfinite(d)) sb.append((long) d);
            else sb.append(d);
            return;
        }
        if (value instanceof Map) {
            sb.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> e : ((Map<?, ?>) value).entrySet()) {
                if (!first) sb.append(',');
                first = false;
                writeString(sb, String.valueOf(e.getKey()));
                sb.append(':');
                writeTo(sb, e.getValue());
            }
            sb.append('}');
            return;
        }
        if (value instanceof Iterable) {
            sb.append('[');
            boolean first = true;
            for (Object o : (Iterable<?>) value) {
                if (!first) sb.append(',');
                first = false;
                writeTo(sb, o);
            }
            sb.append(']');
            return;
        }
        throw new IllegalArgumentException("cannot serialize " + value.getClass().getName());
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
}
