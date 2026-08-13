"""HTTP client for the VCF 9.1 SDDC LCM component upgrade workflow.

Every URL this client builds comes from the spec-derived contract loaded by
:mod:`vcf_lcm.contract` -- the method, path template and query parameters for
an operation are looked up by ``operationId``, never hard-coded here.

The access token is not a constant. It is obtained from a caller-supplied
``token_provider`` and can expire mid-run; see the README for the exact
refresh-and-replay behaviour required.
"""

DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_TIMEOUT = 1800.0


class SddcLcmClient:
    """Client for the five contracted SDDC LCM operations.

    :param base_url: service root, e.g. ``https://sddc-manager.vcf.lab.local/sddc-lcm``
    :param token_provider: zero-argument callable returning the current access
        token as a string. Called lazily -- not before the first request that
        the contract says is authenticated -- and called again to obtain a
        fresh token when a request comes back ``401``.
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
        token_provider,
        contract=None,
        contract_path=None,
        poll_interval=DEFAULT_POLL_INTERVAL,
        timeout=DEFAULT_TIMEOUT,
    ):
        raise NotImplementedError

    # -- operations ---------------------------------------------------------

    def get_health(self):
        """Read the service health. Implements ``getHealth``.

        The specification overrides the default security requirement for this
        operation, so the request carries no ``Authorization`` header and no
        token is obtained for it.
        """
        raise NotImplementedError

    def resolve_depot_components(
        self, depot_fqdn, depot_certificate, components, version=None
    ):
        """Resolve component versions to binary URLs.

        Implements ``resolveDepotComponents``.

        :param depot_fqdn: the Fleet depot FQDN
        :param depot_certificate: the depot's PEM-encoded certificate chain
        :param components: sequence of ``(component, version)`` pairs. A pair
            whose version is ``None`` means "resolve the latest" and must be
            sent as an object carrying only ``component``.
        :param version: optional release version for the whole request; when
            ``None`` the property is omitted from the body entirely.

        Returns the decoded ``ResolvedComponentVersions`` object.
        """
        raise NotImplementedError

    def perform_component_action(
        self,
        component_id,
        action,
        target_version,
        depot_url,
        depot_certificates=None,
        perform_backup=None,
        correlation_id=None,
    ):
        """Start an action on a component. Implements ``performComponentAction``.

        ``action`` must be one of the values the contract records for the
        operation's ``action`` query parameter.

        ``depot_certificates``, ``perform_backup`` and ``correlation_id`` are
        optional. When one is ``None`` the corresponding piece of the request
        is omitted entirely rather than sent empty -- including the whole
        ``lcmPlatformSpec`` object when ``perform_backup`` is ``None``.

        Returns the accepted ``Task`` object. This task is *not* complete.
        """
        raise NotImplementedError

    def get_task(self, task_id):
        """Read a single task. Implements ``getTask``."""
        raise NotImplementedError

    def retry_task(self, task_id):
        """Retry a failed task from the stage that failed.

        Implements ``retryTask``. Returns the ``Task`` object the service
        reports once the retry has been initiated.
        """
        raise NotImplementedError

    # -- workflow -----------------------------------------------------------

    def await_task(self, task_id, poll_interval=None, timeout=None):
        """Poll ``task_id`` until its status is terminal and return that task.

        Reads the task immediately, then re-reads it every ``poll_interval``
        seconds, and stops issuing requests as soon as a terminal status is
        observed. Raises :class:`vcf_lcm.TaskFailedError` for a terminal
        status that is not a success, and :class:`vcf_lcm.TaskTimeoutError` if
        ``timeout`` elapses first.
        """
        raise NotImplementedError

    def apply_component_upgrade(
        self,
        component_id,
        target_version,
        depot_url,
        depot_certificates=None,
        perform_backup=None,
        correlation_id=None,
        poll_interval=None,
        timeout=None,
    ):
        """Apply an upgrade to a component and poll it to a terminal state.

        Starts the ``apply`` action, then polls the returned task. Returns the
        terminal ``Task``. A token that expires while the task is being polled
        must not cost any work already done: the upgrade is started exactly
        once, no matter how many times the token is refreshed.
        """
        raise NotImplementedError
