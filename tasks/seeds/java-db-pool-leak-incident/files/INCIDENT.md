# Funds-transfer pool incident

At 09:42 UTC, the transfer API stopped acquiring database connections even
though database latency and transaction duration were normal. The pool sample
was stable across three scrapes:

```text
pool=funds-db capacity=16 active=16 idle=0 waiting=43
checkouts.total=12844 checkins.total=12828 delta=16
commits.total=12691 rollbacks.total=137
```

The checkout/check-in delta equals every slot in the pool. Sampling request
traces showed that successful requests returned their connection:

```text
request=req-704 checkout=db-11 active=15 idle=1
request=req-704 tx=begin
request=req-704 statement=debit outcome=ok
request=req-704 statement=credit outcome=ok
request=req-704 tx=commit
request=req-704 checkin=db-11 active=14 idle=2
request=req-704 response=204
```

Failed credit writes rolled back, but their traces ended without the matching
check-in:

```text
request=req-711 checkout=db-12 active=15 idle=1
request=req-711 tx=begin
request=req-711 statement=debit outcome=ok
request=req-711 statement=credit outcome=error error=ledger-write-failed
request=req-711 tx=rollback
request=req-711 response=503 error=ledger-write-failed
```

The pool is deliberately bounded. Keep the original database operation failure
as the causal failure reported to the request. Rollback must precede check-in;
failures from rollback and check-in are cleanup evidence and must not replace
that original failure.
