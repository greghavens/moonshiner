# vcf-opsnet-bulk-client

A single-file Java client for **VMware Cloud Foundation Operations for Networks 9.1**
(the successor to vRealize Network Insight), covering the asynchronous bulk data
source operation.

## Layout

| Path | What it is |
| --- | --- |
| `docs/contract.json` | The pinned wire contract. Derived from the product OpenAPI specification. **The authority for this client.** |
| `docs/official_sources.json` | Provenance for the contract: repository, spec path, commit sha, and the operationIds and schemas it was read from. |
| `src/main/java/com/vmware/vcf/opsnet/VcfOpsNetworksClient.java` | The client. Single file, JDK only. |
| `src/test/java/com/vmware/vcf/opsnet/TestMain.java` | Fixed harness that drives the client through the scenario. |
| `tools/mock_vcfon.py` | Loopback mock of exactly the operations the contract names, with a JSON Lines request log. |
| `tools/verify.py` | Verifier. Compiles, runs the harness against the mock, asserts the request wire shape. |
| `run_tests.sh` | `python3 -B tools/verify.py` |

## Running

```sh
./run_tests.sh
```

Everything runs on `127.0.0.1`. No VMware endpoint is contacted.

## Developing against the mock by hand

```sh
python3 tools/mock_vcfon.py --port 8080 --log /tmp/vcfon-requests.jsonl
# then, in another shell:
javac -d build src/main/java/com/vmware/vcf/opsnet/VcfOpsNetworksClient.java \
                src/test/java/com/vmware/vcf/opsnet/TestMain.java
VCFON_BASE_URL=http://127.0.0.1:8080 java -cp build com.vmware.vcf.opsnet.TestMain
cat /tmp/vcfon-requests.jsonl | python3 -m json.tool --json-lines
```
