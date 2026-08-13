"""Client for the SDDC LCM task collection.

Implement this against the contract -- see README.md sections 2.2, 2.3 and 3.
"""


class SddcLcmClient:
    """Collects the SDDC LCM task inventory over the paginated collection."""

    def __init__(self, base_url, token, contract, timeout=10.0):
        raise NotImplementedError

    def get_task(self, task_id):
        """Fetch a single full Task by id."""
        raise NotImplementedError

    def list_tasks(self, filters=None, page_size=None):
        """Return every task summary, from every page, in stable order."""
        raise NotImplementedError

    def collect_tasks(self, filters=None, page_size=None):
        """Return the ordered inventory plus the full Task for each FAILED entry."""
        raise NotImplementedError
