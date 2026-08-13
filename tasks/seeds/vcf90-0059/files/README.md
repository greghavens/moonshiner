# vcsa-update-gate

A small integration component for VMware Cloud Foundation 9.0 fleet tooling.

It drives the vCenter Server Appliance update workflow through the vSphere Automation
API: list what is pending, run the update precheck, and install only when the precheck
comes back clean. A failed precheck must leave the appliance untouched.

## Layout

| Path | What it is |
| --- | --- |
| `src/VcenterUpdateClient.java` | the client — a single file, JDK only, no dependencies |
| `docs/contract.json` | the REST contract this client is written against |
| `docs/official_sources.json` | provenance for `docs/contract.json` |
| `harness/MockVcenter.java` | loopback mock of the endpoints named by the contract |
| `harness/TestMain.java` | drives the client against the mock, writes a result file |
| `fixtures/<scenario>/` | canned API response bodies per scenario |
| `verify/verify.py` | the acceptance check |

`harness/`, `fixtures/` and `verify/` are fixed test scaffolding. The verifier hashes
them and fails if they change, so put your work in `src/` and `docs/`.

## Scenarios

| Scenario | Pending updates | API outcome |
| --- | --- | --- |
| `clean` | two, newest first | no errors (questions only) |
| `advisory` | two, newest first | info and warnings, but an empty errors array |
| `blocked` | two, newest first | one info, one warning, two errors |
| `none` | none | n/a |
| `status_error` | two, newest first | list responds with a non-contract success status |

`TestMain` runs a scenario in one of two modes. `minimal` leaves every optional input
unset; `full` supplies all of them.

## Running

```sh
./run_tests.sh                 # compile, run all scenarios, verify
python3 verify/verify.py       # verify only
```

The mock binds `127.0.0.1` on an ephemeral port. Nothing here talks to a real vCenter.
