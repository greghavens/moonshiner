#!/bin/sh
set -eu

if command -v ruby >/dev/null 2>&1; then
  exec ruby -Ilib protected_tests/slug_unique_rollout_test.rb
fi

for ruby_path in /home/linuxbrew/.linuxbrew/Homebrew/Library/Homebrew/vendor/portable-ruby/*/bin/ruby; do
  if [ -x "$ruby_path" ]; then
    exec "$ruby_path" -Ilib protected_tests/slug_unique_rollout_test.rb
  fi
done

echo "Ruby runtime not found" >&2
exit 127
