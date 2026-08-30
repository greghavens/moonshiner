#!/bin/sh
set -eu
find . -type f -name '*.py' ! -path './tests/*' ! -path './test/*' ! -path './verify/*' ! -path './verifier/*' ! -path './mock/*' -exec python3 -m py_compile {} +
