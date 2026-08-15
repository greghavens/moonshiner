#!/usr/bin/env python3
"""Protected acceptance checks for the confirmed knowledge dashboard."""

from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE_SHA256 = "7d432dca23f3b1e1b06d572879ddd311a3e89787f91facde760a4d02296f82e7"

EXPECTED = """# Weekly Knowledge Dashboard

_Organized from `inbox.md`; source retained unchanged._

## Active

- [ ] **2026-08-14 — Compare neighborhood bike routes**
  - Note: Trace the protected-lane options before Saturday’s library trip.
  - Source: Pocket notebook, page 18
- [ ] **2026-08-14 — Outline autumn reading list**
  - Note: Compare the three library holds before choosing the first book.
  - Source: Reading notebook, folio 6
- [ ] **2026-08-11 — Draft balcony herb plan**
  - Note: Group the pots by morning light and watering frequency.
  - Source: Apartment notebook, balcony tab
- [ ] **2026-08-03 — Index family recipe cards**
  - Note: Add ingredient and occasion tags without rewriting Grandma Inez’s wording.
  - Source: Cedar recipe box, divider C

## Needs input

- [ ] **2026-08-13 — Choose archival photo sleeves**
  - Note: Compare the two acid-free sizes once the album measurements are available.
  - Source: Photo inventory, album sheet A
- [ ] **2026-08-13 — Label oral-history recordings**
  - Note: Match the two unlabeled clips after Sam confirms which interview came first.
  - Source: Voice memos folder, clips 27–28
- [ ] **2026-08-07 — Confirm museum membership dates**
  - Note: Record the renewal window when the paper notice arrives.
  - Source: Household folder, memberships sleeve
- [ ] **2026-07-29 — Map borrowed books**
  - Note: Add current borrowers after Lee replies about the two history titles.
  - Source: Bookshelf index, blue card

## Done

- [x] **2026-08-12 — Back up language-study notes**
  - Note: Copy the annotated vocabulary sheets into the monthly archive.
  - Source: Language binder, August tab
- [x] **2026-08-05 — Scan appliance manuals**
  - Note: Save the warranty pages with searchable model-number labels.
  - Source: Kitchen drawer, manual envelope
- [x] **2026-08-05 — Tag camping checklist**
  - Note: Mark the cold-weather items so the summer packing view can exclude them.
  - Source: Field notebook, back pocket
- [x] **2026-07-22 — Merge duplicate garden notes**
  - Note: Keep the bed measurements from the newer sketch and the soil notes from both.
  - Source: Garden journal, pages 31 and 44
"""


def visible_files() -> set[str]:
    found: set[str] = set()
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if (
            not path.is_file()
            or ".git" in relative.parts
            or ".sandbox-home" in relative.parts
            or "__pycache__" in relative.parts
        ):
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        found.add(relative.as_posix())
    return found


def main() -> None:
    source = ROOT / "inbox.md"
    dashboard = ROOT / "organized" / "weekly-dashboard.md"

    actual_source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    assert actual_source_hash == SOURCE_SHA256, "inbox.md must remain byte-for-byte unchanged"

    assert dashboard.is_file(), "create organized/weekly-dashboard.md after confirmation"
    actual = dashboard.read_text(encoding="utf-8")
    assert actual == EXPECTED, (
        "dashboard must retain every clarified rule while applying only the corrected "
        "path, section name, and newest-first order"
    )

    allowed = {".gitignore", "inbox.md", "test_dashboard.py", "organized/weekly-dashboard.md"}
    assert visible_files() == allowed, "the confirmed delivery permits no other workspace files"
    assert not (ROOT / "knowledge" / "weekly-brief.md").exists(), "do not create the superseded path"

    print("dashboard matches the corrected, confirmed conversation state")


if __name__ == "__main__":
    main()
