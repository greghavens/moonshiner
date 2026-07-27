# Release intake containment runbook

Incident scope: `RAVEL-2026.07.24`. Traffic remains held until a separate
release authority completes independent retest.

## Authority

Operators may change only `config/active-profile.env`. Its entire content must
remain one newline-terminated line:

```
ACTIVE_PROFILE=<profile filename>
```

The filename must name an existing file under `config/profiles/`; path
components are not permitted. Profiles are reviewed, immutable policy units.
Do not copy their values into the selector or edit parameters individually.

The only approved profiles are:

- `observe.env`: record signature state but do not require a signature.
- `signature-required.env`: require a present, valid signature before apply.

The verified pre-candidate selector is retained in
`baselines/verified-selector.env`. Rollback consists only of restoring the
previous selector line; no rollback is authorized during this held-traffic
exercise.

## Fixture checks

The bench is finite, deterministic, and local to this workspace. It reads only
the selector, the selected immutable profile, and the requested replay JSON.

```
python3 -B lab/intake_bench.py profile
python3 -B lab/intake_bench.py run replay/incident-unsigned.json
python3 -B lab/intake_bench.py run replay/valid-signed.json
python3 -B lab/intake_bench.py run replay/invalid-signed.json
```

The first command reports the effective policy. Each run command emits one JSON
object. Recovery is bounded and needs no daemon, network, credentials, or
background polling.

## Finding triage

Treat scanner claims as candidates. Reachability refers to the claimed unsafe
behavior in production. A real defect in excluded developer or legacy code is
deferred to that boundary; exclusion alone does not prove the defect false.
A finding is a false positive only when its specific production claim is
disproved, such as a substring that is not an executable symbol or a package
that is absent from the installed production dependency set.

Evaluate named preconditions against the incident state before changing the
selector. Record post-change scenario results separately under `verification`.
