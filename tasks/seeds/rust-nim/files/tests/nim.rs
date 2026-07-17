// Acceptance tests for the kiosk Nim engine. The bot's choices are pinned
// exactly: among winning moves it must pick the lowest heap index and, within
// that heap, the smallest removal; in lost positions it takes a single object
// from the lowest-index non-empty heap.

use rust_nim::{Game, Variant};

fn game(heaps: &[u32], v: Variant) -> Game {
    Game::new(heaps, v).expect("fixture start must construct")
}

#[test]
fn constructor_rejects_degenerate_starts() {
    assert!(Game::new(&[], Variant::Normal).is_err());
    assert!(Game::new(&[0, 0, 0], Variant::Normal).is_err());
    assert!(Game::new(&[], Variant::Misere).is_err());
    assert!(Game::new(&[0], Variant::Misere).is_err());
    assert!(Game::new(&[0, 0, 7], Variant::Normal).is_ok());
    assert!(Game::new(&[1], Variant::Misere).is_ok());
}

#[test]
fn new_game_state() {
    let g = game(&[3, 4, 5], Variant::Normal);
    assert_eq!(g.heaps(), &[3, 4, 5]);
    assert_eq!(g.turn(), 0);
    assert_eq!(g.variant(), Variant::Normal);
    assert!(!g.is_over());
    assert_eq!(g.winner(), None);
}

#[test]
fn apply_updates_heaps_and_alternates_turns() {
    let mut g = game(&[3, 4, 5], Variant::Normal);
    g.apply(1, 3).unwrap();
    assert_eq!(g.heaps(), &[3, 1, 5]);
    assert_eq!(g.turn(), 1);
    g.apply(0, 3).unwrap();
    assert_eq!(g.heaps(), &[0, 1, 5]);
    assert_eq!(g.turn(), 0);
    assert!(!g.is_over());
}

#[test]
fn apply_rejects_bad_moves_without_changing_state() {
    let mut g = game(&[3, 4, 5], Variant::Normal);
    assert_eq!(g.apply(3, 1), Err("no such heap: 3".to_string()));
    assert_eq!(g.apply(9, 1), Err("no such heap: 9".to_string()));
    assert_eq!(g.apply(0, 0), Err("must take at least one".to_string()));
    assert_eq!(g.apply(1, 5), Err("heap 1 has only 4".to_string()));
    g.apply(0, 3).unwrap();
    assert_eq!(g.apply(0, 1), Err("heap 0 has only 0".to_string()));
    assert_eq!(g.heaps(), &[0, 4, 5]);
    assert_eq!(g.turn(), 1);
}

#[test]
fn normal_play_last_taker_wins() {
    let mut g = game(&[2, 1], Variant::Normal);
    g.apply(0, 2).unwrap(); // player 0
    g.apply(1, 1).unwrap(); // player 1 takes the last object
    assert!(g.is_over());
    assert_eq!(g.winner(), Some(1));
    assert_eq!(g.apply(0, 1), Err("game over".to_string()));
    assert!(!g.is_winning());
    assert_eq!(g.bot_move(), None);
}

#[test]
fn misere_play_last_taker_loses() {
    let mut g = game(&[2, 1], Variant::Misere);
    g.apply(0, 2).unwrap(); // player 0
    g.apply(1, 1).unwrap(); // player 1 takes the last object -> loses
    assert!(g.is_over());
    assert_eq!(g.winner(), Some(0));
}

#[test]
fn is_winning_normal_is_the_xor_rule() {
    assert!(game(&[3, 4, 5], Variant::Normal).is_winning()); // xor 2
    assert!(!game(&[1, 2, 3], Variant::Normal).is_winning()); // xor 0
    assert!(!game(&[4, 4], Variant::Normal).is_winning());
    assert!(game(&[5], Variant::Normal).is_winning());
    assert!(game(&[1, 1, 1], Variant::Normal).is_winning());
    assert!(!game(&[7, 4, 3], Variant::Normal).is_winning()); // 7^4^3 = 0
    assert!(game(&[0, 0, 7], Variant::Normal).is_winning());
}

