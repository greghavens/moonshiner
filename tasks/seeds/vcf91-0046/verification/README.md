# VCF 9.1 SDDC Manager client exercise

Complete `src/VcfSddcClient.java` using only the Java 17 standard library.

The official, spec-derived subset used by this exercise is pinned in
`docs/contract.json`; its repository provenance is in
`docs/official_sources.json`. The acceptance harness starts its own loopback
server and never contacts an SDDC Manager appliance or any public endpoint.

Run:

```sh
python3 verifier.py
```

Only `src/VcfSddcClient.java` is an implementation file. The contract, mock,
tests, and verifier are protected acceptance assets.
