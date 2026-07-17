import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Full acceptance contract for the crew board: the shipped post/search
 * regression suite (RegressionTests) plus the new ranked-search feature —
 * relevance scoring, facet counts, and cursor pagination.
 *
 * Run: java TestMain.java
 */
public final class TestMain {
    private static int passed = 0;
    private static int failed = 0;

    interface Body { void run() throws Exception; }

    private static void test(String name, Body body) {
        try {
            body.run();
            passed++;
            System.out.println("PASS " + name);
        } catch (Throwable t) {
            failed++;
            System.out.println("FAIL " + name + ": " + t);
        }
    }

    private static void eq(String what, Object expected, Object actual) {
        if (!Objects.equals(expected, actual)) {
            throw new AssertionError(what + ": expected <" + expected + "> got <" + actual + ">");
        }
    }

    private static void yes(String what, boolean cond) {
        if (!cond) throw new AssertionError(what);
    }

    private static IllegalArgumentException reject(String what, Runnable r) {
        try {
            r.run();
        } catch (IllegalArgumentException e) {
            return e;
        }
        throw new AssertionError(what + ": expected IllegalArgumentException");
    }

    private static List<String> hitIds(SearchPage page) {
        List<String> ids = new ArrayList<>();
        for (Hit h : page.hits()) {
            ids.add(h.posting().id());
        }
        return ids;
    }

    private static List<Integer> hitScores(SearchPage page) {
        List<Integer> scores = new ArrayList<>();
        for (Hit h : page.hits()) {
            scores.add(h.score());
        }
        return scores;
    }

