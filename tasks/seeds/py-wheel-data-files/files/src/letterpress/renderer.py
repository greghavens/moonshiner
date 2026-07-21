"""Render messages from the package's bundled templates."""

from importlib import resources


def render_welcome(name: str) -> str:
    """Render the welcome message for *name*."""

    template = (
        resources.files("letterpress")
        .joinpath("templates", "welcome.txt")
        .read_text(encoding="utf-8")
    )
    return template.format(name=name)
