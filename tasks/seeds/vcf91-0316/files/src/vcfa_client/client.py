"""VCF Automation 9.1 batch deployment client.

Everything below is a stub. Implement it against docs/contract.json.

The appliance issues short-lived bearer access tokens. One will expire part way
through a batch run, and the run must survive that without redoing work that has
already been done and without submitting any deployment twice.
"""


class TokenExpired(Exception):
    """Raised internally when the appliance rejects the current access token."""


class VcfaClient:
    """Client for the five operations named in docs/contract.json."""

    def __init__(self, config):
        self.base_url = config["baseUrl"].rstrip("/")
        self.client_id = config["clientId"]
        self.client_secret = config["clientSecret"]
        self.refresh_token = config["refreshToken"]
        self.project_id = config["projectId"]
        self.access_token = None

    # -- authentication -------------------------------------------------

    def issue_access_token(self):
        """POST /csp/gateway/am/api/auth/token, refresh_token grant.

        Store the new access token on the instance.
        """
        raise NotImplementedError

    # -- catalog --------------------------------------------------------

    def list_catalog_items(self):
        """GET /catalog/api/items -> list of catalog item objects."""
        raise NotImplementedError

    def request_catalog_item(self, catalog_item_id, spec):
        """POST /catalog/api/items/{id}/request.

        spec is one entry from the batch file. Returns the list of created
        {deploymentId, deploymentName} objects the appliance responds with.
        """
        raise NotImplementedError

    # -- deployments ----------------------------------------------------

    def list_deployment_requests(self, deployment_id):
        """GET /deployment/api/deployments/{deploymentId}/requests -> content list."""
        raise NotImplementedError

    def get_request(self, request_id):
        """GET /deployment/api/requests/{requestId} -> the request object."""
        raise NotImplementedError


def run_batch(config, batch):
    """Submit every entry in the batch and wait for each to reach a terminal state.

    Returns a list of result objects, one per batch entry, each shaped:

        {"deploymentName", "deploymentId", "requestId",
         "status", "completedTasks", "totalTasks"}
    """
    raise NotImplementedError
