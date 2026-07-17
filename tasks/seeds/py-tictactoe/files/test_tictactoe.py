"""Acceptance tests for the tic-tac-toe CLI. Run: python3 test_tictactoe.py"""
import subprocess
import sys

WIN_LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8),
             (0, 3, 6), (1, 4, 7), (2, 5, 8),
             (0, 4, 8), (2, 4, 6))


def judge(board):
    """Independent win check so the bot audit doesn't trust the module."""
    for a, b, c in WIN_LINES:
        if board[a] != "." and board[a] == board[b] == board[c]:
            return board[a]
    return None


def run_cli(args, stdin_text):
    p = subprocess.run([sys.executable, "tictactoe.py", *args],
                       input=stdin_text, capture_output=True, text=True,
                       timeout=60)
    assert p.returncode == 0, (p.returncode, p.stderr)
    return p.stdout


def audit_bot(board, to_move, bot, best_move, seen):
    """Walk every opponent line; the bot plays its own move. Bot must never lose."""
    key = board + to_move
    if key in seen:
        return
    seen.add(key)
    w = judge(board)
    if w is not None:
        assert w == bot, f"bot lost as {bot}: final board {board}"
        return
    if "." not in board:
        return
    nxt = "O" if to_move == "X" else "X"
    if to_move == bot:
        mv = best_move(board, bot)
        assert isinstance(mv, int) and 0 <= mv <= 8 and board[mv] == ".", \
            f"bot suggested illegal move {mv!r} on {board}"
        audit_bot(board[:mv] + bot + board[mv + 1:], nxt, bot, best_move, seen)
    else:
        for i, cell in enumerate(board):
            if cell == ".":
                audit_bot(board[:i] + to_move + board[i + 1:], nxt, bot,
                          best_move, seen)


