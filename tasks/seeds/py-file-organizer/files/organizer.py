"""Rule-based tidier for a downloads-style folder.

Each rule claims a set of file extensions and names a destination subfolder;
destinations may embed strftime placeholders, filled in from the file's
modification time.  The sweep is deliberately shallow: only top-level files
of the target directory are touched, subdirectories and dotfiles are left
alone.  main() is the ``python3 organizer.py <dir> --rule jpg,png:images``
CLI wrapper.
"""
import argparse
import datetime
import os
import shutil


def _normalize_exts(exts):
    """Lower-case every extension and make sure it carries a leading dot."""
    out = []
    for ext in exts:
        ext = ext.lower()
        if not ext.startswith("."):
            ext = "." + ext
        out.append(ext)
    return out


def matches(rule, name):
    """True if the file name's extension is one the rule claims."""
    _, ext = os.path.splitext(name)
    return ext.lower() in _normalize_exts(rule["ext"])


def dest_for(rule, path):
    """Relative destination folder for ``path`` under the given rule.

    A '%' in the rule's dest means it is a strftime pattern to be expanded
    from the file's mtime (so ``archive/%Y/%m`` buckets files by month).
    """
    dest = rule["dest"]
    if "%" in dest:
        stamp = datetime.datetime.fromtimestamp(os.path.getmtime(path))
        dest = stamp.strftime(dest)
    return dest


def scan(root, rules):
    """Yield (name, dest_folder) for every top-level file a rule claims.

    Files are visited in case-insensitive name order, dotfiles and
    subdirectories are skipped, and the first matching rule wins.
    """
    for name in sorted(os.listdir(root), key=str.lower):
        if name.startswith("."):
            continue
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        for rule in rules:
            if matches(rule, name):
                yield name, dest_for(rule, path)
                break


def organize(root, rules):
    """Move every claimed file into its destination folder.

    Destination folders are created on demand.  Returns the executed moves
    as (src_name, dst_relpath) tuples in the order they were performed.
    """
    moves = []
    for name, folder in scan(root, rules):
        os.makedirs(os.path.join(root, folder), exist_ok=True)
        dst = os.path.join(folder, name)
        shutil.move(os.path.join(root, name), os.path.join(root, dst))
        moves.append((name, dst))
    return moves


def parse_rule(spec):
    """Turn an ``EXT[,EXT...]:DEST`` CLI spec into a rule dict."""
    exts, sep, dest = spec.partition(":")
    if not sep or not exts or not dest:
        raise ValueError("bad rule spec: %r" % spec)
    return {"ext": exts.split(","), "dest": dest}


def main(argv=None):
    parser = argparse.ArgumentParser(description="tidy a folder by rules")
    parser.add_argument("root", help="directory to tidy")
    parser.add_argument("--rule", action="append", default=[],
                        metavar="EXTS:DEST", help="e.g. jpg,png:images")
    args = parser.parse_args(argv)
    try:
        rules = [parse_rule(spec) for spec in args.rule]
        for src, dst in organize(args.root, rules):
            print("%s -> %s" % (src, dst))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
