#!/bin/sh
set -eu

if command -v ruby >/dev/null 2>&1; then
  ruby_bin=ruby
else
  ruby_bin=/home/linuxbrew/.linuxbrew/Homebrew/Library/Homebrew/vendor/portable-ruby/current/bin/ruby
fi

if ! command -v "$ruby_bin" >/dev/null 2>&1 && [ ! -x "$ruby_bin" ]; then
  echo "ruby runtime not found" >&2
  exit 127
fi

exec "$ruby_bin" -Ilib test/visibility_timeout_test.rb
