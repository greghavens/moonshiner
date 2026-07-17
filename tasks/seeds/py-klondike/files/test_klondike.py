"""Acceptance tests for the klondike engine + CLI. Run: python3 test_klondike.py"""
import subprocess
import sys


def run_cli(deck, script):
    p = subprocess.run([sys.executable, "klondike.py", deck],
                       input=script, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, (p.returncode, p.stderr)
    return p.stdout


def illegal(fn, *args):
    from klondike import IllegalMove
    try:
        fn(*args)
    except IllegalMove:
        return True
    return False


def value_error(fn, *args):
    try:
        fn(*args)
    except ValueError:
        return True
    return False


def tup(pile):
    return [(card, up) for card, up in pile]


def main():
    from klondike import Game, load

    # -- deck validation --
    good = open("deck2.txt").read().splitlines()
    assert value_error(Game, good[:51])                  # short deck
    assert value_error(Game, good[:51] + [good[0]])      # duplicate
    assert value_error(Game, good[:51] + ["1S"])         # bad rank
    assert value_error(Game, good[:51] + ["AX"])         # bad suit

    # -- the deal: pile p holds p+1 cards, only the top face-up --
    g = load("deck2.txt")
    assert [len(p) for p in g.tableau] == [1, 2, 3, 4, 5, 6, 7]
    for pile in g.tableau:
        assert all(not up for _, up in pile[:-1]) and pile[-1][1]
    assert g.stock_count == 24 and g.waste == []
    assert g.render() == ("stock:24 waste:-\nF: S:- H:- D:- C:-\n"
                          "t1: AS\nt2: # 9S\nt3: # # 8H\nt4: # # # 9C\n"
                          "t5: # # # # 9H\nt6: # # # # # KC\n"
                          "t7: # # # # # # 4C")

    # -- command grammar errors are ValueError, not IllegalMove --
    assert value_error(g.move, "x", "f")
    assert value_error(g.move, "t1", "t9")
    assert value_error(g.move, "t1", "t2", 0)

    # -- draw-3 and waste order: last flipped card is the playable top --
    assert illegal(g.move, "w", "f")                 # nothing in the waste yet
    g.draw()
    assert g.waste == ["4S", "3H", "2S"] and g.stock_count == 21

    # -- foundations build A,2,3... per suit --
    assert illegal(g.move, "w", "f")                 # 2S before the ace
    g.move("t1", "f")
    assert g.foundations["S"] == ["AS"] and g.tableau[0] == []
    g.move("w", "f")
    assert g.foundations["S"] == ["AS", "2S"] and g.waste == ["4S", "3H"]
    assert illegal(g.move, "w", "f")                 # 3H needs the heart ace

    # -- waste to tableau: red on black, one rank down --
    g.move("w", "t7")                                # 3H onto 4C
    assert tup(g.tableau[6])[-2:] == [("4C", True), ("3H", True)]
    assert g.waste == ["4S"]

    # -- tableau legality: colour, rank, face-down blocks, empty piles --
    assert illegal(g.move, "t3", "t5")               # 8H onto 9H: same colour
    assert illegal(g.move, "t3", "t2", 2)            # would drag a face-down card
    g.move("t3", "t2")                               # 8H onto 9S
    assert tup(g.tableau[2]) == [("QS", False), ("TS", True)]   # flip exposed
    g.move("t2", "t4")                               # 8H onto 9C
    assert illegal(g.move, "t4", "t5", 2)            # 9C+8H onto 9H: rank
    assert illegal(g.move, "t2", "t2")               # same pile
    assert illegal(g.move, "t7", "t1", 2)            # 4C-led run onto empty
    g.move("t6", "t1")                               # a king may take an empty pile
    assert tup(g.tableau[0]) == [("KC", True)]
    g.move("t6", "t1")                               # QD onto KC
    assert tup(g.tableau[0]) == [("KC", True), ("QD", True)]
    assert illegal(g.move, "t6", "t1")               # JD onto QD: same colour
    g.move("t3", "t6")                               # TS onto JD
    assert tup(g.tableau[2]) == [("QS", True)]       # bottom card flips up
    g.move("t6", "t3", 2)                            # the JD+TS run rides to QS
    assert tup(g.tableau[2]) == [("QS", True), ("JD", True), ("TS", True)]
    assert tup(g.tableau[5]) == [("2C", False), ("5C", False), ("8C", True)]

    # -- recycling the waste repeats the original draw order --
    g = load("deck2.txt")
    for _ in range(8):
        g.draw()
    assert g.stock_count == 0 and len(g.waste) == 24 and g.waste[-1] == "JC"
    g.draw()                                         # turn the waste over + draw
    assert g.stock_count == 21 and g.waste == ["4S", "3H", "2S"]

    # -- the winnable deal: draw/auto pairs clear the whole board --
    from klondike import IllegalMove
    g = load("deck_win.txt")
    rounds = []
    for _ in range(8):
        g.draw()
        rounds.append(len(g.auto()))
    assert rounds == [3, 3, 3, 3, 3, 3, 4, 30], rounds
    assert g.won
    assert all(g.foundations[s][-1][0] == "K" for s in "SHDC")
    assert g.stock_count == 0 and g.waste == [] and all(not p for p in g.tableau)
    try:
        g.draw()
    except IllegalMove:
        pass
    else:
        raise AssertionError("draw with empty stock and waste must be illegal")

    # -- auto scans waste first, then piles left to right (move log order) --
    g = load("deck_win.txt")
    g.draw()
    assert g.auto() == [("w", "AD"), ("w", "AH"), ("w", "AS")]
    assert g.auto() == []                            # nothing eligible: no moves

    # -- CLI: renders after every command, refusals don't re-render --
    assert run_cli("deck2.txt",
                   "draw\nmove t1 f\nmove w f\nmove w t7\nmove t3 t5\n"
                   "move t3 t2\nshuffle\nmove w f 2\nq\n") == (
        "stock:24 waste:-\nF: S:- H:- D:- C:-\nt1: AS\nt2: # 9S\nt3: # # 8H\n"
        "t4: # # # 9C\nt5: # # # # 9H\nt6: # # # # # KC\nt7: # # # # # # 4C\n"
        "stock:21 waste:4S 3H 2S\nF: S:- H:- D:- C:-\nt1: AS\nt2: # 9S\n"
        "t3: # # 8H\nt4: # # # 9C\nt5: # # # # 9H\nt6: # # # # # KC\n"
        "t7: # # # # # # 4C\n"
        "stock:21 waste:4S 3H 2S\nF: S:A H:- D:- C:-\nt1: -\nt2: # 9S\n"
        "t3: # # 8H\nt4: # # # 9C\nt5: # # # # 9H\nt6: # # # # # KC\n"
        "t7: # # # # # # 4C\n"
        "stock:21 waste:4S 3H\nF: S:2 H:- D:- C:-\nt1: -\nt2: # 9S\n"
        "t3: # # 8H\nt4: # # # 9C\nt5: # # # # 9H\nt6: # # # # # KC\n"
        "t7: # # # # # # 4C\n"
        "stock:21 waste:4S\nF: S:2 H:- D:- C:-\nt1: -\nt2: # 9S\n"
        "t3: # # 8H\nt4: # # # 9C\nt5: # # # # 9H\nt6: # # # # # KC\n"
        "t7: # # # # # # 4C 3H\n"
        "illegal move\n"
        "stock:21 waste:4S\nF: S:2 H:- D:- C:-\nt1: -\nt2: # 9S 8H\n"
        "t3: # TS\nt4: # # # 9C\nt5: # # # # 9H\nt6: # # # # # KC\n"
        "t7: # # # # # # 4C 3H\n"
        "bad command\nillegal move\n")

    # -- CLI: the full winnable game, byte for byte --
    with open("expected_win.out", encoding="utf-8") as fh:
        expected = fh.read()
    assert run_cli("deck_win.txt", "draw\nauto\n" * 8) == expected

    print("all klondike tests passed")


if __name__ == "__main__":
    main()
