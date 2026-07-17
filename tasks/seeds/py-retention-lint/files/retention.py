"""Retention policy loader for the snapshot janitor.

Policy files are line based:

    # comments and blank lines are ignored (inline comments too)
    rule hot-db    match=db-*    max_age=48h   action=keep
    rule warm-db   match=db-*    max_age=30d   action=archive
    default delete

Rules are evaluated top to bottom; the first rule whose pattern matches the
dataset name AND whose max_age has not been exceeded decides. If no rule
fires, the default action applies (or 'keep' when no default line is given).

Patterns are either a literal dataset name or a literal prefix with a single
trailing '*' — nothing fancier, on purpose.
"""

ACTIONS = ("keep", "archive", "delete")


class Rule:
    def __init__(self, name, pattern, max_age_hours, action):
        self.name = name
        self.pattern = pattern
        self.max_age_hours = max_age_hours
        self.action = action

    def __repr__(self):
        return (f"Rule({self.name!r}, {self.pattern!r}, "
                f"{self.max_age_hours}h, {self.action!r})")


class Policy:
    def __init__(self, rules, default_action):
        self.rules = rules
        self.default_action = default_action


def _parse_age(raw):
    unit = raw[-1:]
    if unit not in ("h", "d") or not raw[:-1].isdigit():
        raise ValueError(f"bad age {raw!r}: expected a positive integer with h or d")
    value = int(raw[:-1])
    if value <= 0:
        raise ValueError(f"bad age {raw!r}: must be positive")
    return value * 24 if unit == "d" else value


def _check_pattern(pattern):
    if not pattern:
        raise ValueError("bad pattern '': empty")
    if "*" in pattern[:-1] or pattern.count("*") > 1:
        raise ValueError(
            f"bad pattern {pattern!r}: only a single trailing '*' is allowed")


def load_policy(text):
    """Parse policy text into a Policy. Raises ValueError on any bad line."""
    rules = []
    names = set()
    default_action = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        tokens = line.split()
        if tokens[0] == "default":
            if len(tokens) != 2 or tokens[1] not in ACTIONS:
                raise ValueError(f"line {lineno}: bad default line")
            if default_action is not None:
                raise ValueError(f"line {lineno}: duplicate default line")
            default_action = tokens[1]
            continue
        if tokens[0] != "rule" or len(tokens) < 2:
            raise ValueError(f"line {lineno}: unparseable line: {line!r}")
        name = tokens[1]
        if name in names:
            raise ValueError(f"line {lineno}: duplicate rule name {name!r}")
        fields = {}
        for tok in tokens[2:]:
            key, eq, value = tok.partition("=")
            if not eq or key not in ("match", "max_age", "action") or not value:
                raise ValueError(f"line {lineno}: bad field {tok!r}")
            if key in fields:
                raise ValueError(f"line {lineno}: repeated field {key!r}")
            fields[key] = value
        missing = {"match", "max_age", "action"} - set(fields)
        if missing:
            raise ValueError(f"line {lineno}: missing {sorted(missing)}")
        if fields["action"] not in ACTIONS:
            raise ValueError(f"line {lineno}: unknown action {fields['action']!r}")
        _check_pattern(fields["match"])
        names.add(name)
        rules.append(Rule(name, fields["match"],
                          _parse_age(fields["max_age"]), fields["action"]))
    return Policy(rules, default_action or "keep")


def pattern_matches(pattern, name):
    if pattern.endswith("*"):
        return name.startswith(pattern[:-1])
    return name == pattern


def evaluate(policy, name, age_hours):
    """Action for a snapshot of `name` that is `age_hours` old."""
    for rule in policy.rules:
        if pattern_matches(rule.pattern, name) and age_hours < rule.max_age_hours:
            return rule.action
    return policy.default_action
