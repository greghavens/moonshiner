// Acceptance tests for the club-ladder Othello referee. Move lists come out
// in row-major order (rank 1 first, file a first within a rank), the greedy
// bot's tie-break is "first in that order", and transcripts are compared
// token for token.

use rust_reversi::{transcript, Game};

const MIDGAME: &str = include_str!("fixtures/midgame.brd");
const RADIAL: &str = include_str!("fixtures/radial.brd");
const ENDPASS: &str = include_str!("fixtures/endpass.brd");
const DRAWN: &str = include_str!("fixtures/drawn.brd");
const OPENING: &str = include_str!("fixtures/opening.txt");

#[test]
fn new_game_is_the_standard_start() {
    let g = Game::new();
    assert_eq!(
        g.render(),
        "\
........
........
........
...WB...
...BW...
........
........
........"
    );
    assert_eq!(g.turn(), 'B');
    assert_eq!(g.counts(), (2, 2));
    assert!(!g.is_over());
    assert_eq!(g.winner(), None);
}

#[test]
fn opening_moves_come_out_row_major_and_the_bot_takes_the_first_tie() {
    let g = Game::new();
    assert_eq!(g.legal_moves(), vec!["d3", "c4", "f5", "e6"]);
    // All four openers flip exactly one disc; the tie-break is row-major.
    assert_eq!(g.bot_move(), Some("d3".to_string()));
}

#[test]
fn load_rejects_malformed_fixtures() {
    assert_eq!(Game::load("").err(), Some("missing turn line".to_string()));
    assert_eq!(
        Game::load("turn: X\n........\n").err(),
        Some("bad turn line: turn: X".to_string())
    );
    let seven = format!("turn: B\n{}", "........\n".repeat(7));
    assert_eq!(Game::load(&seven).err(), Some("expected 8 board rows, got 7".to_string()));
    let short = format!("turn: B\n{}.......\n{}", "........\n".repeat(2), "........\n".repeat(5));
    assert_eq!(Game::load(&short).err(), Some("row 3 must be 8 cells, got 7".to_string()));
    let bad = format!("turn: B\n{}...x....\n{}", "........\n".repeat(3), "........\n".repeat(4));
    assert_eq!(Game::load(&bad).err(), Some("bad cell at d4: 'x'".to_string()));
}

#[test]
fn load_render_round_trips_and_counts() {
    let g = Game::load(MIDGAME).unwrap();
    assert_eq!(g.turn(), 'B');
    assert_eq!(format!("turn: B\n{}\n", g.render()), MIDGAME);
    assert_eq!(g.counts(), (10, 6));
    // Blank lines in a fixture are ignored.
    let spaced = MIDGAME.replace('\n', "\n\n");
    assert_eq!(Game::load(&spaced).unwrap().render(), g.render());
}

#[test]
fn a_single_move_can_flip_in_all_eight_directions() {
    let mut g = Game::load(RADIAL).unwrap();
    assert_eq!(g.legal_moves(), vec!["d4"]);
    g.apply("d4").unwrap();
    assert_eq!(
        g.render(),
        "\
........
.B.B.B..
..BBB...
.BBBBB..
..BBB...
.B.B.B..
........
........"
    );
    assert_eq!(g.counts(), (17, 0));
    assert_eq!(g.turn(), 'W');
}

#[test]
fn illegal_and_malformed_moves_are_rejected_without_side_effects() {
    let mut g = Game::new();
    for bad in ["z9", "d9", "i1", "d", "d10", "4d", ""] {
        assert_eq!(g.apply(bad), Err(format!("bad move: {bad}")));
    }
    // Empty square that flips nothing.
    assert_eq!(g.apply("a1"), Err("illegal move: a1".to_string()));
    // Occupied square.
    assert_eq!(g.apply("d4"), Err("illegal move: d4".to_string()));
    // Passing while moves exist.
    assert_eq!(g.apply("pass"), Err("cannot pass: legal moves exist".to_string()));
    assert_eq!(g.turn(), 'B');
    assert_eq!(g.counts(), (2, 2));
}

#[test]
fn a_stuck_player_must_pass_and_the_turn_flips() {
    let mut g = Game::load(ENDPASS).unwrap();
    assert_eq!(g.turn(), 'W');
    assert_eq!(g.legal_moves(), Vec::<String>::new());
    assert!(!g.is_over(), "black can still move, so the game is not over");
    assert_eq!(g.bot_move(), Some("pass".to_string()));
    assert_eq!(g.apply("h8"), Err("illegal move: h8".to_string()));
    g.apply("pass").unwrap();
    assert_eq!(g.turn(), 'B');
    assert_eq!(g.legal_moves(), vec!["h8"]);
    g.apply("h8").unwrap();
    assert!(g.is_over());
    assert_eq!(g.winner(), Some('B'));
    assert_eq!(g.counts(), (63, 0));
    assert_eq!(g.apply("pass"), Err("game over".to_string()));
    assert_eq!(g.bot_move(), None);
}

