#!/usr/bin/env bash
set -euo pipefail
repo="greghavens/moonshiner"
version="latest"
prefix="${HOME}/.local"
storage=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --version) version="$2"; shift 2;;
    --prefix) prefix="$2"; shift 2;;
    --storage) storage="$2"; shift 2;;
    --uninstall) rm -f "$prefix/bin/moonshiner"; rm -rf "$prefix/share/moonshiner/runtime"; exit 0;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done
command -v python3 >/dev/null || { echo "Python 3.11+ is required" >&2; exit 1; }
if [ "$version" = latest ]; then
  latest_url="$(curl -fsSL -o /dev/null -w '%{url_effective}' \
    "https://github.com/$repo/releases/latest")"
  version="${latest_url##*/v}"
  case "$version" in
    [0-9]*.[0-9]*.[0-9]*) ;;
    *) echo "could not resolve the latest Moonshiner release" >&2; exit 1;;
  esac
fi
base="https://github.com/$repo/releases/download/v$version"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
wheel="moonshiner-$version-py3-none-any.whl"
curl -fsSL "$base/$wheel" -o "$tmp/$wheel"
curl -fsSL "$base/SHA256SUMS" -o "$tmp/SHA256SUMS"
(cd "$tmp" && grep "  $wheel\$" SHA256SUMS | sha256sum -c -)
runtime="$prefix/share/moonshiner/runtime/$version"
python3 -m venv "$runtime"
"$runtime/bin/python" -m pip install "$tmp/$wheel"
mkdir -p "$prefix/bin"
ln -sfn "$runtime/bin/moonshiner" "$prefix/bin/moonshiner"
if [ -n "$storage" ]; then
  echo "note: storage is project-local; cd '$storage' and run moonshiner there"
fi
echo "installed moonshiner $version -> $prefix/bin/moonshiner"
case ":$PATH:" in *":$prefix/bin:"*) ;; *) echo "add $prefix/bin to PATH";; esac
