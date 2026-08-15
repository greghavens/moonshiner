import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Generates the pinned VCF management-component migration architecture. */
public final class MigrationPlanner {
    private MigrationPlanner() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 3) {
            throw new IllegalArgumentException(
                    "usage: MigrationPlanner <estate.json> <compatibility-snapshot.json> <output.json>");
        }
        Map<String, Object> estate = Json.object(Json.parse(Files.readString(
                Path.of(args[0]), StandardCharsets.UTF_8)));
        Map<String, Object> snapshot = Json.object(Json.parse(Files.readString(
                Path.of(args[1]), StandardCharsets.UTF_8)));
        Map<String, Object> plan = buildPlan(estate, snapshot);
        Files.writeString(Path.of(args[2]), Json.stringify(plan) + System.lineSeparator(),
                StandardCharsets.UTF_8);
    }

    static Map<String, Object> buildPlan(Map<String, Object> estate,
                                         Map<String, Object> snapshot) {
        throw new UnsupportedOperationException("migration architecture is not implemented");
    }

    /** Tiny dependency-free JSON reader/writer for the supplied fixtures and output. */
    static final class Json {
        private final String input;
        private int position;

        private Json(String input) {
            this.input = input;
        }

        static Object parse(String input) {
            Json parser = new Json(input);
            Object value = parser.value();
            parser.whitespace();
            if (parser.position != input.length()) {
                throw parser.error("trailing input");
            }
            return value;
        }

        @SuppressWarnings("unchecked")
        static Map<String, Object> object(Object value) {
            if (!(value instanceof Map<?, ?>)) {
                throw new IllegalArgumentException("expected JSON object");
            }
            return (Map<String, Object>) value;
        }

        @SuppressWarnings("unchecked")
        static List<Object> array(Object value) {
            if (!(value instanceof List<?>)) {
                throw new IllegalArgumentException("expected JSON array");
            }
            return (List<Object>) value;
        }

        static String string(Object value) {
            if (!(value instanceof String text)) {
                throw new IllegalArgumentException("expected JSON string");
            }
            return text;
        }

        static int integer(Object value) {
            if (!(value instanceof Number number)) {
                throw new IllegalArgumentException("expected JSON number");
            }
            return number.intValue();
        }

        static String stringify(Object value) {
            StringBuilder output = new StringBuilder();
            write(value, output);
            return output.toString();
        }

        private Object value() {
            whitespace();
            if (position >= input.length()) throw error("expected value");
            return switch (input.charAt(position)) {
                case '{' -> objectValue();
                case '[' -> arrayValue();
                case '"' -> stringValue();
                case 't' -> literal("true", Boolean.TRUE);
                case 'f' -> literal("false", Boolean.FALSE);
                case 'n' -> literal("null", null);
                default -> numberValue();
            };
        }

        private Map<String, Object> objectValue() {
            position++;
            LinkedHashMap<String, Object> result = new LinkedHashMap<>();
            whitespace();
            if (take('}')) return result;
            while (true) {
                whitespace();
                if (position >= input.length() || input.charAt(position) != '"') {
                    throw error("expected object key");
                }
                String key = stringValue();
                whitespace();
                expect(':');
                result.put(key, value());
                whitespace();
                if (take('}')) return result;
                expect(',');
            }
        }

        private List<Object> arrayValue() {
            position++;
            ArrayList<Object> result = new ArrayList<>();
            whitespace();
            if (take(']')) return result;
            while (true) {
                result.add(value());
                whitespace();
                if (take(']')) return result;
                expect(',');
            }
        }

        private String stringValue() {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (position < input.length()) {
                char current = input.charAt(position++);
                if (current == '"') return result.toString();
                if (current != '\\') {
                    if (current < 0x20) throw error("control character in string");
                    result.append(current);
                    continue;
                }
                if (position >= input.length()) throw error("incomplete escape");
                char escaped = input.charAt(position++);
                switch (escaped) {
                    case '"', '\\', '/' -> result.append(escaped);
                    case 'b' -> result.append('\b');
                    case 'f' -> result.append('\f');
                    case 'n' -> result.append('\n');
                    case 'r' -> result.append('\r');
                    case 't' -> result.append('\t');
                    case 'u' -> {
                        if (position + 4 > input.length()) throw error("incomplete unicode escape");
                        result.append((char) Integer.parseInt(input.substring(position, position + 4), 16));
                        position += 4;
                    }
                    default -> throw error("invalid escape");
                }
            }
            throw error("unterminated string");
        }

        private Object numberValue() {
            int start = position;
            if (take('-')) {}
            while (position < input.length() && Character.isDigit(input.charAt(position))) position++;
            boolean decimal = false;
            if (take('.')) {
                decimal = true;
                while (position < input.length() && Character.isDigit(input.charAt(position))) position++;
            }
            if (position < input.length() && (input.charAt(position) == 'e' || input.charAt(position) == 'E')) {
                decimal = true;
                position++;
                if (position < input.length() && (input.charAt(position) == '+' || input.charAt(position) == '-')) position++;
                while (position < input.length() && Character.isDigit(input.charAt(position))) position++;
            }
            if (start == position) throw error("expected number");
            String text = input.substring(start, position);
            try {
                return decimal ? Double.valueOf(text) : Long.valueOf(text);
            } catch (NumberFormatException failure) {
                throw error("invalid number");
            }
        }

        private Object literal(String text, Object value) {
            if (!input.startsWith(text, position)) throw error("invalid literal");
            position += text.length();
            return value;
        }

        private void whitespace() {
            while (position < input.length() && Character.isWhitespace(input.charAt(position))) position++;
        }

        private boolean take(char expected) {
            if (position < input.length() && input.charAt(position) == expected) {
                position++;
                return true;
            }
            return false;
        }

        private void expect(char expected) {
            if (!take(expected)) throw error("expected '" + expected + "'");
        }

        private IllegalArgumentException error(String message) {
            return new IllegalArgumentException(message + " at character " + position);
        }

        private static void write(Object value, StringBuilder output) {
            if (value == null) {
                output.append("null");
            } else if (value instanceof String text) {
                quote(text, output);
            } else if (value instanceof Number || value instanceof Boolean) {
                output.append(value);
            } else if (value instanceof Map<?, ?> map) {
                output.append('{');
                boolean first = true;
                for (Map.Entry<?, ?> entry : map.entrySet()) {
                    if (!first) output.append(',');
                    first = false;
                    quote(String.valueOf(entry.getKey()), output);
                    output.append(':');
                    write(entry.getValue(), output);
                }
                output.append('}');
            } else if (value instanceof List<?> list) {
                output.append('[');
                for (int index = 0; index < list.size(); index++) {
                    if (index > 0) output.append(',');
                    write(list.get(index), output);
                }
                output.append(']');
            } else {
                throw new IllegalArgumentException("cannot encode " + value.getClass());
            }
        }

        private static void quote(String text, StringBuilder output) {
            output.append('"');
            for (int index = 0; index < text.length(); index++) {
                char current = text.charAt(index);
                switch (current) {
                    case '"' -> output.append("\\\"");
                    case '\\' -> output.append("\\\\");
                    case '\b' -> output.append("\\b");
                    case '\f' -> output.append("\\f");
                    case '\n' -> output.append("\\n");
                    case '\r' -> output.append("\\r");
                    case '\t' -> output.append("\\t");
                    default -> {
                        if (current < 0x20) output.append(String.format("\\u%04x", (int) current));
                        else output.append(current);
                    }
                }
            }
            output.append('"');
        }
    }
}
