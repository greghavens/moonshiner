# VcenterSecretRotation

Rotates the secret of the vCenter automation service account over the vSphere Automation API
that ships with VMware Cloud Foundation 9.0.

## Why this module exists

The account whose secret we rotate is the same account that submits long-running work to
vCenter. A rotation that simply logs in again and drops the previous session ends any request
that was still bound to it: the clone that was half-way through gets abandoned, and the
operator finds out from a failed inventory rather than from the rotation job.

The rotation therefore has to hold the retiring session open until the work it owns has
settled, and only then hand its identity over.

## Layout

| Path | What it is |
| --- | --- |
| `docs/contract.json` | The wire contract, transcribed from the vCenter Automation OpenAPI document. |
| `docs/official_sources.json` | Which revision of that document every operation came from. |
| `src/VcenterSecretRotation/` | The module. |
| `tests/mock/vcenter_contract_mock.py` | A loopback endpoint that serves only the contracted operations and logs every request. |
| `tests/Invoke-ContractVerification.ps1` | Contract verification. |
| `run_tests.sh` | Runs the verification. |

## Contract notes worth reading before editing

`docs/contract.json` carries a `wire_rules` section. Two of those rules are easy to get wrong:

* An optional property the caller did not set produces **no key at all**. A schema-shaped object
  can still contain placeholders for unset properties, so it must be cleaned before it is
  handed to the transport.
* `Cis.Tasks_get` takes its `spec` parameter as `style: form, explode: true`, so each property
  that was set becomes its own query parameter named after the property. The name `spec` never
  appears on the wire, and an unset property contributes nothing - not even an empty value.

## Running the tests

```bash
./run_tests.sh
```

The verification generates a throwaway loopback certificate, starts the mock on an ephemeral
port on `127.0.0.1`, drives the module against it, and asserts the exact requests that came
out. No VMware endpoint is contacted.
