public class TestMain {
    private static int failures = 0;

    private static void checkEquals(String expected, String actual, String what) {
        if (expected.equals(actual)) {
            System.out.println("ok   " + what);
        } else {
            failures++;
            System.out.println("FAIL " + what + " (expected \"" + expected + "\", got \"" + actual + "\")");
        }
    }

    public static void main(String[] args) {
        checkEquals("\\\\printsrv\\queues\\accounts",
                DropRouter.route("INV-20260712-004.PDF"),
                "INV tickets go to accounts");
        checkEquals("\\\\printsrv\\queues\\prepress",
                DropRouter.route("PROOF-20260712-011.pdf"),
                "PROOF tickets go to prepress");
        checkEquals("\\\\printsrv\\queues\\production",
                DropRouter.route("job-20260713-002.pdf"),
                "prefix match ignores case");
        checkEquals("\\\\printsrv\\queues\\holding",
                DropRouter.route("MISC-20260713-001.pdf"),
                "unknown prefixes go to holding");
        checkEquals("\\\\printsrv\\queues\\holding",
                DropRouter.route("nodashname.pdf"),
                "no dash means no prefix, so holding");
        checkEquals("\\\\printsrv\\queues\\holding",
                DropRouter.route("-20260713-009.pdf"),
                "leading dash means empty prefix, so holding");

        checkEquals("INV", DropRouter.prefixOf("  INV-20260712-004.PDF "),
                "prefix extraction trims and uppercases");
        checkEquals("", DropRouter.prefixOf("plain.pdf"),
                "no dash yields empty prefix");

        checkEquals("INV-20260712-004.pdf",
                DropRouter.normalize("INV-20260712-004.PDF"),
                "extension is lowercased, base kept as scanned");
        checkEquals("JOB-20260713_rush_copy.pdf",
                DropRouter.normalize("  JOB-20260713 rush copy.PDF  "),
                "spaces squeeze to single underscores");
        checkEquals("README",
                DropRouter.normalize("README"),
                "no extension is left alone");
        checkEquals("weird.",
                DropRouter.normalize("weird."),
                "trailing dot is left alone");

        checkEquals("INV-20260714-001.pdf -> \\\\printsrv\\queues\\accounts",
                DropRouter.manifestLine("INV-20260714-001.PDF"),
                "manifest line combines normalized name and route");

        if (failures > 0) {
            System.out.println(failures + " check(s) failing");
            System.exit(1);
        }
        System.out.println("all checks passed");
    }
}
