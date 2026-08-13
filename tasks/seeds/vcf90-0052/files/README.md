# gosc — guest OS customization roller for VCF 9.0 vCenter

`gosc` applies operator-authored *customization profiles* to virtual machines through
the vSphere Automation API on a VMware Cloud Foundation 9.0 vCenter.

A profile (`profiles/*.json`) is the shape our operators actually maintain: flat,
opinionated, and deliberately **not** the shape the API wants. The `guestcust`
package is the translation layer between the two.

## Why the precheck matters

Applying a customization to a VM that cannot accept one used to leave the fleet in a
half-configured state: the PUT would land, the guest would never pick it up, and the
next reconcile pass would see a pending spec it could not explain. VCF 9.0 added a
customization *check* operation, so we now gate every mutation behind it. If the
check says the VM is not customizable, nothing is sent.

## Layout

| Path | What it is |
| --- | --- |
| `profiles/` | Operator-authored customization profiles (fixtures). |
| `guestcust/` | The client package: profile → API request translation, precheck gate. |
| `docs/` | The pinned API contract and its provenance. |
| `internal/mockvc/` | Contract-pinned loopback vCenter used by tests. Records every request. |
| `verify/` | Acceptance checks. |

## Running the checks

```sh
bash verify/run.sh
```

## The loopback vCenter

`internal/mockvc` builds its routing table from `docs/contract.json` and refuses to
start if that contract does not describe the operations this project depends on. It
serves those operations and nothing else — anything else is a 404. Every request it
receives (including the rejected ones) is retained and readable via `Requests()`, so
tests can assert on the exact bytes that went over the wire:

```go
srv := mockvc.New(t, "../docs/contract.json")
srv.SetCheckInfo(200, mockvc.CheckInfo("SUPPORTED", nil, nil))
// ... drive the client at srv.URL() ...
for _, r := range srv.Requests() {
        t.Log(r.Method, r.Path, r.RawQuery, string(r.Body))
}
```

It never talks to a real vCenter, and neither does anything under `verify/`.
