package moonshiner.routes;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Arrays;
import java.util.List;

import moonshiner.routes.RouteSpecParser.OperationLimitExceededException;
import moonshiner.routes.RouteSpecParser.ParseResult;
import moonshiner.routes.RouteSpecParser.SyntaxException;

/** Dependency-free protected regression tests. */
public final class RouteSpecParserTest {
    private RouteSpecParserTest() {
    }

    public static void main(String[] args) {
        usesAnExplicitParserRatherThanAnotherRegex();
        acceptsTheEntireGrammar();
        rejectsInvalidSyntaxAtTheOriginalOffsets();
        exhaustivelyChecksShortInputsAndExactAccounting();
        keepsPublicArgumentValidation();
        preservesCharacterInspectionAccountingAndLimits();
        preservesUnmodifiableResults();
        rejectsAdversarialRoutesInLinearOperations();
        parsesLargeValidRoutesInLinearOperations();
        System.out.println("All RouteSpecParser tests passed");
    }

    private static void usesAnExplicitParserRatherThanAnotherRegex() {
        Path sourcePath = Paths.get(
                "src/main/java/moonshiner/routes/RouteSpecParser.java");
        String source;
        try {
            source = new String(Files.readAllBytes(sourcePath), StandardCharsets.UTF_8);
        } catch (IOException exception) {
            throw new AssertionError("could not read RouteSpecParser source", exception);
        }
        assertNotContains(source, "java.util.regex", "regex package usage");
        assertNotContains(source, "Pattern.compile", "compiled regex usage");
        assertNotContains(source, ".matches(", "String.matches usage");
    }

    private static void acceptsTheEntireGrammar() {
        for (char letter = 'a'; letter <= 'z'; letter++) {
            String segment = String.valueOf(letter);
            expectSegments(segment, segment);
        }
        expectSegments("a", "a");
        expectSegments("alpha", "alpha");
        expectSegments("a.bc.def", "a", "bc", "def");
        expectSegments("route.with.many.lowercase.segments",
                "route", "with", "many", "lowercase", "segments");
    }

    private static void rejectsInvalidSyntaxAtTheOriginalOffsets() {
        long diagnosticBudget = 10_000;
        expectSyntax("", 0, diagnosticBudget);
        expectSyntax(".", 0, diagnosticBudget);
        expectSyntax(".abc", 0, diagnosticBudget);
        expectSyntax("abc.", 4, diagnosticBudget);
        expectSyntax("abc..def", 4, diagnosticBudget);
        expectSyntax("abc#def", 3, diagnosticBudget);
        expectSyntax("abc.D", 4, diagnosticBudget);
        expectSyntax("ABC", 0, diagnosticBudget);
        expectSyntax("a1", 1, diagnosticBudget);
        expectSyntax("caf\u00e9", 3, diagnosticBudget);
    }

    private static void exhaustivelyChecksShortInputsAndExactAccounting() {
        char[] alphabet = {'a', 'z', '.', '#', 'A', '1', '\u00e9'};
        for (int length = 0; length <= 4; length++) {
            checkInputsOfLength(alphabet, new char[length], 0);
        }
    }

    private static void checkInputsOfLength(char[] alphabet, char[] input, int index) {
        if (index < input.length) {
            for (char character : alphabet) {
                input[index] = character;
                checkInputsOfLength(alphabet, input, index + 1);
            }
            return;
        }

        String value = new String(input);
        int errorOffset = expectedErrorOffset(value);
        long budget = Math.max(1, value.length());
        if (errorOffset < 0) {
            ParseResult result = RouteSpecParser.parseMeasured(value, budget);
            assertEquals(Arrays.asList(value.split("\\.", -1)), result.segments(),
                    "exhaustive segments for " + printable(value));
            assertEquals((long) value.length(), result.operationCount(),
                    "exhaustive valid operation count for " + printable(value));
            return;
        }

        SyntaxException exception = expectSyntax(value, errorOffset, budget);
        long expectedOperations = errorOffset < value.length() ? errorOffset + 1L : value.length();
        assertEquals(expectedOperations, exception.operationCount(),
                "exhaustive invalid operation count for " + printable(value));
    }

    private static int expectedErrorOffset(String input) {
        if (input.isEmpty()) {
            return 0;
        }

        boolean expectingLetter = true;
        for (int index = 0; index < input.length(); index++) {
            char current = input.charAt(index);
            if (current >= 'a' && current <= 'z') {
                expectingLetter = false;
            } else if (current == '.' && !expectingLetter) {
                expectingLetter = true;
            } else {
                return index;
            }
        }
        return expectingLetter ? input.length() : -1;
    }

    private static void keepsPublicArgumentValidation() {
        expectThrows(NullPointerException.class,
                () -> RouteSpecParser.parseMeasured(null, 10), "null input");
        expectThrows(IllegalArgumentException.class,
                () -> RouteSpecParser.parseMeasured("a", 0), "zero budget");
        expectThrows(IllegalArgumentException.class,
                () -> RouteSpecParser.parseMeasured("a", -1), "negative budget");
    }

