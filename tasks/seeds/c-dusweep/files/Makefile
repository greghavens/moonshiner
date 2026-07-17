CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror -g

test_sweep: test_sweep.c sweep.c sweep.h mintest.h
	$(CC) $(CFLAGS) -o $@ test_sweep.c sweep.c

.PHONY: test clean
test: test_sweep
	./test_sweep

clean:
	rm -f test_sweep
