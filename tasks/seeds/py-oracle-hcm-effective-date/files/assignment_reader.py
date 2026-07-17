"""Read-side access to a worker assignment (child of workRelationships).

This is the piece that already works in production: it fetches the singular
assignment row, keeps the ETag from the response header, and mirrors the
changeIndicator that Fusion also exposes in the self link's properties.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit

from hcm_transport import API_ROOT


def assignment_path(worker_uid, period_of_service_id, assignment_uid):
    return (
        f"{API_ROOT}/workers/{worker_uid}"
        f"/child/workRelationships/{period_of_service_id}"
        f"/child/assignments/{assignment_uid}"
    )


@dataclass
class AssignmentSnapshot:
    fields: dict
    etag: str
    change_indicator: str
    self_path: str


def read_assignment(session, worker_uid, period_of_service_id, assignment_uid):
    path = assignment_path(worker_uid, period_of_service_id, assignment_uid)
    resp = session.request("GET", path)
    if resp.status != 200:
        raise RuntimeError(f"assignment read failed with HTTP {resp.status}")
    fields = dict(resp.body)
    links = fields.pop("links", [])
    change_indicator = None
    self_path = path
    for link in links:
        if link.get("rel") == "self":
            change_indicator = (link.get("properties") or {}).get("changeIndicator")
            href = link.get("href")
            if href:
                self_path = urlsplit(href).path
    return AssignmentSnapshot(
        fields=fields,
        etag=resp.headers.get("etag"),
        change_indicator=change_indicator,
        self_path=self_path,
    )
