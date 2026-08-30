# vcfa-policy-sync

A small, standard-library-only Python package that our platform CI runs to keep
VCF Automation deployment-lease policies in step with what is checked into git.
It targets **VCF Automation in VMware Cloud Foundation 9.1** — the successor to
vRealize Automation and Aria Automation.

## Why there is a hand-written contract

VCF Automation ships no machine-readable API specification. It is absent from
the `vmware/vcf-api-specs` repository and the Broadcom Developer Portal exposes
it only as human-readable xAPIs reference pages. So `docs/contract.json` is a
hand transcription of those pages, and `docs/official_sources.json` records
every page we read, which operation it documents, and when we fetched it.

`docs/contract.json` says so in its own `source_statement`. Treat it as a
reviewed transcription that can drift from the appliance, not as vendor truth.

## Layout

```
docs/contract.json          the wire contract (three operations)
docs/official_sources.json  provenance: page URL -> operation -> fetch date
src/vcfa_policy/            the client package
tests/vcfa_mock.py          loopback appliance stand-in, pinned to the contract
tests/test_contract.py      the contract verifier
run_verification.sh         bash run_verification.sh
```

## Testing

`tests/vcfa_mock.py` builds its routing table from `docs/contract.json`, serves
only the operations named there, and appends every request it receives to a
JSONL log the tests read back. It binds `127.0.0.1` on an ephemeral port; no
live VMware endpoint is contacted by the test suite.
