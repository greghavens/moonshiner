import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Small JSON codec used only by the protected harness and mock. */
final class TestJson {
    private TestJson() {
    }

    static Object parse(String text) {
        Parser parser = new Parser(text);
        Object value = parser.value();
        parser.ws();
        if (parser.pos != text.length()) {
            throw new IllegalArgumentException("trailing JSON");
        }
        return value;
    }

    static String stringify(Object value) {
        StringBuilder out = new StringBuilder();
        write(value, out);
        return out.toString();
    }

    @SuppressWarnings("unchecked")
    private static void write(Object value, StringBuilder out) {
        if (value == null) {
            out.append("null");
        } else if (value instanceof String string) {
            quote(string, out);
        } else if (value instanceof Boolean || value instanceof Number) {
            out.append(value);
        } else if (value instanceof Map<?, ?> map) {
            out.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                if (!first) out.append(',');
                first = false;
                quote((String) entry.getKey(), out);
                out.append(':');
                write(entry.getValue(), out);
            }
            out.append('}');
        } else if (value instanceof Iterable<?> iterable) {
            out.append('[');
            boolean first = true;
            for (Object item : iterable) {
                if (!first) out.append(',');
                first = false;
                write(item, out);
            }
            out.append(']');
        } else {
            throw new IllegalArgumentException("unsupported JSON value");
        }
    }

    private static void quote(String text, StringBuilder out) {
        out.append('"');
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            switch (c) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\b' -> out.append("\\b");
                case '\f' -> out.append("\\f");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                default -> {
                    if (c < 0x20) out.append(String.format("\\u%04x", (int) c));
                    else out.append(c);
                }
            }
        }
        out.append('"');
    }

    private static final class Parser {
        private final String text;
        private int pos;

        private Parser(String text) {
            this.text = text;
        }

        private Object value() {
            ws();
            if (pos >= text.length()) throw fail();
            return switch (text.charAt(pos)) {
                case '{' -> object();
                case '[' -> array();
                case '"' -> string();
                case 't' -> literal("true", Boolean.TRUE);
                case 'f' -> literal("false", Boolean.FALSE);
                case 'n' -> literal("null", null);
                default -> number();
            };
        }

        private Map<String, Object> object() {
            pos++;
            LinkedHashMap<String, Object> result = new LinkedHashMap<>();
            ws();
            if (take('}')) return result;
            while (true) {
                ws();
                String key = string();
                ws();
                require(':');
                if (result.putIfAbsent(key, value()) != null) throw fail();
                ws();
                if (take('}')) return result;
                require(',');
            }
        }

        private List<Object> array() {
            pos++;
            ArrayList<Object> result = new ArrayList<>();
            ws();
            if (take(']')) return result;
            while (true) {
                result.add(value());
                ws();
                if (take(']')) return result;
                require(',');
            }
        }

        private String string() {
            require('"');
            StringBuilder out = new StringBuilder();
            while (pos < text.length()) {
                char c = text.charAt(pos++);
                if (c == '"') return out.toString();
                if (c != '\\') {
                    if (c < 0x20) throw fail();
                    out.append(c);
                    continue;
                }
                if (pos >= text.length()) throw fail();
                char escape = text.charAt(pos++);
                switch (escape) {
                    case '"', '\\', '/' -> out.append(escape);
                    case 'b' -> out.append('\b');
                    case 'f' -> out.append('\f');
                    case 'n' -> out.append('\n');
                    case 'r' -> out.append('\r');
                    case 't' -> out.append('\t');
                    case 'u' -> {
                        if (pos + 4 > text.length()) throw fail();
                        try {
                            out.append((char) Integer.parseInt(text.substring(pos, pos + 4), 16));
                        } catch (NumberFormatException e) {
                            throw fail();
                        }
                        pos += 4;
                    }
                    default -> throw fail();
                }
            }
            throw fail();
        }

        private Object number() {
            int start = pos;
            if (take('-')) {
                // sign consumed
            }
            while (pos < text.length() && Character.isDigit(text.charAt(pos))) pos++;
            if (take('.')) while (pos < text.length() && Character.isDigit(text.charAt(pos))) pos++;
            if (pos < text.length() && (text.charAt(pos) == 'e' || text.charAt(pos) == 'E')) {
                pos++;
                if (pos < text.length() && (text.charAt(pos) == '+' || text.charAt(pos) == '-')) pos++;
                while (pos < text.length() && Character.isDigit(text.charAt(pos))) pos++;
            }
            if (start == pos) throw fail();
            String raw = text.substring(start, pos);
            try {
                return raw.indexOf('.') >= 0 || raw.indexOf('e') >= 0 || raw.indexOf('E') >= 0
                        ? Double.valueOf(raw) : Long.valueOf(raw);
            } catch (NumberFormatException e) {
                throw fail();
            }
        }

        private Object literal(String expected, Object value) {
            if (!text.startsWith(expected, pos)) throw fail();
            pos += expected.length();
            return value;
        }

        private void ws() {
            while (pos < text.length() && " \t\r\n".indexOf(text.charAt(pos)) >= 0) pos++;
        }

        private boolean take(char c) {
            if (pos < text.length() && text.charAt(pos) == c) {
                pos++;
                return true;
            }
            return false;
        }

        private void require(char c) {
            if (!take(c)) throw fail();
        }

        private IllegalArgumentException fail() {
            return new IllegalArgumentException("invalid JSON at " + pos);
        }
    }
}
