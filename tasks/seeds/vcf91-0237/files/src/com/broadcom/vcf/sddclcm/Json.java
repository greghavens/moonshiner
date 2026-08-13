package com.broadcom.vcf.sddclcm;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal dependency-free JSON codec shared by the SDDC LCM client and the
 * protected verification harness.
 *
 * <p>Decoding maps a JSON object to a {@link LinkedHashMap}, a JSON array to an
 * {@link ArrayList}, a JSON string to {@link String}, a JSON number to
 * {@link Double} or {@link Long}, a JSON boolean to {@link Boolean} and JSON
 * null to {@code null}.
 *
 * <p>Encoding is compact: no space and no newline is emitted between tokens, and
 * object members are written in map iteration order. The encoder is faithful, so
 * a member whose value is {@code null} is encoded as {@code "name":null} and a
 * member whose value is an empty collection is encoded as {@code "name":[]} or
 * {@code "name":{}}. Omitting an unset optional member is the caller's
 * responsibility: do not put it into the map at all.
 *
 * <p>This file is part of the protected harness. Do not modify it.
 */
public final class Json {

    private Json() {
    }

    /** Creates an order-preserving JSON object builder. */
    public static Map<String, Object> object() {
        return new LinkedHashMap<>();
    }

    /** Creates a JSON array builder. */
    public static List<Object> array() {
        return new ArrayList<>();
    }

    /** Decodes {@code text}. Throws {@link JsonException} when {@code text} is not valid JSON. */
    public static Object parse(String text) {
        if (text == null) {
            throw new JsonException("cannot parse a null document");
        }
        Parser parser = new Parser(text);
        parser.skipWhitespace();
        Object value = parser.readValue();
        parser.skipWhitespace();
        if (!parser.atEnd()) {
            throw new JsonException("trailing content at offset " + parser.offset());
        }
        return value;
    }

    /** Decodes {@code text} and requires the document root to be a JSON object. */
    @SuppressWarnings("unchecked")
    public static Map<String, Object> parseObject(String text) {
        Object value = parse(text);
        if (!(value instanceof Map)) {
            throw new JsonException("document root is not a JSON object");
        }
        return (Map<String, Object>) value;
    }

    /** Encodes {@code value} into compact JSON text. */
    public static String write(Object value) {
        StringBuilder out = new StringBuilder();
        encode(value, out);
        return out.toString();
    }

    private static void encode(Object value, StringBuilder out) {
        switch (value) {
            case null -> out.append("null");
            case String s -> encodeString(s, out);
            case Boolean b -> out.append(b.booleanValue() ? "true" : "false");
            case Long l -> out.append(l.longValue());
            case Integer i -> out.append(i.intValue());
            case Double d -> {
                if (d.isNaN() || d.isInfinite()) {
                    throw new JsonException("cannot encode the non-finite number " + d);
                }
                if (d.doubleValue() == Math.rint(d.doubleValue())) {
                    out.append((long) d.doubleValue());
                } else {
                    out.append(d.doubleValue());
                }
            }
            case Number n -> out.append(n.toString());
            case Map<?, ?> map -> {
                out.append('{');
                boolean first = true;
                for (Map.Entry<?, ?> entry : map.entrySet()) {
                    if (!first) {
                        out.append(',');
                    }
                    first = false;
                    if (!(entry.getKey() instanceof String key)) {
                        throw new JsonException("object member names must be strings");
                    }
                    encodeString(key, out);
                    out.append(':');
                    encode(entry.getValue(), out);
                }
                out.append('}');
            }
            case Iterable<?> items -> {
                out.append('[');
                boolean first = true;
                for (Object item : items) {
                    if (!first) {
                        out.append(',');
                    }
                    first = false;
                    encode(item, out);
                }
                out.append(']');
            }
            default -> throw new JsonException("cannot encode a value of type " + value.getClass().getName());
        }
    }

