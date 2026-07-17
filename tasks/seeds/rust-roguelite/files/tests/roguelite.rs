// Acceptance tests for the kiosk dungeon crawler. Everything is
// deterministic: fog of war is Chebyshev radius 2 and permanent for terrain,
// monsters are drawn only while currently inside that radius, chasers take
// the first Manhattan-reducing step in N, E, S, W order, and combat numbers
// are fixed (player 10 hp / 3 damage, monsters 6 hp / 2 damage, potions +4
// capped at 10).

use rust_roguelite::{Game, Status};

const CRYPT: &str = include_str!("fixtures/crypt.map");
const LAIR: &str = include_str!("fixtures/lair.map");

#[test]
fn constants_are_the_agreed_numbers() {
    assert_eq!(rust_roguelite::PLAYER_MAX_HP, 10);
    assert_eq!(rust_roguelite::PLAYER_ATTACK, 3);
    assert_eq!(rust_roguelite::MONSTER_HP, 6);
    assert_eq!(rust_roguelite::MONSTER_ATTACK, 2);
    assert_eq!(rust_roguelite::SIGHT_RADIUS, 2);
}

#[test]
fn load_rejects_broken_maps() {
    assert_eq!(Game::load("").err(), Some("empty map".to_string()));
    assert_eq!(
        Game::load("###\n####\n").err(),
        Some("map must be rectangular".to_string())
    );
    assert_eq!(
        Game::load("###\n#.#\n###\n").err(),
        Some("expected exactly one '@', got 0".to_string())
    );
    assert_eq!(
        Game::load("####\n#@@#\n####\n").err(),
        Some("expected exactly one '@', got 2".to_string())
    );
    assert_eq!(
        Game::load("###\n#x#\n###\n").err(),
        Some("bad tile: 'x'".to_string())
    );
}

#[test]
fn fog_starts_at_chebyshev_radius_two() {
    let g = Game::load(CRYPT).unwrap();
    assert_eq!(g.hp(), 10);
    assert_eq!(g.status(), Status::Playing);
    assert_eq!(g.monsters_left(), 1);
    // Only rows 0-3 / cols 0-3 around the start are revealed; the potion at
    // row 3 col 4 and the monster are both still hidden.
    assert_eq!(
        g.render(),
        "\
####?????
#@..?????
#.#.?????
#...?????
?????????
?????????
?????????"
    );
}

#[test]
fn bad_commands_and_blocked_moves_do_not_advance_the_turn() {
    let mut g = Game::load(CRYPT).unwrap();
    let before = g.render();
    assert_eq!(g.step('Q'), Err("bad command: Q".to_string()));
    assert_eq!(g.step('n'), Err("bad command: n".to_string()));
    assert_eq!(g.step('N'), Err("blocked".to_string())); // wall above
    assert_eq!(g.step('W'), Err("blocked".to_string()));
    assert_eq!(g.render(), before);
    assert_eq!(g.hp(), 10);
}

#[test]
fn crypt_walkthrough_pins_fog_chase_and_the_win() {
    let mut g = Game::load(CRYPT).unwrap();
    g.run("SS").unwrap();
    // Terrain seen on the way down stays revealed; the crypt monster has
    // been chasing (two W steps along the bottom corridor) and is now
    // visible two rows below the player.
    assert_eq!(
        g.render(),
        "\
####?????
#...?????
#.#.?????
#@..?????
#.##?????
#M..?????
?????????"
    );
    g.run("EE").unwrap();
    assert_eq!(
        g.render(),
        "\
####?????
#...#.???
#.#.#.???
#M.@!.???
#.####???
#.....???
?????????"
    );
    // Grab the potion (already at full health — no overheal) and head for
    // the stairs; the monster keeps pace but never reaches us.
    g.run("EENN").unwrap();
    assert_eq!(g.hp(), 10);
    assert_eq!(
        g.render(),
        "\
########?
#..M#@.>?
#.#.#.#.?
#.....#.?
#.#####.?
#.......?
?????????"
    );
    g.run("E").unwrap();
    // One square from the stairs the monster falls out of sight range even
    // though we remember the floor it stands on.
    assert_eq!(
        g.render(),
        "\
#########
#...#.@>#
#.#.#.#.#
#.....#.#
#.#####.?
#.......?
?????????"
    );
    g.run("E").unwrap();
    assert_eq!(g.status(), Status::Won);
    assert_eq!(g.hp(), 10);
    assert_eq!(g.monsters_left(), 1);
    assert_eq!(
        g.render(),
        "\
#########
#...#..@#
#.#.#.#.#
#.....#.#
#.#####.?
#.......?
?????????"
    );
    assert_eq!(g.step('.'), Err("game over".to_string()));
}

