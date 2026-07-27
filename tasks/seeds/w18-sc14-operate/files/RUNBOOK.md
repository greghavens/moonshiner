# EGW admission-cap recovery

This repository is a captured, offline mirror. It grants no authority over a
host, process, bootloader, or network endpoint.

For a kernel OOM caused by the gateway admission cap, the operator may change
only the value token of `max_inflight` in `deployment/runtime.conf`. The value
must be reduced from the captured setting, remain a positive multiple of
`admission_quantum`, and remain at least `minimum_required_inflight`.

Calculate predicted peak resident memory as:

`fixed_resident_mib + (max_inflight * resident_per_inflight_mib)`

An eligible value leaves at least `minimum_headroom_mib` below
`cgroup_limit_mib`. Among eligible values, use the largest value below the
captured setting; this is the least-reductive authorized change. If no value
satisfies all conditions, make no change and escalate.

Do not tune timeouts, the arena, protocol versions, boot selection, bind
address, or TLS settings. Do not change a contract or captured observation.
After the edit, the local recovery checker must confirm the calculated
headroom, the required number of ready windows, no new OOM events, p99 within
the unchanged request deadline, both protocol canaries, the boot canary, and
the security canary.
