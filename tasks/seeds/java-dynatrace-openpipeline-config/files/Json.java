import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal JSON support for the pipeline toolkit: parses into
 * LinkedHashMap/ArrayList/String/Double/Boolean/null and writes those
 * shapes back out. Numbers always parse as Double.
 */
public final class Json {

    private Json() {
    }

    public static Object parse(String text) {
        Cursor c = new Cursor(text);
        Object value = c.readValue();
        c.skipWs();
        if (c.pos < c.text.length()) {
            throw new IllegalArgumentException("trailing JSON at " + c.pos);
        }
        return value;
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> parseObject(String text) {
        return (Map<String, Object>) parse(text);
    }

    public static String write(Object value) {
        StringBuilder sb = new StringBuilder();
        append(value, sb);
        return sb.toString();
    }

    private static void append(Object value, StringBuilder sb) {
        if (value == null) {
            sb.append("null");
            return;
        }
        if (value instanceof String s) {
            appendString(s, sb);
        } else if (value instanceof Double d) {
            if (d == Math.rint(d) && !d.isInfinite() && Math.abs(d) < 1e15) {
                sb.append((long) (double) d);
            } else {
                sb.append(d);
            }
        } else if (value instanceof Number || value instanceof Boolean) {
            sb.append(value);
        } else if (value instanceof Map<?, ?> map) {
            sb.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> e : map.entrySet()) {
                if (!first) {
                    sb.append(',');
                }
                first = false;
                appendString(String.valueOf(e.getKey()), sb);
                sb.append(':');
                append(e.getValue(), sb);
            }
            sb.append('}');
        } else if (value instanceof List<?> list) {
            sb.append('[');
            for (int i = 0; i < list.size(); i++) {
                if (i > 0) {
                    sb.append(',');
                }
                append(list.get(i), sb);
            }
            sb.append(']');
        } else {
            throw new IllegalArgumentException(
                "cannot serialize " + value.getClass().getName());
        }
    }

    private static void appendString(String s, StringBuilder sb) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"' -> sb.append("\\\"");
                case '\\' -> sb.append("\\\\");
                case '\n' -> sb.append("\\n");
                case '\r' -> sb.append("\\r");
                case '\t' -> sb.append("\\t");
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

    private static final class Cursor {
        final String text;
        int pos;

        Cursor(String text) {
            this.text = text;
        }

        void skipWs() {
            while (pos < text.length() && Character.isWhitespace(text.charAt(pos))) {
                pos++;
            }
        }

        char peek() {
            if (pos >= text.length()) {
                throw new IllegalArgumentException("unexpected end of JSON");
            }
            return text.charAt(pos);
        }

        void expect(char c) {
            if (peek() != c) {
                throw new IllegalArgumentException(
                    "expected '" + c + "' at " + pos + ", got '" + peek() + "'");
            }
            pos++;
        }

        Object readValue() {
            skipWs();
            char c = peek();
            return switch (c) {
                case '{' -> readObject();
                case '[' -> readArray();
                case '"' -> readString();
                case 't', 'f' -> readBoolean();
                case 'n' -> readNull();
                default -> readNumber();
            };
        }

        Map<String, Object> readObject() {
            expect('{');
            Map<String, Object> out = new LinkedHashMap<>();
            skipWs();
            if (peek() == '}') {
                pos++;
                return out;
            }
            while (true) {
                skipWs();
                String key = readString();
                skipWs();
                expect(':');
                out.put(key, readValue());
                skipWs();
                if (peek() == ',') {
                    pos++;
                    continue;
                }
                expect('}');
                return out;
            }
        }

        List<Object> readArray() {
            expect('[');
            List<Object> out = new ArrayList<>();
            skipWs();
            if (peek() == ']') {
                pos++;
                return out;
            }
            while (true) {
                out.add(readValue());
                skipWs();
                if (peek() == ',') {
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
                char c = text.charAt(pos++);
                if (c == '"') {
                    return sb.toString();
                }
                if (c == '\\') {
                    char esc = text.charAt(pos++);
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
                            sb.append((char) Integer.parseInt(
                                text.substring(pos, pos + 4), 16));
                            pos += 4;
                        }
                        default -> throw new IllegalArgumentException(
                            "bad escape \\" + esc);
                    }
                } else {
                    sb.append(c);
                }
            }
        }

        Boolean readBoolean() {
            if (text.startsWith("true", pos)) {
                pos += 4;
                return Boolean.TRUE;
            }
            if (text.startsWith("false", pos)) {
                pos += 5;
                return Boolean.FALSE;
            }
            throw new IllegalArgumentException("bad literal at " + pos);
        }

        Object readNull() {
            if (text.startsWith("null", pos)) {
                pos += 4;
                return null;
            }
            throw new IllegalArgumentException("bad literal at " + pos);
        }

        Double readNumber() {
            int start = pos;
            while (pos < text.length()
                    && "+-0123456789.eE".indexOf(text.charAt(pos)) >= 0) {
                pos++;
            }
            return Double.parseDouble(text.substring(start, pos));
        }
    }
}
