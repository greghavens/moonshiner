# Request metric cardinality incident

The gateway's request-duration metric created far more active series than the
route inventory could explain. A small production snapshot is preserved in
`incident/metric_samples.jsonl`. Each row is one exported series and includes
the most recent exemplar separately from the metric labels.

`RequestMetrics` receives both the raw request target and the router template
selected by the gateway. Its configured route inventory is the cardinality
boundary: selected templates outside that inventory are treated the same as a
missing match. Method and status class also have finite fallback buckets.

Repair the collector so requests are aggregated by the bounded dimensions
described in the task while trace IDs remain useful exemplar metadata.

Run the offline regression suite with:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 python3 -m unittest discover -s tests -p 'test_*.py' -v
```
