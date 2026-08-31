# vcfops-rotate

Internal tooling for our VMware Cloud Foundation 9.1 fleet.

This repository holds `vcfops_rotate`, the small Python client our collector hosts
use to talk to the **VCF Operations** API (the `/suite-api` service on the
VCF Operations appliance — *not* VCF Operations for Logs / log management, and not
VCF Operations for Networks).

## Why this exists

Our VCF Operations service account password is rotated on a schedule by the
secrets platform. The collector process is long-lived and multi-threaded: at any
moment it may have several API calls in flight. The previous shell-based rotation
script tore down the API session the instant the new password was minted, which
stranded whatever was in flight on the now-dead session token and produced a burst
of 401s in the collector log every rotation window.

The replacement has to hand over cleanly: new work moves to the new session token
immediately, in-flight work finishes on the token it started with, and the old
token is only released once nothing is using it.

## Layout

    src/vcfops_rotate/    the client package
    verification/         acceptance checks

## Running the checks

    python3 verification/verify.py

Run from the repository root. The checker adds `src/` to `sys.path` itself; there
is no build step and nothing to install.

## House rules

- Python 3.11+, standard library only. Collector hosts have no package index
  access, so `requests`/`httpx`/`pydantic`/`pytest` are not available.
