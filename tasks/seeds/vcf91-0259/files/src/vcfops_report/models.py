"""Small value types returned by the client. Already implemented."""

from __future__ import annotations

from collections import namedtuple

#: Outcome of a completed end-to-end report generation.
#:
#: report_id -- identifier assigned by createReport
#: status    -- the terminal status observed by getReport (always the contract's
#:              successStatus; a failure raises instead of returning)
#: polls     -- number of getReport calls made while waiting for a terminal status
#: content   -- the downloaded report bytes
ReportResult = namedtuple("ReportResult", "report_id status polls content")
