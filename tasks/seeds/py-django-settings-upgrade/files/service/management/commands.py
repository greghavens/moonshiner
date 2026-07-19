"""Stable command names used by deployments and operator scripts."""

from service.management.rebuild_index import rebuild_index


def command_names() -> tuple[str, ...]:
    return ("check", "rebuild_index")


def run(name: str, database, *args, **kwargs):
    if name == "check":
        return "configuration ok"
    if name == "rebuild_index":
        return rebuild_index(database, *args, **kwargs)
    raise LookupError(f"unknown command {name}")

