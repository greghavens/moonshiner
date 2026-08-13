"""The syslog forwarding plan that operators hand to this tool.

A plan is a set of credentials plus the syslog targets that should exist on the
appliance, in the order they must be applied. Optional fields default to None,
which means "the operator did not set this" -- not "set this to an empty value".

Standard library only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class Domain:
    domain_type: str
    value: Optional[str] = None


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str
    domain: Optional[Domain] = None


@dataclass(frozen=True)
class SyslogTargetSpec:
    ip_or_fqdn: str
    port: int
    protocol: str
    nick_name: Optional[str] = None
    collector_id: Optional[str] = None


@dataclass(frozen=True)
class SyslogPlan:
    credentials: Credentials
    targets: List[SyslogTargetSpec] = field(default_factory=list)


def plan_from_dict(raw):
    """Build a SyslogPlan from a decoded JSON document."""
    raw_credentials = raw["credentials"]
    raw_domain = raw_credentials.get("domain")
    domain = None
    if raw_domain is not None:
        domain = Domain(
            domain_type=raw_domain["domain_type"],
            value=raw_domain.get("value"),
        )
    credentials = Credentials(
        username=raw_credentials["username"],
        password=raw_credentials["password"],
        domain=domain,
    )
    targets = [
        SyslogTargetSpec(
            ip_or_fqdn=entry["ip_or_fqdn"],
            port=entry["port"],
            protocol=entry["protocol"],
            nick_name=entry.get("nick_name"),
            collector_id=entry.get("collector_id"),
        )
        for entry in raw.get("targets", [])
    ]
    return SyslogPlan(credentials=credentials, targets=targets)


def load_plan(path):
    """Read a plan from a JSON file."""
    with open(path, "r", encoding="utf-8") as handle:
        return plan_from_dict(json.load(handle))
