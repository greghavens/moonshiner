import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Objects;

/**
 * Acceptance tests for the correspondence-club move-legality engine.
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

    private static String fixtureText(String name) throws Exception {
        return Files.readString(Path.of("positions", name + ".pos")).trim();
    }

    private static Position fixture(String name) throws Exception {
        return Position.parse(fixtureText(name));
    }

    /** Builds a position block from compact 8-character rank strings (rank 8 first). */
    private static String pos(String turn, String castles, String ep, String... ranks) {
        StringBuilder sb = new StringBuilder();
        sb.append("turn ").append(turn).append('\n');
        sb.append("castles ").append(castles).append('\n');
        sb.append("enpassant ").append(ep);
        for (int i = 0; i < 8; i++) {
            sb.append('\n').append(8 - i);
            for (char c : ranks[i].toCharArray()) sb.append(' ').append(c);
        }
        return sb.toString();
    }

    private static long perft(Position p, int depth) {
        List<String> moves = p.legalMoves();
        if (depth == 1) return moves.size();
        long total = 0;
        for (String m : moves) total += perft(p.apply(m), depth - 1);
        return total;
    }

    public static void main(String[] args) throws Exception {

        // ---- parsing and rendering ------------------------------------------

        test("fixtures_round_trip_through_render", () -> {
            eq("start", fixtureText("start"), fixture("start").render());
            eq("kiwipete", fixtureText("kiwipete"), fixture("kiwipete").render());
            eq("turn", 'w', fixture("start").turn());
        });

        test("parse_rejects_bad_headers", () -> {
            String board = fixtureText("start");
            thrown(IllegalArgumentException.class,
                    () -> Position.parse(board.replace("turn w", "turn x")));
            thrown(IllegalArgumentException.class,
                    () -> Position.parse(board.replace("castles KQkq", "castles QK")));
            thrown(IllegalArgumentException.class,
                    () -> Position.parse(board.replace("castles KQkq", "castles KK")));
            thrown(IllegalArgumentException.class,
                    () -> Position.parse(board.replace("enpassant -", "enpassant x9")));
            // a white-to-move en passant target must sit on rank 6
            thrown(IllegalArgumentException.class,
                    () -> Position.parse(board.replace("enpassant -", "enpassant e3")));
        });

        test("parse_rejects_malformed_boards", () -> {
            String board = fixtureText("start");
            thrown(IllegalArgumentException.class,
                    () -> Position.parse(board.replace("5 . . . . . . . .\n", "")));
            thrown(IllegalArgumentException.class,
                    () -> Position.parse(board.replace("5 . . . . . . . .", "5 . . . . . . .")));
            thrown(IllegalArgumentException.class,
                    () -> Position.parse(board.replace("5 . . . . . . . .", "4 . . . . . . . .")));
            thrown(IllegalArgumentException.class,
                    () -> Position.parse(board.replace("6 . . . . . . . .", "6 . . . z . . . .")));
        });

        test("parse_enforces_king_and_pawn_sanity", () -> {
            thrown(IllegalArgumentException.class, () -> Position.parse(pos("w", "-", "-",
                    "....k...", "........", "........", "........",
                    "........", "........", "........", "..K.K...")));
            thrown(IllegalArgumentException.class, () -> Position.parse(pos("w", "-", "-",
                    "........", "........", "........", "........",
                    "........", "........", "........", "....K...")));
            thrown(IllegalArgumentException.class, () -> Position.parse(pos("w", "-", "-",
                    "P...k...", "........", "........", "........",
                    "........", "........", "........", "....K...")));
        });

        test("castling_rights_must_match_the_board", () -> {
            thrown(IllegalArgumentException.class, () -> Position.parse(pos("w", "K", "-",
                    "....k...", "........", "........", "........",
                    "........", "........", "........", "....K...")));
            thrown(IllegalArgumentException.class, () -> Position.parse(pos("b", "q", "-",
                    "....k..r", "........", "........", "........",
                    "........", "........", "........", "....K...")));
        });

        // ---- pinned move lists ------------------------------------------------

        test("opening_position_has_the_twenty_book_moves", () -> {
            eq("move list", List.of(
                    "a2a3", "a2a4", "b1a3", "b1c3", "b2b3", "b2b4", "c2c3", "c2c4",
                    "d2d3", "d2d4", "e2e3", "e2e4", "f2f3", "f2f4", "g1f3", "g1h3",
                    "g2g3", "g2g4", "h2h3", "h2h4"), fixture("start").legalMoves());
        });

        test("kiwipete_enumeration_pinned", () -> {
            eq("move list", List.of(
                    "a1b1", "a1c1", "a1d1", "a2a3", "a2a4", "b2b3", "c3a4", "c3b1",
                    "c3b5", "c3d1", "d2c1", "d2e3", "d2f4", "d2g5", "d2h6", "d5d6",
                    "d5e6", "e1c1", "e1d1", "e1f1", "e1g1", "e2a6", "e2b5", "e2c4",
                    "e2d1", "e2d3", "e2f1", "e5c4", "e5c6", "e5d3", "e5d7", "e5f7",
                    "e5g4", "e5g6", "f3d3", "f3e3", "f3f4", "f3f5", "f3f6", "f3g3",
                    "f3g4", "f3h3", "f3h5", "g2g3", "g2g4", "g2h3", "h1f1", "h1g1"),
                    fixture("kiwipete").legalMoves());
        });

        test("check_evasions_only", () -> {
            Position p = Position.parse(pos("b", "-", "-",
                    "....k...", "........", "........", "........",
                    "........", "........", "........", "....R.K."));
            eq("status", "check", p.status());
            eq("move list", List.of("e8d7", "e8d8", "e8f7", "e8f8"), p.legalMoves());
        });

        // ---- en passant ----------------------------------------------------------

        test("en_passant_capture_is_offered", () -> {
            Position p = Position.parse(pos("w", "-", "d6",
                    "....k...", "........", "........", "...pP...",
                    "........", "........", "........", "....K..."));
            eq("move list", List.of("e1d1", "e1d2", "e1e2", "e1f1", "e1f2", "e5d6", "e5e6"),
                    p.legalMoves());
            eq("after the capture", pos("b", "-", "-",
                    "....k...", "........", "...P....", "........",
                    "........", "........", "........", "....K..."),
                    p.apply("e5d6").render());
        });

        test("en_passant_that_exposes_the_king_is_illegal", () -> {
            Position p = Position.parse(pos("w", "-", "c6",
                    "....k...", "........", "........", "KPp....r",
                    "........", "........", "........", "........"));
            eq("move list", List.of("a5a4", "a5a6", "a5b6", "b5b6"), p.legalMoves());
        });

        test("only_a_double_push_leaves_an_en_passant_target", () -> {
            Position p = fixture("start");
            eq("double push", pos("b", "KQkq", "e3",
                    "rnbqkbnr", "pppppppp", "........", "........",
                    "....P...", "........", "PPPP.PPP", "RNBQKBNR"),
                    p.apply("e2e4").render());
            yes("quiet move clears it",
                    p.apply("g1f3").render().contains("enpassant -"));
        });

        // ---- promotion ---------------------------------------------------------------

        test("promotions_come_in_all_four_flavours", () -> {
            Position p = Position.parse(pos("w", "-", "-",
                    ".......k", "P.......", "........", "........",
                    "........", "........", "........", "K......."));
            eq("move list", List.of("a1a2", "a1b1", "a1b2",
                    "a7a8b", "a7a8n", "a7a8q", "a7a8r"), p.legalMoves());
            Position q = p.apply("a7a8q");
            eq("board after queening", pos("b", "-", "-",
                    "Q......k", "........", "........", "........",
                    "........", "........", "........", "K......."), q.render());
            eq("the new queen gives check", "check", q.status());
            eq("black must step off the back rank", List.of("h8g7", "h8h7"), q.legalMoves());
        });

        // ---- castling ---------------------------------------------------------------------

        test("castling_both_ways_appears_in_kiwipete", () -> {
            List<String> moves = fixture("kiwipete").legalMoves();
            yes("short", moves.contains("e1g1"));
            yes("long", moves.contains("e1c1"));
        });

        test("castling_through_an_attacked_square_is_illegal", () -> {
            Position p = Position.parse(pos("w", "KQ", "-",
                    "....kr..", "........", "........", "........",
                    "........", "........", "........", "R...K..R"));
            List<String> moves = p.legalMoves();
            yes("f1 is covered, no short castle", !moves.contains("e1g1"));
            yes("long side is clean", moves.contains("e1c1"));
        });

        test("castling_needs_every_square_between_empty", () -> {
            Position p = Position.parse(pos("w", "KQ", "-",
                    "....k...", "........", "........", "........",
                    "........", "........", "........", "RN..K..R"));
            List<String> moves = p.legalMoves();
            yes("b1 knight blocks the long side", !moves.contains("e1c1"));
            yes("short side is clean", moves.contains("e1g1"));
        });

        test("no_castling_out_of_check", () -> {
            Position p = Position.parse(pos("w", "KQ", "-",
                    "....r..k", "........", "........", "........",
                    "........", "........", "........", "R...K..R"));
            eq("status", "check", p.status());
            List<String> moves = p.legalMoves();
            yes("no short castle", !moves.contains("e1g1"));
            yes("no long castle", !moves.contains("e1c1"));
        });

        test("no_castling_without_the_right", () -> {
            Position p = Position.parse(pos("w", "-", "-",
                    ".......k", "........", "........", "........",
                    "........", "........", "........", "R...K..R"));
            List<String> moves = p.legalMoves();
            yes("no short castle", !moves.contains("e1g1"));
            yes("no long castle", !moves.contains("e1c1"));
            yes("plain king steps remain", moves.contains("e1f1"));
        });

        test("castling_moves_the_rook_too", () -> {
            eq("after short castling", String.join("\n",
                    "turn b",
                    "castles kq",
                    "enpassant -",
                    "8 r . . . k . . r",
                    "7 p . p p q p b .",
                    "6 b n . . p n p .",
                    "5 . . . P N . . .",
                    "4 . p . . P . . .",
                    "3 . . N . . Q . p",
                    "2 P P P B B P P P",
                    "1 R . . . . R K ."), fixture("kiwipete").apply("e1g1").render());
        });

        test("moving_a_rook_forfeits_that_side", () -> {
            eq("after h1g1", String.join("\n",
                    "turn b",
                    "castles Qkq",
                    "enpassant -",
                    "8 r . . . k . . r",
                    "7 p . p p q p b .",
                    "6 b n . . p n p .",
                    "5 . . . P N . . .",
                    "4 . p . . P . . .",
                    "3 . . N . . Q . p",
                    "2 P P P B B P P P",
                    "1 R . . . K . R ."), fixture("kiwipete").apply("h1g1").render());
            eq("after a1b1", String.join("\n",
                    "turn b",
                    "castles Kkq",
                    "enpassant -",
                    "8 r . . . k . . r",
                    "7 p . p p q p b .",
                    "6 b n . . p n p .",
                    "5 . . . P N . . .",
                    "4 . p . . P . . .",
                    "3 . . N . . Q . p",
                    "2 P P P B B P P P",
                    "1 . R . . K . . R"), fixture("kiwipete").apply("a1b1").render());
        });

        test("moving_the_king_forfeits_both_sides", () -> {
            eq("after a2a3 e8d8", String.join("\n",
                    "turn w",
                    "castles KQ",
                    "enpassant -",
                    "8 r . . k . . . r",
                    "7 p . p p q p b .",
                    "6 b n . . p n p .",
                    "5 . . . P N . . .",
                    "4 . p . . P . . .",
                    "3 P . N . . Q . p",
                    "2 . P P B B P P P",
                    "1 R . . . K . . R"),
                    fixture("kiwipete").apply("a2a3").apply("e8d8").render());
        });

        // ---- applying moves --------------------------------------------------------------------

        test("apply_rejects_anything_not_in_the_legal_list", () -> {
            Position p = fixture("start");
            thrown(IllegalArgumentException.class, () -> p.apply("e2e5"));
            thrown(IllegalArgumentException.class, () -> p.apply("e7e5")); // not your piece
            thrown(IllegalArgumentException.class, () -> p.apply("a1a2")); // blocked
            thrown(IllegalArgumentException.class, () -> p.apply("nonsense"));
        });

        test("apply_returns_a_new_position", () -> {
            Position p = fixture("start");
            p.apply("e2e4");
            eq("original untouched", fixtureText("start"), p.render());
        });

        // ---- verdicts ----------------------------------------------------------------------------

        test("status_verdicts_on_the_fixture_set", () -> {
            eq("start", "normal", fixture("start").status());
            eq("kiwipete", "normal", fixture("kiwipete").status());
            eq("pinwork", "normal", fixture("pinwork").status());
            eq("promorush", "check", fixture("promorush").status());
            eq("foolsmate", "checkmate", fixture("foolsmate").status());
            eq("cornered", "stalemate", fixture("cornered").status());
        });

        test("mated_and_stalemated_sides_have_no_moves", () -> {
            eq("foolsmate", List.of(), fixture("foolsmate").legalMoves());
            eq("cornered", List.of(), fixture("cornered").legalMoves());
        });

        // ---- perft: the counts that catch everything ------------------------------------------------

        test("perft_start", () -> {
            Position p = fixture("start");
            eq("depth 1", 20L, perft(p, 1));
            eq("depth 2", 400L, perft(p, 2));
            eq("depth 3", 8902L, perft(p, 3));
        });

        test("perft_kiwipete", () -> {
            Position p = fixture("kiwipete");
            eq("depth 1", 48L, perft(p, 1));
            eq("depth 2", 2039L, perft(p, 2));
            eq("depth 3", 97862L, perft(p, 3));
        });

        test("perft_pinwork", () -> {
            Position p = fixture("pinwork");
            eq("depth 1", 14L, perft(p, 1));
            eq("depth 2", 191L, perft(p, 2));
            eq("depth 3", 2812L, perft(p, 3));
        });

        test("perft_promorush", () -> {
            Position p = fixture("promorush");
            eq("depth 1", 6L, perft(p, 1));
            eq("depth 2", 264L, perft(p, 2));
            eq("depth 3", 9467L, perft(p, 3));
        });

        test("perft_tangle", () -> {
            Position p = fixture("tangle");
            eq("depth 1", 44L, perft(p, 1));
            eq("depth 2", 1486L, perft(p, 2));
            eq("depth 3", 62379L, perft(p, 3));
        });

        System.out.println(passed + " passed, " + failed + " failed");
        if (failed > 0) System.exit(1);
    }
}
