#!/usr/bin/env bash
# Verification gate: assemble the evaluator with the harness and run the suite.
set -euo pipefail
gcc -Wall -Wextra -Werror -O2 -o runner test_bjval.c bjval.s
./runner
