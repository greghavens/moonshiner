"""Plugin system for the static-site builder.

Plugins register a factory under a name; the build instantiates them and
feeds every page dict through their hooks. A hook that raises must abort
the build with the plugin's own error — quietly publishing half-processed
pages is far worse than a red build.
"""


class PluginError(Exception):
    """Raised for plugin wiring problems (e.g. an unknown plugin name)."""


_FACTORIES = {}


def register(name):
    """Class decorator: make a plugin available under *name*."""
    def decorator(factory):
        _FACTORIES[name] = factory
        return factory
    return decorator


def registered_names():
    return sorted(_FACTORIES)


def load_plugin(name, options=None):
    """Instantiate the plugin registered under *name*.

    Unknown names raise PluginError. If the plugin rejects its options
    (configure() raising), that original error propagates unchanged so the
    site author sees what is actually wrong.
    """
    try:
        factory = _FACTORIES[name]
        plugin = factory()
        if options:
            plugin.configure(options)
        return plugin
    except Exception:
        raise PluginError(f"no plugin registered under {name!r}")


def run_hooks(plugins, event, page):
    """Feed *page* through every plugin's *event* hook, in order.

    A hook may return a new page dict (which replaces the page) or None
    (page passes through). Hook failures propagate to the caller.
    """
    for plugin in plugins:
        hook = getattr(plugin, event, None)
        if hook is None:
            continue
        try:
            result = hook(page)
        except Exception:
            continue
        if result is not None:
            page = result
    return page
