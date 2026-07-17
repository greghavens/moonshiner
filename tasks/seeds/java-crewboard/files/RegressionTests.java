import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

/**
 * Pins the shipped CrewBoard behavior: post/size/duplicate handling and the
 * plain search() contract (AND terms, case-insensitive, freshest first).
 * The mariner-facing phone app calls search() exactly like this; it must not
 * change.
 *
 * Standalone run: java RegressionTests.java
 * (TestMain also runs this suite as part of the full contract.)
 */
public final class RegressionTests {
    private static int passed = 0;
    private static int failed = 0;

    interface Body { void run() throws Exception; }

    private static void test(String name, Body body) {
        try {
            body.run();
            passed++;
            System.out.println("PASS reg:" + name);
        } catch (Throwable t) {
            failed++;
            System.out.println("FAIL reg:" + name + ": " + t);
        }
    }

    private static void eq(String what, Object expected, Object actual) {
        if (!Objects.equals(expected, actual)) {
            throw new AssertionError(what + ": expected <" + expected + "> got <" + actual + ">");
        }
    }

    static CrewBoard fixtureBoard() {
        CrewBoard b = new CrewBoard();
        b.post(new Posting("B-1001", "Deckhand for harbor tug",
                "Line handling and barge moves in the ship channel.",
                "tug", "Galveston", "deckhand", 32000, 9010));
        b.post(new Posting("B-1002", "Deckhand wanted immediately",
                "Trawler deck work; tug time counts toward sea days.",
                "trawler", "Dutch Harbor", "deckhand", 41000, 9020));
        b.post(new Posting("B-1003", "Tug mate, nights",
                "Backs up the master; shares deckhand duties on deck.",
                "tug", "New Bedford", "mate", 45000, 9020));
        b.post(new Posting("B-1004", "Galley cook",
                "Cook for a crab boat; no deckhand or tug experience needed.",
                "trawler", "Dutch Harbor", "cook", 30000, 9005));
        b.post(new Posting("B-1005", "Harbor ferry deckhand",
                "Weekend shifts; tug assists during dockings.",
                "ferry", "Galveston", "deckhand", 28000, 9020));
        b.post(new Posting("B-1006", "Ferry master",
                "Twin screw, day boat, no overnights.",
                "ferry", "New Bedford", "master", 52000, 9015));
        b.post(new Posting("B-1007", "Ferry engineer",
                "Keep the old girl running.",
                "ferry", "New Bedford", "engineer", 50000, 9015));
        b.post(new Posting("B-1008", "Night ferry mate",
                "Ferry runs across the sound.",
                "ferry", "Galveston", "mate", 47000, 9022));
        b.post(new Posting("B-1009", "OSV able seaman",
                "Supply runs to the platforms.",
                "osv", "Galveston", "ab", 39000, 9001));
        return b;
    }

    static List<String> ids(List<Posting> postings) {
        List<String> ids = new ArrayList<>();
        for (Posting p : postings) {
            ids.add(p.id());
        }
        return ids;
    }

    /** Runs the suite, printing one line per test; returns the failure count. */
    public static int runAll() {
        passed = 0;
        failed = 0;

        test("post_and_size", () -> {
            eq("size", 9, fixtureBoard().size());
        });

        test("duplicate_posting_ids_are_rejected", () -> {
            CrewBoard b = fixtureBoard();
            try {
                b.post(new Posting("B-1001", "Anything", "Anything.", "tug",
                        "Galveston", "deckhand", 1000, 9030));
                throw new AssertionError("expected IllegalArgumentException");
            } catch (IllegalArgumentException e) {
                eq("message", "duplicate posting 'B-1001'", e.getMessage());
            }
            eq("size unchanged", 9, b.size());
        });

        test("search_requires_every_term_in_title_or_description", () -> {
            eq("harbor AND barge", List.of("B-1001"), ids(fixtureBoard().search("harbor barge")));
        });

        test("search_orders_by_posted_day_desc_then_id", () -> {
            eq("tug deckhand order",
                    List.of("B-1002", "B-1003", "B-1005", "B-1001", "B-1004"),
                    ids(fixtureBoard().search("tug deckhand")));
        });

        test("search_is_case_insensitive", () -> {
            eq("FERRY", List.of("B-1008", "B-1005", "B-1006", "B-1007"),
                    ids(fixtureBoard().search("FERRY")));
        });

        test("blank_query_lists_the_whole_board_freshest_first", () -> {
            List<String> all = List.of("B-1008", "B-1002", "B-1003", "B-1005",
                    "B-1006", "B-1007", "B-1001", "B-1004", "B-1009");
            eq("empty string", all, ids(fixtureBoard().search("")));
            eq("whitespace", all, ids(fixtureBoard().search("   ")));
            eq("null", all, ids(fixtureBoard().search(null)));
        });

        test("no_match_returns_empty_list", () -> {
            eq("kraken", List.of(), ids(fixtureBoard().search("kraken")));
        });

        System.out.println("regression: " + passed + " passed, " + failed + " failed");
        return failed;
    }

    public static void main(String[] args) {
        int f = runAll();
        System.exit(f > 0 ? 1 : 0);
    }
}
