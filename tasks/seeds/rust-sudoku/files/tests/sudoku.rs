// Acceptance tests for the puzzle-desk sudoku toolbox. The solver's search
// order is part of the contract: first empty cell in row-major order, digits
// tried ascending — several pins below only hold under exactly that order.

use rust_sudoku::{count_solutions, is_complete, parse, render, solve, validate, Grid};

const SEVENTEEN: &str = include_str!("fixtures/seventeen.txt");
const MULTI: &str = include_str!("fixtures/multi.txt");
const UNSAT: &str = include_str!("fixtures/unsat.txt");
const EASY: &str = include_str!("fixtures/easy.txt");
const INVALID: &str = include_str!("fixtures/invalid.txt");

const SEVENTEEN_SOLUTION: &str = "\
693784512
487512936
125963874
932651487
568247391
741398625
319475268
856129743
274836159";

fn grid(text: &str) -> Grid {
    parse(text).expect("fixture must parse")
}

#[test]
fn parse_accepts_the_fixtures() {
    for f in [SEVENTEEN, MULTI, UNSAT, EASY, INVALID] {
        assert!(parse(f).is_ok());
    }
    let g = grid(SEVENTEEN);
    assert_eq!(g[0][7], 1);
    assert_eq!(g[1][0], 4);
    assert_eq!(g[8][3], 8);
    assert_eq!(g[8][5], 6);
    assert_eq!(g[4][4], 0);
    let clues: usize = g.iter().flatten().filter(|&&v| v != 0).count();
    assert_eq!(clues, 17);
}

#[test]
fn parse_rejects_malformed_grids() {
    let eight_rows = ".........\n".repeat(8);
    assert_eq!(parse(&eight_rows), Err("expected 9 rows, got 8".to_string()));
    let ten_rows = ".........\n".repeat(10);
    assert_eq!(parse(&ten_rows), Err("expected 9 rows, got 10".to_string()));
    let short_row = format!("{}{}{}", ".........\n".repeat(2), "........\n", ".........\n".repeat(6));
    assert_eq!(parse(&short_row), Err("row 3 must be 9 cells, got 8".to_string()));
    let bad_char = format!("{}{}{}", ".........\n".repeat(1), "....x....\n", ".........\n".repeat(7));
    assert_eq!(parse(&bad_char), Err("bad cell at row 2 col 5: 'x'".to_string()));
    let zero = format!("0........\n{}", ".........\n".repeat(8));
    assert_eq!(parse(&zero), Err("bad cell at row 1 col 1: '0'".to_string()));
}

#[test]
fn parse_skips_blank_lines_and_render_round_trips() {
    let spaced = SEVENTEEN.replace('\n', "\n\n");
    assert_eq!(parse(&spaced), Ok(grid(SEVENTEEN)));
    assert_eq!(render(&grid(SEVENTEEN)), SEVENTEEN.trim_end());
    assert_eq!(render(&grid(EASY)), EASY.trim_end());
}

#[test]
fn validate_reports_the_first_duplicate_row_then_column_then_box() {
    assert_eq!(validate(&grid(SEVENTEEN)), Ok(()));
    assert_eq!(validate(&grid(UNSAT)), Ok(()));
    assert_eq!(
        validate(&grid(INVALID)),
        Err("duplicate 4 in row 2".to_string())
    );
    let col_dup = parse("5........\n.........\n.........\n.........\n5........\n.........\n.........\n.........\n.........\n").unwrap();
    assert_eq!(validate(&col_dup), Err("duplicate 5 in column 1".to_string()));
    let box_dup = parse("7........\n..7......\n.........\n.........\n.........\n.........\n.........\n.........\n.........\n").unwrap();
    assert_eq!(validate(&box_dup), Err("duplicate 7 in box 1".to_string()));
    let last_box = parse(".........\n.........\n.........\n.........\n.........\n.........\n......2..\n.........\n........2\n").unwrap();
    assert_eq!(validate(&last_box), Err("duplicate 2 in box 9".to_string()));
    // A grid with both a column and a box conflict: columns are scanned
    // before boxes.
    let both = parse("9........\n9........\n.........\n.........\n.........\n.........\n.........\n.........\n.........\n").unwrap();
    assert_eq!(validate(&both), Err("duplicate 9 in column 1".to_string()));
}

#[test]
fn solves_the_seventeen_clue_puzzle() {
    let sol = solve(&grid(SEVENTEEN)).expect("the 17-clue puzzle is solvable");
    assert_eq!(render(&sol), SEVENTEEN_SOLUTION);
    assert!(is_complete(&sol));
    // Every clue must survive into the solution.
    let g = grid(SEVENTEEN);
    for r in 0..9 {
        for c in 0..9 {
            if g[r][c] != 0 {
                assert_eq!(sol[r][c], g[r][c], "clue at ({r},{c}) was overwritten");
            }
        }
    }
}

#[test]
fn seventeen_clue_solution_is_unique() {
    assert_eq!(count_solutions(&grid(SEVENTEEN), 2), 1);
}

#[test]
fn easy_puzzle_reaches_the_same_unique_solution() {
    let sol = solve(&grid(EASY)).expect("easy fixture is solvable");
    assert_eq!(render(&sol), SEVENTEEN_SOLUTION);
    assert_eq!(count_solutions(&grid(EASY), 2), 1);
}

#[test]
fn multi_solution_grid_is_detected_and_solved_in_pinned_order() {
    let g = grid(MULTI);
    assert_eq!(count_solutions(&g, 100), 2);
    assert_eq!(count_solutions(&g, 2), 2);
    assert_eq!(count_solutions(&g, 1), 1, "the cap must stop the search early");
    // With the pinned order, the first solution found is exactly this one
    // (a digits-descending or column-major solver lands on the other).
    let first = solve(&g).unwrap();
    assert_eq!(render(&first), SEVENTEEN_SOLUTION);
}

#[test]
fn unsatisfiable_grid_solves_to_none() {
    // The fixture passes validation — the contradiction only appears deep in
    // the search.
    assert_eq!(validate(&grid(UNSAT)), Ok(()));
    assert_eq!(solve(&grid(UNSAT)), None);
    assert_eq!(count_solutions(&grid(UNSAT), 5), 0);
}

#[test]
fn invalid_grid_never_solves() {
    assert_eq!(solve(&grid(INVALID)), None);
    assert_eq!(count_solutions(&grid(INVALID), 5), 0);
}

#[test]
fn empty_grid_solve_pins_the_search_order() {
    let empty: Grid = [[0; 9]; 9];
    let sol = solve(&empty).unwrap();
    assert_eq!(
        render(&sol),
        "\
123456789
456789123
789123456
214365897
365897214
897214365
531642978
642978531
978531642"
    );
    assert_eq!(count_solutions(&empty, 10), 10);
}

#[test]
fn solving_a_complete_grid_returns_it_unchanged() {
    let sol = solve(&grid(SEVENTEEN)).unwrap();
    assert_eq!(solve(&sol), Some(sol));
    assert_eq!(count_solutions(&sol, 3), 1);
}

#[test]
fn is_complete_wants_full_and_conflict_free() {
    assert!(!is_complete(&grid(SEVENTEEN)));
    assert!(!is_complete(&grid(EASY)));
    let sol = solve(&grid(SEVENTEEN)).unwrap();
    assert!(is_complete(&sol));
    // Full grid with two cells swapped: complete in shape, invalid in fact.
    let mut broken = sol;
    broken[0].swap(0, 1);
    assert!(!is_complete(&broken));
}
