# Fleet handoff sandbox

`./fleetctl` is the sole supported interface to the sandboxed fleet register.
Run `./fleetctl --help` to learn its genuine command-line interface before
performing fleet operations. Invoke one operation directly per Bash tool call.

After producing the requested handoff artifact, run the protected verifier:

```text
python3 -B .protected/verify.py
```

The fleet register, runtime journal, and protected files are managed inputs;
do not inspect or modify them directly.
