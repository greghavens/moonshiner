"""Password strength meter behind the signup form's live indicator.

score() grades a candidate password 0 (hopeless) to 4 (strong) from two
signals: sheer length, and how many character classes it draws from.
verdict() maps the grade onto the label the UI shows next to the meter.
"""
import string


def char_classes(password):
    """The set of character classes the password draws from.

    Classes are 'lower', 'upper', 'digit' and 'symbol'; anything that is
    not an ASCII letter or digit counts as a symbol.
    """
    classes = set()
    for ch in password:
        if ch in string.ascii_lowercase:
            classes.add("lower")
        elif ch in string.ascii_uppercase:
            classes.add("upper")
        elif ch in string.digits:
            classes.add("digit")
        else:
            classes.add("symbol")
    return classes


def length_points(password):
    """0-3 points for length alone: tiers at 8, 12 and 16 characters."""
    n = len(password)
    if n >= 16:
        return 3
    if n >= 12:
        return 2
    if n >= 8:
        return 1
    return 0


def score(password):
    """Overall grade 0-4: length points plus one per extra class, capped."""
    if not password:
        return 0
    variety = len(char_classes(password)) - 1
    return min(4, length_points(password) + variety)


VERDICTS = ["very weak", "weak", "fair", "good", "strong"]


def verdict(password):
    """The label the signup form shows for this password."""
    return VERDICTS[score(password)]
