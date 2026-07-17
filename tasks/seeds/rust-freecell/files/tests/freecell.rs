// Acceptance tests for the solitaire-corner FreeCell engine. Moves are
// two-character strings (source then destination): cascades '1'-'8', free
// cells 'a'-'d', foundations 'h'. The supermove capacity law
// (free cells empty + 1) * 2^(empty cascades) is pinned exactly, including
// the rule that an empty destination cascade doesn't count toward the
// multiplier.

use rust_freecell::Game;

const SOLVABLE: &str = include_str!("fixtures/solvable.deal");
const SUPERMOVE: &str = include_str!("fixtures/supermove.deal");

// 53 moves that finish the solvable fixture deal.
const WIN: &str = "5a 5h ah 5h 5h 5h 5h 1h 1h 1h 1h 1h 1h 1h 6h 6h 6h 6h 6h 6h \
2h 2h 2h 2h 2h 2h 2h 7h 7h 7h 7h 7h 7h 3h 3h 3h 3h 3h 3h 3h \
8h 8h 8h 8h 8h 8h 4h 4h 4h 4h 4h 4h 4h";

#[test]
fn load_renders_the_deal_verbatim() {
    let g = Game::load(SOLVABLE).unwrap();
    assert_eq!(
        g.render(),
        "\
free: . . . .
home: C:. D:. H:. S:.
1: KC QC JC TC 9C 8C 7C
2: KD QD JD TD 9D 8D 7D
3: KH QH JH TH 9H 8H 7H
4: KS QS JS TS 9S 8S 7S
5: 6C 5C 4C 3C AC 2C
6: 6D 5D 4D 3D 2D AD
7: 6H 5H 4H 3H 2H AH
8: 6S 5S 4S 3S 2S AS"
    );
    assert!(!g.is_won());
    // Blank lines between cascades are tolerated.
    let spaced = SOLVABLE.replace('\n', "\n\n");
    assert_eq!(Game::load(&spaced).unwrap().render(), g.render());
}

#[test]
fn load_rejects_malformed_deals() {
    let seven_lines: Vec<&str> = SOLVABLE.lines().take(7).collect();
    assert_eq!(
        Game::load(&seven_lines.join("\n")).err(),
        Some("expected 8 cascades, got 7".to_string())
    );
    // Swap a 7-card line into a 6-card slot.
    let mut lines: Vec<&str> = SOLVABLE.lines().collect();
    lines[4] = lines[0];
    assert_eq!(
        Game::load(&lines.join("\n")).err(),
        Some("cascade 5 must have 6 cards, got 7".to_string())
    );
    let bad = SOLVABLE.replace("9H", "9X");
    assert_eq!(Game::load(&bad).err(), Some("bad card: 9X".to_string()));
    let bad = SOLVABLE.replace("TH", "10H");
    assert_eq!(Game::load(&bad).err(), Some("bad card: 10H".to_string()));
    let dup = SOLVABLE.replace("2D", "5H");
    assert_eq!(Game::load(&dup).err(), Some("duplicate card: 5H".to_string()));
}

#[test]
fn malformed_move_strings_are_rejected() {
    let mut g = Game::load(SOLVABLE).unwrap();
    for bad in ["", "1", "9a", "1x", "11", "aa", "ab", "h1", "1hh", "5H"] {
        assert_eq!(g.apply(bad), Err(format!("bad move: {bad}")));
    }
}

#[test]
fn free_cells_are_named_and_single_card() {
    let mut g = Game::load(SOLVABLE).unwrap();
    g.apply("1a").unwrap(); // 7C to cell a
    assert_eq!(g.apply("1a"), Err("free cell a is occupied".to_string()));
    g.apply("1b").unwrap(); // 8C to cell b
    assert_eq!(g.apply("c1"), Err("free cell c is empty".to_string()));
    // 7C back down: 9C won't take it, neither will 7D.
    assert_eq!(g.apply("a1"), Err("does not fit".to_string()));
    assert_eq!(g.apply("a2"), Err("does not fit".to_string()));
    assert_eq!(g.apply("b1"), Err("does not fit".to_string()));
    g.apply("2c").unwrap(); // 7D out of the way
    g.apply("a2").unwrap(); // 7C on 8D: down one, opposite color
    let render = g.render();
    assert!(render.starts_with("free: . 8C 7D .\n"), "got:\n{render}");
    assert!(render.contains("\n2: KD QD JD TD 9D 8D 7C"), "got:\n{render}");
    assert!(render.contains("\n1: KC QC JC TC 9C"), "got:\n{render}");
}

#[test]
fn foundations_build_up_by_suit_from_the_ace() {
    let mut g = Game::load(SOLVABLE).unwrap();
    // 2C is on top of its ace — it can't go home yet.
    assert_eq!(g.apply("5h"), Err("does not fit".to_string()));
    g.apply_script("5a 5h ah").unwrap();
    assert_eq!(
        g.render(),
        "\
free: . . . .
home: C:2 D:. H:. S:.
1: KC QC JC TC 9C 8C 7C
2: KD QD JD TD 9D 8D 7D
3: KH QH JH TH 9H 8H 7H
4: KS QS JS TS 9S 8S 7S
5: 6C 5C 4C 3C
6: 6D 5D 4D 3D 2D AD
7: 6H 5H 4H 3H 2H AH
8: 6S 5S 4S 3S 2S AS"
    );
    // Wrong rank still refuses: 7C can't land on the club 2.
    assert_eq!(g.apply("1h"), Err("does not fit".to_string()));
    g.apply("5h").unwrap(); // 3C
}

