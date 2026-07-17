"""Near-duplicate grouping for field incident reports.

Technicians file short free-text reports from handhelds. The same fault
arrives many times with different casing, punctuation, and spacing; the
dispatch board should show one card per distinct fault with the repeats
folded underneath it.

Near-duplicate rule (canonical, do not change): two reports match when
their normalized token sequences are equal. ``normalize`` is the single
source of truth for that rule; ``same`` is the pairwise predicate on top
of it.

``dedupe`` additionally accepts an ``on_compare`` hook: the perf suite
injects a counter through it, so every pairwise text comparison the
implementation performs must be reported by calling the hook once first.
"""


def normalize(text):
    """Canonical token sequence for a report.

    Casefold the text, treat every non-alphanumeric character as a
    separator, and return the tokens as a tuple (original order kept).
    """
    cleaned = "".join(ch if ch.isalnum() else " " for ch in text.casefold())
    return tuple(cleaned.split())


def same(a, b):
    """True when two report texts are near-duplicates of each other."""
    return normalize(a) == normalize(b)


def dedupe(reports, on_compare=None):
    """Fold near-duplicate reports into groups.

    Returns ``(keepers, groups)``:

    - ``keepers``: indices of first occurrences, in input order.
    - ``groups``: dict mapping a keeper index to the indices of its later
      duplicates (input order). Keepers without duplicates do not appear.

    ``on_compare``, when given, is a zero-argument callable invoked once
    per pairwise near-duplicate check between two report texts.
    """
    keepers = []
    groups = {}
    for i, text in enumerate(reports):
        hit = None
        for k in keepers:
            if on_compare is not None:
                on_compare()
            if same(reports[k], text):
                hit = k
                break
        if hit is None:
            keepers.append(i)
        else:
            groups.setdefault(hit, []).append(i)
    return keepers, groups
