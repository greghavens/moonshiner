"""Behavior checks for the plugin system. Run: python3 test_plugins.py"""
from plugins import PluginError, load_plugin, register, run_hooks


@register("title-case")
class TitleCase:
    def configure(self, options):
        pass

    def on_render(self, page):
        return dict(page, title=page["title"].title())


@register("minify")
class Minify:
    def configure(self, options):
        level = options.get("level", 1)
        if not 0 <= level <= 2:
            raise ValueError(f"minify: level must be 0-2, got {level}")
        self.level = level

    def on_render(self, page):
        return dict(page, body=" ".join(page["body"].split()))


@register("broken-lint")
class BrokenLint:
    def on_render(self, page):
        raise RuntimeError("lint pass hit an unclosed template block")


def main():
    # Plain loading and hook application work.
    tc = load_plugin("title-case")
    mini = load_plugin("minify", {"level": 2})
    page = {"title": "hello world", "body": "a    b\n\nc"}
    out = run_hooks([tc, mini], "on_render", page)
    assert out["title"] == "Hello World", f"got {out['title']!r}"
    assert out["body"] == "a b c", f"got {out['body']!r}"
    assert page["title"] == "hello world", "input page must not be mutated"

    # A genuinely unknown plugin is a PluginError naming it.
    try:
        load_plugin("does-not-exist")
        raise AssertionError("loading an unregistered plugin must fail")
    except PluginError as e:
        assert "does-not-exist" in str(e), f"got {e!r}"

    # Bad options surface the plugin's own error, not a misleading one.
    try:
        load_plugin("minify", {"level": 9})
        raise AssertionError("configure() rejecting options must fail the load")
    except ValueError as e:
        assert "level" in str(e), f"expected the minify option error, got {e!r}"

    # A hook blowing up mid-build aborts the build with that error.
    lint = load_plugin("broken-lint")
    try:
        run_hooks([tc, lint], "on_render", {"title": "x", "body": "y"})
        raise AssertionError("a raising hook must abort run_hooks")
    except RuntimeError as e:
        assert "unclosed template block" in str(e), f"got {e!r}"

    # Plugins without the hook are skipped, order of the rest is preserved.
    out = run_hooks([object(), tc], "on_render", {"title": "abc def", "body": ""})
    assert out["title"] == "Abc Def", f"got {out['title']!r}"

    print("all checks passed")


if __name__ == "__main__":
    main()
