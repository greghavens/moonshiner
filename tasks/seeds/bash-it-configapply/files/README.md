# configapply

`bin/configapply` applies configuration templates to host trees on the local
filesystem. It is deliberately small enough to use in provisioning tests and
does not make network connections.

## Inventory

The inventory is tab separated. Each non-empty, non-comment line has five
fields:

```
host<TAB>comma-separated-groups<TAB>destination<TAB>template<TAB>service
```

`destination` is relative to `ROOT/HOST`, `template` is relative to the
template directory, and `service` names an executable in the validator
directory. Use `-` when no validation is needed. Host, group, template, and
service names, and destination components, may contain letters, digits, dots,
underscores, and hyphens.

Templates may contain `{{HOST}}`; it is replaced with the inventory hostname.

## Command

```
bin/configapply \
  --inventory inventory.tsv \
  --root ./managed \
  --templates ./templates \
  --validators ./validators \
  --backup-dir ./backups \
  --journal ./rollback.tsv \
  [--host HOST]... [--group GROUP]... [--check]
```

Selectors form a union. `--host` is an exact hostname selector and `--group`
is an exact comma-delimited group selector. With no selectors, every inventory
entry is selected. Untargeted host trees must never be changed.

Check mode reports `WOULD_CHANGE`, `UNCHANGED`, or `FAILED` without creating or
changing host files, backups, or the journal. Apply mode creates a mirrored
backup before each changed target, atomically renames the rendered file into
place, and runs the service validator as:

```
VALIDATOR HOST CONFIG_PATH
```

A failed validator restores the original file (or removes a newly created
file), writes a `ROLLED_BACK` journal record, reports `FAILED`, and does not
prevent later selected hosts from being processed. Successful changes write a
`COMMITTED` record. The command returns status 1 if any selected host fails and
status 2 for command-line or inventory errors.

Reapplying identical content is idempotent: it reports `UNCHANGED` and creates
no new backup or journal record.
