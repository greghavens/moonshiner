package com.example.vcf.harness;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal dependency-free JSON reader/writer.
 *
 * <p>Values map to: {@link LinkedHashMap} for objects, {@link List} for arrays, {@link String},
 * {@link Long} or {@link Double} for numbers, {@link Boolean}, and {@code null}.
 *
 * <p>This utility is part of the protected harness, but the client under test is free to use it.
 */
public final class MiniJson {

    private final String src;
    private int pos;

    private MiniJson(String src) {
        this.src = src;
    }

    /** Parses a complete JSON document. Throws {@link IllegalArgumentException} on malformed input. */
    public static Object parse(String text) {
        if (text == null) {
            throw new IllegalArgumentException("cannot parse null");
        }
        MiniJson p = new MiniJson(text);
        p.skipWs();
        Object value = p.readValue();
        p.skipWs();
        if (p.pos != p.src.length()) {
            throw new IllegalArgumentException("trailing content at offset " + p.pos);
        }
        return value;
    }

    /** Serialises a value produced by {@link #parse} (or built by hand) back to JSON text. */
    public static String write(Object value) {
        StringBuilder sb = new StringBuilder();
        writeInto(sb, value);
        return sb.toString();
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> asObject(Object value) {
        if (!(value instanceof Map)) {
            throw new IllegalArgumentException("expected a JSON object but got " + describe(value));
        }
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    public static List<Object> asArray(Object value) {
        if (!(value instanceof List)) {
            throw new IllegalArgumentException("expected a JSON array but got " + describe(value));
        }
        return (List<Object>) value;
    }

    public static String asString(Object value) {
        if (!(value instanceof String)) {
            throw new IllegalArgumentException("expected a JSON string but got " + describe(value));
        }
        return (String) value;
    }

    public static String describe(Object value) {
        if (value == null) {
            return "null";
        }
        if (value instanceof Map) {
            return "an object";
        }
        if (value instanceof List) {
            return "an array";
        }
        if (value instanceof String) {
            return "the string " + write(value);
        }
        return "the " + value.getClass().getSimpleName() + " " + write(value);
    }

    /** Convenience builder: {@code obj("a", 1, "b", 2)}. */
    public static Map<String, Object> obj(Object... keyValuePairs) {
        if (keyValuePairs.length % 2 != 0) {
            throw new IllegalArgumentException("obj() needs an even number of arguments");
        }
        Map<String, Object> map = new LinkedHashMap<>();
        for (int i = 0; i < keyValuePairs.length; i += 2) {
            map.put(String.valueOf(keyValuePairs[i]), keyValuePairs[i + 1]);
        }
        return map;
    }

    private static void writeInto(StringBuilder sb, Object value) {
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
                writeString(sb, String.valueOf(e.getKey()));
                sb.append(':');
                writeInto(sb, e.getValue());
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
                writeInto(sb, item);
            }
            sb.append(']');
        } else if (value instanceof String) {
            writeString(sb, (String) value);
        } else if (value instanceof Boolean || value instanceof Number) {
            sb.append(value);
        } else {
            writeString(sb, String.valueOf(value));
        }
    }

    private static void writeString(StringBuilder sb, String s) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"' -> sb.append("\\\"");
                case '\\' -> sb.append("\\\\");
                case '\n' -> sb.append("\\n");
                case '\r' -> sb.append("\\r");
                case '\t' -> sb.append("\\t");
                case '\b' -> sb.append("\\b");
                case '\f' -> sb.append("\\f");
                default -> {
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
                }
            }
        }
        sb.append('"');
    }

    private Object readValue() {
        if (pos >= src.length()) {
            throw new IllegalArgumentException("unexpected end of input");
        }
        char c = src.charAt(pos);
        return switch (c) {
            case '{' -> readObject();
            case '[' -> readArray();
            case '"' -> readString();
            case 't', 'f' -> readBoolean();
            case 'n' -> readNull();
            default -> readNumber();
        };
    }

    private Map<String, Object> readObject() {
        expect('{');
        Map<String, Object> map = new LinkedHashMap<>();
        skipWs();
        if (peek() == '}') {
            pos++;
            return map;
        }
        while (true) {
            skipWs();
            String key = readString();
            skipWs();
            expect(':');
            skipWs();
            map.put(key, readValue());
            skipWs();
            char c = peek();
            if (c == ',') {
                pos++;
                continue;
            }
            expect('}');
            return map;
        }
    }

    private List<Object> readArray() {
        expect('[');
        List<Object> list = new ArrayList<>();
        skipWs();
        if (peek() == ']') {
            pos++;
            return list;
        }
        while (true) {
            skipWs();
            list.add(readValue());
            skipWs();
            char c = peek();
            if (c == ',') {
                pos++;
                continue;
            }
            expect(']');
            return list;
        }
    }

    private String readString() {
        expect('"');
        StringBuilder sb = new StringBuilder();
        while (true) {
            if (pos >= src.length()) {
                throw new IllegalArgumentException("unterminated string");
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
                default -> throw new IllegalArgumentException("bad escape \\" + esc);
            }
        }
    }

    private Boolean readBoolean() {
        if (src.startsWith("true", pos)) {
            pos += 4;
            return Boolean.TRUE;
        }
        if (src.startsWith("false", pos)) {
            pos += 5;
            return Boolean.FALSE;
        }
        throw new IllegalArgumentException("bad literal at offset " + pos);
    }

    private Object readNull() {
        if (src.startsWith("null", pos)) {
            pos += 4;
            return null;
        }
        throw new IllegalArgumentException("bad literal at offset " + pos);
    }

    private Object readNumber() {
        int start = pos;
        while (pos < src.length() && "+-0123456789.eE".indexOf(src.charAt(pos)) >= 0) {
            pos++;
        }
        String text = src.substring(start, pos);
        if (text.isEmpty()) {
            throw new IllegalArgumentException("unexpected character '" + src.charAt(start) + "' at offset " + start);
        }
        if (text.indexOf('.') < 0 && text.indexOf('e') < 0 && text.indexOf('E') < 0) {
            return Long.parseLong(text);
        }
        return Double.parseDouble(text);
    }

    private void skipWs() {
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

    private void expect(char c) {
        if (pos >= src.length() || src.charAt(pos) != c) {
            throw new IllegalArgumentException("expected '" + c + "' at offset " + pos);
        }
        pos++;
    }
}
