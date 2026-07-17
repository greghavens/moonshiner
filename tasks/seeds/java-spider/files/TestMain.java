import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Objects;

/**
 * Acceptance tests for the one-suit Spider engine.
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

    private static <X extends Throwable> X thrown(Class<X> type, Body body) {
        try {
            body.run();
        } catch (Throwable t) {
            if (type.isInstance(t)) return type.cast(t);
            throw new AssertionError("expected " + type.getSimpleName() + " but got " + t, t);
        }
        throw new AssertionError("expected " + type.getSimpleName() + " but nothing was thrown");
    }

    private static String deckText() throws Exception {
        return Files.readString(Path.of("decks", "club.deck"));
    }

    private static Spider layout(String name) throws Exception {
        return Spider.fromLayout(Files.readString(Path.of("layouts", name)));
    }

    private static final String FRESH_RENDER = String.join("\n",
            "stock 5 removed 0",
            "1: ? ? ? ? ? Q",
            "2: ? ? ? ? ? K",
            "3: ? ? ? ? ? A",
            "4: ? ? ? ? ? 2",
            "5: ? ? ? ? 6",
            "6: ? ? ? ? 7",
            "7: ? ? ? ? 8",
            "8: ? ? ? ? 9",
            "9: ? ? ? ? T",
            "10: ? ? ? ? J");

    public static void main(String[] args) throws Exception {

        // ---- dealing a fresh game ----------------------------------------

        test("fresh_deal_render_pinned", () -> {
            eq("initial tableau", FRESH_RENDER, Spider.fromDeck(deckText()).render());
        });

        test("deck_must_hold_104_cards", () -> {
            thrown(IllegalArgumentException.class,
                    () -> Spider.fromDeck(deckText().trim().substring(2))); // 103 cards
            thrown(IllegalArgumentException.class,
                    () -> Spider.fromDeck(deckText() + " A"));
        });

        test("deck_must_hold_eight_of_each_rank", () -> {
            String skewed = deckText().replaceFirst("K", "A"); // nine aces, seven kings
            thrown(IllegalArgumentException.class, () -> Spider.fromDeck(skewed));
        });

        test("deck_rejects_unknown_rank_tokens", () -> {
            thrown(IllegalArgumentException.class,
                    () -> Spider.fromDeck(deckText().replaceFirst("K", "X")));
        });

        // ---- loading a saved layout --------------------------------------

        test("layout_round_trips_through_render", () -> {
            Spider g = layout("gap.layout");
            eq("render", String.join("\n",
                    "stock 1 removed 6",
                    "1: K Q T J 9",
                    "2: K Q J",
                    "3: -",
                    "4: 8 7 6",
                    "5: 5 4 3",
                    "6: 2 A",
                    "7: -",
                    "8: -",
                    "9: -",
                    "10: -"), g.render());
            eq("removed", 6, g.removed());
            yes("not won", !g.won());
        });

        test("layout_rejects_wrong_column_count", () -> {
            thrown(IllegalArgumentException.class, () -> Spider.fromLayout(
                    "removed 8\ncol |\ncol |\ncol |\ncol |\ncol |\ncol |\ncol |\ncol |\ncol |"));
        });

        test("layout_rejects_bad_removed_count", () -> {
            thrown(IllegalArgumentException.class, () -> Spider.fromLayout(
                    "removed 9\ncol |\ncol |\ncol |\ncol |\ncol |\ncol |\ncol |\ncol |\ncol |\ncol |"));
        });

        test("layout_rejects_short_deal_rows", () -> {
            thrown(IllegalArgumentException.class, () -> Spider.fromLayout(String.join("\n",
                    "removed 7",
                    "deal 3 4 5",
                    "col | K Q J T 9 8 7 6 5 4 3 2",
                    "col | A", "col |", "col |", "col |",
                    "col |", "col |", "col |", "col |", "col |")));
        });

        test("layout_card_count_must_reconcile", () -> {
            // an extra five: 105 cards in circulation
            thrown(IllegalArgumentException.class, () -> Spider.fromLayout(String.join("\n",
                    "removed 7",
                    "col | K Q J T 9 8 7 6 5 4 3 2",
                    "col | A", "col | 5", "col |", "col |",
                    "col |", "col |", "col |", "col |", "col |")));
        });

        test("layout_rank_counts_must_reconcile", () -> {
            // 104 cards but a second ace where the two should be
            thrown(IllegalArgumentException.class, () -> Spider.fromLayout(String.join("\n",
                    "removed 7",
                    "col | K Q J T 9 8 7 6 5 4 3 A",
                    "col | A", "col |", "col |", "col |",
                    "col |", "col |", "col |", "col |", "col |")));
        });

        test("layout_rejects_face_down_pile_with_no_face_up_card", () -> {
            thrown(IllegalArgumentException.class, () -> Spider.fromLayout(String.join("\n",
                    "removed 7",
                    "col K Q J T 9 8 7 6 5 4 3 2 |",
                    "col | A", "col |", "col |", "col |",
                    "col |", "col |", "col |", "col |", "col |")));
        });

        // ---- run moves -----------------------------------------------------

        test("single_card_move_flips_what_it_uncovers", () -> {
            Spider g = Spider.fromDeck(deckText());
            g.move(3, 1, 4); // ace onto the two
            eq("board", FRESH_RENDER
                    .replace("3: ? ? ? ? ? A", "3: ? ? ? ? 4")
                    .replace("4: ? ? ? ? ? 2", "4: ? ? ? ? ? 2 A"), g.render());
        });

        test("multi_card_run_moves_as_a_block", () -> {
            Spider g = layout("gap.layout");
            g.move(5, 3, 4); // 5 4 3 onto the 6
            eq("board", String.join("\n",
                    "stock 1 removed 6",
                    "1: K Q T J 9",
                    "2: K Q J",
                    "3: -",
                    "4: 8 7 6 5 4 3",
                    "5: -",
                    "6: 2 A",
                    "7: -",
                    "8: -",
                    "9: -",
                    "10: -"), g.render());
        });

        test("any_run_may_land_on_an_empty_column", () -> {
            Spider g = layout("gap.layout");
            g.move(2, 3, 7); // whole K Q J block to an empty spot
            eq("board", String.join("\n",
                    "stock 1 removed 6",
                    "1: K Q T J 9",
                    "2: -",
                    "3: -",
                    "4: 8 7 6",
                    "5: 5 4 3",
                    "6: 2 A",
                    "7: K Q J",
                    "8: -",
                    "9: -",
                    "10: -"), g.render());
        });

        test("lifted_cards_must_descend_one_by_one", () -> {
            Spider g = layout("gap.layout");
            thrown(IllegalArgumentException.class, () -> g.move(1, 2, 2)); // J 9 is gapped
            thrown(IllegalArgumentException.class, () -> g.move(1, 3, 3)); // T J 9 is not a run
        });

        test("landing_card_must_be_one_rank_below_the_target_top", () -> {
            Spider g = layout("gap.layout");
            thrown(IllegalArgumentException.class, () -> g.move(6, 2, 4)); // 2 onto a 6
            thrown(IllegalArgumentException.class, () -> g.move(1, 1, 4)); // 9 onto a 6
        });

        test("cannot_lift_more_than_the_face_up_cards", () -> {
            Spider g = layout("gap.layout");
            thrown(IllegalArgumentException.class, () -> g.move(6, 3, 3));
            Spider fresh = Spider.fromDeck(deckText());
            thrown(IllegalArgumentException.class, () -> fresh.move(1, 2, 2)); // only one face-up
        });

        test("column_numbers_and_counts_are_checked", () -> {
            Spider g = layout("gap.layout");
            thrown(IllegalArgumentException.class, () -> g.move(0, 1, 2));
            thrown(IllegalArgumentException.class, () -> g.move(1, 1, 11));
            thrown(IllegalArgumentException.class, () -> g.move(4, 0, 5));
            thrown(IllegalArgumentException.class, () -> g.move(4, 1, 4)); // src == dst
            thrown(IllegalArgumentException.class, () -> g.move(3, 1, 4)); // empty source
        });

        // ---- the deal row ---------------------------------------------------

        test("deal_refuses_while_any_column_is_empty", () -> {
            Spider g = layout("gap.layout");
            thrown(IllegalStateException.class, () -> g.deal());
            g.move(2, 3, 3); // fill one gap...
            thrown(IllegalStateException.class, () -> g.deal()); // ...others remain
        });

        test("deal_refuses_when_the_stock_is_empty", () -> {
            Spider g = layout("endgame.layout");
            thrown(IllegalStateException.class, () -> g.deal());
        });

        test("deal_drops_one_card_per_column_left_to_right", () -> {
            Spider g = Spider.fromDeck(deckText());
            g.deal();
            eq("board", String.join("\n",
                    "stock 4 removed 0",
                    "1: ? ? ? ? ? Q 3",
                    "2: ? ? ? ? ? K 4",
                    "3: ? ? ? ? ? A 5",
                    "4: ? ? ? ? ? 2 6",
                    "5: ? ? ? ? 6 7",
                    "6: ? ? ? ? 7 8",
                    "7: ? ? ? ? 8 9",
                    "8: ? ? ? ? 9 T",
                    "9: ? ? ? ? T J",
                    "10: ? ? ? ? J Q"), g.render());
        });

        // ---- completed runs -------------------------------------------------

        test("deal_that_completes_a_run_clears_it_and_flips", () -> {
            Spider g = layout("rundeal.layout");
            eq("before", 4, g.removed());
            g.deal();
            eq("removed run counted", 5, g.removed());
            eq("board", String.join("\n",
                    "stock 0 removed 5",
                    "1: 7",
                    "2: Q J T 9 8 2",
                    "3: K Q J 3",
                    "4: 7 6 5 4",
                    "5: 4 3 2 5",
                    "6: A 6",
                    "7: K Q 8",
                    "8: 7 6 5 9",
                    "9: 4 3 2 T",
                    "10: K T 9 8 A A J"), g.render());
            yes("not won at five runs", !g.won());
        });

        test("move_that_completes_the_eighth_run_wins", () -> {
            Spider g = layout("endgame.layout");
            g.move(2, 1, 1); // the ace caps K..2
            eq("all runs out", 8, g.removed());
            yes("won", g.won());
            eq("board", String.join("\n",
                    "stock 0 removed 8",
                    "1: -", "2: -", "3: -", "4: -", "5: -",
                    "6: -", "7: -", "8: -", "9: -", "10: -"), g.render());
        });

        test("twelve_cards_are_not_a_run", () -> {
            Spider g = layout("endgame.layout");
            eq("still seven", 7, g.removed());
            yes("not won on a near-run", !g.won());
        });

        // ---- scripted replays -----------------------------------------------

        test("replay_of_an_opening", () -> {
            String expected = String.join("\n",
                    "=== start ===",
                    FRESH_RENDER,
                    "",
                    "> m 3 1 4",
                    FRESH_RENDER
                            .replace("3: ? ? ? ? ? A", "3: ? ? ? ? 4")
                            .replace("4: ? ? ? ? ? 2", "4: ? ? ? ? ? 2 A"),
                    "",
                    "> m 5 1 6",
                    FRESH_RENDER
                            .replace("3: ? ? ? ? ? A", "3: ? ? ? ? 4")
                            .replace("4: ? ? ? ? ? 2", "4: ? ? ? ? ? 2 A")
                            .replace("5: ? ? ? ? 6", "5: ? ? ? 9")
                            .replace("6: ? ? ? ? 7", "6: ? ? ? ? 7 6"),
                    "",
                    "> m 8 1 9",
                    FRESH_RENDER
                            .replace("3: ? ? ? ? ? A", "3: ? ? ? ? 4")
                            .replace("4: ? ? ? ? ? 2", "4: ? ? ? ? ? 2 A")
                            .replace("5: ? ? ? ? 6", "5: ? ? ? 9")
                            .replace("6: ? ? ? ? 7", "6: ? ? ? ? 7 6")
                            .replace("8: ? ? ? ? 9", "8: ? ? ? Q")
                            .replace("9: ? ? ? ? T", "9: ? ? ? ? T 9"),
                    "",
                    "> m 6 2 7",
                    FRESH_RENDER
                            .replace("3: ? ? ? ? ? A", "3: ? ? ? ? 4")
                            .replace("4: ? ? ? ? ? 2", "4: ? ? ? ? ? 2 A")
                            .replace("5: ? ? ? ? 6", "5: ? ? ? 9")
                            .replace("6: ? ? ? ? 7", "6: ? ? ? T")
                            .replace("8: ? ? ? ? 9", "8: ? ? ? Q")
                            .replace("9: ? ? ? ? T", "9: ? ? ? ? T 9")
                            .replace("7: ? ? ? ? 8", "7: ? ? ? ? 8 7 6"),
                    "",
                    "> d",
                    String.join("\n",
                            "stock 4 removed 0",
                            "1: ? ? ? ? ? Q 3",
                            "2: ? ? ? ? ? K 4",
                            "3: ? ? ? ? 4 5",
                            "4: ? ? ? ? ? 2 A 6",
                            "5: ? ? ? 9 7",
                            "6: ? ? ? T 8",
                            "7: ? ? ? ? 8 7 6 9",
                            "8: ? ? ? Q T",
                            "9: ? ? ? ? T 9 J",
                            "10: ? ? ? ? J Q"),
                    "",
                    "result: playing",
                    "");
            eq("transcript", expected, Spider.replay(Spider.fromDeck(deckText()),
                    List.of("m 3 1 4", "m 5 1 6", "m 8 1 9", "m 6 2 7", "d")));
        });

        test("replay_of_a_win", () -> {
            String expected = String.join("\n",
                    "=== start ===",
                    String.join("\n",
                            "stock 0 removed 7",
                            "1: K Q J T 9 8 7 6 5 4 3 2",
                            "2: A", "3: -", "4: -", "5: -",
                            "6: -", "7: -", "8: -", "9: -", "10: -"),
                    "",
                    "> m 2 1 1",
                    String.join("\n",
                            "stock 0 removed 8",
                            "1: -", "2: -", "3: -", "4: -", "5: -",
                            "6: -", "7: -", "8: -", "9: -", "10: -"),
                    "",
                    "result: won",
                    "");
            eq("transcript", expected,
                    Spider.replay(layout("endgame.layout"), List.of("m 2 1 1")));
        });

        test("replay_aborts_on_bad_commands", () -> {
            thrown(IllegalArgumentException.class,
                    () -> Spider.replay(layout("gap.layout"), List.of("x 1 2 3")));
            thrown(IllegalArgumentException.class,
                    () -> Spider.replay(layout("gap.layout"), List.of("m 1 2")));
            thrown(IllegalStateException.class,
                    () -> Spider.replay(layout("gap.layout"), List.of("d")));
        });

        System.out.println(passed + " passed, " + failed + " failed");
        if (failed > 0) System.exit(1);
    }
}
