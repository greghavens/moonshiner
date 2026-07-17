"""shiftlog — the ops rotation's shift-handoff log, kept in a JSON file.

Each entry records which shift wrote it, a free-text message, and optional
tags. The handoff wrapper renders the log as a table for the wiki and the
terminal summary.
"""
import json
from pathlib import Path

import click

DEFAULT_COLUMNS = ["id", "shift", "message"]


def load_entries(path):
    p = Path(path)
    if not p.exists():
        return []
    return json.loads(p.read_text())


def save_entries(path, entries):
    Path(path).write_text(json.dumps(entries, indent=2) + "\n")


def render_table(entries, columns):
    header = " | ".join(columns)
    lines = [header]
    for entry in entries:
        cells = []
        for column in columns:
            value = entry.get(column, "")
            if isinstance(value, list):
                value = ",".join(value)
            cells.append(str(value))
        lines.append(" | ".join(cells))
    return "\n".join(lines)


@click.group()
@click.option("--data-file", default="shiftlog.json", show_default=True,
              help="Path of the JSON log file.")
@click.pass_context
def cli(ctx, data_file):
    """Shift-handoff log for the ops rotation."""
    ctx.ensure_object(dict)
    ctx.obj["data_file"] = data_file


@cli.command()
@click.argument("message")
@click.option("--shift", required=True, help="Shift label, e.g. mon-early.")
@click.option("--tag", "tags", multiple=True, help="Optional tags.")
@click.pass_context
def add(ctx, message, shift, tags):
    """Append an entry to the log."""
    path = ctx.obj["data_file"]
    entries = load_entries(path)
    next_id = max((entry["id"] for entry in entries), default=0) + 1
    entries.append({"id": next_id, "shift": shift, "message": message,
                    "tags": sorted(tags)})
    save_entries(path, entries)
    click.echo(f"added entry {next_id}")


@cli.command(name="list")
@click.option("--show-tags", is_flag=True, help="Include the tags column.")
@click.pass_context
def list_entries(ctx, show_tags):
    """Print the log as a table."""
    entries = load_entries(ctx.obj["data_file"])
    columns = DEFAULT_COLUMNS
    if show_tags:
        columns.append("tags")
    click.echo(render_table(entries, columns))


@cli.command()
@click.argument("entry_id", type=int)
@click.pass_context
def show(ctx, entry_id):
    """Print one entry in full."""
    entries = load_entries(ctx.obj["data_file"])
    for entry in entries:
        if entry["id"] == entry_id:
            click.echo(f"id: {entry['id']}")
            click.echo(f"shift: {entry['shift']}")
            click.echo(f"message: {entry['message']}")
            click.echo(f"tags: {','.join(entry['tags'])}")
            return
    click.echo(f"error: no entry with id {entry_id}", err=True)
    return 1


if __name__ == "__main__":
    cli()