    private static void preservesCharacterInspectionAccountingAndLimits() {
        ParseResult valid = RouteSpecParser.parseMeasured("ab.cd", 100);
        assertEquals(5L, valid.operationCount(), "valid input operation count");

        SyntaxException invalidCharacter = expectSyntax("ab#tail", 2, 100);
        assertEquals(3L, invalidCharacter.operationCount(),
                "invalid-character operation count");

        SyntaxException trailingSeparator = expectSyntax("ab.", 3, 100);
        assertEquals(3L, trailingSeparator.operationCount(),
                "EOF-only operation count");

        SyntaxException empty = expectSyntax("", 0, 100);
        assertEquals(0L, empty.operationCount(), "empty input operation count");

        OperationLimitExceededException validLimit = expectThrows(
                OperationLimitExceededException.class,
                () -> RouteSpecParser.parseMeasured("abcd", 3),
                "valid input operation limit");
        assertEquals(3L, validLimit.operationLimit(), "reported valid-input limit");

        OperationLimitExceededException invalidLimit = expectThrows(
                OperationLimitExceededException.class,
                () -> RouteSpecParser.parseMeasured("abc!", 3),
                "invalid input operation limit");
        assertEquals(3L, invalidLimit.operationLimit(), "reported invalid-input limit");
    }

    private static void preservesUnmodifiableResults() {
        List<String> parsed = RouteSpecParser.parse("a.bc");
        expectThrows(UnsupportedOperationException.class,
                () -> parsed.add("def"), "parse result mutability");

        List<String> measured = RouteSpecParser.parseMeasured("a.bc", 4).segments();
        expectThrows(UnsupportedOperationException.class,
                () -> measured.set(0, "changed"), "measured result mutability");
    }

    private static void parsesLargeValidRoutesInLinearOperations() {
        String first = repeat('a', 20_000);
        String input = first + ".b";
        long generousLinearBound = input.length() * 4L;
        ParseResult result = RouteSpecParser.parseMeasured(input, generousLinearBound);
        assertEquals(Arrays.asList(first, "b"), result.segments(), "large valid segments");
        assertEquals((long) input.length(), result.operationCount(),
                "large valid operation count");
    }

    private static void rejectsAdversarialRoutesInLinearOperations() {
        String invalidCharacter = repeat('a', 64) + "!";
        SyntaxException first = expectSyntax(invalidCharacter, 64, invalidCharacter.length() + 1L);
        assertEquals((long) invalidCharacter.length(), first.operationCount(),
                "invalid-character operation count");

        String trailingSeparator = repeat('z', 2_048) + ".";
        SyntaxException second = expectSyntax(
                trailingSeparator, trailingSeparator.length(), trailingSeparator.length() + 1L);
        assertEquals((long) trailingSeparator.length(), second.operationCount(),
                "trailing-separator operation count");
    }

    private static void expectSegments(String input, String... expected) {
        List<String> actual = RouteSpecParser.parse(input);
        assertEquals(Arrays.asList(expected), actual, "segments for " + printable(input));
    }

    private static SyntaxException expectSyntax(String input, int expectedOffset, long budget) {
        try {
            RouteSpecParser.parseMeasured(input, budget);
        } catch (SyntaxException exception) {
            assertEquals(expectedOffset, exception.errorOffset(),
                    "error offset for " + printable(input));
            return exception;
        } catch (OperationLimitExceededException exception) {
            throw new AssertionError("operation budget exceeded for " + printable(input), exception);
        }
        throw new AssertionError("expected syntax error for " + printable(input));
    }

    private static <T extends Throwable> T expectThrows(
            Class<T> expected, ThrowingRunnable action, String label) {
        try {
            action.run();
        } catch (Throwable throwable) {
            if (expected.isInstance(throwable)) {
                return expected.cast(throwable);
            }
            throw new AssertionError(label + ": expected " + expected.getSimpleName()
                    + " but got " + throwable.getClass().getSimpleName(), throwable);
        }
        throw new AssertionError(label + ": expected " + expected.getSimpleName());
    }

    private static String repeat(char character, int count) {
        char[] characters = new char[count];
        Arrays.fill(characters, character);
        return new String(characters);
    }

    private static String printable(String input) {
        if (input.length() <= 40) {
            return '"' + input + '"';
        }
        return "input of length " + input.length();
    }

    private static void assertNotContains(String value, String forbidden, String label) {
        if (value.contains(forbidden)) {
            throw new AssertionError(label + ": found forbidden text " + forbidden);
        }
    }

    private static void assertEquals(Object expected, Object actual, String label) {
        if (!expected.equals(actual)) {
            throw new AssertionError(label + ": expected " + expected + " but got " + actual);
        }
    }

    private interface ThrowingRunnable {
        void run() throws Throwable;
    }
}
