package com.moonshiner.telemetry;

import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

/**
 * Parses newline-delimited records in the form {@code sequence|key|payload}.
 * The key and payload accept {@code \|}, {@code \\}, and {@code \n} escapes.
 */
public final class TelemetryParser {
    private final ParserAllocationCounter allocations;

    public TelemetryParser() {
        this(new ParserAllocationCounter());
    }

    public TelemetryParser(ParserAllocationCounter allocations) {
        this.allocations = Objects.requireNonNull(allocations, "allocations");
    }

    public List<TelemetryRecord> parse(String input) {
        Objects.requireNonNull(input, "input");
        List<TelemetryRecord> records = new ArrayList<>();
        int offset = 0;
        int line = 1;

        while (offset < input.length()) {
            int recordEnd = findRecordEnd(input, offset, line);
            if (recordEnd == offset) {
                throw error(line, 1, "empty record");
            }

            RecordScratch scratch =
                    new RecordScratch(recordEnd - offset, allocations);
            records.add(parseRecord(input, offset, recordEnd, line, scratch));

            if (recordEnd == input.length()) {
                offset = recordEnd;
            } else if (input.charAt(recordEnd) == '\r') {
                offset = recordEnd + 2;
            } else {
                offset = recordEnd + 1;
            }
            line++;
        }

        return List.copyOf(records);
    }

    private static int findRecordEnd(String input, int start, int line) {
        int cursor = start;
        while (cursor < input.length()) {
            char current = input.charAt(cursor);
            if (current == '\n') {
                return cursor;
            }
            if (current == '\r') {
                if (cursor + 1 >= input.length() || input.charAt(cursor + 1) != '\n') {
                    throw error(line, cursor - start + 1, "bare carriage return");
                }
                return cursor;
            }
            cursor++;
        }
        return cursor;
    }

    private static TelemetryRecord parseRecord(
            String input,
            int start,
            int end,
            int line,
            RecordScratch scratch) {
        int firstSeparator = start;
        while (firstSeparator < end && input.charAt(firstSeparator) != '|') {
            firstSeparator++;
        }
        if (firstSeparator == end) {
            throw error(line, end - start + 1, "expected '|' after sequence");
        }

        long sequence = parseSequence(input, start, firstSeparator, line);
        int cursor = firstSeparator + 1;
        int keyStart = scratch.mark();

        while (cursor < end && input.charAt(cursor) != '|') {
            cursor = appendDecoded(input, start, end, line, cursor, scratch);
        }
        if (cursor == end) {
            throw error(line, end - start + 1, "expected '|' after key");
        }
        if (scratch.length() == keyStart) {
            throw error(line, firstSeparator - start + 2, "key must not be empty");
        }

        String key = scratch.stringFrom(keyStart);
        cursor++;
        int payloadStart = scratch.mark();
        while (cursor < end) {
            if (input.charAt(cursor) == '|') {
                throw error(line, cursor - start + 1, "unexpected fourth field");
            }
            cursor = appendDecoded(input, start, end, line, cursor, scratch);
        }

        return new TelemetryRecord(sequence, key, scratch.stringFrom(payloadStart));
    }

    private static long parseSequence(String input, int start, int end, int line) {
        if (start == end) {
            throw error(line, 1, "sequence must not be empty");
        }

        long value = 0;
        for (int cursor = start; cursor < end; cursor++) {
            char current = input.charAt(cursor);
            if (current < '0' || current > '9') {
                throw error(line, cursor - start + 1, "sequence must be an unsigned integer");
            }
            int digit = current - '0';
            if (value > (Long.MAX_VALUE - digit) / 10) {
                throw error(line, 1, "sequence is out of range");
            }
            value = value * 10 + digit;
        }
        return value;
    }

    private static int appendDecoded(
            String input,
            int recordStart,
            int recordEnd,
            int line,
            int cursor,
            RecordScratch scratch) {
        char current = input.charAt(cursor);
        if (current != '\\') {
            scratch.append(current);
            return cursor + 1;
        }

        if (cursor + 1 >= recordEnd) {
            throw error(line, cursor - recordStart + 1, "trailing escape");
        }
        char escaped = input.charAt(cursor + 1);
        if (escaped == '|' || escaped == '\\') {
            scratch.append(escaped);
        } else if (escaped == 'n') {
            scratch.append('\n');
        } else {
            throw error(
                    line,
                    cursor - recordStart + 1,
                    "invalid escape '\\" + escaped + "'");
        }
        return cursor + 2;
    }

    private static TelemetryParseException error(int line, int column, String reason) {
        return new TelemetryParseException(line, column, reason);
    }

    private static final class RecordScratch {
        private final char[] characters;
        private int length;

        private RecordScratch(int capacity, ParserAllocationCounter allocations) {
            allocations.scratchAllocated(capacity);
            characters = new char[capacity];
        }

        private int mark() {
            return length;
        }

        private int length() {
            return length;
        }

        private void append(char value) {
            characters[length++] = value;
        }

        private String stringFrom(int start) {
            return new String(characters, start, length - start);
        }
    }
}
