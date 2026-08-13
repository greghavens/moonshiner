# vcf-rightsizer

A small operator tool for VMware Cloud Foundation 9.0 labs. It applies a
right-sizing change to one virtual machine through the vSphere Automation API
of vCenter Server, and reports what it managed to apply.

Right-sizing is a *sequence* of API calls, not one call. Some of them mutate
the VM. When a later call is rejected the earlier mutations have already
happened and the VM is left part-way through the change, so the report the tool
produces is the only record an operator has of where things stopped.

## Layout

| Path | What it is |
| --- | --- |
| `docs/CONTRACT_FORMAT.md` | Shape of the two files under `docs/`. |
| `docs/contract.json` | The slice of the API this tool uses, derived from the OpenAPI document. |
| `docs/official_sources.json` | Where that contract was copied from, pinned to a commit. |
| `src/VcenterRightSizer.java` | The client. One file, no dependencies beyond the JDK and `lib/MiniJson.java`. |
| `lib/MiniJson.java` | Dependency-free JSON reader/writer, on the classpath for every component. |
| `harness/TestMain.java` | Test entry point. Fixes the client's public shape and captures its report. |
| `mock/VcenterMock.java` | Loopback stand-in for a vCenter endpoint, pinned to `docs/contract.json`. |
| `mock/fixtures/inventory.json` | The inventory the mock serves. |
| `config/lab-vcenter.json` | Credentials, target VM and the right-sizing plan. |
| `verify/verify.py` | Builds everything, runs it against the mock and checks the result. |
| `run.sh` | The same build-and-run loop without the checks, for iterating. |

`build/` is scratch: compiled classes, the mock's request log, and the report
the client returned.

## The client

`harness/TestMain.java` is the only caller, and it fixes the public shape:

```java
public VcenterRightSizer(String baseUrl, String username, String password)
public String rightSize(String vmId, long cpuCount, long memorySizeMib,
                        long diskCapacityBytes, long diskScsiBus)
```

`baseUrl` is an origin such as `http://127.0.0.1:41337` with no path — the
client appends the contract's base path and the operation path to it.

`rightSize` returns the run report as a JSON document. It returns normally when
vCenter rejects a step: a rejected step is a result to report, not an exception
to propagate.

## The run report

```jsonc
{
  "vm": "<vm identifier that was operated on>",

  // "SUCCEEDED"       every step succeeded
  // "PARTIAL_FAILURE" at least one step succeeded and at least one failed
  // "FAILED"          no step succeeded
  "outcome": "PARTIAL_FAILURE",

  // One entry per request actually issued, in the order issued.
  "steps": [
    {
      // operation_id of the contract operation this request invoked.
      "operation_id": "<string>",

      // "SUCCEEDED" when the response status matched the contract's
      // success_status for that operation, "FAILED" otherwise.
      "status": "SUCCEEDED",

      // The status code that actually came back.
      "http_status": 204
    },
    {
      "operation_id": "<string>",
      "status": "FAILED",
      "http_status": 400,

      // Only on failed steps, read off the error response body: the body's
      // error_type discriminator, and the default_message of the first entry
      // of its messages array, copied verbatim. Successful steps carry
      // neither key.
      "error_type": "<string>",
      "error_message": "<string>"
    }
  ],

  // Whether the VM is powered off now, as an operator would need to know
  // before walking away. Derive it from the steps already taken -- do not
  // issue an extra request to look it up.
  "vm_left_powered_off": true
}
```

Rules the report has to hold to:

- Stop at the first failed step. Do not retry it, do not carry on to later
  steps, and do not try to undo the steps that already succeeded.
- Report only what happened. A step that was never attempted has no entry; a
  step that succeeded is not downgraded because a later one failed.

## Building and running

```
./run.sh          # compile, start the mock, run TestMain, print the report
python3 verify/verify.py
```

Both need a JDK on `PATH`. Nothing else is fetched at build time and nothing
outside this directory is written to.
