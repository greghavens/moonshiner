"""Schema builder for patient intake forms.

Each clinic assembles its own intake form out of a shared field vocabulary.
A FormSchema holds the configured fields for one clinic; every field carries
a list of validator rules that check_submission() applies to the answers a
patient submits. Rules are plain tuples — ("required",), ("max_len", 120),
("one_of", [...]), ("digits",) — so a schema can be handed to the form
renderer or serialized without translation.
"""


def _check_rule(rule, value):
    """Return an error message for *value* under *rule*, or None if it passes."""
    name = rule[0]
    if name == "required":
        return None if str(value).strip() else "is required"
    if value == "":
        return None  # remaining rules only apply once something was entered
    if name == "max_len":
        limit = rule[1]
        return None if len(str(value)) <= limit else f"must be at most {limit} characters"
    if name == "one_of":
        options = rule[1]
        return None if value in options else "is not one of the allowed options"
    if name == "digits":
        return None if str(value).isdigit() else "must contain only digits"
    raise ValueError(f"unknown validator rule: {name!r}")


class FormSchema:
    """The intake form for a single clinic."""

    def __init__(self, clinic, fields={}):
        self.clinic = clinic
        self.fields = fields

    def add_field(self, name, kind="text", validators=[]):
        """Register a field on this form.

        *validators* is a list of rule tuples applied by check_submission();
        more rules can be attached later with add_rule()/require().
        """
        self.fields[name] = {"kind": kind, "validators": validators}
        return self

    def add_rule(self, field_name, rule, arg=None):
        """Attach one validator rule to an existing field."""
        if field_name not in self.fields:
            raise KeyError(f"no such field on {self.clinic!r} form: {field_name!r}")
        entry = (rule,) if arg is None else (rule, arg)
        self.fields[field_name]["validators"].append(entry)
        return self

    def require(self, *field_names):
        """Mark the given fields as mandatory."""
        for name in field_names:
            self.add_rule(name, "required")
        return self

    def field_names(self):
        return sorted(self.fields)

    def rules_for(self, field_name):
        """The validator rules currently attached to a field."""
        return list(self.fields[field_name]["validators"])

    def check_submission(self, answers):
        """Validate a submitted answer dict against this schema.

        Returns {field_name: [messages]} for every field with problems;
        an empty dict means the submission is acceptable. Fields missing
        from *answers* are treated as left blank.
        """
        errors = {}
        for name, field in self.fields.items():
            value = answers.get(name, "")
            problems = []
            for rule in field["validators"]:
                message = _check_rule(rule, value)
                if message is not None:
                    problems.append(f"{name} {message}")
            if problems:
                errors[name] = problems
        return errors
