CC ?= cc
CFLAGS ?= -std=c17 -O2 -Wall -Wextra -Werror -pedantic
CPPFLAGS ?=

.PHONY: test
test:
	@set -eu; \
	bin=.test_boundary_parser; \
	trap 'rm -f "$$bin"' EXIT HUP INT TERM; \
	$(CC) $(CPPFLAGS) $(CFLAGS) -Iinclude \
		src/boundary_parser.c tests/test_boundary_parser.c -o "$$bin"; \
	"./$$bin"