#[test]
fn is_winning_misere_flips_only_in_the_all_ones_endgame() {
    // Some heap has 2+: same as normal play.
    assert!(game(&[3, 4, 5], Variant::Misere).is_winning());
    assert!(!game(&[1, 2, 3], Variant::Misere).is_winning());
    assert!(game(&[2], Variant::Misere).is_winning());
    assert!(!game(&[2, 2], Variant::Misere).is_winning());
    // Every heap 0 or 1: mover wins iff the count of ones is even.
    assert!(!game(&[1], Variant::Misere).is_winning());
    assert!(game(&[1, 1], Variant::Misere).is_winning());
    assert!(!game(&[1, 1, 1], Variant::Misere).is_winning());
    assert!(game(&[1, 1, 1, 1], Variant::Misere).is_winning());
    assert!(!game(&[0, 1, 0], Variant::Misere).is_winning());
    assert!(game(&[1, 0, 1], Variant::Misere).is_winning());
    // Contrast with normal play on the same all-ones heaps.
    assert!(game(&[1], Variant::Normal).is_winning());
    assert!(!game(&[1, 1], Variant::Normal).is_winning());
}

#[test]
fn bot_moves_normal_are_pinned() {
    assert_eq!(game(&[3, 4, 5], Variant::Normal).bot_move(), Some((0, 2)));
    assert_eq!(game(&[5], Variant::Normal).bot_move(), Some((0, 5)));
    assert_eq!(game(&[7, 7, 7], Variant::Normal).bot_move(), Some((0, 7)));
    assert_eq!(game(&[1, 1, 1], Variant::Normal).bot_move(), Some((0, 1)));
    assert_eq!(game(&[0, 0, 7], Variant::Normal).bot_move(), Some((2, 7)));
    assert_eq!(game(&[2, 1], Variant::Normal).bot_move(), Some((0, 1)));
    assert_eq!(game(&[6, 1, 1], Variant::Normal).bot_move(), Some((0, 6)));
    // Lost position: pinned fallback, one off the lowest non-empty heap.
    assert_eq!(game(&[1, 2, 3], Variant::Normal).bot_move(), Some((0, 1)));
    assert_eq!(game(&[0, 5, 5], Variant::Normal).bot_move(), Some((1, 1)));
}

#[test]
fn bot_moves_misere_are_pinned() {
    // Agrees with normal play while big heaps remain...
    assert_eq!(game(&[3, 4, 5], Variant::Misere).bot_move(), Some((0, 2)));
    assert_eq!(game(&[2, 2, 2], Variant::Misere).bot_move(), Some((0, 2)));
    assert_eq!(game(&[3, 3, 1], Variant::Misere).bot_move(), Some((0, 1)));
    // ...but steers the all-ones endgame to the opposite parity.
    assert_eq!(game(&[2, 1], Variant::Misere).bot_move(), Some((0, 2)));
    assert_eq!(game(&[4], Variant::Misere).bot_move(), Some((0, 3)));
    assert_eq!(game(&[1, 1], Variant::Misere).bot_move(), Some((0, 1)));
    assert_eq!(game(&[1, 1, 1, 1], Variant::Misere).bot_move(), Some((0, 1)));
    // Lost positions: pinned fallback.
    assert_eq!(game(&[1, 1, 1], Variant::Misere).bot_move(), Some((0, 1)));
    assert_eq!(game(&[5, 5], Variant::Misere).bot_move(), Some((0, 1)));
    assert_eq!(game(&[0, 1], Variant::Misere).bot_move(), Some((1, 1)));
}

#[test]
fn the_two_variants_split_on_two_one() {
    // Same heaps, same winning claim, different correct move.
    assert!(game(&[2, 1], Variant::Normal).is_winning());
    assert!(game(&[2, 1], Variant::Misere).is_winning());
    assert_eq!(game(&[2, 1], Variant::Normal).bot_move(), Some((0, 1)));
    assert_eq!(game(&[2, 1], Variant::Misere).bot_move(), Some((0, 2)));
}

#[test]
fn full_playout_normal_345_is_pinned() {
    let mut g = game(&[3, 4, 5], Variant::Normal);
    let log = g.play_out();
    assert!(g.is_over());
    assert_eq!(g.winner(), Some(0));
    assert_eq!(
        log,
        vec![
            (0, 0, 2),
            (1, 0, 1),
            (0, 2, 1),
            (1, 1, 1),
            (0, 2, 1),
            (1, 1, 1),
            (0, 2, 1),
            (1, 1, 1),
            (0, 2, 1),
            (1, 1, 1),
            (0, 2, 1),
        ]
    );
}