#[test]
fn lair_fight_pins_combat_potions_and_the_cap() {
    let mut g = Game::load(LAIR).unwrap();
    assert_eq!(g.monsters_left(), 2);
    // Step up to the first monster; it retaliates while the second one
    // rounds the corner toward us.
    g.step('E').unwrap();
    assert_eq!(g.hp(), 8);
    assert_eq!(
        g.render(),
        "\
#####??
#.@M.??
#..M.??
#....??
???????"
    );
    // Two bumps at 3 damage kill the 6 hp monster; each answer costs 2.
    g.step('E').unwrap();
    assert_eq!(g.hp(), 6);
    assert_eq!(g.monsters_left(), 2);
    g.step('E').unwrap();
    assert_eq!(g.hp(), 4);
    assert_eq!(g.monsters_left(), 1, "first monster dies to the second bump");
    // Walk east; the survivor trails us by one square.
    g.run("EE").unwrap();
    assert_eq!(g.hp(), 4);
    assert_eq!(
        g.render(),
        "\
#######
#..M@!#
#....!#
#.....#
???????"
    );
    // First potion: 4 + 4 = 8.
    g.step('E').unwrap();
    assert_eq!(g.hp(), 8);
    // East is a wall — the rejected step must not give the monster a turn.
    assert_eq!(g.step('E'), Err("blocked".to_string()));
    assert_eq!(g.hp(), 8);
    // Second potion: 8 + 4 caps at 10.
    g.step('S').unwrap();
    assert_eq!(g.hp(), 10);
    assert_eq!(
        g.render(),
        "\
#######
#....M#
#....@#
#.....#
???####"
    );
}

#[test]
fn chasers_prefer_north_east_south_west_among_reducing_steps() {
    let map = "#######\n#.....#\n#.....#\n#...M.#\n#.....#\n#@....#\n#######\n";
    let mut g = Game::load(map).unwrap();
    // Monster starts out of sight; S and W both reduce distance, S wins.
    g.step('.').unwrap(); // -> row 4 (S), still hidden
    g.step('.').unwrap(); // -> row 5 (S), still hidden (col distance 3)
    g.step('.').unwrap(); // -> one step W, enters sight range
    assert_eq!(
        g.render(),
        "\
???????
???????
???????
#...???
#...???
#@.M???
####???"
    );
    g.step('.').unwrap(); // W again, now adjacent
    assert_eq!(
        g.render(),
        "\
???????
???????
???????
#...???
#...???
#@M.???
####???"
    );
    assert_eq!(g.hp(), 10);
    g.step('.').unwrap(); // adjacent monsters attack instead of moving
    assert_eq!(g.hp(), 8);
}

#[test]
fn walled_in_monsters_stay_put() {
    let mut g = Game::load("#####\n#@#M#\n#####\n").unwrap();
    for _ in 0..3 {
        g.step('.').unwrap();
    }
    assert_eq!(g.hp(), 10);
    // Chebyshev distance 2: we can see it sulking behind the wall (the far
    // corner column is still out of sight range).
    assert_eq!(g.render(), "####?\n#@#M?\n####?");
}

#[test]
fn monsters_never_step_onto_potions_or_stairs() {
    // The only reducing step is the potion square — the monster must wait.
    let mut g = Game::load("#####\n#@!M#\n#####\n").unwrap();
    g.step('.').unwrap();
    assert_eq!(g.render(), "####?\n#@!M?\n####?");
    // Walking onto the potion at full health consumes it but heals nothing;
    // the monster (still off the potion square) is now adjacent and hits.
    g.step('E').unwrap();
    assert_eq!(g.hp(), 8);
    assert_eq!(g.render(), "#####\n#.@M#\n#####");

    // Same with stairs — and stepping onto them wins BEFORE the adjacent
    // monster gets its swing in.
    let mut g = Game::load("#####\n#@>M#\n#####\n").unwrap();
    g.step('.').unwrap();
    assert_eq!(g.render(), "####?\n#@>M?\n####?");
    g.step('E').unwrap();
    assert_eq!(g.status(), Status::Won);
    assert_eq!(g.hp(), 10);
}

#[test]
fn an_adjacent_monster_grinds_the_player_down() {
    let mut g = Game::load("#####\n#@M.#\n#####\n").unwrap();
    for expected in [8, 6, 4, 2] {
        g.step('.').unwrap();
        assert_eq!(g.hp(), expected);
        assert_eq!(g.status(), Status::Playing);
    }
    g.step('.').unwrap();
    assert_eq!(g.hp(), 0);
    assert_eq!(g.status(), Status::Dead);
    assert_eq!(g.step('E'), Err("game over".to_string()));
}

#[test]
fn run_counts_steps_and_wraps_errors() {
    let mut g = Game::load(CRYPT).unwrap();
    assert_eq!(g.run("SS EEEE\nNN EE"), Ok(10));
    assert_eq!(g.status(), Status::Won);
    let mut g = Game::load(CRYPT).unwrap();
    assert_eq!(g.run("N"), Err("step 1 (N): blocked".to_string()));
    assert_eq!(g.run("SSX"), Err("step 3 (X): bad command: X".to_string()));
    assert_eq!(g.run(""), Ok(0));
}
