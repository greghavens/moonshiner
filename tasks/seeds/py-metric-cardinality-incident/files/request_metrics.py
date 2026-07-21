"""A deterministic in-memory request-duration metric collector."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


_KNOWN_METHODS = frozenset(
    {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"}
)


@dataclass(frozen=True)
class RequestObservation:
    method: str
    target: str
    route_template: str | None
    status_code: int
    duration_seconds: float
    trace_id: str | None = None


@dataclass(frozen=True)
class Exemplar:
    trace_id: str
    value: float


@dataclass(frozen=True)
class MetricSample:
    labels: tuple[tuple[str, str], ...]
    count: int
    total: float
    exemplar: Exemplar | None


@dataclass
class _Series:
    count: int = 0
    total: float = 0.0
    exemplar: Exemplar | None = None


class RequestMetrics:
    """Aggregate request durations using a configured route inventory."""

    def __init__(self, route_templates: Iterable[str]) -> None:
        self._route_templates = frozenset(route_templates)
        self._series: dict[tuple[tuple[str, str], ...], _Series] = {}

    def observe(self, observation: RequestObservation) -> None:
        if observation.duration_seconds < 0:
            raise ValueError("duration_seconds must not be negative")

        labels = self._labels(observation)
        series = self._series.setdefault(labels, _Series())
        series.count += 1
        series.total += observation.duration_seconds
        if observation.trace_id:
            series.exemplar = Exemplar(
                trace_id=observation.trace_id,
                value=observation.duration_seconds,
            )

    def collect(self) -> tuple[MetricSample, ...]:
        return tuple(
            MetricSample(
                labels=labels,
                count=series.count,
                total=series.total,
                exemplar=series.exemplar,
            )
            for labels, series in sorted(self._series.items())
        )

    def _labels(
        self, observation: RequestObservation
    ) -> tuple[tuple[str, str], ...]:
        return (
            ("method", self._method_label(observation.method)),
            ("request", observation.target),
            ("status_class", self._status_class_label(observation.status_code)),
        )

    def _route_label(self, route_template: str | None) -> str:
        if route_template in self._route_templates:
            return route_template
        return "unmatched"

    @staticmethod
    def _method_label(method: str) -> str:
        normalized = method.upper()
        if normalized in _KNOWN_METHODS:
            return normalized
        return "OTHER"

    @staticmethod
    def _status_class_label(status_code: int) -> str:
        if 100 <= status_code <= 599:
            return f"{status_code // 100}xx"
        return "unknown"