    private static void encodeString(String text, StringBuilder out) {
        out.append('"');
        for (int index = 0; index < text.length(); index++) {
            char character = text.charAt(index);
            switch (character) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                case '\b' -> out.append("\\b");
                case '\f' -> out.append("\\f");
                default -> {
                    if (character < 0x20) {
                        out.append(String.format("\\u%04x", (int) character));
                    } else {
                        out.append(character);
                    }
                }
            }
        }
        out.append('"');
    }

    /** Signals a malformed JSON document or an unencodable value. */
    public static final class JsonException extends RuntimeException {
        private static final long serialVersionUID = 1L;

        JsonException(String message) {
            super(message);
        }
    }

    private static final class Parser {
        private final String text;
        private int position;

        Parser(String text) {
            this.text = text;
        }

        int offset() {
            return position;
        }

        boolean atEnd() {
            return position >= text.length();
        }

        void skipWhitespace() {
            while (position < text.length()) {
                char character = text.charAt(position);
                if (character == ' ' || character == '\t' || character == '\n' || character == '\r') {
                    position++;
                } else {
                    return;
                }
            }
        }

        Object readValue() {
            if (atEnd()) {
                throw new JsonException("unexpected end of document");
            }
            char character = text.charAt(position);
            return switch (character) {
                case '{' -> readObject();
                case '[' -> readArray();
                case '"' -> readString();
                case 't' -> readLiteral("true", Boolean.TRUE);
                case 'f' -> readLiteral("false", Boolean.FALSE);
                case 'n' -> readLiteral("null", null);
                default -> readNumber();
            };
        }

        private Map<String, Object> readObject() {
            expect('{');
            Map<String, Object> members = new LinkedHashMap<>();
            skipWhitespace();
            if (peek() == '}') {
                position++;
                return members;
            }
            while (true) {
                skipWhitespace();
                String name = readString();
                skipWhitespace();
                expect(':');
                skipWhitespace();
                members.put(name, readValue());
                skipWhitespace();
                char next = peek();
                if (next == ',') {
                    position++;
                    continue;
                }
                if (next == '}') {
                    position++;
                    return members;
                }
                throw new JsonException("expected ',' or '}' at offset " + position);
            }
        }

        private List<Object> readArray() {
            expect('[');
            List<Object> items = new ArrayList<>();
            skipWhitespace();
            if (peek() == ']') {
                position++;
                return items;
            }
            while (true) {
                skipWhitespace();
                items.add(readValue());
                skipWhitespace();
                char next = peek();
                if (next == ',') {
                    position++;
                    continue;
                }
                if (next == ']') {
                    position++;
                    return items;
                }
                throw new JsonException("expected ',' or ']' at offset " + position);
            }
        }

        private String readString() {
            expect('"');
            StringBuilder value = new StringBuilder();
            while (true) {
                if (atEnd()) {
                    throw new JsonException("unterminated string");
                }
                char character = text.charAt(position++);
                if (character == '"') {
                    return value.toString();
                }
                if (character != '\\') {
                    value.append(character);
                    continue;
                }
                if (atEnd()) {
                    throw new JsonException("unterminated escape sequence");
                }
                char escape = text.charAt(position++);
                switch (escape) {
                    case '"' -> value.append('"');
                    case '\\' -> value.append('\\');
                    case '/' -> value.append('/');
                    case 'b' -> value.append('\b');
                    case 'f' -> value.append('\f');
                    case 'n' -> value.append('\n');
                    case 'r' -> value.append('\r');
                    case 't' -> value.append('\t');
                    case 'u' -> {
                        if (position + 4 > text.length()) {
                            throw new JsonException("truncated unicode escape");
                        }
                        value.append((char) Integer.parseInt(text.substring(position, position + 4), 16));
                        position += 4;
                    }
                    default -> throw new JsonException("invalid escape '\\" + escape + "'");
                }
            }
        }

        private Object readNumber() {
            int start = position;
            if (peek() == '-') {
                position++;
            }
            boolean fractional = false;
            while (!atEnd()) {
                char character = text.charAt(position);
                if (character >= '0' && character <= '9') {
                    position++;
                } else if (character == '.' || character == 'e' || character == 'E'
                        || character == '+' || character == '-') {
                    fractional = fractional || character == '.' || character == 'e' || character == 'E';
                    position++;
                } else {
                    break;
                }
            }
            String literal = text.substring(start, position);
            if (literal.isEmpty() || literal.equals("-")) {
                throw new JsonException("invalid number at offset " + start);
            }
            try {
                return fractional ? (Object) Double.valueOf(literal) : (Object) Long.valueOf(literal);
            } catch (NumberFormatException failure) {
                throw new JsonException("invalid number '" + literal + "'");
            }
        }

        private Object readLiteral(String literal, Object value) {
            if (!text.startsWith(literal, position)) {
                throw new JsonException("invalid literal at offset " + position);
            }
            position += literal.length();
            return value;
        }

        private char peek() {
            if (atEnd()) {
                throw new JsonException("unexpected end of document");
            }
            return text.charAt(position);
        }

        private void expect(char character) {
            if (atEnd() || text.charAt(position) != character) {
                throw new JsonException("expected '" + character + "' at offset " + position);
            }
            position++;
        }
    }
}
