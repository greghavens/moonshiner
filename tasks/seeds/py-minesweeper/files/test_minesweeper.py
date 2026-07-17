"""Acceptance tests for the minesweeper engine + CLI. Run: python3 test_minesweeper.py"""
import subprocess
import sys

LAYOUT1 = "layout1.txt"   # 6x6, mines at (1,1) (2,2) (3,4) (5,1)
LAYOUT2 = "layout2.txt"   # 4x4, mines at (0,0) (2,2)


def run_cli(layout, script):
    p = subprocess.run([sys.executable, "minesweeper.py", layout],
                       input=script, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, (p.returncode, p.stderr)
    return p.stdout


def expect_raises(fn, *args):
    try:
        fn(*args)
    except ValueError:
        return
    raise AssertionError(f"{fn.__name__}{args} should raise ValueError")


def main():
    from minesweeper import Game, load

    # -- layout validation --
    expect_raises(Game, [])
    expect_raises(Game, ["..", "..."])       # ragged
    expect_raises(Game, ["._"])              # unknown char
    expect_raises(Game, [""])                # zero-width

    # -- basic shape and counters --
    g = load(LAYOUT1)
    assert (g.rows, g.cols, g.mine_count) == (6, 6, 4)
    assert g.state == "playing"
    assert g.flags_left == 4

    # -- adjacency counts across the whole grid --
    expected = ["111000",
                "112100",
                "121211",
                "011201",
                "111111",
                "101000"]
    got = ["".join(str(g.adjacent(r, c)) for c in range(6)) for r in range(6)]
    assert got == expected, got

    # -- everything starts hidden --
    assert g.render() == "\n".join(["######"] * 6)

    # -- out-of-range coordinates are rejected --
    expect_raises(g.adjacent, -1, 0)
    expect_raises(g.reveal, 0, 6)
    expect_raises(g.flag, 6, 0)

    # -- revealing a numbered cell reveals only that cell --
    g = load(LAYOUT1)
    g.reveal(0, 0)
    assert g.render() == ("1#####\n######\n######\n######\n######\n######")
    assert g.state == "playing"

    # -- flood fill opens the connected zero region plus its numbered rim --
    g = load(LAYOUT1)
    g.reveal(0, 5)
    assert g.render() == ("##1...\n##21..\n###211\n######\n######\n######")
    assert len(g.revealed) == 11
    g2 = load(LAYOUT1)
    g2.reveal(5, 5)
    assert g2.render() == ("######\n######\n######\n######\n##1111\n##1...")
    assert len(g2.revealed) == 8

    # -- flags: toggle, block reveals, and are skipped by flood fill --
    g = load(LAYOUT1)
    g.flag(0, 4)
    assert g.flags_left == 3
    g.reveal(0, 4)                       # flagged: reveal is a no-op
    assert (0, 4) not in g.revealed
    g.reveal(0, 5)                       # flood must flow around the flag
    assert g.render() == ("##1.F.\n##21..\n###211\n######\n######\n######")
    assert len(g.revealed) == 10
    g.flag(0, 4)                         # unflag; still unrevealed
    assert g.flags_left == 4
    assert (0, 4) not in g.revealed
    g.reveal(0, 4)
    assert (0, 4) in g.revealed
    g.flag(0, 4)                         # flagging a revealed cell: no-op
    assert g.flags_left == 4
    g.flag(3, 0)
    g.flag(3, 1)
    g.flag(3, 2)
    g.flag(3, 3)
    g.flag(3, 5)
    assert g.flags_left == -1            # over-flagging just goes negative

    # -- stepping on a mine loses and unmasks every mine --
    g = load(LAYOUT1)
    g.reveal(0, 0)
    g.reveal(1, 1)
    assert g.state == "lost"
    assert g.render() == ("1#####\n#*####\n##*###\n####*#\n######\n#*####")
    before = set(g.revealed)
    g.reveal(4, 4)                       # game over: further moves are no-ops
    g.flag(4, 4)
    assert g.revealed == before and not g.flags

    # -- winning = every safe cell revealed; flags are irrelevant --
    g = load(LAYOUT2)
    for r, c in [(0, 3), (1, 0), (2, 0), (2, 3), (3, 3), (3, 2)]:
        assert g.state == "playing"
        g.reveal(r, c)
    assert g.state == "won"
    assert len(g.revealed) == 14

    # -- CLI: full winning transcript with a flag and a rejected command --
    assert run_cli(LAYOUT2, "r 0 3\nf 2 2\nr 3 0\nbogus\nr 2 3\nr 3 3\nr 3 2\n") == (
        "#1..\n#211\n####\n####\n"
        "#1..\n#211\n##F#\n####\n"
        "#1..\n1211\n.1F#\n.1##\n"
        "invalid command\n"
        "#1..\n1211\n.1F1\n.1##\n"
        "#1..\n1211\n.1F1\n.1#1\n"
        "#1..\n1211\n.1F1\n.111\n"
        "you win\n")

    # -- CLI: flag toggling then a mine ends the run with boom --
    assert run_cli(LAYOUT1, "r 0 0\nr 5 5\nf 1 1\nf 1 1\nr 2 2\n") == (
        "1#####\n######\n######\n######\n######\n######\n"
        "1#####\n######\n######\n######\n##1111\n##1...\n"
        "1#####\n#F####\n######\n######\n##1111\n##1...\n"
        "1#####\n######\n######\n######\n##1111\n##1...\n"
        "1#####\n#*####\n##*###\n####*#\n##1111\n#*1...\n"
        "boom\n")

    # -- CLI: junk, out-of-range and short commands are refused; q quits --
    assert run_cli(LAYOUT1, "x 1 1\nr 9 9\nr 1\n\nr 0 5\nq\nr 2 2\n") == (
        "invalid command\ninvalid command\ninvalid command\ninvalid command\n"
        "##1...\n##21..\n###211\n######\n######\n######\n")

    print("all minesweeper tests passed")


if __name__ == "__main__":
    main()
