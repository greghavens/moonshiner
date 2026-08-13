"""The operator-authored onboarding plan and its JSON loader.

A plan is a batch of vCenter data sources to register with one VCF Operations
for Networks appliance, plus the appliance credentials used to obtain a token.

Every optional field is ``None`` when the operator did not set it. ``None`` here
means "unset", which is why a caller must never copy it onto the wire: the
contract requires unset optional fields to be absent from the JSON object.

Note that ``enabled`` is a tri-state: ``None`` (unset), ``True`` or ``False``.
``False`` is a value the operator chose, not an absence.

Standard library only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional


class PlanError(ValueError):
    """The plan document is not usable."""


@dataclass(frozen=True)
class Credentials:
    """PasswordCredentials, or the user half of a UserCredential."""

    username: str
    password: str


@dataclass(frozen=True)
class Domain:
    """A Domain for the auth token request.

    ``value`` is not required for a LOCAL domain and is ``None`` when unset.
    """

    domain_type: str
    value: Optional[str] = None


@dataclass(frozen=True)
class IpfixIntent:
    """The operator's IPFIX intent for one vCenter.

    The presence of this object means "configure IPFIX". Its fields select which
    distributed switches are affected and are ``None`` when unset.
    """

    enable_all: Optional[bool] = None
    enable_for_dvs: Optional[str] = None
    disable_for_dvs: Optional[str] = None


@dataclass(frozen=True)
class VcenterSpec:
    """One vCenter to onboard.

    Exactly one of ``ip`` and ``fqdn`` is set; the other is ``None``.
    """

    nickname: str
    proxy_id: str
    credentials: Credentials
    ip: Optional[str] = None
    fqdn: Optional[str] = None
    notes: Optional[str] = None
    enabled: Optional[bool] = None
    is_vmc: Optional[bool] = None
    ipfix: Optional[IpfixIntent] = None

    @property
    def host(self):
        """The address the appliance will reach this vCenter on."""
        return self.ip if self.ip is not None else self.fqdn


@dataclass(frozen=True)
class OnboardingPlan:
    """A whole batch."""

    credentials: Credentials
    datasources: List[VcenterSpec] = field(default_factory=list)
    domain: Optional[Domain] = None


def _require(mapping, key, where):
    if key not in mapping or mapping[key] is None:
        raise PlanError("%s is missing required field %r" % (where, key))
    return mapping[key]


def _optional_bool(mapping, key, where):
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise PlanError("%s field %r must be a boolean" % (where, key))
    return value


def _credentials_from_dict(mapping, where):
    if not isinstance(mapping, dict):
        raise PlanError("%s credentials must be an object" % where)
    return Credentials(
        username=_require(mapping, "username", where),
        password=_require(mapping, "password", where),
    )


def _ipfix_from_dict(mapping, where):
    if mapping is None:
        return None
    if not isinstance(mapping, dict):
        raise PlanError("%s ipfix must be an object" % where)
    return IpfixIntent(
        enable_all=_optional_bool(mapping, "enable_all", where),
        enable_for_dvs=mapping.get("enable_for_dvs"),
        disable_for_dvs=mapping.get("disable_for_dvs"),
    )


def _vcenter_from_dict(mapping, index):
    where = "datasources[%d]" % index
    if not isinstance(mapping, dict):
        raise PlanError("%s must be an object" % where)
    ip = mapping.get("ip")
    fqdn = mapping.get("fqdn")
    if (ip is None) == (fqdn is None):
        raise PlanError("%s must set exactly one of 'ip' and 'fqdn'" % where)
    return VcenterSpec(
        nickname=_require(mapping, "nickname", where),
        proxy_id=_require(mapping, "proxy_id", where),
        credentials=_credentials_from_dict(_require(mapping, "credentials", where), where),
        ip=ip,
        fqdn=fqdn,
        notes=mapping.get("notes"),
        enabled=_optional_bool(mapping, "enabled", where),
        is_vmc=_optional_bool(mapping, "is_vmc", where),
        ipfix=_ipfix_from_dict(mapping.get("ipfix"), where),
    )


def plan_from_dict(document):
    """Build an :class:`OnboardingPlan` from a decoded plan document."""
    if not isinstance(document, dict):
        raise PlanError("a plan document must be an object")

    domain_document = document.get("domain")
    if domain_document is None:
        domain = None
    elif isinstance(domain_document, dict):
        domain = Domain(
            domain_type=_require(domain_document, "domain_type", "domain"),
            value=domain_document.get("value"),
        )
    else:
        raise PlanError("domain must be an object")

    datasources = document.get("datasources")
    if not isinstance(datasources, list) or not datasources:
        raise PlanError("a plan must carry a non-empty 'datasources' array")

    return OnboardingPlan(
        credentials=_credentials_from_dict(_require(document, "credentials", "plan"), "plan"),
        datasources=[_vcenter_from_dict(entry, index) for index, entry in enumerate(datasources)],
        domain=domain,
    )


def load_plan(path):
    """Read a plan document from ``path``."""
    with open(path, "r", encoding="utf-8") as handle:
        return plan_from_dict(json.load(handle))
