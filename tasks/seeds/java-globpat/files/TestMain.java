import java.util.regex.Matcher;

/**
 * Acceptance contract for GlobRename, the matching/renaming core of the
 * media-library batch renamer.
 *
 * Run: java TestMain.java
 */
public class TestMain {

    interface Body {
        void run() throws Exception;
    }

    static int passed = 0;
    static int failed = 0;

    static void test(String name, Body body) {
        try {
            body.run();
            passed++;
            System.out.println("PASS " + name);
        } catch (Throwable t) {
            failed++;
            System.out.println("FAIL " + name + ": " + t);
        }
    }

    static void eq(Object actual, Object expected, String what) {
        if (actual == null ? expected != null : !actual.equals(expected)) {
            throw new AssertionError(what + ": expected <" + expected + "> got <" + actual + ">");
        }
    }

    static void yes(boolean cond, String what) {
        if (!cond) {
            throw new AssertionError(what);
        }
    }

    static boolean m(String glob, String name) {
        return GlobRename.compile(glob).matcher(name).matches();
    }

    public static void main(String[] args) {
        test("star matches any run within one path segment", () -> {
            yes(m("*.csv", "sales.csv"), "*.csv should match sales.csv");
            yes(m("*.csv", ".csv"), "* may match the empty run");
            yes(!m("*.csv", "archive/sales.csv"), "* must not cross a slash");
            yes(!m("*.csv", "sales.csvx"), "match must cover the whole name");
            yes(!m("*.csv", "xsales.csv2"), "suffix must match literally");
        });

        test("question mark matches exactly one non-slash character", () -> {
            yes(m("scan-?.tif", "scan-4.tif"), "one char");
            yes(!m("scan-?.tif", "scan-12.tif"), "not two chars");
            yes(!m("scan-?.tif", "scan-.tif"), "not zero chars");
            yes(!m("a?b", "a/b"), "? must not match a slash");
        });

        test("regex metacharacters in the glob are plain text", () -> {
            yes(m("cost (est).txt", "cost (est).txt"), "parens literal");
            yes(m("a+b.txt", "a+b.txt"), "plus literal");
            yes(!m("a+b.txt", "aab.txt"), "plus must not repeat");
            yes(m("v1.2-*", "v1.2-rc1"), "dot literal, star after");
            yes(!m("v1.2-*", "v192-rc1"), "dot must not match any char");
            yes(m("take|two.mov", "take|two.mov"), "pipe literal");
        });

        test("backslash in the glob makes the next character literal", () -> {
            yes(m("data\\*.csv", "data*.csv"), "escaped star is a literal star");
            yes(!m("data\\*.csv", "dataX.csv"), "escaped star must not wildcard");
            yes(m("who\\?.txt", "who?.txt"), "escaped question mark");
            yes(!m("who\\?.txt", "whoa.txt"), "escaped ? must not wildcard");
        });

        test("the double-escape ladder: four in source, two in the glob, one in the name", () -> {
            // Java source "\\\\" is the two glob characters \\ ,
            // which the glob rules read as one literal backslash.
            yes(m("\\\\*", "\\report"), "leading literal backslash plus star");
            yes(m("tmp\\\\Extra*.log", "tmp\\Extra-01.log"),
                    "literal backslash directly before E");
            yes(m("a\\\\Qb*", "a\\Qb-1"), "literal backslash directly before Q");
            yes(!m("tmp\\\\Extra*.log", "tmpExtra-01.log"),
                    "the backslash in the name is required");
        });

        test("a trailing lone backslash is rejected", () -> {
            try {
                GlobRename.compile("oops\\");
                yes(false, "expected IllegalArgumentException");
            } catch (IllegalArgumentException expected) {
            }
        });

        test("wildcards capture, numbered left to right", () -> {
            Matcher matcher = GlobRename.compile("*-*.csv").matcher("sales-2024.csv");
            yes(matcher.matches(), "should match");
            eq(matcher.groupCount(), 2, "group count");
            eq(matcher.group(1), "sales", "group 1");
            eq(matcher.group(2), "2024", "group 2");
        });

        test("empty glob matches only the empty name", () -> {
            yes(m("", ""), "empty matches empty");
            yes(!m("", "x"), "empty must not match x");
        });

        test("rename swaps captured runs into the target", () -> {
            eq(GlobRename.rename("sales-2024.csv", "*-*.csv", "{2}/{1}.csv"),
                    "2024/sales.csv", "swap");
            eq(GlobRename.rename("scan-7.tif", "scan-?.tif", "page{1}.tif"),
                    "page7.tif", "question-mark capture");
        });

        test("rename returns null when the glob does not match", () -> {
            eq(GlobRename.rename("sales.json", "*.csv", "{1}.bak"), null, "no match");
            eq(GlobRename.rename("a/b.txt", "*.txt", "{1}"), null, "slash blocks *");
        });

        test("dollar signs in the target are plain text", () -> {
            eq(GlobRename.rename("draft.txt", "*.txt", "{1}$final.txt"),
                    "draft$final.txt", "dollar mid-target");
            eq(GlobRename.rename("draft.txt", "*.txt", "$0-{1}.txt"),
                    "$0-draft.txt", "dollar-zero must not mean whole match");
        });

        test("backslashes in the target are plain text", () -> {
            eq(GlobRename.rename("draft.txt", "*.txt", "old\\{1}.txt"),
                    "old\\draft.txt", "backslash before a placeholder");
            eq(GlobRename.rename("draft.txt", "*.txt", "{1}\\1.txt"),
                    "draft\\1.txt", "backslash-digit must not be a group ref");
        });

        test("captured text with special characters survives rename", () -> {
            eq(GlobRename.rename("q$3-final.csv", "*-final.csv", "{1}.csv"),
                    "q$3.csv", "dollar in the captured run");
            eq(GlobRename.rename("a\\b.txt", "*.txt", "{1}!"),
                    "a\\b!", "backslash in the captured run");
        });

        test("braces that are not {digits} are plain text", () -> {
            eq(GlobRename.rename("x.txt", "*.txt", "brace {x} {1}"),
                    "brace {x} x", "non-numeric brace");
            eq(GlobRename.rename("x.txt", "*.txt", "{}{1}"),
                    "{}x", "empty brace");
        });

        test("a placeholder beyond the wildcard count is rejected", () -> {
            try {
                GlobRename.rename("sales-2024.csv", "*-*.csv", "{3}.csv");
                yes(false, "expected IllegalArgumentException");
            } catch (IllegalArgumentException expected) {
            }
        });

        System.out.println(passed + " passed, " + failed + " failed");
        if (failed > 0) {
            System.exit(1);
        }
    }
}
