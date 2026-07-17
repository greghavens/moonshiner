#!/usr/bin/env bash
# Verification gate: assemble the codec with the harness and run the suite.
set -euo pipefail
gcc -Wall -Wextra -Werror -O2 -o runner test_rle.c rle.s
./runner
