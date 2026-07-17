def merge_configs(base, override, merged={}):
    """Deep-merge override into base and return a new config dict.

    Neither input is modified; nested dicts are merged key by key.
    """
    merged.update(base)
    for key, value in override.items():
        if isinstance(value, dict) and key in merged and isinstance(merged[key], dict):
            node = merged[key]
            node.update(value)
            merged[key] = node
        else:
            merged[key] = value
    return merged
