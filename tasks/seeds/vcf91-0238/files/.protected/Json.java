import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal JSON reader/writer supplied by the harness so that the client under test can stay a
 * single file and depend on nothing outside the JDK.
 *
 * parse() yields LinkedHashMap&lt;String,Object&gt;, ArrayList&lt;Object&gt;, String, Long, Double,
 * Boolean or null. Integral numbers become Long, everything else Double.
 *
 * write() emits compact JSON (no insignificant whitespace) and preserves Map iteration order, so a
 * LinkedHashMap round-trips with its key order intact.
 */
public final class Json {

    private Json() {
    }

    // ---------------------------------------------------------------- parsing

    public static Object parse(String text) {
        P p = new P(text);
        p.ws();
        Object v = p.value();
        p.ws();
        if (p.i != p.s.length()) {
            throw new IllegalArgumentException("trailing content at offset " + p.i);
        }
        return v;
    }

    private static final class P {
        private final String s;
        private int i;

        P(String s) {
            this.s = s;
        }

        void ws() {
            while (i < s.length()) {
                char c = s.charAt(i);
                if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
                    i++;
                } else {
                    break;
                }
            }
        }

        char peek() {
            if (i >= s.length()) {
                throw new IllegalArgumentException("unexpected end of input");
            }
            return s.charAt(i);
        }

        void expect(char c) {
            if (peek() != c) {
                throw new IllegalArgumentException("expected '" + c + "' at offset " + i);
            }
            i++;
        }

        Object value() {
            char c = peek();
            switch (c) {
                case '{':
                    return object();
                case '[':
                    return array();
                case '"':
                    return string();
                case 't':
                    lit("true");
                    return Boolean.TRUE;
                case 'f':
                    lit("false");
                    return Boolean.FALSE;
                case 'n':
                    lit("null");
                    return null;
                default:
                    return number();
            }
        }

        void lit(String w) {
            if (!s.startsWith(w, i)) {
                throw new IllegalArgumentException("bad literal at offset " + i);
            }
            i += w.length();
        }

        Map<String, Object> object() {
            expect('{');
            Map<String, Object> m = new LinkedHashMap<>();
            ws();
            if (peek() == '}') {
                i++;
                return m;
            }
            while (true) {
                ws();
                String k = string();
                ws();
                expect(':');
                ws();
                m.put(k, value());
                ws();
                char c = peek();
                i++;
                if (c == '}') {
                    return m;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("expected ',' or '}' at offset " + (i - 1));
                }
            }
        }

        List<Object> array() {
            expect('[');
            List<Object> l = new ArrayList<>();
            ws();
            if (peek() == ']') {
                i++;
                return l;
            }
            while (true) {
                ws();
                l.add(value());
                ws();
                char c = peek();
                i++;
                if (c == ']') {
                    return l;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("expected ',' or ']' at offset " + (i - 1));
                }
            }
        }

        String string() {
            expect('"');
            StringBuilder b = new StringBuilder();
            while (true) {
                char c = peek();
                i++;
                if (c == '"') {
                    return b.toString();
                }
                if (c != '\\') {
                    b.append(c);
                    continue;
                }
                char e = peek();
                i++;
                switch (e) {
                    case '"':
                        b.append('"');
                        break;
                    case '\\':
                        b.append('\\');
                        break;
                    case '/':
                        b.append('/');
                        break;
                    case 'b':
                        b.append('\b');
                        break;
                    case 'f':
                        b.append('\f');
                        break;
                    case 'n':
                        b.append('\n');
                        break;
                    case 'r':
                        b.append('\r');
                        break;
                    case 't':
                        b.append('\t');
                        break;
                    case 'u':
                        b.append((char) Integer.parseInt(s.substring(i, i + 4), 16));
                        i += 4;
                        break;
                    default:
                        throw new IllegalArgumentException("bad escape at offset " + (i - 1));
                }
            }
        }

        Object number() {
            int start = i;
            if (i < s.length() && (s.charAt(i) == '-' || s.charAt(i) == '+')) {
                i++;
            }
            boolean fp = false;
            while (i < s.length()) {
                char c = s.charAt(i);
                if (c >= '0' && c <= '9') {
                    i++;
                } else if (c == '.' || c == 'e' || c == 'E' || c == '+' || c == '-') {
                    fp = fp || c == '.' || c == 'e' || c == 'E';
                    i++;
                } else {
                    break;
                }
            }
            String raw = s.substring(start, i);
            if (raw.isEmpty()) {
                throw new IllegalArgumentException("bad number at offset " + start);
            }
            if (!fp) {
                try {
                    return Long.valueOf(raw);
                } catch (NumberFormatException ignored) {
                    // fall through to double
                }
            }
            return Double.valueOf(raw);
        }
    }

    // ---------------------------------------------------------------- writing

    public static String write(Object v) {
        StringBuilder b = new StringBuilder();
        emit(v, b);
        return b.toString();
    }

    private static void emit(Object v, StringBuilder b) {
        if (v == null) {
            b.append("null");
        } else if (v instanceof String) {
            quote((String) v, b);
        } else if (v instanceof Boolean) {
            b.append(v.toString());
        } else if (v instanceof Long || v instanceof Integer || v instanceof Short
                || v instanceof Byte) {
            b.append(v.toString());
        } else if (v instanceof Double || v instanceof Float) {
            double d = ((Number) v).doubleValue();
            if (d == Math.rint(d) && !Double.isInfinite(d)) {
                b.append(Long.toString((long) d));
            } else {
                b.append(Double.toString(d));
            }
        } else if (v instanceof Map) {
            b.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) {
                if (!first) {
                    b.append(',');
                }
                first = false;
                quote(String.valueOf(e.getKey()), b);
                b.append(':');
                emit(e.getValue(), b);
            }
            b.append('}');
        } else if (v instanceof Iterable) {
            b.append('[');
            boolean first = true;
            for (Object e : (Iterable<?>) v) {
                if (!first) {
                    b.append(',');
                }
                first = false;
                emit(e, b);
            }
            b.append(']');
        } else {
            throw new IllegalArgumentException("cannot serialize " + v.getClass().getName());
        }
    }

    private static void quote(String s, StringBuilder b) {
        b.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"':
                    b.append("\\\"");
                    break;
                case '\\':
                    b.append("\\\\");
                    break;
                case '\n':
                    b.append("\\n");
                    break;
                case '\r':
                    b.append("\\r");
                    break;
                case '\t':
                    b.append("\\t");
                    break;
                case '\b':
                    b.append("\\b");
                    break;
                case '\f':
                    b.append("\\f");
                    break;
                default:
                    if (c < 0x20) {
                        b.append(String.format("\\u%04x", (int) c));
                    } else {
                        b.append(c);
                    }
            }
        }
        b.append('"');
    }

    // ------------------------------------------------------------ convenience

    @SuppressWarnings("unchecked")
    public static Map<String, Object> obj(Object v) {
        return v == null ? null : (Map<String, Object>) v;
    }

    @SuppressWarnings("unchecked")
    public static List<Object> arr(Object v) {
        return v == null ? null : (List<Object>) v;
    }

    public static String str(Object v) {
        return v == null ? null : (String) v;
    }

    public static Long num(Object v) {
        if (v == null) {
            return null;
        }
        return ((Number) v).longValue();
    }
}
