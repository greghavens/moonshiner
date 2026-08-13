import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A small, dependency-free JSON reader and writer supplied with this exercise.
 *
 * <p>{@link #parse} maps JSON onto plain Java types:
 * <ul>
 *   <li>object -&gt; {@code LinkedHashMap<String, Object>} (insertion ordered)</li>
 *   <li>array -&gt; {@code ArrayList<Object>}</li>
 *   <li>string -&gt; {@code String}</li>
 *   <li>number -&gt; {@code Double}</li>
 *   <li>true / false -&gt; {@code Boolean}</li>
 *   <li>null -&gt; {@code null}</li>
 * </ul>
 *
 * <p>This file is provided. Do not edit it.
 */
public final class Json {

    private Json() {
    }

    /** Thrown when input is not well-formed JSON. */
    public static final class JsonException extends RuntimeException {
        public JsonException(String message) {
            super(message);
        }
    }

    public static Object parse(String text) {
        if (text == null) {
            throw new JsonException("no JSON text");
        }
        Parser parser = new Parser(text);
        parser.skipWhitespace();
        Object value = parser.readValue();
        parser.skipWhitespace();
        if (!parser.atEnd()) {
            throw new JsonException("trailing content at offset " + parser.pos);
        }
        return value;
    }

    public static String write(Object value) {
        StringBuilder out = new StringBuilder();
        writeValue(value, out);
        return out.toString();
    }

    private static void writeValue(Object value, StringBuilder out) {
        if (value == null) {
            out.append("null");
        } else if (value instanceof String s) {
            writeString(s, out);
        } else if (value instanceof Boolean b) {
            out.append(b.booleanValue());
        } else if (value instanceof Integer || value instanceof Long) {
            out.append(value);
        } else if (value instanceof Number n) {
            double d = n.doubleValue();
            if (!Double.isFinite(d)) {
                throw new JsonException("cannot serialise non-finite number " + d);
            }
            if (d == Math.rint(d) && d >= -0x1.0p63 && d < 0x1.0p63) {
                out.append((long) d);
            } else {
                out.append(d);
            }
        } else if (value instanceof Map<?, ?> map) {
            out.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                if (!first) {
                    out.append(',');
                }
                first = false;
                writeString(String.valueOf(entry.getKey()), out);
                out.append(':');
                writeValue(entry.getValue(), out);
            }
            out.append('}');
        } else if (value instanceof Iterable<?> items) {
            out.append('[');
            boolean first = true;
            for (Object item : items) {
                if (!first) {
                    out.append(',');
                }
                first = false;
                writeValue(item, out);
            }
            out.append(']');
        } else {
            throw new JsonException("cannot serialise " + value.getClass().getName());
        }
    }

    private static void writeString(String s, StringBuilder out) {
        out.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                case '\b' -> out.append("\\b");
                case '\f' -> out.append("\\f");
                default -> {
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
                }
            }
        }
        out.append('"');
    }

    private static final class Parser {
        private final String text;
        private int pos;

        Parser(String text) {
            this.text = text;
        }

        boolean atEnd() {
            return pos >= text.length();
        }

        void skipWhitespace() {
            while (pos < text.length()) {
                char c = text.charAt(pos);
                if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
                    pos++;
                } else {
                    break;
                }
            }
        }

        Object readValue() {
            if (atEnd()) {
                throw new JsonException("unexpected end of JSON input");
            }
            char c = text.charAt(pos);
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

        private Object readLiteral(String literal, Object value) {
            if (!text.startsWith(literal, pos)) {
                throw new JsonException("bad literal at offset " + pos);
            }
            pos += literal.length();
            return value;
        }

        private Map<String, Object> readObject() {
            expect('{');
            Map<String, Object> map = new LinkedHashMap<>();
            skipWhitespace();
            if (peek() == '}') {
                pos++;
                return map;
            }
            while (true) {
                skipWhitespace();
                String key = readString();
                skipWhitespace();
                expect(':');
                skipWhitespace();
                map.put(key, readValue());
                skipWhitespace();
                char c = peek();
                if (c == ',') {
                    pos++;
                } else if (c == '}') {
                    pos++;
                    return map;
                } else {
                    throw new JsonException("expected , or } at offset " + pos);
                }
            }
        }

        private List<Object> readArray() {
            expect('[');
            List<Object> list = new ArrayList<>();
            skipWhitespace();
            if (peek() == ']') {
                pos++;
                return list;
            }
            while (true) {
                skipWhitespace();
                list.add(readValue());
                skipWhitespace();
                char c = peek();
                if (c == ',') {
                    pos++;
                } else if (c == ']') {
                    pos++;
                    return list;
                } else {
                    throw new JsonException("expected , or ] at offset " + pos);
                }
            }
        }

        private String readString() {
            expect('"');
            StringBuilder out = new StringBuilder();
            while (true) {
                if (atEnd()) {
                    throw new JsonException("unterminated string");
                }
                char c = text.charAt(pos++);
                if (c == '"') {
                    return out.toString();
                }
                if (c != '\\') {
                    if (c < 0x20) {
                        throw new JsonException("unescaped control character at offset " + (pos - 1));
                    }
                    out.append(c);
                    continue;
                }
                if (atEnd()) {
                    throw new JsonException("unterminated escape");
                }
                char esc = text.charAt(pos++);
                switch (esc) {
                    case '"' -> out.append('"');
                    case '\\' -> out.append('\\');
                    case '/' -> out.append('/');
                    case 'b' -> out.append('\b');
                    case 'f' -> out.append('\f');
                    case 'n' -> out.append('\n');
                    case 'r' -> out.append('\r');
                    case 't' -> out.append('\t');
                    case 'u' -> {
                        if (pos + 4 > text.length()) {
                            throw new JsonException("truncated \\u escape");
                        }
                        out.append((char) Integer.parseInt(text.substring(pos, pos + 4), 16));
                        pos += 4;
                    }
                    default -> throw new JsonException("bad escape \\" + esc);
                }
            }
        }

        private Double readNumber() {
            int start = pos;
            if (peek() == '-') {
                pos++;
            }

            if (pos >= text.length()) {
                throw new JsonException("bad number at offset " + start);
            }
            char first = text.charAt(pos);
            if (first == '0') {
                pos++;
                if (pos < text.length() && Character.isDigit(text.charAt(pos))) {
                    throw new JsonException("leading zero in number at offset " + start);
                }
            } else if (first >= '1' && first <= '9') {
                do {
                    pos++;
                } while (pos < text.length() && Character.isDigit(text.charAt(pos)));
            } else {
                throw new JsonException("bad number at offset " + start);
            }

            if (pos < text.length() && text.charAt(pos) == '.') {
                pos++;
                int fractionStart = pos;
                while (pos < text.length() && Character.isDigit(text.charAt(pos))) {
                    pos++;
                }
                if (pos == fractionStart) {
                    throw new JsonException("fraction has no digits at offset " + start);
                }
            }

            if (pos < text.length() && (text.charAt(pos) == 'e' || text.charAt(pos) == 'E')) {
                pos++;
                if (pos < text.length() && (text.charAt(pos) == '+' || text.charAt(pos) == '-')) {
                    pos++;
                }
                int exponentStart = pos;
                while (pos < text.length() && Character.isDigit(text.charAt(pos))) {
                    pos++;
                }
                if (pos == exponentStart) {
                    throw new JsonException("exponent has no digits at offset " + start);
                }
            }

            String raw = text.substring(start, pos);
            try {
                double value = Double.parseDouble(raw);
                if (!Double.isFinite(value)) {
                    throw new JsonException("number is outside the supported range at offset " + start);
                }
                return value;
            } catch (NumberFormatException e) {
                throw new JsonException("bad number '" + raw + "' at offset " + start);
            }
        }

        private char peek() {
            if (atEnd()) {
                throw new JsonException("unexpected end of JSON input");
            }
            return text.charAt(pos);
        }

        private void expect(char expected) {
            if (atEnd() || text.charAt(pos) != expected) {
                throw new JsonException("expected '" + expected + "' at offset " + pos);
            }
            pos++;
        }
    }
}
