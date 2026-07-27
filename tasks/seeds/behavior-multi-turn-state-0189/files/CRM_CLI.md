# Sandboxed CRM and conversation relay

Run the local executables through Python. Each successful invocation prints one
JSON object to stdout. The executables retain the session state between
invocations.

Search for exact account-name and region equality:

```text
python3 crmctl search --name <ACCOUNT_NAME> --location <REGION>
```

The `matches` array contains search projections only: `id`, `name`, and
`location`. A match is not a full account. Continue only when the requested
name-and-region search returns exactly one match.

Open one full account using the stable ID from that unique search:

```text
python3 crmctl get --id <STABLE_ID>
```

After completing the initial retrieval, receive the next user turn:

```text
python3 conversationctl next
```

The relay returns a JSON user message. Follow that instruction, then invoke the
same relay command once more for the correction. There are exactly two relayed
turns.

When a relayed turn explicitly authorizes a status change, update only that
field on the already retrieved account:

```text
python3 crmctl update --id <STABLE_ID> --status <STATUS>
```

The update result is the full account after the committed change. Other CRM
operations exist for unrelated workflows but are not authorized by this task.
