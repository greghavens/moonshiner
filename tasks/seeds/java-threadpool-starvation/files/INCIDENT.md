# Pricing batch incident

At 14:07 UTC, two pricing batches stopped making progress. CPU was idle and no
outbound price-client calls were observed. The executor sample remained stable
for several minutes:

```text
executor=pricing-request active=2 poolSize=2 maxPoolSize=2
queued=2 remainingCapacity=0 completed=1842 rejected=0
queue[0]=FutureTask -> PricingBatchService.priceOne price-client call
queue[1]=FutureTask -> PricingBatchService.priceOne price-client call
```

The matching thread dump was:

```text
"pricing-request-1" WAITING
  at java.util.concurrent.FutureTask.awaitDone(FutureTask.java:447)
  at java.util.concurrent.FutureTask.get(FutureTask.java:190)
  at incident.PricingBatchService.priceOne(PricingBatchService.java:76)

"pricing-request-2" WAITING
  at java.util.concurrent.FutureTask.awaitDone(FutureTask.java:447)
  at java.util.concurrent.FutureTask.get(FutureTask.java:190)
  at incident.PricingBatchService.priceOne(PricingBatchService.java:76)
```

The queue is intentionally bounded and uses caller-runs rejection to propagate
backpressure. Those properties must remain true after the repair. Do not mask
the incident with a larger or unbounded queue, polling, sleeps, or timeouts that
discard work. The service owns its execution resources and must close all of
them.
