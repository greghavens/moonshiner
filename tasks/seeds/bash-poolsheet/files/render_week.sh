#!/usr/bin/env bash
# render_week.sh -- fill the weekly schedule sheet template for the front desk.
# usage: render_week.sh <template> <week-label>
set -u

if [ $# -ne 2 ]; then
  echo "usage: render_week.sh <template> <week-label>" >&2
  exit 64
fi

template=$1
week=$2

if [ ! -r "$template" ]; then
  echo "render_week.sh: cannot read template: $template" >&2
  exit 66
fi

echo "rendering week of $week" >&2

# Placeholders live in the template; facility wording lives here, so the desk
# staff never have to edit the template itself.
sed -e "s/{{WEEK}}/$week/g" \
    -e "s/{{DESK}}/front desk, ext. 4145/g" \
    -e 's/{{POOL}}/O'\'Brien Aquatic Centre/g' \
    "$template"

echo "render complete" >&2
