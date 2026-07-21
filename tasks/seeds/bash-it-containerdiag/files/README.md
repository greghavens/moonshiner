# Local container incident fixture

`bin/containerctl` is a deterministic, read-only stand-in for the small subset
of a container engine needed for this incident. It never contacts a daemon or
registry and does not persist runtime state.

Useful evidence commands:

```sh
bash bin/containerctl image inspect localhost/ledger-api:1.4
bash bin/containerctl inspect ledger-api
bash bin/containerctl logs ledger-api
bash bin/containerctl diagnose ledger-api
```

After correcting the deployment environment, the safe verification sequence is:

```sh
bash bin/containerctl recreate ledger-api
bash bin/containerctl health ledger-api
bash tests/test_containerdiag.sh
```

The simulated recreate is configuration-only. It always reuses the image ID
recorded in `fixtures/image-inspect.json`.