#[test]
fn failed_moves_change_nothing() {
    let mut g = Game::load(SUPERMOVE).unwrap();
    let before = g.render();
    for bad in ["14", "5h", "23", "zz", "a1"] {
        assert!(g.apply(bad).is_err());
        assert_eq!(g.render(), before, "state drifted after rejected {bad}");
    }
}

#[test]
fn the_win_script_solves_the_fixture_deal() {
    let mut g = Game::load(SOLVABLE).unwrap();
    let moves: Vec<&str> = WIN.split_whitespace().collect();
    assert_eq!(moves.len(), 53);
    for (i, mv) in moves.iter().enumerate() {
        assert!(!g.is_won(), "won before move {}", i + 1);
        g.apply(mv)
            .unwrap_or_else(|e| panic!("move {} ({mv}) refused: {e}", i + 1));
    }
    assert!(g.is_won());
    assert_eq!(
        g.render(),
        "\
free: . . . .
home: C:K D:K H:K S:K
1:
2:
3:
4:
5:
6:
7:
8:"
    );
    assert_eq!(g.apply("1h"), Err("empty source".to_string()));
    assert_eq!(g.apply("1a"), Err("empty source".to_string()));
    assert_eq!(g.apply("12"), Err("empty source".to_string()));
}

#[test]
fn apply_script_counts_moves_and_reports_the_failing_one() {
    let mut g = Game::load(SOLVABLE).unwrap();
    assert_eq!(g.apply_script("5a 5h ah"), Ok(3));
    let mut g = Game::load(SOLVABLE).unwrap();
    assert_eq!(
        g.apply_script("5a 5a"),
        Err("move 2 (5a): free cell a is occupied".to_string())
    );
    assert_eq!(g.apply_script(""), Ok(0));
}

#[test]
fn supermove_capacity_law_is_exact() {
    let mut g = Game::load(SUPERMOVE).unwrap();
    // Fresh: 4 free cells, no empty cascade.
    assert_eq!(g.max_supermove(false), 5);
    assert_eq!(g.max_supermove(true), 5);
    // Cascade 1 tops a 7-card run 8D..2H; moving 7S..2H onto 8H needs 6.
    assert_eq!(
        g.apply("14"),
        Err("supermove needs 6 cards, capacity is 5".to_string())
    );
    // Two occupied cells shrink capacity to (2+1) * 2^0.
    g.apply_script("5a 5b").unwrap();
    assert_eq!(g.max_supermove(false), 3);
    // Unload cascade 5 entirely: aces and twos go home, cells drain.
    g.apply_script("5h 5h 5h 5h ah bh").unwrap();
    assert_eq!(g.max_supermove(false), 10); // (4+1) * 2^1
    assert_eq!(g.max_supermove(true), 5); // the empty target doesn't double
    assert_eq!(
        g.render(),
        "\
free: . . . .
home: C:2 D:2 H:A S:A
1: 8D 7S 6H 5S 4H 3S 2H
2: 9C KH KD KC QH QD QC
3: 9S KS QS JC JD JH JS
4: 9H TC TD TH TS 8C 8H
5:
6: 2S 3C 3D 3H 4C 4D
7: 4S 5C 5D 5H 6C 6D
8: 6S 7C 7D 7H 8S 9D"
    );
    // Now the six-card supermove fits under capacity 10.
    g.apply("14").unwrap();
    assert_eq!(
        g.render(),
        "\
free: . . . .
home: C:2 D:2 H:A S:A
1: 8D
2: 9C KH KD KC QH QD QC
3: 9S KS QS JC JD JH JS
4: 9H TC TD TH TS 8C 8H 7S 6H 5S 4H 3S 2H
5:
6: 2S 3C 3D 3H 4C 4D
7: 4S 5C 5D 5H 6C 6D
8: 6S 7C 7D 7H 8S 9D"
    );
    // Moving onto the empty cascade takes the whole (1-card) run.
    g.apply("15").unwrap();
    assert_eq!(g.max_supermove(false), 10); // cascade 1 is the empty one now
    assert_eq!(g.max_supermove(true), 5);
    // Partial supermoves peel just the fitting tail off a longer run.
    g.apply("46").unwrap(); // 3S 2H onto 4D
    g.apply("47").unwrap(); // 5S 4H onto 6D
    assert_eq!(g.apply("48"), Err("does not fit".to_string()));
    assert_eq!(
        g.render(),
        "\
free: . . . .
home: C:2 D:2 H:A S:A
1:
2: 9C KH KD KC QH QD QC
3: 9S KS QS JC JD JH JS
4: 9H TC TD TH TS 8C 8H 7S 6H
5: 8D
6: 2S 3C 3D 3H 4C 4D 3S 2H
7: 4S 5C 5D 5H 6C 6D 5S 4H
8: 6S 7C 7D 7H 8S 9D"
    );
}
