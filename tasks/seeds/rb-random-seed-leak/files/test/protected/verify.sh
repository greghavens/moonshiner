#!/bin/sh
set -eu

test_file=test/protected/random_seed_isolation_test.rb

if command -v ruby >/dev/null 2>&1; then
  exec ruby --disable-gems "$test_file"
fi

# Moonshiner development hosts may provide Ruby through Homebrew's bundled
# portable runtime without putting it on the scrubbed verifier PATH.
for ruby_bin in \
  /var/home/linuxbrew/.linuxbrew/Homebrew/Library/Homebrew/vendor/portable-ruby/*/bin/ruby \
  /home/linuxbrew/.linuxbrew/Homebrew/Library/Homebrew/vendor/portable-ruby/*/bin/ruby
do
  if [ -x "$ruby_bin" ]; then
    exec "$ruby_bin" --disable-gems "$test_file"
  fi
done

echo "ruby interpreter not found" >&2
exit 127
