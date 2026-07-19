"""Protected, offline surface of the framework contract used by this seed."""

from dataclasses import dataclass
from types import ModuleType


class RemovedSettingError(RuntimeError):
    pass


@dataclass(frozen=True)
class StorageAlias:
    backend: str
    options: dict[str, object]


@dataclass(frozen=True)
class RuntimeSettings:
    middleware: tuple[str, ...]
    storages: dict[str, StorageAlias]
    databases: dict[str, dict[str, object]]


def force_str(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    return str(value)


def load_settings(module: ModuleType) -> RuntimeSettings:
    for removed in ("DEFAULT_FILE_STORAGE", "STATICFILES_STORAGE"):
        if hasattr(module, removed):
            raise RemovedSettingError(f"removed setting {removed} is defined")

    raw_storages = getattr(module, "STORAGES", None)
    if not isinstance(raw_storages, dict):
        raise TypeError("STORAGES must be a mapping")
    if set(raw_storages) != {"default", "staticfiles"}:
        raise ValueError("STORAGES must define default and staticfiles aliases")

    storages: dict[str, StorageAlias] = {}
    for alias, raw in raw_storages.items():
        if not isinstance(raw, dict) or not isinstance(raw.get("BACKEND"), str):
            raise TypeError(f"storage {alias} must define BACKEND")
        options = raw.get("OPTIONS", {})
        if not isinstance(options, dict):
            raise TypeError(f"storage {alias} OPTIONS must be a mapping")
        unexpected = set(raw) - {"BACKEND", "OPTIONS"}
        if unexpected:
            raise ValueError(f"storage {alias} has misplaced options: {sorted(unexpected)}")
        storages[alias] = StorageAlias(raw["BACKEND"], dict(options))

    middleware = getattr(module, "MIDDLEWARE", None)
    if not isinstance(middleware, (list, tuple)):
        raise TypeError("MIDDLEWARE must be ordered")
    databases = getattr(module, "DATABASES", None)
    if not isinstance(databases, dict):
        raise TypeError("DATABASES must be a mapping")
    return RuntimeSettings(tuple(middleware), storages, databases)

