import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Objects;

/**
 * Acceptance tests for the harbour-duel referee.
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

    private static String fleet(String name) throws Exception {
        return Files.readString(Path.of("fleets", name));
    }

    private static List<String> shots(String name) throws Exception {
        return Files.readAllLines(Path.of("shots", name)).stream()
                .map(String::trim).filter(s -> !s.isEmpty()).toList();
    }

    public static void main(String[] args) throws Exception {

        // ---- placement validation ---------------------------------------

        test("valid_fixture_fleets_parse", () -> {
            Board a = Board.parse(fleet("alpha.fleet"));
            Board b = Board.parse(fleet("bravo.fleet"));
            yes("alpha afloat", !a.allSunk());
            yes("bravo afloat", !b.allSunk());
        });

        test("own_render_of_a_fresh_board", () -> {
            String expected = String.join("\n",
                    "A .......###",
                    "B .#####....",
                    "C ..........",
                    "D ..........",
                    "E ....#.....",
                    "F ....#.....",
                    "G ....#.....",
                    "H ....#..###",
                    "I ..........",
                    "J ##........");
            eq("alpha own grid", expected, Board.parse(fleet("alpha.fleet")).renderOwn());
        });

        test("ships_may_not_touch_diagonally", () -> {
            thrown(IllegalArgumentException.class, () -> Board.parse(String.join("\n",
                    "carrier B2 H",
                    "destroyer C7 H",
                    "battleship E1 V",
                    "cruiser J4 H",
                    "submarine H8 H")));
        });

        test("ships_may_not_touch_side_by_side", () -> {
            thrown(IllegalArgumentException.class, () -> Board.parse(String.join("\n",
                    "carrier B2 H",
                    "submarine C4 H",
                    "battleship E1 V",
                    "cruiser J4 H",
                    "destroyer G8 V")));
        });

        test("ships_may_not_overlap", () -> {
            thrown(IllegalArgumentException.class, () -> Board.parse(String.join("\n",
                    "carrier B2 H",
                    "submarine B4 H",
                    "battleship E1 V",
                    "cruiser J4 H",
                    "destroyer G8 V")));
        });

        test("ships_must_stay_on_the_board", () -> {
            thrown(IllegalArgumentException.class, () -> Board.parse(String.join("\n",
                    "carrier B2 H",
                    "cruiser J9 H",
                    "battleship E1 V",
                    "submarine H8 H",
                    "destroyer D8 V")));
            thrown(IllegalArgumentException.class, () -> Board.parse(String.join("\n",
                    "carrier B2 H",
                    "cruiser J4 H",
                    "battleship E1 V",
                    "submarine H8 H",
                    "destroyer J10 V")));
        });

        test("the_whole_fleet_must_be_placed_exactly_once", () -> {
            thrown(IllegalArgumentException.class, () -> Board.parse(String.join("\n",
                    "carrier B2 H",
                    "battleship E5 V",
                    "cruiser H8 H",
                    "submarine A8 H")));
            thrown(IllegalArgumentException.class, () -> Board.parse(String.join("\n",
                    "carrier B2 H",
                    "carrier E5 V",
                    "cruiser H8 H",
                    "submarine A8 H",
                    "destroyer J1 H")));
        });

        test("unknown_ship_classes_are_rejected", () -> {
            thrown(IllegalArgumentException.class, () -> Board.parse(String.join("\n",
                    "frigate B2 H",
                    "battleship E5 V",
                    "cruiser H8 H",
                    "submarine A8 H",
                    "destroyer J1 H")));
        });

        test("bad_coordinates_and_directions_are_rejected", () -> {
            for (String line : List.of("carrier B0 H", "carrier B11 H", "carrier K2 H",
                    "carrier 2B H", "carrier B2 D", "carrier B2", "carrier B02 H")) {
                thrown(IllegalArgumentException.class, () -> Board.parse(String.join("\n",
                        line,
                        "battleship E5 V",
                        "cruiser H8 H",
                        "submarine A8 H",
                        "destroyer J1 H")));
            }
        });

        // ---- shot resolution ---------------------------------------------

        test("miss_hit_and_sink_announcements", () -> {
            Board b = Board.parse(fleet("alpha.fleet"));
            eq("open water", "miss", b.shoot("C3"));
            eq("first hit", "hit", b.shoot("J1"));
            yes("hit ship not yet sunk", !b.allSunk());
            eq("finishing shot names the ship", "sunk destroyer", b.shoot("J2"));
        });

        test("coordinates_are_case_insensitive", () -> {
            Board b = Board.parse(fleet("alpha.fleet"));
            eq("lowercase works", "hit", b.shoot("b2"));
        });

        test("the_same_square_cannot_be_shot_twice", () -> {
            Board b = Board.parse(fleet("alpha.fleet"));
            b.shoot("C3");
            thrown(IllegalArgumentException.class, () -> b.shoot("C3"));
            b.shoot("B2");
            thrown(IllegalArgumentException.class, () -> b.shoot("b2"));
        });

        test("malformed_shot_coordinates_are_rejected", () -> {
            Board b = Board.parse(fleet("alpha.fleet"));
            thrown(IllegalArgumentException.class, () -> b.shoot("K1"));
            thrown(IllegalArgumentException.class, () -> b.shoot("A0"));
            thrown(IllegalArgumentException.class, () -> b.shoot("A11"));
            thrown(IllegalArgumentException.class, () -> b.shoot(""));
        });

        test("sinking_the_whole_fleet", () -> {
            Board b = Board.parse(fleet("bravo.fleet"));
            String[] cells = {"A1", "B1", "C1", "D1", "E1", "A10", "B10", "C10", "D10",
                    "J4", "J5", "J6", "C4", "C5", "C6", "G8", "H8"};
            for (int i = 0; i < cells.length - 1; i++) {
                b.shoot(cells[i]);
                yes("still afloat after " + cells[i], !b.allSunk());
            }
            eq("last cell", "sunk destroyer", b.shoot("H8"));
            yes("all sunk", b.allSunk());
            thrown(IllegalStateException.class, () -> b.shoot("J10"));
        });

        // ---- board renders -----------------------------------------------

        test("own_and_tracking_grids_after_ten_shots", () -> {
            Board b = Board.parse(fleet("bravo.fleet"));
            for (String s : List.of("A1", "B1", "A2", "C1", "D1", "E1", "F5", "C4", "C5", "C6")) {
                b.shoot(s);
            }
            String own = String.join("\n",
                    "A Xo.......#",
                    "B X........#",
                    "C X..XXX...#",
                    "D X........#",
                    "E X.........",
                    "F ....o.....",
                    "G .......#..",
                    "H .......#..",
                    "I ..........",
                    "J ...###....");
            eq("own grid shows damage", own, b.renderOwn());
            String tracking = String.join("\n",
                    "A Xo........",
                    "B X.........",
                    "C X..XXX....",
                    "D X.........",
                    "E X.........",
                    "F ....o.....",
                    "G ..........",
                    "H ..........",
                    "I ..........",
                    "J ..........");
            eq("tracking grid hides intact ships", tracking, b.renderTracking());
        });

        // ---- the match runner ----------------------------------------------

        test("scripted_match_full_transcript", () -> {
            String expected = String.join("\n",
                    "1 A A1: hit",
                    "2 B B2: hit",
                    "3 A B1: hit",
                    "4 B B3: hit",
                    "5 A A2: miss",
                    "6 B B4: hit",
                    "7 A C1: hit",
                    "8 B B5: hit",
                    "9 A D1: hit",
                    "10 B B6: sunk carrier",
                    "11 A E1: sunk carrier",
                    "12 B A8: hit",
                    "13 A F5: miss",
                    "14 B A9: hit",
                    "15 A C4: hit",
                    "16 B A10: sunk submarine",
                    "17 A C5: hit",
                    "18 B E5: hit",
                    "19 A C6: sunk submarine",
                    "20 B F5: hit",
                    "21 A G8: hit",
                    "22 B G5: hit",
                    "23 A H8: sunk destroyer",
                    "24 B H5: sunk battleship",
                    "25 A J4: hit",
                    "26 B J1: hit",
                    "27 A J5: hit",
                    "28 B J2: sunk destroyer",
                    "29 A J6: sunk cruiser",
                    "30 B H8: hit",
                    "31 A A10: hit",
                    "32 B H9: hit",
                    "33 A B10: hit",
                    "34 B A1: miss",
                    "35 A C10: hit",
                    "36 B C3: miss",
                    "37 A J10: miss",
                    "38 B D7: miss",
                    "39 A D10: sunk battleship",
                    "winner A on shot 39",
                    "");
            eq("transcript", expected, Match.run(fleet("alpha.fleet"), fleet("bravo.fleet"),
                    shots("alpha.shots"), shots("bravo.shots")));
        });

        test("match_stops_the_moment_the_last_ship_goes_down", () -> {
            // B never gets to reply to shot 39: exactly 19 of B's shots are consumed
            String transcript = Match.run(fleet("alpha.fleet"), fleet("bravo.fleet"),
                    shots("alpha.shots"), shots("bravo.shots"));
            yes("no 40th shot", !transcript.contains("40 "));
        });

        test("match_with_exhausted_scripts_is_an_error", () -> {
            thrown(IllegalStateException.class, () -> Match.run(
                    fleet("alpha.fleet"), fleet("bravo.fleet"),
                    List.of("A1", "A2"), List.of("C3", "C4")));
        });

        test("a_script_that_repeats_a_square_aborts_the_match", () -> {
            thrown(IllegalArgumentException.class, () -> Match.run(
                    fleet("alpha.fleet"), fleet("bravo.fleet"),
                    List.of("A1", "A1", "A3"), List.of("C3", "C4", "C5")));
        });

        System.out.println(passed + " passed, " + failed + " failed");
        if (failed > 0) System.exit(1);
    }
}
