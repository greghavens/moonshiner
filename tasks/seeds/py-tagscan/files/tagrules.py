"""Regex patterns for the asset-tag formats the scanner fleet emits."""
import re

# Site asset tags: two-letter site code, dash, four digits ("IT-0042").
TAG_PATTERN = re.compile("^(?P<site>[A-Z]{2})-(?P<num>\d{4})$")

# Room stickers on the door frames: "room 12b", any case.
ROOM_PATTERN = re.compile("^room\s+(\w+)$", re.IGNORECASE)

# Dates the handhelds stamp into free-text notes: 2026.07.01
DATE_PATTERN = re.compile("(\d{4})\.(\d{2})\.(\d{2})")


def is_tag(text):
    return TAG_PATTERN.match(text) is not None
