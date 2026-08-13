package com.broadcom.vcfa;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal JSON reader/writer with no third-party dependencies.
 *
 * <p>Provided as-is so that the client and the test harness agree on wire encoding. Do not modify;
 * the verifier restores this file from a pristine copy before it compiles anything.
 *
 * <p>Parsed values map to: {@code LinkedHashMap<String,Object>}, {@code ArrayList<Object>},
 * {@code String}, {@code Double}, {@code Boolean}, {@code null}. Written objects keep the insertion
 * order of the map they came from, which is what makes request bodies byte-comparable.
 */
public final class Json {

    private Json() {}

    // ---------------------------------------------------------------- writing

    /** Serialises a map/list/string/number/boolean/null tree to compact JSON. */
    public static String write(Object value) {
        StringBuilder sb = new StringBuilder();
        writeInto(sb, value);
        return sb.toString();
    }

    private static void writeInto(StringBuilder sb, Object value) {
        if (value == null) {
            sb.append("null");
        } else if (value instanceof String s) {
            writeString(sb, s);
        } else if (value instanceof Boolean b) {
            sb.append(b.booleanValue() ? "true" : "false");
        } else if (value instanceof Integer || value instanceof Long) {
            sb.append(value.toString());
        } else if (value instanceof Number n) {
            double d = n.doubleValue();
            if (d == Math.rint(d) && !Double.isInfinite(d)) {
                sb.append(Long.toString((long) d));
            } else {
                sb.append(Double.toString(d));
            }
        } else if (value instanceof Map<?, ?> m) {
            sb.append('{');
            boolean firstEntry = true;
            for (Map.Entry<?, ?> e : m.entrySet()) {
                if (!firstEntry) {
                    sb.append(',');
                }
                firstEntry = false;
                writeString(sb, String.valueOf(e.getKey()));
                sb.append(':');
                writeInto(sb, e.getValue());
            }
            sb.append('}');
        } else if (value instanceof List<?> l) {
            sb.append('[');
            for (int i = 0; i < l.size(); i++) {
                if (i > 0) {
                    sb.append(',');
                }
                writeInto(sb, l.get(i));
            }
            sb.append(']');
        } else {
            throw new IllegalArgumentException("cannot serialise " + value.getClass().getName());
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

    // ---------------------------------------------------------------- reading

    /** Parses a JSON document. Throws {@link IllegalArgumentException} on malformed input. */
    public static Object parse(String text) {
        Parser p = new Parser(text);
        p.skipWhitespace();
        Object v = p.readValue();
        p.skipWhitespace();
        if (p.pos != text.length()) {
            throw new IllegalArgumentException("trailing content at offset " + p.pos);
        }
        return v;
    }

    private static final class Parser {
        private final String src;
        private int pos;

        Parser(String src) {
            this.src = src;
        }

        void skipWhitespace() {
            while (pos < src.length() && Character.isWhitespace(src.charAt(pos))) {
                pos++;
            }
        }

        Object readValue() {
            if (pos >= src.length()) {
                throw new IllegalArgumentException("unexpected end of input");
            }
            char c = src.charAt(pos);
            return switch (c) {
                case '{' -> readObject();
                case '[' -> readArray();
                case '"' -> readString();
                case 't' -> readLiteral("true", Boolean.TRUE);
                case 'f' -> readLiteral("false", Boolean.FALSE);
                case 'n' -> readLiteral("null", null);
                default -> readNumber();
            };
        }

        Map<String, Object> readObject() {
            Map<String, Object> out = new LinkedHashMap<>();
            expect('{');
            skipWhitespace();
            if (peek() == '}') {
                pos++;
                return out;
            }
            while (true) {
                skipWhitespace();
                String key = readString();
                skipWhitespace();
                expect(':');
                skipWhitespace();
                out.put(key, readValue());
                skipWhitespace();
                char c = peek();
                if (c == ',') {
                    pos++;
                    continue;
                }
                expect('}');
                return out;
            }
        }

        List<Object> readArray() {
            List<Object> out = new ArrayList<>();
            expect('[');
            skipWhitespace();
            if (peek() == ']') {
                pos++;
                return out;
            }
            while (true) {
                skipWhitespace();
                out.add(readValue());
                skipWhitespace();
                char c = peek();
                if (c == ',') {
                    pos++;
                    continue;
                }
                expect(']');
                return out;
            }
        }

        String readString() {
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

        Object readNumber() {
            int start = pos;
            if (peek() == '-') {
                pos++;
            }
            while (pos < src.length() && "0123456789+-.eE".indexOf(src.charAt(pos)) >= 0) {
                pos++;
            }
            if (start == pos) {
                throw new IllegalArgumentException("unexpected character at offset " + pos);
            }
            return Double.valueOf(src.substring(start, pos));
        }

        Object readLiteral(String literal, Object value) {
            if (!src.startsWith(literal, pos)) {
                throw new IllegalArgumentException("unexpected character at offset " + pos);
            }
            pos += literal.length();
            return value;
        }

        char peek() {
            if (pos >= src.length()) {
                throw new IllegalArgumentException("unexpected end of input");
            }
            return src.charAt(pos);
        }

        void expect(char c) {
            if (pos >= src.length() || src.charAt(pos) != c) {
                throw new IllegalArgumentException("expected '" + c + "' at offset " + pos);
            }
            pos++;
        }
    }

    // ------------------------------------------------------------- convenience

    /** Casts a parsed value to a JSON object. */
    @SuppressWarnings("unchecked")
    public static Map<String, Object> asObject(Object value) {
        if (!(value instanceof Map)) {
            throw new IllegalArgumentException("expected a JSON object, got " + describe(value));
        }
        return (Map<String, Object>) value;
    }

    /** Casts a parsed value to a JSON array. */
    @SuppressWarnings("unchecked")
    public static List<Object> asArray(Object value) {
        if (!(value instanceof List)) {
            throw new IllegalArgumentException("expected a JSON array, got " + describe(value));
        }
        return (List<Object>) value;
    }

    /** Reads a string member, or null when absent or JSON null. */
    public static String optString(Map<String, Object> object, String key) {
        Object v = object.get(key);
        return v == null ? null : String.valueOf(v);
    }

    /** Reads an integer member, or {@code fallback} when absent or JSON null. */
    public static int optInt(Map<String, Object> object, String key, int fallback) {
        Object v = object.get(key);
        return v instanceof Number n ? n.intValue() : fallback;
    }

    private static String describe(Object value) {
        return value == null ? "null" : value.getClass().getSimpleName();
    }
}
