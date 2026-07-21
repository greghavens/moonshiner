# Offline C++ ABI release check

This repository contains the ABI comparison step used by the release job for
`liborbit`. The job consumes deterministic snapshots committed by the build:

* `symbols.txt` records public ELF exports as
  `kind|name|version|status|alias-target`. `status` is `default` or `compat`,
  and a dash means that the export is not an alias.
* `layouts.txt` records public C++ record sizes, alignments, base classes, and
  fields. Offsets and sizes are bytes.

Run the same check as CI with:

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The checker itself can be invoked with two snapshot directories:

```sh
python3 tools/check_abi.py BASELINE_DIR CANDIDATE_DIR
```

New exports and new records are permitted. Removing or changing an existing
export, including a compatibility alias, is an ABI break. Existing record
layouts must remain byte-for-byte compatible.
