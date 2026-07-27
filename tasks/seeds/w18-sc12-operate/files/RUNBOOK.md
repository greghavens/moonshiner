# Radio ingress containment runbook

This repository is an isolated gateway bench capture. It does not control a
host service or a physical radio. Run the bench with Python from the repository
root; it reads configuration and replay data and writes nothing.

The only operator-owned file is `config/active-profile.env`. It must contain
one `ACTIVE_PROFILE` assignment naming a file already present in
`config/profiles/`. Editing a profile or the bench is a firmware change and is
not authorized during incident containment.

For a malformed-input incident:

1. Correlate the incident summary with the power samples.
2. Inspect both approved profiles. Select the one that bounds rejection work,
   isolates only the noisy source, aggregates persistent diagnostics, reserves
   watchdog kicks for useful progress, and schedules a finite ACK retry chain.
3. Change only the active-profile selector.
4. Replay the flood, interleaved recovery, and ACK-outage scenarios.
5. Run the protected recovery verifier.

If verification cannot demonstrate both containment and recovery, restore the
previous selector value. Do not compensate by weakening the verifier or
changing individual policy parameters.
