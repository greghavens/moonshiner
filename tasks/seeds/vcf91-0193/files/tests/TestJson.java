import java.io.IOException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Strict, dependency-free JSON helper used only by the protected harness. */
final class TestJson {
    private TestJson() {
    }

    static Object parse(String source) throws IOException {
        return new Parser(source).document();
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> object(Object value) throws IOException {
        if (!(value instanceof Map<?, ?> object)) {
            throw new IOException("expected JSON object");
        }
        for (Object key : object.keySet()) {
            if (!(key instanceof String)) {
                throw new IOException("JSON object key is not a string");
            }
        }
        return (Map<String, Object>) object;
    }

    @SuppressWarnings("unchecked")
    static List<Object> array(Object value) throws IOException {
        if (!(value instanceof List<?> array)) {
            throw new IOException("expected JSON array");
        }
        return (List<Object>) array;
    }

    static String write(Object value) throws IOException {
        StringBuilder out = new StringBuilder();
        writeValue(value, out);
        return out.toString();
    }

    private static void writeValue(Object value, StringBuilder out) throws IOException {
        if (value == null) {
            out.append("null");
        } else if (value instanceof String text) {
            quote(text, out);
        } else if (value instanceof Boolean
                || value instanceof Byte
                || value instanceof Short
                || value instanceof Integer
                || value instanceof Long) {
            out.append(value);
        } else if (value instanceof Map<?, ?> object) {
            out.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> entry : object.entrySet()) {
                if (!(entry.getKey() instanceof String key)) {
                    throw new IOException("JSON object key is not a string");
                }
                if (!first) {
                    out.append(',');
                }
                first = false;
                quote(key, out);
                out.append(':');
                writeValue(entry.getValue(), out);
            }
            out.append('}');
        } else if (value instanceof Iterable<?> array) {
            out.append('[');
            boolean first = true;
            for (Object item : array) {
                if (!first) {
                    out.append(',');
                }
                first = false;
                writeValue(item, out);
            }
            out.append(']');
        } else {
            throw new IOException("unsupported JSON value");
        }
    }

    private static void quote(String text, StringBuilder out) {
        out.append('"');
        for (int index = 0; index < text.length(); index++) {
            char c = text.charAt(index);
            switch (c) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\b' -> out.append("\\b");
                case '\f' -> out.append("\\f");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
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
        private final String source;
        private int at;

        Parser(String source) {
            this.source = source;
        }

        Object document() throws IOException {
            Object value = value();
            whitespace();
            if (at != source.length()) {
                throw malformed();
            }
            return value;
        }

        private Object value() throws IOException {
            whitespace();
            if (at >= source.length()) {
                throw malformed();
            }
            return switch (source.charAt(at)) {
                case '{' -> object();
                case '[' -> array();
                case '"' -> string();
                case 't' -> literal("true", Boolean.TRUE);
                case 'f' -> literal("false", Boolean.FALSE);
                case 'n' -> literal("null", null);
                default -> number();
            };
        }

        private Map<String, Object> object() throws IOException {
            at++;
            LinkedHashMap<String, Object> result = new LinkedHashMap<>();
            whitespace();
            if (take('}')) {
                return result;
            }
            while (true) {
                whitespace();
                if (at >= source.length() || source.charAt(at) != '"') {
                    throw malformed();
                }
                String key = string();
                whitespace();
                require(':');
                if (result.containsKey(key)) {
                    throw malformed();
                }
                result.put(key, value());
                whitespace();
                if (take('}')) {
                    return result;
                }
                require(',');
            }
        }

        private List<Object> array() throws IOException {
            at++;
            ArrayList<Object> result = new ArrayList<>();
            whitespace();
            if (take(']')) {
                return result;
            }
            while (true) {
                result.add(value());
                whitespace();
                if (take(']')) {
                    return result;
                }
                require(',');
            }
        }

        private String string() throws IOException {
            require('"');
            StringBuilder result = new StringBuilder();
            while (at < source.length()) {
                char c = source.charAt(at++);
                if (c == '"') {
                    return result.toString();
                }
                if (c == '\\') {
                    if (at >= source.length()) {
                        throw malformed();
                    }
                    char escaped = source.charAt(at++);
                    switch (escaped) {
                        case '"', '\\', '/' -> result.append(escaped);
                        case 'b' -> result.append('\b');
                        case 'f' -> result.append('\f');
                        case 'n' -> result.append('\n');
                        case 'r' -> result.append('\r');
                        case 't' -> result.append('\t');
                        case 'u' -> result.append(unicode());
                        default -> throw malformed();
                    }
                } else {
                    if (c < 0x20) {
                        throw malformed();
                    }
                    result.append(c);
                }
            }
            throw malformed();
        }

        private char unicode() throws IOException {
            if (at + 4 > source.length()) {
                throw malformed();
            }
            try {
                char result = (char) Integer.parseInt(source.substring(at, at + 4), 16);
                at += 4;
                return result;
            } catch (NumberFormatException badHex) {
                throw malformed();
            }
        }

        private Object number() throws IOException {
            int start = at;
            take('-');
            if (take('0')) {
                // zero consumed
            } else {
                digits();
            }
            boolean fractional = false;
            if (take('.')) {
                fractional = true;
                digits();
            }
            if (at < source.length()
                    && (source.charAt(at) == 'e' || source.charAt(at) == 'E')) {
                fractional = true;
                at++;
                if (!take('+')) {
                    take('-');
                }
                digits();
            }
            if (start == at) {
                throw malformed();
            }
            try {
                String token = source.substring(start, at);
                if (fractional) {
                    return Double.valueOf(token);
                }
                return Long.valueOf(token);
            } catch (NumberFormatException badNumber) {
                throw malformed();
            }
        }

        private void digits() throws IOException {
            int start = at;
            while (at < source.length() && Character.isDigit(source.charAt(at))) {
                at++;
            }
            if (start == at) {
                throw malformed();
            }
        }

        private Object literal(String expected, Object value) throws IOException {
            if (!source.startsWith(expected, at)) {
                throw malformed();
            }
            at += expected.length();
            return value;
        }

        private boolean take(char expected) {
            if (at < source.length() && source.charAt(at) == expected) {
                at++;
                return true;
            }
            return false;
        }

        private void require(char expected) throws IOException {
            if (!take(expected)) {
                throw malformed();
            }
        }

        private void whitespace() {
            while (at < source.length()) {
                char c = source.charAt(at);
                if (c != ' ' && c != '\n' && c != '\r' && c != '\t') {
                    return;
                }
                at++;
            }
        }

        private IOException malformed() {
            return new IOException("malformed JSON");
        }
    }
}
