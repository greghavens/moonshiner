"""The alert sweep: page through queryAlert, then pull details with getAlert."""

from .client import TokenExpired

DEFAULT_DETAIL_LEVELS = ("CRITICAL", "IMMEDIATE")


class SweepResult:
    """What one sweep collected."""

    def __init__(self):
        self.alerts = []
        self.details = {}
        self.detail_order = []
        self.total_count = 0
        self.pages_fetched = 0

    def as_report(self, client):
        return {
            "totalCount": self.total_count,
            "pagesFetched": self.pages_fetched,
            "tokenAcquisitions": client.token_acquisitions,
            "alertIds": [a["alertId"] for a in self.alerts],
            "alerts": self.alerts,
            "detailOrder": self.detail_order,
            "details": self.details,
        }


def sweep_alerts(client, page_size, filters=None, detail_levels=DEFAULT_DETAIL_LEVELS):
    """Collect every alert matching `filters`, then the full record for the interesting ones.

    Pages are walked from 0 upwards until the accumulated count reaches the reported
    totalCount or a page comes back empty.  Details are then fetched for every collected
    alert whose alertLevel is in `detail_levels`, in the order the alerts were returned.
    """
    while True:
        try:
            return _sweep(client, page_size, filters, detail_levels)
        except TokenExpired:
            # The access token died part way through.  Get a new one and try again.
            client.acquire_token()


def _sweep(client, page_size, filters, detail_levels):
    result = SweepResult()
    wanted = set(detail_levels)

    page = 0
    while True:
        payload = client.query_alerts(page, page_size, filters)
        result.pages_fetched += 1
        batch = payload.get("alerts") or []
        result.alerts.extend(batch)
        result.total_count = (payload.get("pageInfo") or {}).get("totalCount", len(result.alerts))
        if not batch or len(result.alerts) >= result.total_count:
            break
        page += 1

    for alert in result.alerts:
        alert_id = alert["alertId"]
        if alert.get("alertLevel") not in wanted or alert_id in result.details:
            continue
        result.details[alert_id] = client.get_alert(alert_id)
        result.detail_order.append(alert_id)

    return result
