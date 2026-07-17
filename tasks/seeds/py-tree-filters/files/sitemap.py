"""Print an indented tree of a site's content directory.

The docs build pipeline calls tree_lines() to show writers what the
generated site layout will look like before a build kicks off; main() is
the ``python3 sitemap.py <dir>`` CLI wrapper around it.
"""
import argparse
import os


def _entries(path):
    """Split a directory into (dirs, files), each sorted case-insensitively.

    Dot-prefixed entries (VCS metadata, editor droppings) are never part of
    the published site, so they are skipped outright.
    """
    dirs, files = [], []
    for name in os.listdir(path):
        if name.startswith("."):
            continue
        if os.path.isdir(os.path.join(path, name)):
            dirs.append(name)
        else:
            files.append(name)
    return sorted(dirs, key=str.lower), sorted(files, key=str.lower)


def tree_lines(root):
    """Return the tree under ``root`` as a list of lines.

    Directories come first at every level, everything is sorted
    case-insensitively, directories carry a trailing '/', and each level
    of nesting indents by two spaces.  The root itself is not printed.
    """
    if not os.path.isdir(root):
        raise ValueError("not a directory: %s" % root)

    lines = []

    def walk(path, depth):
        dirs, files = _entries(path)
        indent = "  " * (depth - 1)
        for d in dirs:
            lines.append("%s%s/" % (indent, d))
            walk(os.path.join(path, d), depth + 1)
        for f in files:
            lines.append(indent + f)

    walk(root, 1)
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="print an indented tree of a content directory")
    parser.add_argument("root", help="directory to walk")
    args = parser.parse_args(argv)
    try:
        for line in tree_lines(args.root):
            print(line)
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
