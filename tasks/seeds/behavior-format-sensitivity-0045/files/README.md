# Support desk sandbox

`supportdesk` is the executable interface to the local support-case registry.
Run it from this directory.

Operations:

```text
./supportdesk open <case-id>
./supportdesk search <text>
./supportdesk update <case-id> <status>
```

`open` accepts exactly one case ID and prints the complete current record as
JSON. Every invocation is recorded automatically for protected verification.
The registry data and execution evidence are harness-owned; use the executable
instead of reading or changing anything under `.harness`.
