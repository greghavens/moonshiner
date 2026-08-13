"""HTTP client for the VCF 9.1 SDDC LCM support-bundle workflow.

Every URL this client builds comes from the spec-derived contract loaded by
:mod:`vcf_lcm.contract` -- the method and path template for an operation are
looked up by ``operationId``, never hard-coded here.
"""

DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_TIMEOUT = 1800.0


class LcmApiError(RuntimeError):
    """A non-2xx response, or a response the workflow cannot use.

    ``status_code`` is the HTTP status when one was received, else ``None``.
    ``payload`` is the decoded ``ErrorResponse`` body when the body was JSON.
    """

    def __init__(self, message, status_code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class TaskFailedError(RuntimeError):
    """A polled task reached a terminal status that is not a success.

    ``task`` is the terminal task object as returned by the service.
    """

    def __init__(self, message, task=None):
        super().__init__(message)
        self.task = task


class TaskTimeoutError(RuntimeError):
    """A polled task did not reach a terminal status within the timeout.

    ``task`` is the last task object observed, if any.
    """

    def __init__(self, message, task=None):
        super().__init__(message)
        self.task = task


class SddcLcmClient:
    """Client for the three contracted SDDC LCM operations.

    :param base_url: service root, e.g. ``https://vcf.example.com/sddc-lcm``
    :param token: bearer token presented on every request
    :param contract: an already-loaded contract dict; when ``None`` the
        contract is loaded from ``contract_path``
    :param contract_path: path to ``contract.json``; when ``None`` the default
        location is used
    :param poll_interval: default seconds between task reads
    :param timeout: default seconds to wait for a task to become terminal
    """

    def __init__(
        self,
        base_url,
        token,
        contract=None,
        contract_path=None,
        poll_interval=DEFAULT_POLL_INTERVAL,
        timeout=DEFAULT_TIMEOUT,
    ):
        raise NotImplementedError

    def generate_support_bundle(
        self, component_id, look_back_window=None, correlation_id=None
    ):
        """Start bundle generation for ``component_id``.

        Implements ``generateComponentSupportBundle``. ``look_back_window`` and
        ``correlation_id`` are optional: when either is ``None`` the
        corresponding body property / header is omitted from the request
        entirely rather than sent with an empty or zero value.

        Returns the accepted ``Task`` object. This task is *not* complete.
        """
        raise NotImplementedError

    def get_task(self, task_id):
        """Read a single task. Implements ``getTask``."""
        raise NotImplementedError

    def list_support_bundles(self, component_id):
        """List a component's support bundles.

        Implements ``getComponentSupportBundles``. Returns a list of
        ``SupportBundle`` objects.
        """
        raise NotImplementedError

    def await_task(self, task_id, poll_interval=None, timeout=None):
        """Poll ``task_id`` until its status is terminal and return that task.

        Re-reads the task every ``poll_interval`` seconds and stops issuing
        requests as soon as a terminal status is observed. Raises
        :class:`TaskFailedError` for a terminal status that is not a success,
        and :class:`TaskTimeoutError` if ``timeout`` elapses first.
        """
        raise NotImplementedError

    def generate_support_bundle_and_wait(
        self,
        component_id,
        look_back_window=None,
        correlation_id=None,
        poll_interval=None,
        timeout=None,
    ):
        """Run the whole workflow and return the produced ``SupportBundle``.

        Starts generation, polls the task to a terminal state, then lists the
        component's bundles and returns the one whose ``id`` matches the
        terminal task's ``resourceId``. Raises :class:`LcmApiError` when no
        such bundle is listed.
        """
        raise NotImplementedError
