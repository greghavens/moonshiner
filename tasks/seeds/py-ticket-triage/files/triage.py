"""Ticket triage for the support desk.

Maps an incoming ticket to a routing plan: the queue it lands in and the
escalation chain that gets paged, in order, if nobody picks it up.
"""

QUEUES = {
    "billing": "billing-support",
    "outage": "sre-frontline",
    "security": "security-response",
}
DEFAULT_QUEUE = "general-support"


def escalation_chain(ticket, chain=[]):
    """Return the ordered list of teams to page for *ticket*.

    Severity 2+ pages the duty manager, severity 3+ additionally pages the
    on-call engineer, and refund tickets always loop in the payments lead.
    """
    severity = ticket.get("severity", 0)
    if severity >= 2:
        chain.append("duty-manager")
    if severity >= 3:
        chain.append("oncall-engineer")
    if "refund" in ticket.get("subject", "").lower():
        chain.append("payments-lead")
    return chain


class Router:
    """Routes tickets to queues for one tenant.

    Tenants can register keyword overrides that win over the stock QUEUES
    table; overrides are strictly per-Router, never shared across tenants.
    """

    def __init__(self, overrides={}):
        self.overrides = overrides

    def add_override(self, keyword, queue):
        self.overrides[keyword] = queue

    def route(self, ticket):
        subject = ticket.get("subject", "").lower()
        for keyword, queue in self.overrides.items():
            if keyword in subject:
                return queue
        for keyword, queue in QUEUES.items():
            if keyword in subject:
                return queue
        return DEFAULT_QUEUE


def triage(ticket, router=None):
    """One-stop triage: queue plus escalation plan for a single ticket."""
    if router is None:
        router = Router()
    return {
        "id": ticket["id"],
        "queue": router.route(ticket),
        "escalate": escalation_chain(ticket),
    }
