# DNS cache incident triage

`dnscache.sh` turns one captured DNS incident snapshot into a deterministic
evidence report. It does not query DNS or execute a flush. This keeps incident
review reproducible and lets an operator review the proposed scope first.

Run it with a TSV containing this exact header and one data row:

```
record	ttl	changed_at	observed_at	previous	authoritative	resolver	client
```

The old cached answer is allowed through the instant before
`changed_at + ttl`; at that boundary its TTL is exhausted. A resolver mismatch
is remediated with BIND's per-name `rndc flushname` operation. If the resolver
is current and only the client is stale, the report keeps remediation at the
client/name. A global resolver flush is intentionally excluded: a snapshot
about one record provides no evidence that unrelated cache entries are bad.

The script only prints the proposed action. It never changes a live cache.
