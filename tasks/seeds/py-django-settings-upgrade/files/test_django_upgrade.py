from pathlib import Path
import importlib
import sys

sys.dont_write_bytecode = True

from framework51 import RemovedSettingError, load_settings
from service import settings
from service.database import IndexDatabase, connection_policy
from service.web import handle_probe


ROOT = Path(__file__).resolve().parent
passed = 0
failed = 0


def check(name, body):
    global passed, failed
    try:
        body()
        passed += 1
        print(f"PASS {name}")
    except Exception as error:
        failed += 1
        print(f"FAIL {name}: {type(error).__name__}: {error}")


def current_settings_load():
    runtime = load_settings(settings)
    assert runtime.middleware == (
        "service.web.SecurityHeadersMiddleware",
        "service.web.RequestIdMiddleware",
        "service.web.SessionMiddleware",
        "service.web.AuditMiddleware",
    )
    assert runtime.storages["default"].backend == "service.storage.TenantDocumentStorage"
    assert runtime.storages["default"].options == {
        "root": "var/documents",
        "permissions": "private",
    }
    assert runtime.storages["staticfiles"].backend == "service.storage.ManifestStaticStorage"
    assert runtime.storages["staticfiles"].options == {
        "manifest": "assets/static-manifest.json",
    }


def middleware_order_is_unchanged():
    result = handle_probe(settings, "request-fixture-17")
    assert result == {
        "request_id": "request-fixture-17",
        "trace": [
            "SecurityHeadersMiddleware",
            "RequestIdMiddleware",
            "SessionMiddleware",
            "AuditMiddleware",
        ],
        "frame": "DENY",
        "seen_request_id": "request-fixture-17",
        "session": "loaded",
        "audit": "request-fixture-17:loaded",
    }


def database_settings_are_unchanged():
    assert connection_policy(settings) == {
        "engine": "django.db.backends.sqlite3",
        "name": "var/service-index.sqlite3",
        "persistent_seconds": 45,
        "atomic_requests": True,
    }


def commands_use_current_api_and_keep_transactions():
    sys.modules.pop("service.management.commands", None)
    sys.modules.pop("service.management.rebuild_index", None)
    commands = importlib.import_module("service.management.commands")
    assert commands.command_names() == ("check", "rebuild_index")
    db = IndexDatabase()
    assert commands.run("check", db) == "configuration ok"
    assert commands.run("rebuild_index", db, [b" alpha ", "beta"]) == ["alpha", "beta"]
    assert db.rows == ["alpha", "beta"]
    assert (db.commits, db.rollbacks) == (1, 0)

    preview = commands.run("rebuild_index", db, [b" gamma "], dry_run=True)
    assert preview == ["gamma"]
    assert db.rows == ["alpha", "beta"]
    assert (db.commits, db.rollbacks) == (1, 0)

    try:
        commands.run("rebuild_index", db, ["delta", "reject-this-label", "epsilon"])
        raise AssertionError("a rejected row must fail the command")
    except ValueError as error:
        assert str(error) == "index label rejected"
    assert db.rows == ["alpha", "beta"]
    assert (db.commits, db.rollbacks) == (1, 1)


def protected_notes_record_both_contracts():
    notes = (ROOT / "contracts" / "django_51_migration_notes.md").read_text()
    for phrase in (
        "`DEFAULT_FILE_STORAGE` and `STATICFILES_STORAGE`",
        "`default` and `staticfiles` aliases",
        "ordered `MIDDLEWARE` sequence",
        "`CONN_MAX_AGE`",
        "`force_text` is absent",
    ):
        assert phrase in notes


def main():
    check("current settings loader accepts the migrated configuration", current_settings_load)
    check("middleware order and effects remain stable", middleware_order_is_unchanged)
    check("database behavior remains stable", database_settings_are_unchanged)
    check("management commands use the current text API", commands_use_current_api_and_keep_transactions)
    check("protected migration notes record old and new contracts", protected_notes_record_both_contracts)
    print(f"checks: {passed} passed, {failed} failed")
    raise SystemExit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()

