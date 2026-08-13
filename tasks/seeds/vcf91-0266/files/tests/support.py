"""Shared harness: run the triage once against the contract mock."""

import tempfile
from collections import namedtuple
from pathlib import Path

from vcfops_triage import Credentials, OperationsClient, diagnose

from . import fixtures
from .mock_vcf_operations import contract_mock

USERNAME = "svc-triage"
PASSWORD = "dummy-not-a-real-secret"
EXPECTED_AUTHORIZATION = "vRealizeOpsToken " + fixtures.ISSUED_TOKEN

TriageRun = namedtuple("TriageRun", "diagnosis log token_released")

_CACHED = None


def run_triage(
    *,
    credentials=None,
    fail_operation=None,
    invalid_json_operation=None,
    invalid_shape_operation=None,
):
    """Drive :func:`diagnose` against a fresh mock and return the run."""

    if credentials is None:
        credentials = Credentials(username=USERNAME, password=PASSWORD)
    with tempfile.TemporaryDirectory() as workspace:
        log_path = Path(workspace) / "requests.jsonl"
        with contract_mock(
            log_path,
            fail_operation=fail_operation,
            invalid_json_operation=invalid_json_operation,
            invalid_shape_operation=invalid_shape_operation,
        ) as mock:
            error = None
            result = None
            try:
                result = diagnose(
                    OperationsClient(mock.base_url),
                    credentials=credentials,
                    idp_config_id=fixtures.IDP_CONFIG_ID,
                )
            except Exception as exc:  # surfaced to the caller with the log
                error = exc
            log = mock.read_log()
            released = mock.token_released
    if error is not None:
        raise TriageFailed(error, log, released)
    return TriageRun(result, log, released)


class TriageFailed(Exception):
    """The triage raised; carries the request log recorded up to that point."""

    def __init__(self, error, log, token_released):
        super().__init__(str(error))
        self.error = error
        self.log = log
        self.token_released = token_released


def nominal_run():
    """The nominal triage run, executed at most once per test session."""

    global _CACHED
    if _CACHED is None:
        _CACHED = run_triage()
    return _CACHED


def headers_of(entry):
    """Group an entry's recorded header pairs into ``{name: [values]}``."""

    grouped = {}
    for name, value in entry["header_pairs"]:
        grouped.setdefault(name, []).append(value)
    return grouped
