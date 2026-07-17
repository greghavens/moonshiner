#!/usr/bin/env bash
# Verification gate: assemble the renderer with the harness and run the suite.
set -euo pipefail
gcc -Wall -Wextra -Werror -O2 -o runner test_boardfmt.c boardfmt.s
./runner
