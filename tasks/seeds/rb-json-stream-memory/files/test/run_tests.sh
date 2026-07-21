#!/bin/sh
set -eu

if command -v ruby >/dev/null 2>&1; then
  exec ruby -Ilib test/protected_json_stream_transformer_test.rb
fi

for ruby_bin in \
  /home/linuxbrew/.linuxbrew/Homebrew/Library/Homebrew/vendor/portable-ruby/*/bin/ruby \
  /var/home/linuxbrew/.linuxbrew/Homebrew/Library/Homebrew/vendor/portable-ruby/*/bin/ruby
do
  if [ -x "$ruby_bin" ]; then
    exec "$ruby_bin" -Ilib test/protected_json_stream_transformer_test.rb
  fi
done

echo "Ruby runtime not found" >&2
exit 127
