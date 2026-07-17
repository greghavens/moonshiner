CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

test_minesolver: test_minesolver.c minesolver.c minesolver.h mintest.h
	$(CC) $(CFLAGS) -o $@ test_minesolver.c minesolver.c

.PHONY: test clean
test: test_minesolver
	./test_minesolver

clean:
	rm -f test_minesolver