    public static void main(String[] args) {
        int regressionFailures = RegressionTests.runAll();

        test("scores_follow_the_pinned_formula", () -> {
            SearchPage page = RegressionTests.fixtureBoard()
                    .find(new SearchRequest("deckhand tug", 10, null));
            eq("ranked ids", List.of("B-1001", "B-1003", "B-1002", "B-1005", "B-1004"),
                    hitIds(page));
            eq("scores", List.of(8, 6, 4, 4, 2), hitScores(page));
        });

        test("duplicate_query_terms_count_once", () -> {
            SearchPage once = RegressionTests.fixtureBoard()
                    .find(new SearchRequest("tug deckhand", 10, null));
            SearchPage twice = RegressionTests.fixtureBoard()
                    .find(new SearchRequest("tug tug deckhand TUG", 10, null));
            eq("same ids", hitIds(once), hitIds(twice));
            eq("same scores", hitScores(once), hitScores(twice));
        });

        test("ties_break_by_recency_then_id", () -> {
            SearchPage page = RegressionTests.fixtureBoard()
                    .find(new SearchRequest("ferry", 10, null));
            // B-1008 gets the description bonus; the other three tie at 5 and
            // fall back to postedDay desc, then id asc.
            eq("ranked ids", List.of("B-1008", "B-1005", "B-1006", "B-1007"), hitIds(page));
            eq("scores", List.of(6, 5, 5, 5), hitScores(page));
        });

        test("blank_query_ranks_everything_at_zero", () -> {
            SearchPage page = RegressionTests.fixtureBoard()
                    .find(new SearchRequest("", 20, null));
            eq("ids match plain search order",
                    List.of("B-1008", "B-1002", "B-1003", "B-1005", "B-1006",
                            "B-1007", "B-1001", "B-1004", "B-1009"),
                    hitIds(page));
            eq("all scores zero", List.of(0, 0, 0, 0, 0, 0, 0, 0, 0), hitScores(page));
        });

        test("facet_dimensions_come_in_pinned_order", () -> {
            SearchPage page = RegressionTests.fixtureBoard()
                    .find(new SearchRequest("deckhand tug", 2, null));
            eq("dimensions", "[vesselType, rank, homePort]",
                    page.facets().keySet().toString());
        });

        test("facets_count_the_full_match_set_not_the_page", () -> {
            SearchPage page = RegressionTests.fixtureBoard()
                    .find(new SearchRequest("deckhand tug", 2, null));
            eq("page is trimmed", 2, page.hits().size());
            Map<String, Map<String, Integer>> f = page.facets();
            eq("vesselType", "{trawler=2, tug=2, ferry=1}", f.get("vesselType").toString());
            eq("rank", "{deckhand=3, cook=1, mate=1}", f.get("rank").toString());
            eq("homePort", "{Dutch Harbor=2, Galveston=2, New Bedford=1}",
                    f.get("homePort").toString());
        });

        test("facet_values_order_by_count_desc_then_value_asc", () -> {
            SearchPage page = RegressionTests.fixtureBoard()
                    .find(new SearchRequest("ferry", 1, null));
            Map<String, Map<String, Integer>> f = page.facets();
            eq("vesselType", "{ferry=4}", f.get("vesselType").toString());
            eq("rank", "{deckhand=1, engineer=1, master=1, mate=1}", f.get("rank").toString());
            eq("homePort", "{Galveston=2, New Bedford=2}", f.get("homePort").toString());
        });

        test("cursor_walk_visits_every_hit_once_in_rank_order", () -> {
            CrewBoard board = RegressionTests.fixtureBoard();
            List<String> walked = new ArrayList<>();
            String cursor = null;
            int pages = 0;
            do {
                SearchPage page = board.find(new SearchRequest("deckhand tug", 2, cursor));
                walked.addAll(hitIds(page));
                cursor = page.nextCursor();
                pages++;
            } while (cursor != null);
            eq("page count", 3, pages);
            eq("walked ids", List.of("B-1001", "B-1003", "B-1002", "B-1005", "B-1004"), walked);
        });

        test("final_page_has_null_cursor", () -> {
            SearchPage all = RegressionTests.fixtureBoard()
                    .find(new SearchRequest("deckhand tug", 50, null));
            eq("everything on one page", 5, all.hits().size());
            eq("no next", null, all.nextCursor());
        });

        test("cursor_is_opaque_not_a_raw_id", () -> {
            SearchPage page = RegressionTests.fixtureBoard()
                    .find(new SearchRequest("deckhand tug", 2, null));
            String cursor = page.nextCursor();
            yes("cursor exists", cursor != null);
            yes("cursor does not leak the id", !cursor.contains("B-1003"));
            yes("cursor does not leak the score", !cursor.contains("6|"));
        });

        test("postings_added_ahead_of_the_cursor_do_not_disturb_the_walk", () -> {
            CrewBoard board = RegressionTests.fixtureBoard();
            SearchPage first = board.find(new SearchRequest("deckhand tug", 2, null));
            eq("first page", List.of("B-1001", "B-1003"), hitIds(first));
            // A brand-new top hit lands while the mariner is on page 1.
            board.post(new Posting("B-2000", "Deckhand tug boss",
                    "Deckhand crews on tug jobs across the harbor.",
                    "tug", "Galveston", "foreman", 60000, 9050));
            SearchPage second = board.find(new SearchRequest("deckhand tug", 2, first.nextCursor()));
            eq("page 2 unchanged", List.of("B-1002", "B-1005"), hitIds(second));
            SearchPage fresh = board.find(new SearchRequest("deckhand tug", 3, null));
            eq("fresh search sees the new berth on top",
                    List.of("B-2000", "B-1001", "B-1003"), hitIds(fresh));
            eq("new top score", 10, fresh.hits().get(0).score());
        });

        test("postings_landing_after_the_cursor_position_appear", () -> {
            CrewBoard board = RegressionTests.fixtureBoard();
            SearchPage p1 = board.find(new SearchRequest("deckhand tug", 2, null));
            SearchPage p2 = board.find(new SearchRequest("deckhand tug", 2, p1.nextCursor()));
            eq("page 2", List.of("B-1002", "B-1005"), hitIds(p2));
            board.post(new Posting("B-2001", "Standby galley hand",
                    "Join the deckhand tug standby list at the hall.",
                    "trawler", "Dutch Harbor", "cook", 25000, 9040));
            SearchPage p3 = board.find(new SearchRequest("deckhand tug", 2, p2.nextCursor()));
            eq("late insert ranks into the tail page", List.of("B-2001", "B-1004"), hitIds(p3));
            eq("tail is final", null, p3.nextCursor());
        });

        test("garbage_cursors_are_rejected", () -> {
            CrewBoard board = RegressionTests.fixtureBoard();
            eq("not base64", "bad cursor",
                    reject("junk", () -> board.find(new SearchRequest("ferry", 2, "not-a-cursor!!")))
                            .getMessage());
            eq("wrong payload", "bad cursor",
                    reject("hello", () -> board.find(new SearchRequest("ferry", 2, "aGVsbG8")))
                            .getMessage());
        });

        test("page_size_must_be_positive", () -> {
            CrewBoard board = RegressionTests.fixtureBoard();
            eq("zero", "pageSize must be positive",
                    reject("0", () -> board.find(new SearchRequest("ferry", 0, null))).getMessage());
            eq("negative", "pageSize must be positive",
                    reject("-3", () -> board.find(new SearchRequest("ferry", -3, null))).getMessage());
        });

        test("empty_result_still_reports_facet_dimensions", () -> {
            SearchPage page = RegressionTests.fixtureBoard()
                    .find(new SearchRequest("kraken", 3, null));
            eq("hits", List.of(), hitIds(page));
            eq("dimensions", "[vesselType, rank, homePort]", page.facets().keySet().toString());
            eq("vesselType empty", "{}", page.facets().get("vesselType").toString());
            eq("no next", null, page.nextCursor());
        });

        test("find_matches_exactly_what_search_matches", () -> {
            CrewBoard board = RegressionTests.fixtureBoard();
            List<String> viaSearch = RegressionTests.ids(board.search("harbor barge"));
            SearchPage viaFind = board.find(new SearchRequest("harbor barge", 10, null));
            eq("same matches", viaSearch, hitIds(viaFind));
        });

        System.out.println("feature: " + passed + " passed, " + failed + " failed");
        int total = regressionFailures + failed;
        System.out.println("TOTAL failures: " + total);
        System.exit(total > 0 ? 1 : 0);
    }
}
