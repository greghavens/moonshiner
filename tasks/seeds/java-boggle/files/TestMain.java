import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Objects;

/**
 * Acceptance tests for the word-hunt puzzle checker.
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

    private static Boggle grid(String name) throws Exception {
        return Boggle.parse(Files.readString(Path.of("grids", name)));
    }

    private static List<String> dict() throws Exception {
        return Boggle.dictionary(Files.readString(Path.of("words.txt")));
    }

    private static final List<String> LETTERS_WORDS = List.of(
            "CEDE", "CODE", "COED", "COG", "COLD", "DECO", "DEMO", "DOE", "DOG", "DOME",
            "GEM", "GIRL", "GOD", "GOLD", "GRIM", "GRIME", "GRIN", "KING", "LATE", "LED",
            "LOG", "MODE", "MOLE", "ODE", "OGLE", "OLD", "RING", "RODE", "ROLE", "SPAR",
            "SPRIG", "SPRING", "TALE", "TAP", "TAPS", "TEAL");

    private static final List<String> QUVEIL_WORDS = List.of(
            "CANE", "CAP", "CLOD", "CODE", "COED", "COLD", "CONE", "CORD", "DOCK", "DOE",
            "EQUIP", "LOCK", "NAP", "NOTE", "OAK", "ODE", "OLD", "PACK", "PAN", "QUA",
            "QUACK", "QUANT", "QUEST", "QUIP", "REDO", "ROCK", "RODE", "SENT", "SNORE",
            "SNORT", "STONE", "TENOR", "TONE");

    public static void main(String[] args) throws Exception {

        // ---- grid parsing ----------------------------------------------

        test("parse_accepts_a_plain_grid", () -> {
            Boggle b = grid("letters.grid");
            yes("has a simple word", b.contains("DOG"));
        });

        test("parse_accepts_the_Qu_tile", () -> {
            Boggle b = grid("quveil.grid");
            yes("Qu tile matched as QU", b.contains("QUA"));
        });

        test("parse_rejects_wrong_row_count", () -> {
            thrown(IllegalArgumentException.class,
                    () -> Boggle.parse("A B C D\nE F G H\nI J K L"));
            thrown(IllegalArgumentException.class,
                    () -> Boggle.parse("A B C D\nE F G H\nI J K L\nM N O P\nR S T U"));
        });

        test("parse_rejects_wrong_column_count", () -> {
            thrown(IllegalArgumentException.class,
                    () -> Boggle.parse("A B C\nE F G H\nI J K L\nM N O P"));
            thrown(IllegalArgumentException.class,
                    () -> Boggle.parse("A B C D E\nE F G H\nI J K L\nM N O P"));
        });

        test("parse_rejects_bad_tiles", () -> {
            // a bare Q is not a tile: the cube face is Qu
            thrown(IllegalArgumentException.class,
                    () -> Boggle.parse("Q B C D\nE F G H\nI J K L\nM N O P"));
            thrown(IllegalArgumentException.class,
                    () -> Boggle.parse("a B C D\nE F G H\nI J K L\nM N O P"));
            thrown(IllegalArgumentException.class,
                    () -> Boggle.parse("AB B C D\nE F G H\nI J K L\nM N O P"));
            thrown(IllegalArgumentException.class,
                    () -> Boggle.parse("QU B C D\nE F G H\nI J K L\nM N O P"));
        });

        // ---- dictionary loading ----------------------------------------

        test("dictionary_normalizes_and_skips_blanks", () -> {
            eq("words", List.of("APPLE", "PEAR"), Boggle.dictionary("apple\n\n  Pear \n"));
        });

        test("dictionary_file_loads_fully", () -> {
            eq("size", 82, dict().size());
        });

        // ---- path finding ----------------------------------------------

        test("contains_follows_diagonal_paths", () -> {
            Boggle b = grid("letters.grid");
            yes("TEAL zig-zags", b.contains("TEAL"));
            yes("SPRING snakes through six cells", b.contains("SPRING"));
        });

        test("contains_is_case_insensitive", () -> {
            Boggle b = grid("letters.grid");
            yes("lowercase query", b.contains("spring"));
        });

        test("contains_requires_adjacency", () -> {
            Boggle b = grid("letters.grid");
            yes("RASP letters exist but do not connect", !b.contains("RASP"));
            yes("OGRE letters exist but do not connect", !b.contains("OGRE"));
        });

        test("a_cell_may_be_used_only_once_per_word", () -> {
            Boggle b = grid("letters.grid");
            yes("DOLED needs the single D twice", !b.contains("DOLED"));
            Boggle q = grid("quveil.grid");
            yes("ERODE needs the same E at both ends", !q.contains("ERODE"));
            yes("NOON needs O and N twice", !q.contains("NOON"));
        });

        test("repeated_letters_on_distinct_cells_are_fine", () -> {
            Boggle b = grid("letters.grid");
            yes("CEDE uses the two different Es", b.contains("CEDE"));
        });

        test("qu_tile_spends_both_letters_together", () -> {
            Boggle q = grid("quveil.grid");
            yes("QUIP runs through the Qu tile", q.contains("QUIP"));
            yes("EQUIP enters the Qu tile mid-word", q.contains("EQUIP"));
            yes("the whole tile is QU, not Q", !q.contains("Q"));
            yes("a word cannot stop halfway into the tile", !q.contains("PIQ"));
            yes("QU alone is the full tile", q.contains("QU"));
            yes("QUIT: I connects but T does not", !q.contains("QUIT"));
        });

        // ---- scoring ---------------------------------------------------

        test("score_follows_the_length_table", () -> {
            eq("2 letters", 0, Boggle.score("GO"));
            eq("3 letters", 1, Boggle.score("CAT"));
            eq("4 letters", 1, Boggle.score("CATS"));
            eq("5 letters", 2, Boggle.score("QUEST"));
            eq("6 letters", 3, Boggle.score("SPRING"));
            eq("7 letters", 5, Boggle.score("SPARROW"));
            eq("8 letters", 11, Boggle.score("SPARKLES"));
            eq("9 letters", 11, Boggle.score("BLACKJACK"));
        });

        test("qu_words_score_by_word_length_not_cells_used", () -> {
            // QUIP touches three cells but is a four-letter word
            eq("QUIP", 1, Boggle.score("QUIP"));
            eq("QUA", 1, Boggle.score("QUA"));
        });

        // ---- find-all sweep --------------------------------------------

        test("findAll_letters_grid_pinned", () -> {
            eq("word list", LETTERS_WORDS, grid("letters.grid").findAll(dict()));
        });

        test("findAll_quveil_grid_pinned", () -> {
            eq("word list", QUVEIL_WORDS, grid("quveil.grid").findAll(dict()));
        });

        test("findAll_drops_words_shorter_than_three", () -> {
            // GO and PA are both on the boards but never score
            Boggle b = grid("letters.grid");
            eq("short words gone", List.of("DOG"), b.findAll(List.of("GO", "DOG", "PA")));
        });

        test("findAll_reports_each_word_once", () -> {
            Boggle b = grid("letters.grid");
            eq("dedup", List.of("CODE", "DOG"),
                    b.findAll(List.of("DOG", "CODE", "DOG", "code")));
        });

        test("total_scores_pinned", () -> {
            eq("letters grid", 40, grid("letters.grid").totalScore(dict()));
            eq("quveil grid", 41, grid("quveil.grid").totalScore(dict()));
        });

        test("report_lists_words_with_scores_and_total", () -> {
            String expected = "QUA 1\nQUEST 2\nQUIP 1\ntotal 4\n";
            eq("report", expected, grid("quveil.grid")
                    .report(List.of("QUEST", "QUIP", "QUA", "QUIT", "MOCK")));
        });

        test("report_with_no_hits_is_just_the_total", () -> {
            eq("report", "total 0\n", grid("letters.grid").report(List.of("QUEEN", "XYLOPHONE")));
        });

        System.out.println(passed + " passed, " + failed + " failed");
        if (failed > 0) System.exit(1);
    }
}
