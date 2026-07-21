package com.moonshiner.telemetry;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.List;

public final class TelemetryParserTest {
    private static int failures;

    public static void main(String[] args) {
        run("valid records and escapes", TelemetryParserTest::validRecordsAndEscapes);
        run("public parser API", TelemetryParserTest::publicParserApi);
        run("empty input allocation budget", TelemetryParserTest::emptyInputDoesNotAllocate);
        run("scratch reset across records and calls", TelemetryParserTest::scratchIsReset);
        run("malformed diagnostics", TelemetryParserTest::malformedDiagnostics);
        run("scratch is invocation-local", TelemetryParserTest::scratchIsInvocationLocal);
        run("allocation count is per invocation", TelemetryParserTest::allocationCountIsPerInvocation);

        if (failures != 0) {
            System.err.println("FAILED: " + failures + " test(s)");
            System.exit(1);
        }
        System.out.println("All telemetry parser tests passed.");
    }

    private static void validRecordsAndEscapes() {
        ParserAllocationCounter counter = new ParserAllocationCounter();
        TelemetryParser parser = new TelemetryParser(counter);
        List<TelemetryRecord> actual = parser.parse(
                "7|cpu\\|total|rack\\\\7\\nready\r\n"
                        + "8|memory|ok\n"
                        + "9|empty-payload|");

        assertEquals(
                List.of(
                        new TelemetryRecord(7, "cpu|total", "rack\\7\nready"),
                        new TelemetryRecord(8, "memory", "ok"),
                        new TelemetryRecord(9, "empty-payload", "")),
                actual,
                "decoded records");
    }

    private static void publicParserApi() {
        TelemetryParser parser = new TelemetryParser();
        assertEquals(
                List.of(new TelemetryRecord(1, "key", "payload")),
                parser.parse("1|key|payload"),
                "default constructor parse");
        try {
            TelemetryParser.class.getConstructor();
            TelemetryParser.class.getConstructor(ParserAllocationCounter.class);
            Method parse = TelemetryParser.class.getMethod("parse", String.class);
            assertEquals(List.class, parse.getReturnType(), "parse return type");
            if (Modifier.isStatic(parse.getModifiers())) {
                fail("parse must remain an instance method");
            }
        } catch (NoSuchMethodException error) {
            fail("public parser API changed: " + error.getMessage());
        }
    }

    private static void emptyInputDoesNotAllocate() {
        ParserAllocationCounter counter = new ParserAllocationCounter();
        List<TelemetryRecord> records = new TelemetryParser(counter).parse("");
        assertEquals(List.of(), records, "empty result");
        assertEquals(0L, counter.scratchInstances(), "empty scratch instances");
        assertEquals(0L, counter.scratchArrays(), "empty scratch arrays");
    }

    private static void scratchIsReset() {
        ParserAllocationCounter counter = new ParserAllocationCounter();
        TelemetryParser parser = new TelemetryParser(counter);
        String longPayload = "x".repeat(200) + "\\|tail";

        List<TelemetryRecord> first = parser.parse(
                "10|long-key|" + longPayload + "\n11|k|z");
        assertEquals(2, first.size(), "first call size");
        assertEquals("x".repeat(200) + "|tail", first.get(0).payload(), "long payload");
        assertEquals(new TelemetryRecord(11, "k", "z"), first.get(1), "short record");

        List<TelemetryRecord> second = parser.parse("12|q|r");
        assertEquals(List.of(new TelemetryRecord(12, "q", "r")), second, "second call");
    }

    private static void malformedDiagnostics() {
        assertDiagnostic("", "17", 1, 3,
                "expected '|' after sequence");
        assertDiagnostic("", "|key|value", 1, 1,
                "sequence must not be empty");
        assertDiagnostic("", "x|key|value", 1, 1,
                "sequence must be an unsigned integer");
        assertDiagnostic("1|ok|first\n", "2|a\\q|bad", 2, 4,
                "invalid escape '\\q'");
        assertDiagnostic("", "2|key|tail\\", 1, 11,
                "trailing escape");
        assertDiagnostic("", "9|only-two", 1, 11,
                "expected '|' after key");
        assertDiagnostic("", "3||payload", 1, 3,
                "key must not be empty");
        assertDiagnostic("", "4|key|value|extra", 1, 12,
                "unexpected fourth field");
        assertDiagnostic("", "5|key|value\rnext", 1, 12,
                "bare carriage return");
        assertDiagnostic("", "9223372036854775808|key|value", 1, 1,
                "sequence is out of range");
        assertDiagnostic("1|ok|first\r\n", "\n", 2, 1,
                "empty record");
    }

