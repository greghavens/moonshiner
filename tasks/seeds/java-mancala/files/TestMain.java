import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Objects;

/**
 * Acceptance tests for the club Kalah referee engine.
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

    private static String fixture(String name) throws Exception {
        return Files.readString(Path.of("positions", name));
    }

    public static void main(String[] args) throws Exception {

        // ---- parsing and rendering -------------------------------------

        test("parse_render_round_trip_start", () -> {
            Kalah g = Kalah.parse(fixture("start.pos"));
            eq("render", "turn A\nA 4 4 4 4 4 4 | 0\nB 4 4 4 4 4 4 | 0", g.render());
            eq("turn", 'A', g.turn());
            yes("not over", !g.isOver());
        });

        test("parse_render_round_trip_midgame", () -> {
            Kalah g = Kalah.parse(fixture("capture.pos"));
            eq("render", "turn A\nA 1 0 4 4 4 4 | 3\nB 5 4 4 1 4 4 | 2", g.render());
        });

        test("parse_tolerates_blank_lines_and_padding", () -> {
            Kalah g = Kalah.parse("\n  turn B  \n\nA 1 2 3 4 5 6 | 7\n  B 6 5 4 3 2 1 | 8 \n\n");
            eq("render", "turn B\nA 1 2 3 4 5 6 | 7\nB 6 5 4 3 2 1 | 8", g.render());
            eq("turn", 'B', g.turn());
        });

        test("parse_rejects_bad_turn_line", () -> {
            thrown(IllegalArgumentException.class,
                    () -> Kalah.parse("turn C\nA 4 4 4 4 4 4 | 0\nB 4 4 4 4 4 4 | 0"));
            thrown(IllegalArgumentException.class,
                    () -> Kalah.parse("A 4 4 4 4 4 4 | 0\nB 4 4 4 4 4 4 | 0"));
        });

        test("parse_rejects_wrong_pit_count", () -> {
            thrown(IllegalArgumentException.class,
                    () -> Kalah.parse("turn A\nA 4 4 4 4 4 | 0\nB 4 4 4 4 4 4 | 0"));
            thrown(IllegalArgumentException.class,
                    () -> Kalah.parse("turn A\nA 4 4 4 4 4 4 4 | 0\nB 4 4 4 4 4 4 | 0"));
        });

        test("parse_rejects_negative_and_garbage", () -> {
            thrown(IllegalArgumentException.class,
                    () -> Kalah.parse("turn A\nA 4 4 -1 4 4 4 | 0\nB 4 4 4 4 4 4 | 0"));
            thrown(IllegalArgumentException.class,
                    () -> Kalah.parse("turn A\nA 4 4 x 4 4 4 | 0\nB 4 4 4 4 4 4 | 0"));
        });

        test("parse_rejects_missing_store_separator", () -> {
            thrown(IllegalArgumentException.class,
                    () -> Kalah.parse("turn A\nA 4 4 4 4 4 4 0\nB 4 4 4 4 4 4 | 0"));
        });

        test("parse_rejects_missing_or_extra_rows", () -> {
            thrown(IllegalArgumentException.class,
                    () -> Kalah.parse("turn A\nA 4 4 4 4 4 4 | 0"));
            thrown(IllegalArgumentException.class,
                    () -> Kalah.parse("turn A\nA 4 4 4 4 4 4 | 0\nB 4 4 4 4 4 4 | 0\nB 4 4 4 4 4 4 | 0"));
            thrown(IllegalArgumentException.class,
                    () -> Kalah.parse("turn A\nB 4 4 4 4 4 4 | 0\nA 4 4 4 4 4 4 | 0"));
        });

        // ---- basic sowing ----------------------------------------------

        test("opening_move_into_store_earns_extra_turn", () -> {
            Kalah g = Kalah.parse(fixture("start.pos"));
            eq("event", "extra", g.move(3));
            eq("board", "turn A\nA 4 4 0 5 5 5 | 1\nB 4 4 4 4 4 4 | 0", g.render());
            eq("still A to move", 'A', g.turn());
        });

        test("plain_move_passes_the_turn", () -> {
            Kalah g = Kalah.parse(fixture("start.pos"));
            g.move(3);
            eq("event", "move", g.move(6));
            eq("board", "turn B\nA 4 4 0 5 5 0 | 2\nB 5 5 5 5 4 4 | 0", g.render());
        });

        test("sowing_skips_the_opponents_store", () -> {
            Kalah g = Kalah.parse(fixture("longsow.pos"));
            eq("event", "move", g.move(3));
            eq("board", "turn B\nA 5 4 0 5 5 5 | 1\nB 4 4 4 4 4 4 | 7", g.render());
        });

        test("thirteen_stones_wrap_all_the_way_home", () -> {
            Kalah g = Kalah.parse(fixture("wrap13.pos"));
            eq("event", "capture 5", g.move(3));
            eq("board", "turn B\nA 5 5 0 5 5 5 | 6\nB 4 4 4 0 4 4 | 7", g.render());
        });

        // ---- capture rule ----------------------------------------------

        test("last_stone_in_own_empty_pit_captures_opposite", () -> {
            Kalah g = Kalah.parse(fixture("capture.pos"));
            eq("event", "capture 5", g.move(1));
            eq("board", "turn B\nA 0 0 4 4 4 4 | 8\nB 5 4 4 1 0 4 | 2", g.render());
        });

        test("no_capture_when_opposite_pit_is_empty", () -> {
            Kalah g = Kalah.parse(fixture("nocapture.pos"));
            eq("event", "move", g.move(2));
            eq("board", "turn A\nA 1 0 4 0 0 4 | 3\nB 4 0 1 4 4 4 | 2", g.render());
        });

        test("landing_in_own_occupied_pit_does_not_capture", () -> {
            Kalah g = Kalah.parse("turn A\nA 2 0 3 4 4 4 | 3\nB 5 4 4 1 4 4 | 2");
            eq("event", "move", g.move(1));
            eq("board", "turn B\nA 0 1 4 4 4 4 | 3\nB 5 4 4 1 4 4 | 2", g.render());
        });

        test("landing_in_opponents_pit_never_captures", () -> {
            Kalah g = Kalah.parse("turn A\nA 4 4 4 4 4 4 | 0\nB 4 4 0 4 4 4 | 0");
            // last stone falls into B's empty third pit: nothing happens
            eq("event", "move", g.move(6));
            eq("board", "turn B\nA 4 4 4 4 4 0 | 1\nB 5 5 1 4 4 4 | 0", g.render());
        });

        // ---- illegal moves ---------------------------------------------

        test("cannot_sow_an_empty_pit", () -> {
            Kalah g = Kalah.parse(fixture("capture.pos"));
            thrown(IllegalArgumentException.class, () -> g.move(2));
            eq("board unchanged", "turn A\nA 1 0 4 4 4 4 | 3\nB 5 4 4 1 4 4 | 2", g.render());
        });

        test("pit_number_must_be_1_to_6", () -> {
            Kalah g = Kalah.parse(fixture("start.pos"));
            thrown(IllegalArgumentException.class, () -> g.move(0));
            thrown(IllegalArgumentException.class, () -> g.move(7));
            thrown(IllegalArgumentException.class, () -> g.move(-3));
        });

        // ---- endgame ---------------------------------------------------

        test("capture_that_strands_opponent_triggers_sweep", () -> {
            Kalah g = Kalah.parse(fixture("endgame.pos"));
            eq("event", "capture 4 / sweep A 3 / game over", g.move(1));
            yes("over", g.isOver());
            eq("board", "turn -\nA 0 0 0 0 0 0 | 22\nB 0 0 0 0 0 0 | 27", g.render());
            eq("winner", "B", g.winner());
        });

        test("extra_turn_with_no_stones_left_ends_the_game", () -> {
            Kalah g = Kalah.parse(fixture("drawend.pos"));
            eq("event", "extra / sweep A 4 / game over", g.move(6));
            yes("over", g.isOver());
            eq("board", "turn -\nA 0 0 0 0 0 0 | 24\nB 0 0 0 0 0 0 | 24", g.render());
            eq("winner", "draw", g.winner());
        });

        test("sweep_goes_to_the_side_that_still_has_stones", () -> {
            Kalah g = Kalah.parse("turn B\nA 0 0 0 0 0 9 | 10\nB 0 0 0 0 0 1 | 28");
            eq("event", "extra / sweep A 9 / game over", g.move(6));
            eq("board", "turn -\nA 0 0 0 0 0 0 | 19\nB 0 0 0 0 0 0 | 29", g.render());
            eq("winner", "B", g.winner());
        });

        test("winner_A_when_its_store_is_bigger", () -> {
            Kalah g = Kalah.parse("turn A\nA 0 0 0 0 0 1 | 30\nB 0 0 0 0 0 0 | 17");
            eq("event", "extra / sweep B 0 / game over", g.move(6));
            eq("winner", "A", g.winner());
        });

        test("no_moves_after_game_over", () -> {
            Kalah g = Kalah.parse(fixture("drawend.pos"));
            g.move(6);
            thrown(IllegalStateException.class, () -> g.move(1));
        });

        test("winner_requires_a_finished_game", () -> {
            Kalah g = Kalah.parse(fixture("start.pos"));
            thrown(IllegalStateException.class, () -> g.winner());
        });

        // ---- transcripts -----------------------------------------------

        test("transcript_of_a_midgame_exchange", () -> {
            String expected = String.join("\n",
                    "=== start ===",
                    "turn A",
                    "A 4 4 4 4 4 4 | 0",
                    "B 4 4 4 4 4 4 | 0",
                    "",
                    "move 1 A pit 3: extra",
                    "turn A",
                    "A 4 4 0 5 5 5 | 1",
                    "B 4 4 4 4 4 4 | 0",
                    "",
                    "move 2 A pit 6: move",
                    "turn B",
                    "A 4 4 0 5 5 0 | 2",
                    "B 5 5 5 5 4 4 | 0",
                    "",
                    "move 3 B pit 4: move",
                    "turn A",
                    "A 5 5 0 5 5 0 | 2",
                    "B 5 5 5 0 5 5 | 1",
                    "",
                    "move 4 A pit 2: extra",
                    "turn A",
                    "A 5 0 1 6 6 1 | 3",
                    "B 5 5 5 0 5 5 | 1",
                    "",
                    "move 5 A pit 3: move",
                    "turn B",
                    "A 5 0 0 7 6 1 | 3",
                    "B 5 5 5 0 5 5 | 1",
                    "",
                    "move 6 B pit 1: move",
                    "turn A",
                    "A 5 0 0 7 6 1 | 3",
                    "B 0 6 6 1 6 6 | 1",
                    "",
                    "move 7 A pit 1: move",
                    "turn B",
                    "A 0 1 1 8 7 2 | 3",
                    "B 0 6 6 1 6 6 | 1",
                    "",
                    "move 8 B pit 2: move",
                    "turn A",
                    "A 1 1 1 8 7 2 | 3",
                    "B 0 0 7 2 7 7 | 2",
                    "",
                    "result: next A",
                    "");
            eq("transcript", expected,
                    Kalah.transcript(fixture("start.pos"), new int[]{3, 6, 4, 2, 3, 1, 1, 2}));
        });

        test("transcript_of_a_finish", () -> {
            String expected = String.join("\n",
                    "=== start ===",
                    "turn A",
                    "A 2 0 0 0 2 0 | 15",
                    "B 0 0 0 3 0 0 | 27",
                    "",
                    "move 1 A pit 1: capture 4 / sweep A 3 / game over",
                    "turn -",
                    "A 0 0 0 0 0 0 | 22",
                    "B 0 0 0 0 0 0 | 27",
                    "",
                    "result: winner B",
                    "");
            eq("transcript", expected,
                    Kalah.transcript(fixture("endgame.pos"), new int[]{1}));
        });

        test("transcript_of_a_draw", () -> {
            String expected = String.join("\n",
                    "=== start ===",
                    "turn B",
                    "A 0 0 0 0 0 4 | 20",
                    "B 0 0 0 0 0 1 | 23",
                    "",
                    "move 1 B pit 6: extra / sweep A 4 / game over",
                    "turn -",
                    "A 0 0 0 0 0 0 | 24",
                    "B 0 0 0 0 0 0 | 24",
                    "",
                    "result: winner draw",
                    "");
            eq("transcript", expected,
                    Kalah.transcript(fixture("drawend.pos"), new int[]{6}));
        });

        test("transcript_aborts_on_an_illegal_move", () -> {
            thrown(IllegalArgumentException.class,
                    () -> Kalah.transcript(fixture("capture.pos"), new int[]{2}));
        });

        test("transcript_with_no_moves_is_just_the_start_block", () -> {
            String expected = String.join("\n",
                    "=== start ===",
                    "turn A",
                    "A 4 4 4 4 4 4 | 0",
                    "B 4 4 4 4 4 4 | 0",
                    "",
                    "result: next A",
                    "");
            eq("transcript", expected, Kalah.transcript(fixture("start.pos"), new int[]{}));
        });

        System.out.println(passed + " passed, " + failed + " failed");
        if (failed > 0) System.exit(1);
    }
}
