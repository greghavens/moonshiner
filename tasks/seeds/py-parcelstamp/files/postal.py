"""Postal-code and routing-label validation for the depot conveyor."""

import re

ZIP_RE = re.compile("\d{5}(-\d{4})?")
ROUTE_RE = re.compile("^[A-Z]{2}\s\d{3}$")


def is_zip(text):
    """True for a US ZIP or ZIP+4, e.g. 02134 or 02134-1021."""
    return ZIP_RE.fullmatch(text) is not None


def is_route(text):
    """True for a conveyor routing label like 'NE 140'."""
    return ROUTE_RE.match(text) is not None


def split_units(label):
    """Split a multi-unit label like 'NE 140; NW 022' into its units."""
    return [u for u in re.split("\s*;\s*", label) if u]
