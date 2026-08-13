# SDDC Manager credential rotation client

A single-file, JDK-only client for one VMware Cloud Foundation 9.0 SDDC Manager workflow: rotate the
SSH password of the `root` account on a set of ESXi hosts, without stranding any request that is
already in flight when the client's own access token expires.

## Layout

| Path | What it is |
| --- | --- |
| `SddcCredentialClient.java` | the client — the only file to change |
| `RotationResult.java` | the record `rotateSshPasswords` returns |
| `Json.java` | a small JSON reader/writer, on the source path for you to use |
| `docs/contract.json` | the five operations, transcribed from the pinned OpenAPI document |
| `docs/official_sources.json` | where that transcription came from |
| `MockSddcManager.java` | a loopback stand-in for the appliance, routed from `docs/contract.json` |
| `WireVerifier.java` | asserts the request log the stand-in wrote |
| `TestMain.java` | the harness |

## Running

```
java TestMain.java
```

from the repository root. The stand-in binds `127.0.0.1` on an ephemeral port, so nothing leaves the
machine and no VMware endpoint is contacted.

The stand-in validates what you send against `docs/contract.json` and answers 400 with a message
naming the problem, so a mis-shaped request tells you what is wrong before the wire checks run.