    private static void scratchIsInvocationLocal() {
        TelemetryParser parser = new TelemetryParser();
        parser.parse("1|inspection|record");
        List<Class<?>> parserClasses = new ArrayList<>();
        parserClasses.add(TelemetryParser.class);
        parserClasses.addAll(List.of(TelemetryParser.class.getDeclaredClasses()));

        for (Class<?> type : parserClasses) {
            for (Field field : type.getDeclaredFields()) {
                Class<?> fieldType = field.getType();
                String fieldName = field.getName().toLowerCase();
                boolean scratchCarrier = fieldType.isArray()
                        || ThreadLocal.class.isAssignableFrom(fieldType)
                        || StringBuilder.class.isAssignableFrom(fieldType)
                        || StringBuffer.class.isAssignableFrom(fieldType)
                        || java.nio.Buffer.class.isAssignableFrom(fieldType)
                        || fieldType.getSimpleName().contains("Scratch")
                        || fieldName.contains("scratch")
                        || fieldName.contains("buffer")
                        || fieldName.contains("cache");
                boolean retainedBeyondInvocation = Modifier.isStatic(field.getModifiers())
                        || type == TelemetryParser.class;
                if (scratchCarrier && retainedBeyondInvocation) {
                    fail("field " + type.getName() + "." + field.getName()
                            + " may retain scratch state outside a parse invocation");
                }

                boolean inspectValue = Modifier.isStatic(field.getModifiers())
                        || type == TelemetryParser.class;
                if (!inspectValue) {
                    continue;
                }
                try {
                    field.setAccessible(true);
                    Object value = field.get(
                            Modifier.isStatic(field.getModifiers()) ? null : parser);
                    if (value != null
                            && (value.getClass().isArray()
                            || value instanceof ThreadLocal<?>
                            || value instanceof StringBuilder
                            || value instanceof StringBuffer
                            || value instanceof java.nio.Buffer
                            || value.getClass().getSimpleName().contains("Scratch"))) {
                        fail("retained field " + type.getName() + "." + field.getName()
                                + " holds mutable scratch state");
                    }
                } catch (IllegalAccessException error) {
                    fail("cannot inspect retained field " + type.getName() + "."
                            + field.getName());
                }
            }
        }
    }

    private static void allocationCountIsPerInvocation() {
        StringBuilder input = new StringBuilder();
        for (int index = 0; index < 512; index++) {
            if (index != 0) {
                input.append(index % 2 == 0 ? "\r\n" : "\n");
            }
            input.append(index)
                    .append("|sensor\\|")
                    .append(index % 7)
                    .append("|reading\\n")
                    .append(index);
        }

        ParserAllocationCounter counter = new ParserAllocationCounter();
        TelemetryParser parser = new TelemetryParser(counter);
        List<TelemetryRecord> records = parser.parse(input.toString());
        assertEquals(512, records.size(), "batch size");
        assertEquals(new TelemetryRecord(0, "sensor|0", "reading\n0"),
                records.get(0), "first batch record");
        assertEquals(new TelemetryRecord(511, "sensor|0", "reading\n511"),
                records.get(511), "last batch record");
        for (int index = 0; index < records.size(); index++) {
            assertEquals(
                    new TelemetryRecord(
                            index,
                            "sensor|" + (index % 7),
                            "reading\n" + index),
                    records.get(index),
                    "batch record " + index);
        }
        assertAtMost(1L, counter.scratchInstances(),
                "scratch instances for a 512-record invocation");
        assertAtMost(1L, counter.scratchArrays(),
                "scratch arrays for a 512-record invocation");
        assertEquals(counter.scratchInstances(), counter.scratchArrays(),
                "scratch instance/array accounting");

        long instancesBeforeSecondCall = counter.scratchInstances();
        long arraysBeforeSecondCall = counter.scratchArrays();
        parser.parse("600|single|record");
        assertAtMost(1L, counter.scratchInstances() - instancesBeforeSecondCall,
                "scratch instances in a second nonempty invocation");
        assertAtMost(1L, counter.scratchArrays() - arraysBeforeSecondCall,
                "scratch arrays in a second nonempty invocation");
    }

    private static void assertDiagnostic(
            String prefix,
            String malformed,
            int line,
            int column,
            String reason) {
        TelemetryParser parser = new TelemetryParser(new ParserAllocationCounter());
        try {
            parser.parse(prefix + malformed);
            fail("expected diagnostic for " + malformed);
        } catch (TelemetryParseException error) {
            assertEquals(line, error.line(), "diagnostic line");
            assertEquals(column, error.column(), "diagnostic column");
            assertEquals(reason, error.reason(), "diagnostic reason");
            assertEquals("line " + line + ", column " + column + ": " + reason,
                    error.getMessage(), "diagnostic message");
        }
    }

    private static void run(String name, Runnable test) {
        int before = failures;
        try {
            test.run();
        } catch (Throwable error) {
            failures++;
            System.err.println("FAIL " + name + ": " + error);
        }
        if (failures == before) {
            System.out.println("PASS " + name);
        }
    }

    private static void assertEquals(Object expected, Object actual, String label) {
        if (!expected.equals(actual)) {
            fail(label + ": expected <" + expected + "> but was <" + actual + ">");
        }
    }

    private static void assertAtMost(long maximum, long actual, String label) {
        if (actual > maximum) {
            fail(label + ": expected at most <" + maximum + "> but was <" + actual + ">");
        }
    }

    private static void fail(String message) {
        throw new AssertionError(message);
    }
}