#[test]
fn full_playout_misere_345_is_pinned() {
    let mut g = game(&[3, 4, 5], Variant::Misere);
    let log = g.play_out();
    assert!(g.is_over());
    assert_eq!(g.winner(), Some(0));
    assert_eq!(
        log,
        vec![
            (0, 0, 2),
            (1, 0, 1),
            (0, 2, 1),
            (1, 1, 1),
            (0, 2, 1),
            (1, 1, 1),
            (0, 2, 1),
            (1, 1, 1),
            (0, 2, 2), // leave a lone 1 instead of emptying the board
            (1, 1, 1),
        ]
    );
}

#[test]
fn full_playout_misere_2345_lost_start_is_pinned() {
    let mut g = game(&[2, 3, 4, 5], Variant::Misere);
    assert!(!g.is_winning());
    let log = g.play_out();
    assert_eq!(g.winner(), Some(1));
    assert_eq!(
        log,
        vec![
            (0, 0, 1),
            (1, 1, 3),
            (0, 0, 1),
            (1, 3, 1),
            (0, 2, 1),
            (1, 3, 1),
            (0, 2, 1),
            (1, 3, 1),
            (0, 2, 1),
            (1, 3, 2),
            (0, 2, 1),
        ]
    );
}

#[test]
fn bot_wins_every_winnable_start_and_loses_the_rest() {
    let starts: &[&[u32]] = &[
        &[1],
        &[2],
        &[1, 1],
        &[2, 1],
        &[2, 2],
        &[1, 1, 1],
        &[1, 1, 1, 1],
        &[3, 4, 5],
        &[1, 2, 3],
        &[4, 4, 4],
        &[5, 5],
        &[7, 1, 1],
        &[6, 1, 1],
        &[2, 3, 4, 5],
        &[0, 0, 7],
        &[9, 8, 7, 6, 5],
        &[10, 10, 3],
        &[1, 2, 4, 8],
    ];
    for &heaps in starts {
        for variant in [Variant::Normal, Variant::Misere] {
            let mut g = game(heaps, variant);
            let claimed = g.is_winning();
            let moves = g.play_out();
            assert!(g.is_over());
            assert!(!moves.is_empty());
            let expect = if claimed { 0 } else { 1 };
            assert_eq!(
                g.winner(),
                Some(expect),
                "start {heaps:?} {variant:?}: is_winning said {claimed}"
            );
            // The log must be a legal alternating replay from the start.
            let mut replay = game(heaps, variant);
            for &(player, heap, take) in &moves {
                assert_eq!(replay.turn(), player, "log out of turn for {heaps:?}");
                replay.apply(heap, take).unwrap();
            }
            assert!(replay.is_over());
        }
    }
}

#[test]
fn bot_move_does_not_mutate() {
    let g = game(&[3, 4, 5], Variant::Normal);
    let a = g.bot_move();
    let b = g.bot_move();
    assert_eq!(a, b);
    assert_eq!(g.heaps(), &[3, 4, 5]);
    assert_eq!(g.turn(), 0);
}

#[test]
fn human_vs_bot_mixed_session() {
    // A person plays heap picks against the bot; the bot never blunders out
    // of a won game once the human errs.
    let mut g = game(&[1, 2, 3], Variant::Normal); // lost for the mover
    g.apply(2, 3).unwrap(); // human blunder: [1, 2, 0]
    assert!(g.is_winning()); // bot to move, now winning
    let (h, t) = g.bot_move().unwrap();
    assert_eq!((h, t), (1, 1)); // -> [1, 1, 0]
    g.apply(h, t).unwrap();
    assert_eq!(g.heaps(), &[1, 1, 0]);
    g.apply(0, 1).unwrap(); // human: [0, 1, 0]
    let (h, t) = g.bot_move().unwrap();
    g.apply(h, t).unwrap();
    assert!(g.is_over());
    assert_eq!(g.winner(), Some(1)); // bot moved second, bot wins
}
