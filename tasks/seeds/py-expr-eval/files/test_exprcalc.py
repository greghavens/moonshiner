"""Acceptance tests for the formula evaluator. Run: python3 test_exprcalc.py"""


def expect_error(source, variables=None):
    from exprcalc import ExprError, evaluate
    try:
        evaluate(source, variables)
    except ExprError as e:
        assert hasattr(e, "pos"), "ExprError must carry a .pos attribute"
        return e
    raise AssertionError(f"evaluate({source!r}) should raise ExprError")


def main():
    from exprcalc import evaluate

    # -- arithmetic and precedence --
    assert evaluate("2 + 3 * 4") == 14
    assert evaluate("(2 + 3) * 4") == 20
    assert evaluate("10 - 4 - 3") == 3          # left associative
    assert evaluate("20 / 4 / 5") == 1.0
    assert evaluate("7 % 3") == 1
    assert evaluate("2 ** 10") == 1024
    assert evaluate("2 ** 3 ** 2") == 512       # ** is right associative
    assert evaluate("-2 ** 2") == -4            # unary minus binds looser than **
    assert evaluate("2 ** -2") == 0.25
    assert evaluate("-(-3)") == 3
    assert evaluate("  2+3 ") == 5

    # integers stay integers; / always yields a float
    r = evaluate("2 + 3")
    assert r == 5 and isinstance(r, int) and not isinstance(r, bool)
    assert evaluate("7 / 2") == 3.5
    assert evaluate("0.5 + 0.25") == 0.75
    assert evaluate("1.5 * 2") == 3.0

    # -- variables --
    assert evaluate("price * qty", {"price": 2.5, "qty": 4}) == 10.0
    assert evaluate("a + a * a", {"a": 3}) == 12
    assert evaluate("1 + 1") == 2               # variables argument is optional

    # -- comparisons return real booleans --
    assert evaluate("1 + 1 == 2") is True
    assert evaluate("3 > 4") is False
    assert evaluate("2 <= 2") is True
    assert evaluate("1 != 2") is True
    assert evaluate("2 >= 3") is False
    assert evaluate("1 < 2") is True

    # -- boolean operators: precedence not > and > or, results are bool --
    assert evaluate("1 < 2 and 3 < 4") is True
    assert evaluate("1 > 2 or 3 < 4") is True
    assert evaluate("not 1 > 2") is True
    assert evaluate("1 == 1 or 1 == 2 and 0 == 1") is True
    assert evaluate("(1 == 1 or 1 == 2) and 0 == 1") is False
    assert evaluate("not 0") is True
    assert evaluate("not 3") is False
    # and/or coerce to bool -- they do NOT return the operand
    assert evaluate("x and y", {"x": 1, "y": 2}) is True
    assert evaluate("x or y", {"x": 0, "y": 7}) is True
    assert evaluate("x and y", {"x": 0, "y": 7}) is False

    # an identifier that merely starts with a keyword is a variable
    assert evaluate("nothing + 1", {"nothing": 1}) == 2
    assert evaluate("android", {"android": 9}) == 9

    # -- function calls --
    assert evaluate("min(3, 1, 2)") == 1
    assert evaluate("max(2 * 3, 10 % 7, 4)") == 6
    assert evaluate("abs(3 - 10)") == 7
    assert evaluate("max(min(5, 3), abs(-2))") == 3
    assert evaluate("min(9)") == 9
    # a known function name NOT followed by ( is an ordinary variable
    assert evaluate("min + 1", {"min": 4}) == 5

    # -- errors carry a 0-based position; end of input reports len(source) --
    e = expect_error("2 + ")
    assert e.pos == 4, e.pos
    e = expect_error("")
    assert e.pos == 0, e.pos
    e = expect_error("2 $ 3")
    assert e.pos == 2, e.pos                    # the bad character
    e = expect_error("(1 + 2")
    assert e.pos == 6, e.pos                    # unclosed paren noticed at EOF
    e = expect_error("1 +* 2")
    assert e.pos == 3, e.pos
    e = expect_error("()")
    assert e.pos == 1, e.pos
    e = expect_error("1 2")
    assert e.pos == 2, e.pos                    # trailing garbage
    e = expect_error("1 < 2 < 3")
    assert e.pos == 6, e.pos                    # comparisons do not chain

    # unknown variable: position of the name, name quoted in the message
    e = expect_error("a + 1", {"b": 1})
    assert e.pos == 0 and "a" in str(e), (e.pos, str(e))
    e = expect_error("total * rate", {"total": 3})
    assert e.pos == 8 and "rate" in str(e), (e.pos, str(e))
    e = expect_error("__import__", {})
    assert e.pos == 0

    # unknown function / wrong arity: position of the function name
    e = expect_error("2 + foo(3)")
    assert e.pos == 4 and "foo" in str(e), (e.pos, str(e))
    e = expect_error("abs(1, 2)")
    assert e.pos == 0, e.pos
    e = expect_error("min()")
    assert e.pos == 0, e.pos

    # division / modulo by zero: position of the operator
    e = expect_error("10 / (2 - 2)")
    assert e.pos == 3, e.pos
    e = expect_error("5 % 0")
    assert e.pos == 2, e.pos

    print("ok")


if __name__ == "__main__":
    main()
