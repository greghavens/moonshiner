CC ?= cc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -Werror -pedantic
CPPFLAGS ?= -Iinclude

.PHONY: test
test:
	@set -eu; \
	test_bin=./.serial-writer-tests; \
	trap 'rm -f "$$test_bin"' EXIT HUP INT TERM; \
	$(CC) $(CPPFLAGS) $(CFLAGS) src/serial_writer.c tests/test_serial_writer.c -o "$$test_bin"; \
	"$$test_bin"
