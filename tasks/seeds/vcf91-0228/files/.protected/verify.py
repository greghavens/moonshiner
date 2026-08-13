#!/usr/bin/env python3
"""Verify the SDDC LCM task-inventory collector.

Starts a loopback mock pinned to the solution's own ``docs/contract.json``, drives
the client against it, and asserts the derived contract, the recorded provenance
and the exact request wire shape. No VMware endpoint is contacted.

    python3 -B .protected/verify.py

This file is protected. Read it, run it, but do not modify it.
"""

import copy
import json
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))

import test_wire_contract as T  # noqa: E402
from lcm_mock_server import BEARER_TOKEN, ContractError, MockServer  # noqa: E402


def _load_json(path, checks, label):
    if not path.is_file():
        checks.check(False, "%s exists" % label, "missing %s" % path)
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        checks.check(False, "%s is valid JSON" % label, str(exc))
        return None


def main():
    checks = T.Checks()

    contract_doc = _load_json(ROOT / "docs" / "contract.json", checks, "docs/contract.json")
    sources_doc = _load_json(
        ROOT / "docs" / "official_sources.json", checks, "docs/official_sources.json"
    )

    if contract_doc is not None:
        T.check_contract(checks, contract_doc)
    if sources_doc is not None:
        T.check_sources(checks, sources_doc)

    if contract_doc is None:
        return report(checks)

    # --- import the solution ------------------------------------------------
    try:
        from vcf_sddc_lcm.client import SddcLcmClient
        from vcf_sddc_lcm.contract import Contract
    except Exception as exc:  # noqa: BLE001
        checks.check(False, "vcf_sddc_lcm imports", "%s: %s" % (type(exc).__name__, exc))
        traceback.print_exc()
        return report(checks)

    try:
        contract = Contract.load(ROOT / "docs" / "contract.json")
    except Exception as exc:  # noqa: BLE001
        checks.check(False, "Contract.load(docs/contract.json)", "%s: %s" % (type(exc).__name__, exc))
        traceback.print_exc()
        return report(checks)

    T.check_build_target(checks, contract)
    T.check_no_path_literals(checks)
    T.check_standard_library_only(checks)

    # --- drive the client against the pinned mock ---------------------------
    try:
        with MockServer() as mock:
            client = SddcLcmClient(mock.base_url, BEARER_TOKEN, contract)

            # Run 1: filtered, explicit includeSystemTasks=False, pageSize 2.
            mock.reset_log()
            report_doc = client.collect_tasks(
                filters={
                    "createdBy": "admin",
                    "startTimeGt": T.START_TIME_GT,
                    "includeSystemTasks": False,
                },
                page_size=2,
            )
            run1 = mock.log
            T.check_paging_run(checks, run1)

            # Run 2: includeSystemTasks left unset -- must not be sent at all.
            mock.reset_log()
            listed = client.list_tasks(filters={"createdBy": "admin"}, page_size=2)
            T.check_omitted_default_run(checks, mock.log)

            # Run 3: no filters, no page size.
            mock.reset_log()
            bare = client.list_tasks()
            T.check_bare_run(checks, mock.log)

            # Run 4: exercise every declared non-paging filter, including true
            # and values containing spaces and a literal plus sign.
            mock.reset_log()
            all_filters = client.list_tasks(filters=T.ALL_FILTER_VALUES, page_size=50)
            T.check_all_filters_run(checks, mock.log)

            T.check_results(checks, report_doc, listed, bare, all_filters)
            T.check_rejected_filters(checks, client)

    except ContractError as exc:
        checks.check(False, "the loopback mock could be pinned to docs/contract.json", str(exc))
    except Exception as exc:  # noqa: BLE001
        checks.check(False, "the client run completed", "%s: %s" % (type(exc).__name__, exc))
        traceback.print_exc()

    # A second mock uses changed paths in a temporary copy of the contract. A
    # client that reconstructs or hard-codes the published paths cannot pass.
    try:
        alternate_doc = copy.deepcopy(contract_doc)
        alternate_doc["operations"]["getTasks"]["path"] = T.ALTERNATE_LIST_PATH
        alternate_doc["operations"]["getTask"]["path"] = T.ALTERNATE_DETAIL_PATH
        with tempfile.TemporaryDirectory() as temp_dir:
            alternate_path = Path(temp_dir) / "contract.json"
            alternate_path.write_text(json.dumps(alternate_doc), encoding="utf-8")
            alternate_contract = Contract.load(alternate_path)
            with MockServer(contract_path=alternate_path) as mock:
                alternate_client = SddcLcmClient(mock.base_url, BEARER_TOKEN, alternate_contract)
                alternate_client.list_tasks(page_size=50)
                alternate_client.get_task(T.T2)
                T.check_contract_driven_run(checks, mock.log)
    except Exception as exc:  # noqa: BLE001
        checks.check(
            False,
            "the client follows contract-derived alternate paths",
            "%s: %s" % (type(exc).__name__, exc),
        )
        traceback.print_exc()

    return report(checks)


def report(checks):
    total = checks.passed + len(checks.failures)
    if checks.failures:
        print("FAILED %d/%d checks\n" % (len(checks.failures), total))
        for failure in checks.failures:
            print("  x " + failure)
        print("")
        return 1
    print("PASSED %d/%d checks" % (checks.passed, total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