#[test]
fn a_full_board_split_evenly_is_a_draw() {
    let g = Game::load(DRAWN).unwrap();
    assert!(g.is_over());
    assert_eq!(g.counts(), (32, 32));
    assert_eq!(g.winner(), Some('='));
}

#[test]
fn midgame_fixture_pins_move_list_bot_choice_and_result() {
    let mut g = Game::load(MIDGAME).unwrap();
    assert_eq!(g.legal_moves(), vec!["d1", "f1", "f2", "f4", "f5", "f6"]);
    // f5 flips two discs, strictly more than any earlier square.
    assert_eq!(g.bot_move(), Some("f5".to_string()));
    g.apply("f5").unwrap();
    assert_eq!(g.turn(), 'W');
    assert_eq!(g.counts(), (13, 4));
    assert_eq!(
        g.render(),
        "\
WB......
.W..W...
.BBBWBBB
..BBB...
...BBB..
........
........
........"
    );
}

#[test]
fn bot_vs_bot_from_the_standard_start_is_pinned() {
    let t = transcript("").unwrap();
    let expected = "\
move 1 (B): d3
move 2 (W): c3
move 3 (B): b3
move 4 (W): b2
move 5 (B): b1
move 6 (W): e3
move 7 (B): f3
move 8 (W): a1
move 9 (B): c4
move 10 (W): g3
move 11 (B): h3
move 12 (W): e2
move 13 (B): f5
move 14 (W): a3
move 15 (B): e1
move 16 (W): d6
move 17 (B): c2
move 18 (W): d2
move 19 (B): a2
move 20 (W): c1
move 21 (B): d7
move 22 (W): g6
move 23 (B): d1
move 24 (W): c5
move 25 (B): e6
move 26 (W): f2
move 27 (B): g2
move 28 (W): e7
move 29 (B): e8
move 30 (W): f4
move 31 (B): f6
move 32 (W): h2
move 33 (B): f1
move 34 (W): g1
move 35 (B): h1
move 36 (W): b4
move 37 (B): c6
move 38 (W): c7
move 39 (B): b8
move 40 (W): f7
move 41 (B): g8
move 42 (W): d8
move 43 (B): g4
move 44 (W): h4
move 45 (B): b5
move 46 (W): c8
move 47 (B): b7
move 48 (W): b6
move 49 (B): g5
move 50 (W): h5
move 51 (B): a6
move 52 (W): f8
move 53 (B): g7
move 54 (W): h7
move 55 (B): h6
move 56 (W): a8
move 57 (B): a4
move 58 (W): a5
move 59 (B): h8
move 60 (W): a7
final:
WWWWWWWB
WWWWWWBB
WWWBWWBB
WWBWWWBB
WWWWWWBB
WWWWBBBB
WWWWWWBB
WWWWWWBB
black: 19 white: 45
winner: W";
    assert_eq!(t, expected);
}

#[test]
fn transcript_replays_the_fixture_opening_then_finishes_greedily() {
    let t = transcript(OPENING.trim()).unwrap();
    let lines: Vec<&str> = t.lines().collect();
    assert_eq!(lines[0], "move 1 (B): d3");
    assert_eq!(lines[1], "move 2 (W): c5");
    assert_eq!(lines[5], "move 6 (W): e3");
    // First bot continuation after the six scripted moves:
    assert_eq!(lines[6], "move 7 (B): c4");
    assert_eq!(lines[59], "move 60 (W): h8");
    assert_eq!(
        &t[t.find("final:").unwrap()..],
        "\
final:
WWWBBBBB
WWWWWWWB
WBWWBWBB
WBWBWBWB
WBWWBWBB
WBWBWWWB
WWBBBBWB
WWWWWWWW
black: 26 white: 38
winner: W"
    );
}

#[test]
fn transcript_rejects_bad_openings_with_the_move_number() {
    assert_eq!(
        transcript("a1"),
        Err("opening move 1: illegal move: a1".to_string())
    );
    assert_eq!(
        transcript("d3 d3"),
        Err("opening move 2: illegal move: d3".to_string())
    );
    assert_eq!(
        transcript("d3 zz"),
        Err("opening move 2: bad move: zz".to_string())
    );
    assert_eq!(
        transcript("pass"),
        Err("opening move 1: cannot pass: legal moves exist".to_string())
    );
}
