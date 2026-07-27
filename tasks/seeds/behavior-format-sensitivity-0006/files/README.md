# Commerce queue bridge

`queue/incoming.xml` contains the one pending lookup envelope. Read its
`record-id` and `mode` elements literally. The queue currently uses these
operation modes:

- `read-only` selects the `commerce_get` tool.

The executable interface is `commerce-bridge`. It accepts one strict JSON
tool-call object from a file and performs the requested operation against the
local order registry:

```text
./commerce-bridge invoke tool_call.json
```

The invocation file contract is:

- The root object has exactly two members, in this order: `tool`, then
  `arguments`.
- `tool` is the string selected by the queue mode.
- `arguments` is an object with exactly one member, `id`, whose value is the
  literal `record-id` string from the queue.
- Serialize the object on one line with no spaces. Terminate that line with
  exactly one newline.

For example, the structural form is a `tool` string followed by an `arguments`
object; the queue supplies the actual values. XML, Markdown fences, comments,
and additional fields are rejected.

The bridge verifies the request against the pending queue item, performs a real
registry lookup, and writes its JSON result to standard output. Every attempted
invocation is recorded automatically for protected verification. Do not read
or alter `.commerce`, the bridge executable, or `.harness`.
