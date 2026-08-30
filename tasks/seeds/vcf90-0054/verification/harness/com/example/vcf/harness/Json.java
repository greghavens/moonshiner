package com.example.vcf.harness;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal JSON reader/writer for the harness. No external dependencies.
 *
 * <p>Objects parse to {@link LinkedHashMap} (insertion ordered so the verifier can talk about
 * the literal key order on the wire), arrays to {@link List}, numbers to {@link Double} or
 * {@link Long}, and JSON null to a real Java {@code null} value stored under its key — the
 * verifier needs to be able to tell "key absent" from "key present with a null value".
 */
public final class Json {

    private Json() {
    }

    // ---------------------------------------------------------------- parsing

    public static Object parse(String text) {
        Parser p = new Parser(text);
        p.skipWhitespace();
        Object value = p.readValue();
        p.skipWhitespace();
        if (!p.atEnd()) {
            throw new IllegalArgumentException("trailing content at offset " + p.pos + " in: " + text);
        }
        return value;
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> parseObject(String text) {
        Object value = parse(text);
        if (!(value instanceof Map)) {
            throw new IllegalArgumentException("expected a JSON object, got: " + describe(value));
        }
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> asObject(Object value) {
        if (value == null || value instanceof Map) {
            return (Map<String, Object>) value;
        }
        throw new IllegalArgumentException("expected a JSON object, got: " + describe(value));
    }

    @SuppressWarnings("unchecked")
    public static List<Object> asArray(Object value) {
        if (value == null || value instanceof List) {
            return (List<Object>) value;
        }
        throw new IllegalArgumentException("expected a JSON array, got: " + describe(value));
    }

    public static String describe(Object value) {
        if (value == null) {
            return "null";
        }
        if (value instanceof Map) {
            return "object" + ((Map<?, ?>) value).keySet();
        }
        if (value instanceof List) {
            return "array(size=" + ((List<?>) value).size() + ")";
        }
        return value.getClass().getSimpleName() + "(" + value + ")";
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
                throw new IllegalArgumentException("unexpected end of JSON input");
            }
            char c = src.charAt(pos);
            switch (c) {
                case '{':
                    return readObject();
                case '[':
                    return readArray();
                case '"':
                    return readString();
                case 't':
                    expect("true");
                    return Boolean.TRUE;
                case 'f':
                    expect("false");
                    return Boolean.FALSE;
                case 'n':
                    expect("null");
                    return null;
                default:
                    return readNumber();
            }
        }

        private void expect(String literal) {
            if (!src.startsWith(literal, pos)) {
                throw new IllegalArgumentException("expected '" + literal + "' at offset " + pos);
            }
            pos += literal.length();
        }

        private Map<String, Object> readObject() {
            Map<String, Object> out = new LinkedHashMap<>();
            pos++; // '{'
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
                    throw new IllegalArgumentException("expected ':' at offset " + pos);
                }
                pos++;
                Object value = readValue();
                if (out.containsKey(key)) {
                    throw new IllegalArgumentException("duplicate key '" + key + "' in JSON object");
                }
                out.put(key, value);
                skipWhitespace();
                if (atEnd()) {
                    throw new IllegalArgumentException("unterminated JSON object");
                }
                char c = src.charAt(pos++);
                if (c == '}') {
                    return out;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("expected ',' or '}' at offset " + (pos - 1));
                }
            }
        }

        private List<Object> readArray() {
            List<Object> out = new ArrayList<>();
            pos++; // '['
            skipWhitespace();
            if (!atEnd() && src.charAt(pos) == ']') {
                pos++;
                return out;
            }
            while (true) {
                out.add(readValue());
                skipWhitespace();
                if (atEnd()) {
                    throw new IllegalArgumentException("unterminated JSON array");
                }
                char c = src.charAt(pos++);
                if (c == ']') {
                    return out;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("expected ',' or ']' at offset " + (pos - 1));
                }
            }
        }

        private String readString() {
            if (atEnd() || src.charAt(pos) != '"') {
                throw new IllegalArgumentException("expected '\"' at offset " + pos);
            }
            pos++;
            StringBuilder sb = new StringBuilder();
            while (true) {
                if (atEnd()) {
                    throw new IllegalArgumentException("unterminated JSON string");
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
                        throw new IllegalArgumentException("bad escape '\\" + esc + "' at offset " + (pos - 1));
                }
            }
        }

        private Object readNumber() {
            int start = pos;
            while (pos < src.length() && "+-0123456789.eE".indexOf(src.charAt(pos)) >= 0) {
                pos++;
            }
            String literal = src.substring(start, pos);
            if (literal.isEmpty()) {
                throw new IllegalArgumentException("unexpected character '" + src.charAt(pos) + "' at offset " + pos);
            }
            if (literal.indexOf('.') < 0 && literal.indexOf('e') < 0 && literal.indexOf('E') < 0) {
                return Long.parseLong(literal);
            }
            return Double.parseDouble(literal);
        }
    }

    // ---------------------------------------------------------------- writing

    public static String write(Object value) {
        StringBuilder sb = new StringBuilder();
        writeValue(value, sb);
        return sb.toString();
    }

    private static void writeValue(Object value, StringBuilder sb) {
        if (value == null) {
            sb.append("null");
        } else if (value instanceof Map) {
            sb.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> e : ((Map<?, ?>) value).entrySet()) {
                if (!first) {
                    sb.append(',');
                }
                first = false;
                writeString(String.valueOf(e.getKey()), sb);
                sb.append(':');
                writeValue(e.getValue(), sb);
            }
            sb.append('}');
        } else if (value instanceof List) {
            sb.append('[');
            boolean first = true;
            for (Object item : (List<?>) value) {
                if (!first) {
                    sb.append(',');
                }
                first = false;
                writeValue(item, sb);
            }
            sb.append(']');
        } else if (value instanceof Boolean || value instanceof Number) {
            sb.append(value);
        } else {
            writeString(String.valueOf(value), sb);
        }
    }

    private static void writeString(String s, StringBuilder sb) {
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

    /** Convenience builder for ordered JSON objects. */
    public static Map<String, Object> obj(Object... keysAndValues) {
        if (keysAndValues.length % 2 != 0) {
            throw new IllegalArgumentException("obj() needs an even number of arguments");
        }
        Map<String, Object> out = new LinkedHashMap<>();
        for (int i = 0; i < keysAndValues.length; i += 2) {
            out.put(String.valueOf(keysAndValues[i]), keysAndValues[i + 1]);
        }
        return out;
    }
}
