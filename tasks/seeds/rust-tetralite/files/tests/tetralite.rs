// Acceptance tests for the kiosk Tetris-lite engine. The rotation tables
// below are the same tables the ticket hands the implementer — the engine
// must reproduce them cell for cell, with the box origin pinned at (0, 3) on
// spawn and no wall kicks of any kind.

use rust_tetralite::{Game, Status};

// (row, col) offsets inside the piece box, states 0..3, pieces in feed-char
// order I, O, T, S, Z, J, L.
const TABLE: [(char, [[(usize, usize); 4]; 4]); 7] = [
    ('I', [
        [(1, 0), (1, 1), (1, 2), (1, 3)],
        [(0, 2), (1, 2), (2, 2), (3, 2)],
        [(2, 0), (2, 1), (2, 2), (2, 3)],
        [(0, 1), (1, 1), (2, 1), (3, 1)],
    ]),
    ('O', [
        [(0, 1), (0, 2), (1, 1), (1, 2)],
        [(0, 1), (0, 2), (1, 1), (1, 2)],
        [(0, 1), (0, 2), (1, 1), (1, 2)],
        [(0, 1), (0, 2), (1, 1), (1, 2)],
    ]),
    ('T', [
        [(0, 1), (1, 0), (1, 1), (1, 2)],
        [(0, 1), (1, 1), (1, 2), (2, 1)],
        [(1, 0), (1, 1), (1, 2), (2, 1)],
        [(0, 1), (1, 0), (1, 1), (2, 1)],
    ]),
    ('S', [
        [(0, 1), (0, 2), (1, 0), (1, 1)],
        [(0, 1), (1, 1), (1, 2), (2, 2)],
        [(1, 1), (1, 2), (2, 0), (2, 1)],
        [(0, 0), (1, 0), (1, 1), (2, 1)],
    ]),
    ('Z', [
        [(0, 0), (0, 1), (1, 1), (1, 2)],
        [(0, 2), (1, 1), (1, 2), (2, 1)],
        [(1, 0), (1, 1), (2, 1), (2, 2)],
        [(0, 1), (1, 0), (1, 1), (2, 0)],
    ]),
    ('J', [
        [(0, 0), (1, 0), (1, 1), (1, 2)],
        [(0, 1), (0, 2), (1, 1), (2, 1)],
        [(1, 0), (1, 1), (1, 2), (2, 2)],
        [(0, 1), (1, 1), (2, 0), (2, 1)],
    ]),
    ('L', [
        [(0, 2), (1, 0), (1, 1), (1, 2)],
        [(0, 1), (1, 1), (2, 1), (2, 2)],
        [(1, 0), (1, 1), (1, 2), (2, 0)],
        [(0, 0), (0, 1), (1, 1), (2, 1)],
    ]),
];

/// A 20x10 frame of dots with the given rows replaced wholesale.
fn frame(rows: &[(usize, &str)]) -> String {
    let mut grid: Vec<String> = (0..20).map(|_| ".".repeat(10)).collect();
    for &(r, content) in rows {
        assert_eq!(content.len(), 10);
        grid[r] = content.to_string();
    }
    grid.join("\n")
}

/// The frame for an active piece at box origin (row 0, col 3).
fn spawn_frame(cells: &[(usize, usize); 4]) -> String {
    let mut grid = vec![vec!['.'; 10]; 20];
    for &(dr, dc) in cells {
        grid[dr][3 + dc] = '@';
    }
    grid.into_iter()
        .map(|row| row.into_iter().collect::<String>())
        .collect::<Vec<_>>()
        .join("\n")
}

#[test]
fn feed_validation() {
    assert_eq!(Game::new("").err(), Some("empty feed".to_string()));
    assert_eq!(Game::new("IOX").err(), Some("bad piece: X".to_string()));
    assert_eq!(Game::new("q").err(), Some("bad piece: q".to_string()));
    assert!(Game::new("IOTSZJL").is_ok());
}

#[test]
fn every_piece_matches_the_rotation_tables_in_both_directions() {
    for (ch, states) in TABLE {
        let mut g = Game::new(&ch.to_string()).unwrap();
        assert_eq!(g.render(), spawn_frame(&states[0]), "{ch} spawn");
        for turn in 1..=4 {
            g.step('C').unwrap();
            assert_eq!(
                g.render(),
                spawn_frame(&states[turn % 4]),
                "{ch} after {turn} CW turns"
            );
        }
        // Counter-clockwise from spawn lands on state 3 directly.
        let mut g = Game::new(&ch.to_string()).unwrap();
        g.step('A').unwrap();
        assert_eq!(g.render(), spawn_frame(&states[3]), "{ch} after one CCW turn");
    }
}

#[test]
fn shifts_stop_silently_at_the_walls() {
    let mut g = Game::new("I").unwrap();
    // Six presses, but only three columns of room.
    assert_eq!(g.run("LLLLLL"), Ok(6));
    assert_eq!(g.render(), frame(&[(1, "@@@@......")]));
    assert_eq!(g.run("RRRRRRRRRR"), Ok(10));
    assert_eq!(g.render(), frame(&[(1, "......@@@@")]));
}

#[test]
fn rotations_never_kick() {
    // A vertical I hugging the left wall cannot rotate flat: the flat cells
    // would poke out at column -1, so the press is silently ignored.
    let mut g = Game::new("I").unwrap();
    g.run("ALLLL").unwrap();
    let vertical = frame(&[(0, "#........."), (1, "#........."), (2, "#........."), (3, "#.........")]).replace('#', "@");
    assert_eq!(g.render(), vertical);
    g.step('C').unwrap();
    assert_eq!(g.render(), vertical);
}

