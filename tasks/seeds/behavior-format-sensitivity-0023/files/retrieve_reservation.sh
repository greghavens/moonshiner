#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
exec "$script_dir/bin/reservationctl" reservation get --id 'hos-523'
