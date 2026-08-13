package com.vmware.vcfops.networks;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal dependency-free JSON reader/writer.
 *
 * <p>Provided by the project so that client code does not need to hand-roll JSON handling.
 * Objects parse to {@link LinkedHashMap} (key order preserved), arrays to {@link List},
 * numbers to {@link Long} or {@link Double}, and JSON null to Java null.
 *
 * <p>DO NOT MODIFY. The test harness parses recorded request bodies with this same code.
 */
public final class Json {

    private Json() {}

    // ---------------------------------------------------------------- parsing

    public static Object parse(String text) {
        Parser p = new Parser(text);
        p.skipWhitespace();
        Object value = p.readValue();
        p.skipWhitespace();
        if (!p.atEnd()) {
            throw new IllegalArgumentException("Trailing content at offset " + p.pos);
        }
        return value;
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> parseObject(String text) {
        Object value = parse(text);
        if (!(value instanceof Map)) {
            throw new IllegalArgumentException("Expected a JSON object, got: " + describe(value));
        }
        return (Map<String, Object>) value;
    }

    private static String describe(Object value) {
        return value == null ? "null" : value.getClass().getSimpleName();
    }

    private static final class Parser {
        private final String src;
        private int pos;

        Parser(String src) {
            this.src = src;
        }

        boolean atEnd() {
            return pos >= src.length();
        }

        void skipWhitespace() {
            while (pos < src.length() && Character.isWhitespace(src.charAt(pos))) {
                pos++;
            }
        }

        Object readValue() {
            skipWhitespace();
            if (atEnd()) {
                throw new IllegalArgumentException("Unexpected end of input");
            }
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

        private void expect(String literal) {
            if (!src.startsWith(literal, pos)) {
                throw new IllegalArgumentException("Expected '" + literal + "' at offset " + pos);
            }
            pos += literal.length();
        }

        private Map<String, Object> readObject() {
            Map<String, Object> out = new LinkedHashMap<>();
            pos++; // consume '{'
            skipWhitespace();
            if (!atEnd() && src.charAt(pos) == '}') {
                pos++;
                return out;
            }
            while (true) {
                skipWhitespace();
                String key = readString();
                skipWhitespace();
                if (atEnd() || src.charAt(pos) != ':') {
                    throw new IllegalArgumentException("Expected ':' at offset " + pos);
                }
                pos++;
                out.put(key, readValue());
                skipWhitespace();
                if (atEnd()) {
                    throw new IllegalArgumentException("Unterminated object");
                }
                char c = src.charAt(pos++);
                if (c == '}') {
                    return out;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("Expected ',' or '}' at offset " + (pos - 1));
                }
            }
        }

        private List<Object> readArray() {
            List<Object> out = new ArrayList<>();
            pos++; // consume '['
            skipWhitespace();
            if (!atEnd() && src.charAt(pos) == ']') {
                pos++;
                return out;
            }
            while (true) {
                out.add(readValue());
                skipWhitespace();
                if (atEnd()) {
                    throw new IllegalArgumentException("Unterminated array");
                }
                char c = src.charAt(pos++);
                if (c == ']') {
                    return out;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("Expected ',' or ']' at offset " + (pos - 1));
                }
            }
        }

        private String readString() {
            if (atEnd() || src.charAt(pos) != '"') {
                throw new IllegalArgumentException("Expected '\"' at offset " + pos);
            }
            pos++;
            StringBuilder sb = new StringBuilder();
            while (true) {
                if (atEnd()) {
                    throw new IllegalArgumentException("Unterminated string");
                }
                char c = src.charAt(pos++);
                if (c == '"') {
                    return sb.toString();
                }
                if (c != '\\') {
                    sb.append(c);
                    continue;
                }
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
                    default:
                        throw new IllegalArgumentException("Bad escape '\\" + esc + "' at offset " + (pos - 1));
                }
            }
        }

        private Object readNumber() {
            int start = pos;
            while (pos < src.length() && "+-0123456789.eE".indexOf(src.charAt(pos)) >= 0) {
                pos++;
            }
            String raw = src.substring(start, pos);
            if (raw.isEmpty()) {
                throw new IllegalArgumentException("Expected a value at offset " + start);
            }
            if (raw.indexOf('.') >= 0 || raw.indexOf('e') >= 0 || raw.indexOf('E') >= 0) {
                return Double.valueOf(raw);
            }
            return Long.valueOf(raw);
        }
    }

    // ---------------------------------------------------------------- writing

    /** Serialises maps, lists, strings, numbers, booleans and null to compact JSON. */
    public static String write(Object value) {
        StringBuilder sb = new StringBuilder();
        writeValue(value, sb);
        return sb.toString();
    }

    private static void writeValue(Object value, StringBuilder sb) {
        if (value == null) {
            sb.append("null");
        } else if (value instanceof Map<?, ?> map) {
            sb.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> e : map.entrySet()) {
                if (!first) {
                    sb.append(',');
                }
                first = false;
                writeString(String.valueOf(e.getKey()), sb);
                sb.append(':');
                writeValue(e.getValue(), sb);
            }
            sb.append('}');
        } else if (value instanceof Iterable<?> items) {
            sb.append('[');
            boolean first = true;
            for (Object item : items) {
                if (!first) {
                    sb.append(',');
                }
                first = false;
                writeValue(item, sb);
            }
            sb.append(']');
        } else if (value instanceof CharSequence) {
            writeString(value.toString(), sb);
        } else if (value instanceof Number || value instanceof Boolean) {
            sb.append(value);
        } else {
            writeString(value.toString(), sb);
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

    // ------------------------------------------------------------- accessors

    @SuppressWarnings("unchecked")
    public static Map<String, Object> objectAt(Map<String, Object> source, String key) {
        Object value = source.get(key);
        return value instanceof Map ? (Map<String, Object>) value : null;
    }

    @SuppressWarnings("unchecked")
    public static List<Object> listAt(Map<String, Object> source, String key) {
        Object value = source.get(key);
        return value instanceof List ? (List<Object>) value : null;
    }

    public static String stringAt(Map<String, Object> source, String key) {
        Object value = source.get(key);
        return value instanceof CharSequence ? value.toString() : null;
    }

    public static int intAt(Map<String, Object> source, String key, int fallback) {
        Object value = source.get(key);
        return value instanceof Number n ? n.intValue() : fallback;
    }
}
