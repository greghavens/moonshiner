# Service recovery verification

The examples in this runbook are checked only against the bundled
`service-recovery` fixture. Verification never targets a live service.

```verify id=status safety=safe fixture=service-recovery expected=expected/status.out
bash bin/status.sh
```

The rendered plan is also read-only and suitable for routine verification.

```verify id=plan safety=safe fixture=service-recovery expected=expected/plan.out
bash bin/render-plan.sh
```

The following production mutation is documentation only. The verifier must
report it as skipped without interpreting its fixture fields or body.

```verify id=restart-production safety=mutation fixture=../../live expected=../../never
: "${MUTATION_CANARY:?mutation examples must not run}"
printf 'mutation example ran\n' > "$MUTATION_CANARY"
```

An ordinary shell fence is prose and is not a verification block.

```bash
printf 'this is documentation, not a verification command\n'
```
