"""Inputs and results for the VCF Operations identity-sync triage.

This module is protected: it fixes the public shape of the triage so the
acceptance suite can assert against it. All wire encoding lives in
``vcfops_triage.client``.
"""

from dataclasses import dataclass
from typing import Optional, Tuple


class OperationsError(Exception):
    """A VCF Operations request failed, or returned something unusable."""


@dataclass(frozen=True)
class Credentials:
    """Body of the ``acquireToken`` operation.

    ``auth_source`` is optional in the ``username-password`` schema. When it is
    ``None`` the caller did not set it, and the contract requires it to be
    absent from the request body rather than sent as an empty value.
    """

    username: str
    password: str
    auth_source: Optional[str] = None


@dataclass(frozen=True)
class AlertQuery:
    """Body of the ``queryAlert`` operation.

    Every field is optional in the ``alert-query`` schema. A field left at
    ``None`` was not set by the caller and must not appear on the wire.
    """

    active_only: Optional[bool] = None
    alert_criticality: Optional[Tuple[str, ...]] = None
    alert_name: Optional[str] = None
    alert_status: Optional[Tuple[str, ...]] = None


@dataclass(frozen=True)
class SymptomEvidence:
    """One triggered symptom that contributed to the incident alert."""

    symptom_id: str
    symptom_definition_id: str
    resource_id: str
    start_time_utc: int
    message: str
    alarm_info: str


@dataclass(frozen=True)
class SyncFailure:
    """The LDAP synchronization run that started the incident."""

    sync_log_id: str
    time_stamp: int
    sync_type: str
    sync_result: str
    sync_result_message_key: str
    groups_removed: int
    users_removed: int


@dataclass(frozen=True)
class Diagnosis:
    """Correlated result of the triage.

    ``contributing_symptom_ids`` and ``symptom_evidence`` are ordered exactly as
    the API returned the incident alert's contributing symptoms.
    """

    alert_id: str
    alert_definition_name: str
    alert_level: str
    alert_start_time_utc: int
    resource_id: str
    contributing_symptom_ids: Tuple[str, ...]
    symptom_evidence: Tuple[SymptomEvidence, ...]
    ldap_directory_id: str
    ldap_directory_name: str
    first_failure: SyncFailure
    root_cause_message_key: str
