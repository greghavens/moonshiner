# Django 5.1 migration contract (protected local excerpt)

This fixture records the framework contract used by the service upgrade.  It
is intentionally local so acceptance does not depend on documentation sites.

## Settings

The transitional `DEFAULT_FILE_STORAGE` and `STATICFILES_STORAGE` settings are
not read by the 5.1 settings loader.  Defining either is an error in this
fixture.  Storage configuration is supplied through `STORAGES`, whose
`default` and `staticfiles` aliases each contain a `BACKEND`; backend-specific
values remain below `OPTIONS`.

Middleware remains an ordered `MIDDLEWARE` sequence.  The upgrade does not
change middleware ordering or convert it to a set.

Database dictionaries retain their engine, name, `CONN_MAX_AGE`, and
`ATOMIC_REQUESTS` meanings.  Storage migration must not rewrite database
configuration.

## Text API and commands

The compatibility name `force_text` is absent.  Framework-facing code uses
`force_str`, which returns strings unchanged and UTF-8 decodes byte values.
Management commands remain discoverable by their deployed names and keep
their transaction and dry-run behavior.

