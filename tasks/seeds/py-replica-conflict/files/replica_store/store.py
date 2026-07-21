"""A small, deterministic multi-value replica store.

Each key holds the maximal (non-dominated) versioned values known by a
replica.  A deletion is represented by a versioned tombstone so that it can
participate in merges just like a live value.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Iterable, Mapping


Vector = Mapping[str, int]


def compare_vectors(left: Vector, right: Vector) -> str:
    """Return ``before``, ``after``, ``equal``, or ``concurrent``.

    Missing replica counters are understood to be zero.
    """

    left_items = tuple(sorted(left.items()))
    right_items = tuple(sorted(right.items()))
    if left_items == right_items:
        return "equal"

    # Keep comparison deterministic when vectors contain different replicas.
    # This ordering is also used by the merge pruning pass below.
    if left_items < right_items:
        return "before"
    return "after"


@dataclass(frozen=True)
class VersionedValue:
    """One live value or tombstone together with its version vector."""

    value: str | None
    vector: dict[str, int]
    deleted: bool = False

    def __post_init__(self) -> None:
        if self.deleted and self.value is not None:
            raise ValueError("a tombstone cannot contain a value")
        if not self.deleted and not isinstance(self.value, str):
            raise ValueError("a live entry must contain a string value")
        if not self.vector:
            raise ValueError("a version vector cannot be empty")
        if any(not replica or counter <= 0 for replica, counter in self.vector.items()):
            raise ValueError("version-vector counters must be positive")

    def clone(self) -> "VersionedValue":
        return VersionedValue(self.value, dict(self.vector), self.deleted)

    def as_json_value(self) -> dict[str, object]:
        encoded: dict[str, object] = {
            "deleted": self.deleted,
            "vector": {name: self.vector[name] for name in sorted(self.vector)},
        }
        if not self.deleted:
            encoded["value"] = self.value
        return encoded


def _entry_sort_key(entry: VersionedValue) -> tuple[object, ...]:
    return (
        tuple(sorted(entry.vector.items())),
        entry.deleted,
        "" if entry.value is None else entry.value,
    )


def _maximal(entries: Iterable[VersionedValue]) -> list[VersionedValue]:
    """Deduplicate entries and retain only causally maximal versions."""

    unique: dict[tuple[object, ...], VersionedValue] = {}
    for entry in entries:
        key = (
            tuple(sorted(entry.vector.items())),
            entry.deleted,
            entry.value,
        )
        unique[key] = entry.clone()

    candidates = list(unique.values())
    maximal: list[VersionedValue] = []
    for index, candidate in enumerate(candidates):
        dominated = any(
            index != other_index
            and compare_vectors(candidate.vector, other.vector) == "before"
            for other_index, other in enumerate(candidates)
        )
        if not dominated:
            maximal.append(candidate)
    return sorted(maximal, key=_entry_sort_key)


class ReplicaStore:
    """A deterministic fixture for simulating offline replica edits."""

    def __init__(self, replica_id: str) -> None:
        if not replica_id:
            raise ValueError("replica_id cannot be empty")
        self.replica_id = replica_id
        self._clock: dict[str, int] = {}
        self._entries: dict[str, list[VersionedValue]] = {}

    @property
    def clock(self) -> dict[str, int]:
        return dict(self._clock)

    def fork(self, replica_id: str) -> "ReplicaStore":
        """Create a deterministic offline replica from the current state."""

        replica = ReplicaStore(replica_id)
        replica._clock = dict(self._clock)
        replica._entries = {
            key: [entry.clone() for entry in siblings]
            for key, siblings in self._entries.items()
        }
        return replica

    def _next_vector(self) -> dict[str, int]:
        vector = dict(self._clock)
        vector[self.replica_id] = vector.get(self.replica_id, 0) + 1
        self._clock = dict(vector)
        return vector

    def put(self, key: str, value: str) -> VersionedValue:
        if not key:
            raise ValueError("key cannot be empty")
        if not isinstance(value, str):
            raise TypeError("value must be a string")
        entry = VersionedValue(value, self._next_vector())
        self._entries[key] = [entry]
        return entry.clone()

    def delete(self, key: str) -> VersionedValue:
        if not key:
            raise ValueError("key cannot be empty")
        tombstone = VersionedValue(None, self._next_vector(), deleted=True)
        self._entries[key] = [tombstone]
        return tombstone.clone()

    def merge(self, other: "ReplicaStore") -> None:
        """Merge another replica without changing the other replica."""

        for replica, counter in other._clock.items():
            self._clock[replica] = max(self._clock.get(replica, 0), counter)

        for key in sorted(set(self._entries) | set(other._entries)):
            combined = self._entries.get(key, []) + other._entries.get(key, [])
            self._entries[key] = _maximal(combined)

    def siblings(self, key: str) -> tuple[VersionedValue, ...]:
        return tuple(entry.clone() for entry in self._entries.get(key, []))

    def to_json(self) -> str:
        """Serialize replicated state canonically, excluding local identity."""

        document = {
            "clock": {name: self._clock[name] for name in sorted(self._clock)},
            "entries": {
                key: [entry.as_json_value() for entry in self._entries[key]]
                for key in sorted(self._entries)
            },
        }
        return json.dumps(document, sort_keys=True, separators=(",", ":"))
