# Subscription account-review sandbox

`bin/subscriptionctl` is the sandbox's executable subscription client. Run it
from the sandbox root. It queries the local account-review service data and
returns one JSON document per completed operation. Its execution journal is
used by the protected verifier, so reading the backing files cannot replace
executing the client.

The read-only interface needed for this task is:

- `./bin/subscriptionctl search --name NAME --location LOCATION` searches on
  both exact fields. Search output contains match summaries and stable IDs, but
  it deliberately omits full-record fields.
- `./bin/subscriptionctl get --id ID` retrieves one full record by a stable ID.

The two independent calls in either phase must actually be in flight together.
The client coordinates each pair and rejects a retrieval phase started before
both search responses have completed. Each invocation writes exactly one JSON
line to standard output; use normal shell facilities if you want to keep
concurrent output separate.

Other administrative operations exist in the client but are outside this
read-only audit and are prohibited by the task.
