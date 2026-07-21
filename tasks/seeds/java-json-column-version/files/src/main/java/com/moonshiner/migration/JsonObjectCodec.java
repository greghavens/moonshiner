package com.moonshiner.migration;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Small dependency-free JSON codec used by the migration fixture. */
final class JsonObjectCodec {
    Map<String, Object> parseObject(String json) {
        if (json == null) {
            throw new IllegalArgumentException("JSON document is null");
        }
        Parser parser = new Parser(json);
        Object value = parser.parseValue();
        parser.skipWhitespace();
        if (!parser.atEnd()) {
            throw parser.error("trailing content");
        }
        if (!(value instanceof Map)) {
            throw new IllegalArgumentException("JSON document must be an object");
        }
        @SuppressWarnings("unchecked")
        Map<String, Object> object = (Map<String, Object>) value;
        return object;
    }

    String writeObject(Map<String, Object> object) {
        StringBuilder output = new StringBuilder();
        writeValue(object, output);
        return output.toString();
    }

    private void writeValue(Object value, StringBuilder output) {
        if (value == null) {
            output.append("null");
        } else if (value instanceof String) {
            writeString((String) value, output);
        } else if (value instanceof BigDecimal) {
            output.append(((BigDecimal) value).toPlainString());
        } else if (value instanceof Boolean) {
            output.append(value);
        } else if (value instanceof Map) {
            output.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                if (!first) {
                    output.append(',');
                }
                first = false;
                writeString((String) entry.getKey(), output);
                output.append(':');
                writeValue(entry.getValue(), output);
            }
            output.append('}');
        } else if (value instanceof List) {
            output.append('[');
            boolean first = true;
            for (Object element : (List<?>) value) {
                if (!first) {
                    output.append(',');
                }
                first = false;
                writeValue(element, output);
            }
            output.append(']');
        } else {
            throw new IllegalArgumentException("unsupported JSON value: " + value.getClass());
        }
    }

    private void writeString(String value, StringBuilder output) {
        output.append('"');
        for (int i = 0; i < value.length(); i++) {
            char current = value.charAt(i);
            switch (current) {
                case '"': output.append("\\\""); break;
                case '\\': output.append("\\\\"); break;
                case '\b': output.append("\\b"); break;
                case '\f': output.append("\\f"); break;
                case '\n': output.append("\\n"); break;
                case '\r': output.append("\\r"); break;
                case '\t': output.append("\\t"); break;
                default:
                    if (current < 0x20) {
                        output.append(String.format("\\u%04x", (int) current));
                    } else {
                        output.append(current);
                    }
            }
        }
        output.append('"');
    }

    private static final class Parser {
        private final String input;
        private int offset;

        private Parser(String input) {
            this.input = input;
        }

        private Object parseValue() {
            skipWhitespace();
            if (atEnd()) {
                throw error("expected a value");
            }
            char current = input.charAt(offset);
            if (current == '{') return parseObject();
            if (current == '[') return parseArray();
            if (current == '"') return parseString();
            if (current == 't') return parseLiteral("true", Boolean.TRUE);
            if (current == 'f') return parseLiteral("false", Boolean.FALSE);
            if (current == 'n') return parseLiteral("null", null);
            if (current == '-' || Character.isDigit(current)) return parseNumber();
            throw error("unexpected character '" + current + "'");
        }

        private Map<String, Object> parseObject() {
            LinkedHashMap<String, Object> result = new LinkedHashMap<>();
            offset++;
            skipWhitespace();
            if (consume('}')) return result;
            while (true) {
                skipWhitespace();
                if (atEnd() || input.charAt(offset) != '"') {
                    throw error("expected an object key");
                }
                String key = parseString();
                skipWhitespace();
                expect(':');
                result.put(key, parseValue());
                skipWhitespace();
                if (consume('}')) return result;
                expect(',');
            }
        }

        private List<Object> parseArray() {
            ArrayList<Object> result = new ArrayList<>();
            offset++;
            skipWhitespace();
            if (consume(']')) return result;
            while (true) {
                result.add(parseValue());
                skipWhitespace();
                if (consume(']')) return result;
                expect(',');
            }
        }

        private String parseString() {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (!atEnd()) {
                char current = input.charAt(offset++);
                if (current == '"') return result.toString();
                if (current == '\\') {
                    if (atEnd()) throw error("unterminated escape");
                    char escaped = input.charAt(offset++);
                    switch (escaped) {
                        case '"': result.append('"'); break;
                        case '\\': result.append('\\'); break;
                        case '/': result.append('/'); break;
                        case 'b': result.append('\b'); break;
                        case 'f': result.append('\f'); break;
                        case 'n': result.append('\n'); break;
                        case 'r': result.append('\r'); break;
                        case 't': result.append('\t'); break;
                        case 'u': result.append(parseUnicodeEscape()); break;
                        default: throw error("invalid escape");
                    }
                } else {
                    if (current < 0x20) throw error("control character in string");
                    result.append(current);
                }
            }
            throw error("unterminated string");
        }

        private char parseUnicodeEscape() {
            if (offset + 4 > input.length()) throw error("short unicode escape");
            int value = 0;
            for (int i = 0; i < 4; i++) {
                int digit = Character.digit(input.charAt(offset++), 16);
                if (digit < 0) throw error("invalid unicode escape");
                value = value * 16 + digit;
            }
            return (char) value;
        }

        private Object parseLiteral(String token, Object value) {
            if (!input.startsWith(token, offset)) throw error("invalid literal");
            offset += token.length();
            return value;
        }

        private BigDecimal parseNumber() {
            int start = offset;
            if (consume('-') && atEnd()) throw error("incomplete number");
            if (consume('0')) {
                if (!atEnd() && Character.isDigit(input.charAt(offset))) {
                    throw error("leading zero in number");
                }
            } else {
                consumeDigits();
            }
            if (consume('.')) consumeDigits();
            if (consume('e') || consume('E')) {
                consume('+');
                consume('-');
                consumeDigits();
            }
            try {
                return new BigDecimal(input.substring(start, offset));
            } catch (NumberFormatException invalid) {
                throw error("invalid number");
            }
        }

        private void consumeDigits() {
            int start = offset;
            while (!atEnd() && Character.isDigit(input.charAt(offset))) offset++;
            if (start == offset) throw error("expected a digit");
        }

        private void expect(char expected) {
            if (!consume(expected)) throw error("expected '" + expected + "'");
        }

        private boolean consume(char expected) {
            if (!atEnd() && input.charAt(offset) == expected) {
                offset++;
                return true;
            }
            return false;
        }

        private void skipWhitespace() {
            while (!atEnd()) {
                char current = input.charAt(offset);
                if (current != ' ' && current != '\n' && current != '\r' && current != '\t') return;
                offset++;
            }
        }

        private boolean atEnd() {
            return offset == input.length();
        }

        private IllegalArgumentException error(String message) {
            return new IllegalArgumentException(message + " at offset " + offset);
        }
    }
}
