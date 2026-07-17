"""Acceptance tests for the snake tick engine. Run: python3 test_snake.py"""


def frame(rows):
    bar = "+" + "-" * len(rows[0]) + "+"
    return "\n".join([bar] + ["|" + r + "|" for r in rows] + [bar])


def collect(game, script):
    out = [game.render()]
    for ch in script:
        game.tick(None if ch == "." else ch)
        out.append(game.render())
    return out


def expect_value_error(fn, *args):
    try:
        fn(*args)
    except ValueError:
        return
    raise AssertionError(f"expected ValueError from {fn}{args}")


def main():
    from snake import Game, load

    # -- constructor validation --
    expect_value_error(Game, 1, 5, [(0, 0)], "E", [])
    expect_value_error(Game, 5, 5, [(0, 0)], "Q", [])
    expect_value_error(Game, 5, 5, [], "E", [])
    expect_value_error(Game, 5, 5, [(5, 0)], "E", [])
    expect_value_error(Game, 5, 5, [(1, 1), (2, 1), (1, 1)], "E", [])

    # -- fixture parsing --
    g = load("snake1.txt")
    assert (g.width, g.height) == (8, 5)
    assert g.segments == [(3, 2), (2, 2)]
    assert g.direction == "E"
    assert g.food == (5, 2)
    assert g.score == 0 and g.alive

    # -- movement and growth: eat two queued foods, exact frames --
    assert collect(g, "EE.S") == [
        frame(["........", "........", "..#@.*..", "........", "........"]),
        frame(["........", "........", "...#@*..", "........", "........"]),
        frame(["........", "........", "...##@..", "......*.", "........"]),
        frame(["........", "........", "....##@.", "......*.", "........"]),
        frame(["........", ".*......", "....###.", "......@.", "........"]),
    ]
    assert g.score == 2
    assert g.food == (1, 1)
    assert g.segments == [(6, 3), (6, 2), (5, 2), (4, 2)]

    # -- eating the whole queue: food runs out, no '*' left on the board --
    g = load("snake1.txt")
    collect(g, "EE.SENNWWWWWW")
    assert g.score == 3 and g.alive and g.food is None
    assert g.render() == frame(
        ["........", ".@####..", "........", "........", "........"])
    assert len(g.segments) == 5

    # -- tick with a junk command --
    expect_value_error(g.tick, "X")

    # -- reversing into yourself is ignored (length > 1), then the wall bites --
    g = load("snake1.txt")
    fs = collect(g, "W....")
    assert fs == [
        frame(["........", "........", "..#@.*..", "........", "........"]),
        frame(["........", "........", "...#@*..", "........", "........"]),
        frame(["........", "........", "...##@..", "......*.", "........"]),
        frame(["........", "........", "....##@.", "......*.", "........"]),
        frame(["........", "........", ".....##@", "......*.", "........"]),
        frame(["........", "........", ".....##@", "......*.", "........"]),
    ]
    assert not g.alive
    assert g.score == 1
    assert g.segments == [(7, 2), (6, 2), (5, 2)]

    # -- a single-segment snake may reverse freely --
    g = Game(5, 3, [(2, 1)], "E", [])
    g.tick("W")
    assert g.alive and g.segments == [(1, 1)]
    assert g.render() == frame([".....", ".@...", "....."])

    # -- chasing your own tail is legal: the tail cell vacates this tick --
    g = load("snake2.txt")
    fs = collect(g, "SENWSENW")
    assert g.alive and g.score == 0 and len(g.segments) == 4
    assert fs[0] == frame(["......", "..@#..", "..##..", "......"])
    assert fs[8] == fs[0]                     # two full laps, back to start
    assert fs[4] == fs[0]

    # -- running into your own body is fatal; the board freezes as it was --
    g = load("snake3.txt")
    fs = collect(g, "ESS")
    assert fs == [
        frame([".......", ".@.#...", ".#.#...", ".###...", "......."]),
        frame([".......", ".#@....", ".#.#...", ".###...", "......."]),
        frame([".......", ".##....", ".#@....", ".###...", "......."]),
        frame([".......", ".##....", ".#@....", ".###...", "......."]),
    ]
    assert not g.alive
    # dead means dead: another tick is a hard error
    try:
        g.tick("N")
    except RuntimeError:
        pass
    else:
        raise AssertionError("tick() after death must raise RuntimeError")

    print("all snake tests passed")


if __name__ == "__main__":
    main()
