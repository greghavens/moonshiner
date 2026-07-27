# Subscription review sandbox

`bin/subscriptionctl` is the sandbox's executable subscription client. Run it
from the sandbox root. It queries the local subscription store and emits one
JSON document for every completed operation. The protected verifier checks its
execution journal, so reading backing files cannot replace executing the
client.

The read-only interface for this review is:

- `./bin/subscriptionctl search --name NAME --account ACCOUNT` searches both
  exact fields. It returns match summaries and stable IDs but omits full-record
  fields.
- `./bin/subscriptionctl get --id ID` retrieves one complete record by stable
  ID.

The two independent invocations in each phase must actually overlap. Each
invocation writes one JSON line to standard output; normal shell job control
and separate output files can keep concurrent results distinct. Retrieval is
rejected until two searches have completed, and each retrieved ID must have
been uniquely resolved by its corresponding search.

Administrative `update`, `cancel`, and `notify` operations also exist, but this
review is read-only and must not use them.