def main():
    from tictactoe import best_move, render, winner

    # -- render is the pinned 3-row grid --
    assert render("." * 9) == ".|.|.\n-+-+-\n.|.|.\n-+-+-\n.|.|."
    assert render("XO..X...O") == "X|O|.\n-+-+-\n.|X|.\n-+-+-\n.|.|O"

    # -- winner sees rows, columns, diagonals --
    assert winner("XXX...OO.") == "X"
    assert winner("O..XXXO..") == "X"
    assert winner("O..O..O.X") == "O"           # left column
    assert winner("X.O.O.OX.") == "O"           # anti-diagonal
    assert winner("XO.OX..XO") is None          # nobody yet
    assert winner("." * 9) is None

    # -- pinned bot replies for known positions --
    assert best_move("." * 9, "X") == 0          # all openings draw; lowest index
    assert best_move("X........", "O") == 4      # take the centre
    assert best_move("X...O....", "X") == 1
    assert best_move("XX..O....", "O") == 2      # forced block
    assert best_move("XX.OO....", "O") == 5      # winning beats blocking
    assert best_move("XX.OO....", "X") == 2      # take the immediate win
    assert best_move("XO..X....", "O") == 8      # block the diagonal
    assert best_move("XOXXO....", "O") == 7      # complete the middle column
    assert best_move("X...O...X", "X") == 2
    assert best_move("X...O...X", "O") == 1
    assert best_move(".X.XO...O", "X") == 0

    # -- exhaustive audit: the bot never loses, either side, vs every line --
    audit_bot("." * 9, "X", "O", best_move, set())
    audit_bot("." * 9, "X", "X", best_move, set())

    # -- two-player transcript: X wins across the top row --
    assert run_cli([], "0\n3\n1\n4\n2\n") == (
        ".|.|.\n-+-+-\n.|.|.\n-+-+-\n.|.|.\nX> X|.|.\n-+-+-\n.|.|.\n-+-+-\n"
        ".|.|.\nO> X|.|.\n-+-+-\nO|.|.\n-+-+-\n.|.|.\nX> X|X|.\n-+-+-\nO|.|.\n"
        "-+-+-\n.|.|.\nO> X|X|.\n-+-+-\nO|O|.\n-+-+-\n.|.|.\nX> X|X|X\n-+-+-\n"
        "O|O|.\n-+-+-\n.|.|.\nX wins\n")

    # -- two-player transcript: a full-board draw --
    assert run_cli([], "0\n4\n1\n2\n6\n3\n5\n7\n8\n") == (
        ".|.|.\n-+-+-\n.|.|.\n-+-+-\n.|.|.\nX> X|.|.\n-+-+-\n.|.|.\n-+-+-\n"
        ".|.|.\nO> X|.|.\n-+-+-\n.|O|.\n-+-+-\n.|.|.\nX> X|X|.\n-+-+-\n.|O|.\n"
        "-+-+-\n.|.|.\nO> X|X|O\n-+-+-\n.|O|.\n-+-+-\n.|.|.\nX> X|X|O\n-+-+-\n"
        ".|O|.\n-+-+-\nX|.|.\nO> X|X|O\n-+-+-\nO|O|.\n-+-+-\nX|.|.\nX> X|X|O\n"
        "-+-+-\nO|O|X\n-+-+-\nX|.|.\nO> X|X|O\n-+-+-\nO|O|X\n-+-+-\nX|O|.\n"
        "X> X|X|O\n-+-+-\nO|O|X\n-+-+-\nX|O|X\ndraw\n")

    # -- bad input re-prompts the same player: occupied, junk, out of range --
    assert run_cli([], "0\n0\nabc\n9\n3\n1\n4\n2\n") == (
        ".|.|.\n-+-+-\n.|.|.\n-+-+-\n.|.|.\nX> X|.|.\n-+-+-\n.|.|.\n-+-+-\n"
        ".|.|.\nO> invalid move\nX|.|.\n-+-+-\n.|.|.\n-+-+-\n.|.|.\n"
        "O> invalid move\nX|.|.\n-+-+-\n.|.|.\n-+-+-\n.|.|.\nO> invalid move\n"
        "X|.|.\n-+-+-\n.|.|.\n-+-+-\n.|.|.\nO> X|.|.\n-+-+-\nO|.|.\n-+-+-\n"
        ".|.|.\nX> X|X|.\n-+-+-\nO|.|.\n-+-+-\n.|.|.\nO> X|X|.\n-+-+-\nO|O|.\n"
        "-+-+-\n.|.|.\nX> X|X|X\n-+-+-\nO|O|.\n-+-+-\n.|.|.\nX wins\n")

    # -- bot game: careless human walks into the 2-4-6 diagonal --
    assert run_cli(["--bot"], "0\n1\n3\n") == (
        ".|.|.\n-+-+-\n.|.|.\n-+-+-\n.|.|.\nX> X|.|.\n-+-+-\n.|.|.\n-+-+-\n"
        ".|.|.\nO plays 4\nX|.|.\n-+-+-\n.|O|.\n-+-+-\n.|.|.\nX> X|X|.\n-+-+-\n"
        ".|O|.\n-+-+-\n.|.|.\nO plays 2\nX|X|O\n-+-+-\n.|O|.\n-+-+-\n.|.|.\n"
        "X> X|X|O\n-+-+-\nX|O|.\n-+-+-\n.|.|.\nO plays 6\nX|X|O\n-+-+-\nX|O|.\n"
        "-+-+-\nO|.|.\nO wins\n")

    # -- bot game: careful human reaches a draw --
    assert run_cli(["--bot"], "0\n1\n6\n5\n8\n") == (
        ".|.|.\n-+-+-\n.|.|.\n-+-+-\n.|.|.\nX> X|.|.\n-+-+-\n.|.|.\n-+-+-\n"
        ".|.|.\nO plays 4\nX|.|.\n-+-+-\n.|O|.\n-+-+-\n.|.|.\nX> X|X|.\n-+-+-\n"
        ".|O|.\n-+-+-\n.|.|.\nO plays 2\nX|X|O\n-+-+-\n.|O|.\n-+-+-\n.|.|.\n"
        "X> X|X|O\n-+-+-\n.|O|.\n-+-+-\nX|.|.\nO plays 3\nX|X|O\n-+-+-\nO|O|.\n"
        "-+-+-\nX|.|.\nX> X|X|O\n-+-+-\nO|O|X\n-+-+-\nX|.|.\nO plays 7\nX|X|O\n"
        "-+-+-\nO|O|X\n-+-+-\nX|O|.\nX> X|X|O\n-+-+-\nO|O|X\n-+-+-\nX|O|X\n"
        "draw\n")

    print("all tictactoe tests passed")


if __name__ == "__main__":
    main()
