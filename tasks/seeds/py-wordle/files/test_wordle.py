"""Acceptance tests for the wordle clone. Run: python3 test_wordle.py"""
import subprocess
import sys


def run_cli(args, stdin_text):
    p = subprocess.run([sys.executable, "wordle.py", *args],
                       input=stdin_text, capture_output=True, text=True,
                       timeout=60)
    assert p.returncode == 0, (p.returncode, p.stderr)
    return p.stdout


def main():
    from wordle import score_guess, hard_mode_error

    # -- plain hits and misses --
    assert score_guess("sweet", "sweet") == "GGGGG"
    assert score_guess("crate", "crane") == "GGG-G"
    assert score_guess("trace", "crane") == "-GGYG"
    assert score_guess("raise", "crane") == "YY--G"
    assert score_guess("react", "crane") == "YYGY-"

    # -- the duplicate-letter law: greens consume first, yellows go left
    # to right against whatever is left --
    assert score_guess("llama", "alley") == "YGY--"
    assert score_guess("eerie", "sweet") == "YY---"   # only two E to hand out
    assert score_guess("melee", "sweet") == "-Y-G-"   # green E eats one copy
    assert score_guess("steel", "sweet") == "GYGG-"
    assert score_guess("tenet", "sweet") == "-Y-GG"
    assert score_guess("banal", "alley") == "-Y--Y"
    assert score_guess("label", "alley") == "YY-GY"

    # -- mismatched lengths are a programming error --
    try:
        score_guess("four", "sweet")
    except ValueError:
        pass
    else:
        raise AssertionError("length mismatch must raise ValueError")

    # -- hard mode: greens are anchored, then letter counts, alphabetically --
    assert hard_mode_error("llama", "YGY--", "level") == \
        "hard mode: letter 2 must be L"
    assert hard_mode_error("llama", "YGY--", "sleet") == \
        "hard mode: not enough A"
    assert hard_mode_error("llama", "YGY--", "llama") is None
    assert hard_mode_error("llama", "YGY--", "alley") is None
    assert hard_mode_error("crate", "GG-YG", "crane") == \
        "hard mode: not enough T"
    assert hard_mode_error("crate", "GG-YG", "crate") is None
    assert hard_mode_error("adieu", "-----", "sweet") is None

    # -- CLI: a win prints each feedback then the solve count --
    assert run_cli(["answers.txt", "allowed.txt", "1"],
                   "raise\ntrace\ncrane\n") == \
        "YY--G\n-GGYG\nGGGGG\nsolved in 3\n"

    # -- CLI: six accepted guesses without the answer ends the game --
    assert run_cli(["answers.txt", "allowed.txt", "3"],
                   "adieu\nraise\nsteel\ntenet\nmelee\nsleet\n") == \
        "---G-\n---YY\nGYGG-\n-Y-GG\n-Y-G-\nG-GGG\nout of guesses: sweet\n"

    # -- CLI: unknown words, wrong lengths and junk cost no turn --
    assert run_cli(["answers.txt", "allowed.txt", "2"],
                   "zzzzz\ncat\nhell!\nllama\nalley\n") == \
        ("not a valid word\nnot a valid word\nnot a valid word\n"
         "YGY--\nGGGGG\nsolved in 2\n")

    # -- CLI: answers count as valid guesses even off the allowed list,
    # and input is case-insensitive --
    assert run_cli(["answers.txt", "allowed.txt", "1"],
                   "SWEET\nCrane\n") == "--Y--\nGGGGG\nsolved in 2\n"

    # -- CLI hard mode: refused guesses cost no turn either --
    assert run_cli(["answers.txt", "allowed.txt", "2", "--hard"],
                   "llama\nlevel\nsleet\nllama\nalley\n") == \
        ("YGY--\nhard mode: letter 2 must be L\nhard mode: not enough A\n"
         "YGY--\nGGGGG\nsolved in 3\n")

    # -- CLI: running out of stdin just ends the run --
    assert run_cli(["answers.txt", "allowed.txt", "1"], "raise\n") == "YY--G\n"

    print("all wordle tests passed")


if __name__ == "__main__":
    main()
