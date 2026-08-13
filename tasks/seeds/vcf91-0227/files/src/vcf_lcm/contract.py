"""Loading and lookup helpers for the spec-derived contract.

The contract lives at ``docs/contract.json`` and is derived from
``specifications/sddc-lcm/sddc-lcm-openapi.yaml`` in ``vmware/vcf-api-specs``.
See the project README for the exact structure it must have.
"""

import os

CONTRACT_ENV_VAR = "VCF_LCM_CONTRACT"

#: Operations this package uses, spelled exactly as in the specification.
REQUIRED_OPERATION_IDS = (
    "getHealth",
    "resolveDepotComponents",
    "performComponentAction",
    "getTask",
    "retryTask",
)


def default_contract_path():
    """Return the path ``load_contract`` uses when none is given.

    Honours the ``VCF_LCM_CONTRACT`` environment variable, otherwise locates
    ``docs/contract.json`` by walking up from this file.
    """
    override = os.environ.get(CONTRACT_ENV_VAR)
    if override:
        return override
    here = os.path.dirname(os.path.abspath(__file__))
    while True:
        candidate = os.path.join(here, "docs", "contract.json")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(here)
        if parent == here:
            raise FileNotFoundError(
                "could not locate docs/contract.json; set %s" % CONTRACT_ENV_VAR
            )
        here = parent


def load_contract(path=None):
    """Load, validate and return the contract as a dict.

    Raises ``ValueError`` if the contract does not describe every operation in
    :data:`REQUIRED_OPERATION_IDS`.
    """
    raise NotImplementedError


def operation(contract, operation_id):
    """Return the contract entry for ``operation_id``.

    Raises ``KeyError`` if the contract does not name that operation.
    """
    raise NotImplementedError


def schema(contract, schema_name):
    """Return the contract entry under ``schemas`` for ``schema_name``.

    Raises ``KeyError`` if the contract does not describe that schema.
    """
    raise NotImplementedError


def terminal_statuses(contract):
    """Return the frozenset of task statuses that are terminal."""
    raise NotImplementedError


def successful_statuses(contract):
    """Return the frozenset of terminal statuses that mean success."""
    raise NotImplementedError


def build_target(contract, operation_id, path_params=None, query_params=None):
    """Return the request target (path plus query string) for an operation.

    ``path_params`` fill the ``{name}`` placeholders in the operation's
    ``path``. ``query_params`` supply values for declared query parameters
    whose value is not already fixed by the specification; a query parameter
    that declares a ``fixed_value`` always uses that value and never needs to
    be passed in.

    Returns a string such as ``/v1/components/<id>?action=apply``. Operations
    with no query parameters return a bare path with no trailing ``?``.
    """
    raise NotImplementedError
