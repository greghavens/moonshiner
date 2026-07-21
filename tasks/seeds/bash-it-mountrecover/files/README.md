# mountrecover

`mountrecover` turns four captured pieces of mount evidence into a conservative
diagnosis:

- `fstab` — the table that failed at boot;
- `device-identity.txt` — post-boot `blkid` output;
- `filesystem-state.txt` — output from a read-only `e2fsck -n` check; and
- `boot.log` — the relevant boot and mount messages.

It never runs `fsck` and never performs a real mount.  A candidate entry is
tested only with:

```text
mount --fake --no-mtab --all --fstab corrected.fstab
```

Run the protected regression suite with `make test`.  To inspect the supplied
incident, run `make incident`.

## Safe recovery workflow

Review `report.env` and `corrected.fstab` before changing the host.  In recovery
mode, remount the root filesystem read/write, preserve an exact backup, install
the reviewed candidate, reload systemd, and test the one mount:

```sh
mount -o remount,rw /
cp -a /etc/fstab /etc/fstab.mountrecover.bak
cp corrected.fstab /etc/fstab
systemctl daemon-reload
mount /srv/archive
```

For ext4, ownership and modes belong to the mounted directory's filesystem;
apply them with `chown`/`chmod` after mounting rather than with FAT-style
`uid=`, `gid=`, or `umask=` mount options.

## Recovery-mode reversal

If the candidate causes trouble, return to recovery mode and restore the saved
table before rebooting:

```sh
mount -o remount,rw /
cp -a /etc/fstab.mountrecover.bak /etc/fstab
systemctl daemon-reload
reboot
```

The report repeats this reversal as a single copy-safe command sequence so it
remains available alongside the generated candidate.
