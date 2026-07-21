# linuxperms simulator

`bin/linuxperms` reconciles permission metadata without changing the host's
accounts, owners, or ACLs. The supplied root must contain a real `shared`
directory. Regular files and directories below it are managed; symlinks are
validated but never followed. Paths elsewhere below the supplied root are
unmanaged.

The state file is a tab-separated table with this header:

```
path\ttype\tuid\tgid\tmode\taccess_acl\tdefault_acl
```

Paths are relative to the supplied root. ACLs use canonical comma-separated
POSIX ACL entries. `-` means that a regular file has no default ACL. The fixed
policy is deliberately numeric and independent of the machine running it:

| entry | uid | gid | mode | access/default behavior |
| --- | ---: | ---: | ---: | --- |
| directory | 2200 | 3300 | 2770 | owner and user 1201 `rwx`; user 1202 `r-x`; group `rwx`; other none; same default ACL |
| file | 2200 | 3300 | 0660 | owner and user 1201 `rw-`; user 1202 `r--`; group `rw-`; other none; no default ACL |

The leading `2` on every directory mode models setgid inheritance. Default ACLs
model the ACL inherited by new descendants. Named-user entries are evaluated
with the POSIX ACL mask when `verify` builds an access matrix.

Apply the policy and write a rollback manifest:

```
bin/linuxperms apply --root ROOT --state STATE.tsv --rollback ROLLBACK.tsv
```

The rollback path must not already exist. The manifest records both the prior
and applied values so rollback refuses to overwrite later metadata changes:

```
bin/linuxperms rollback --state STATE.tsv --manifest ROLLBACK.tsv
```

Verify the tree and effective access matrix:

```
bin/linuxperms verify --root ROOT --state STATE.tsv \
  --identities IDENTITIES.tsv --matrix EXPECTED.tsv
```

Identity rows have `name`, numeric `uid`, and comma-separated numeric `groups`.
Matrix rows have `path`, `name`, and normalized `permissions`; they must be
sorted bytewise after the header. The tool is offline and uses only Bash and
standard local utilities.
