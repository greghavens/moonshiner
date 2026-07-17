CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

test_tok: test_tok.c tok.c tok.h mintest.h
	$(CC) $(CFLAGS) -o $@ test_tok.c tok.c

.PHONY: test clean
test: test_tok
	./test_tok

clean:
	rm -f test_tok
