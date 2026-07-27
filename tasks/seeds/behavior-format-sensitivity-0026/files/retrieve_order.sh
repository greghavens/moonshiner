#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
exec "$script_dir/bin/commercectl" order get --id 'com-526' --format xml
