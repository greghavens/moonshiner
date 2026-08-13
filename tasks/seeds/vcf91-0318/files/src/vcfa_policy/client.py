"""VCF Automation Policies client.

Speaks the three operations named in docs/contract.json:

    createOrUpdatePolicy   POST /policy/api/policies
    getPolicy              GET  /policy/api/policies/{id}
    getPolicyType          GET  /policy/api/policyTypes/{id}

Standard library only.
"""

from .errors import ApiError, PolicyTypeNotFoundError, VcfAutomationError  # noqa: F401

DEFAULT_MAX_ATTEMPTS = 4


def default_backoff(attempt):
    """Seconds to wait after a failed attempt (1-based)."""
    return min(2 ** (attempt - 1), 8)


class PolicyClient:
    """Client for the VCF Automation Policies API.

    Args:
        base_url: appliance origin, e.g. "https://automation.vcf.example.com".
            A trailing slash must be tolerated.
        token: bearer token; sent as "Authorization: Bearer <token>".
        timeout: per-request socket timeout in seconds.
        max_attempts: total attempts per request, retries included.
        backoff: callable taking the 1-based number of the attempt that just
            failed and returning seconds to sleep before the next one.
            Defaults to :func:`default_backoff`.
    """

    def __init__(self, base_url, token, *, timeout=30.0, max_attempts=DEFAULT_MAX_ATTEMPTS,
                 backoff=None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.backoff = backoff or default_backoff

    def get_policy_type(self, type_id):
        """GET /policy/api/policyTypes/{id}.

        Returns the PolicyType object. Raises PolicyTypeNotFoundError on 404
        and ApiError on any other non-2xx status.
        """
        raise NotImplementedError

    def get_policy(self, policy_id):
        """GET /policy/api/policies/{id}.

        Returns the Policy object, or None if the appliance answers 404.
        Raises ApiError on any other non-2xx status.
        """
        raise NotImplementedError

    def ensure_policy(self, *, policy_id, type_id, name, definition, description=None,
                      enforcement_type=None, project_id=None, org_id=None):
        """Converge one policy to the requested state, safely retryable.

        Returns the stored Policy object read back from the appliance.
        """
        raise NotImplementedError