#[test]
fn gravity_ticks_then_locks_then_spawns() {
    let mut g = Game::new("II").unwrap();
    g.step('D').unwrap();
    assert_eq!(g.render(), frame(&[(2, "...@@@@...")]));
    g.run("DDDDDDDDDDDDDDDDD").unwrap(); // 17 more ticks: resting on the floor
    assert_eq!(g.render(), frame(&[(19, "...@@@@...")]));
    // On the floor a flat I cannot go vertical (rows 20+ don't exist).
    g.step('C').unwrap();
    assert_eq!(g.render(), frame(&[(19, "...@@@@...")]));
    // The next tick can't move it down: it locks and piece two spawns.
    g.step('D').unwrap();
    assert_eq!(g.render(), frame(&[(1, "...@@@@..."), (19, "...####...")]));
    assert_eq!(g.status(), Status::Playing);
    assert_eq!(g.score(), 0);
    assert_eq!(g.lines(), 0);
}

#[test]
fn single_line_clear_scores_100() {
    let mut g = Game::new("IIO").unwrap();
    g.run("LLLH RH RRRRH").unwrap();
    assert_eq!(g.score(), 100);
    assert_eq!(g.lines(), 1);
    assert_eq!(g.status(), Status::Complete);
    // The O's upper row drops into the cleared line.
    assert_eq!(g.render(), frame(&[(19, "........##")]));
    assert_eq!(g.step('D'), Err("game over".to_string()));
}

#[test]
fn double_line_clear_scores_300() {
    let mut g = Game::new("OOOOO").unwrap();
    g.run("LLLLH LLH H RRH RRRRH").unwrap();
    assert_eq!(g.score(), 300);
    assert_eq!(g.lines(), 2);
    assert_eq!(g.status(), Status::Complete);
    assert_eq!(g.render(), frame(&[]));
}

#[test]
fn triple_line_clear_scores_500() {
    let mut g = Game::new("OOOOJITI").unwrap();
    g.run("LLH H RRH RRRRH").unwrap();
    assert_eq!(
        g.render(),
        frame(&[(0, "...@......"), (1, "...@@@...."), (18, "..########"), (19, "..########")])
    );
    // The J stands in column 1 with its foot poking into column 2.
    g.run("CLLLH").unwrap();
    assert_eq!(
        g.render(),
        frame(&[(1, "...@@@@..."), (17, ".##......."), (18, ".#########"), (19, ".#########")])
    );
    // Flat I bridges columns 3-6, then a T caps columns 7-9 with its stem up.
    g.run("H RRRRH").unwrap();
    assert_eq!(
        g.render(),
        frame(&[(1, "...@@@@..."), (16, "........#."), (17, ".#########"), (18, ".#########"), (19, ".#########")])
    );
    assert_eq!(g.score(), 0);
    // Vertical I into the column-0 well: rows 17, 18, 19 clear at once.
    g.run("ALLLLH").unwrap();
    assert_eq!(g.score(), 500);
    assert_eq!(g.lines(), 3);
    assert_eq!(g.status(), Status::Complete);
    assert_eq!(g.render(), frame(&[(19, "#.......#.")]));
}

#[test]
fn tetris_scores_800() {
    let mut g = Game::new("OOOOOOOOII").unwrap();
    g.run("LLLLH LLH H RRH LLLLH LLH H RRH").unwrap();
    assert_eq!(
        g.render(),
        frame(&[
            (1, "...@@@@..."),
            (16, "########.."),
            (17, "########.."),
            (18, "########.."),
            (19, "########.."),
        ])
    );
    g.run("CRRRH").unwrap();
    assert_eq!(g.score(), 0);
    g.run("CRRRRH").unwrap();
    assert_eq!(g.score(), 800);
    assert_eq!(g.lines(), 4);
    assert_eq!(g.status(), Status::Complete);
    assert_eq!(g.render(), frame(&[]));
}

#[test]
fn spawning_into_the_stack_tops_out() {
    let mut g = Game::new("IIIIII").unwrap();
    g.run("CH CH CH CH CH").unwrap();
    assert_eq!(g.status(), Status::ToppedOut);
    assert!(g.is_over());
    assert_eq!(g.score(), 0);
    assert_eq!(g.lines(), 0);
    // Column 5 is a full tower and the sixth piece never appears.
    let tower: Vec<(usize, &str)> = (0..20).map(|r| (r, ".....#....")).collect();
    assert_eq!(g.render(), frame(&tower));
    assert_eq!(g.step('L'), Err("game over".to_string()));
}

#[test]
fn commands_are_validated_and_run_wraps_errors() {
    let mut g = Game::new("T").unwrap();
    assert_eq!(g.step('x'), Err("bad command: x".to_string()));
    assert_eq!(g.step('h'), Err("bad command: h".to_string()));
    assert_eq!(g.run("Lx"), Err("step 2 (x): bad command: x".to_string()));
    let mut g = Game::new("O").unwrap();
    g.step('H').unwrap();
    assert_eq!(g.status(), Status::Complete);
    assert_eq!(g.run("D"), Err("step 1 (D): game over".to_string()));
    // Whitespace in scripts is ignored, not an error.
    let mut g = Game::new("O").unwrap();
    assert_eq!(g.run(" L\nL "), Ok(2));
}
