"""One provenance lookup for every canonical dataset exporter."""


def value(record: dict, key: str, default=None):
    return (record.get("meta") or {}).get(key, record.get(key, default))
