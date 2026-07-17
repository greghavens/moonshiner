"""Contract tests for the shiftlog CLI — protected file.

The suite drives the CLI in-process with click's CliRunner, exactly like the
handoff wrapper does.
"""
import json

import pytest
from click.testing import CliRunner

from shiftlog import cli


@pytest.fixture()
def runner():
    return CliRunner(mix_stderr=False)


@pytest.fixture()
def log_file(tmp_path):
    return str(tmp_path / "log.json")


def invoke(runner, log_file, *args):
    return runner.invoke(cli, ["--data-file", log_file, *args])


def add_samples(runner, log_file):
    invoke(runner, log_file, "add", "handed over a quiet board",
           "--shift", "mon-early", "--tag", "db", "--tag", "alerts")
    invoke(runner, log_file, "add", "replaced disk on rack 7",
           "--shift", "mon-late")


def test_add_reports_each_new_id(runner, log_file):
    first = invoke(runner, log_file, "add", "all quiet", "--shift", "mon-early")
    second = invoke(runner, log_file, "add", "still quiet", "--shift", "mon-late")
    assert first.exit_code == 0
    assert first.stdout == "added entry 1\n"
    assert second.stdout == "added entry 2\n"


def test_add_persists_entries_to_the_data_file(runner, log_file):
    add_samples(runner, log_file)
    entries = json.loads(open(log_file).read())
    assert [e["id"] for e in entries] == [1, 2]
    assert entries[0]["shift"] == "mon-early"
    assert entries[0]["message"] == "handed over a quiet board"
    assert entries[0]["tags"] == ["alerts", "db"]
    assert entries[1]["tags"] == []


def test_list_shows_default_columns(runner, log_file):
    add_samples(runner, log_file)
    result = invoke(runner, log_file, "list")
    lines = result.stdout.splitlines()
    assert lines[0] == "id | shift | message"
    assert lines[1] == "1 | mon-early | handed over a quiet board"
    assert lines[2] == "2 | mon-late | replaced disk on rack 7"


def test_list_with_show_tags_adds_one_tags_column(runner, log_file):
    add_samples(runner, log_file)
    result = invoke(runner, log_file, "list", "--show-tags")
    lines = result.stdout.splitlines()
    assert lines[0] == "id | shift | message | tags"
    assert lines[1].endswith("| alerts,db")


def test_plain_list_after_show_tags_has_default_columns(runner, log_file):
    add_samples(runner, log_file)
    invoke(runner, log_file, "list", "--show-tags")
    result = invoke(runner, log_file, "list")
    assert result.stdout.splitlines()[0] == "id | shift | message"


def test_show_tags_twice_keeps_the_header_stable(runner, log_file):
    add_samples(runner, log_file)
    first = invoke(runner, log_file, "list", "--show-tags")
    second = invoke(runner, log_file, "list", "--show-tags")
    assert first.stdout.splitlines()[0] == "id | shift | message | tags"
    assert second.stdout.splitlines()[0] == "id | shift | message | tags"


def test_list_on_missing_file_prints_header_only(runner, log_file):
    result = invoke(runner, log_file, "list")
    assert result.exit_code == 0
    assert result.stdout == "id | shift | message\n"


def test_show_prints_the_entry_and_exits_zero(runner, log_file):
    add_samples(runner, log_file)
    result = invoke(runner, log_file, "show", "1")
    assert result.exit_code == 0
    assert "shift: mon-early" in result.stdout
    assert "message: handed over a quiet board" in result.stdout
    assert "tags: alerts,db" in result.stdout


def test_show_unknown_id_exits_with_code_one(runner, log_file):
    add_samples(runner, log_file)
    result = invoke(runner, log_file, "show", "42")
    assert result.exit_code == 1


def test_show_unknown_id_reports_on_stderr_only(runner, log_file):
    add_samples(runner, log_file)
    result = invoke(runner, log_file, "show", "42")
    assert result.stderr == "error: no entry with id 42\n"
    assert result.stdout == ""
