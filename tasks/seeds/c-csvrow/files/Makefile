CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

test_csvrow: test_csvrow.c csvrow.c csvrow.h mintest.h
	$(CC) $(CFLAGS) -o $@ test_csvrow.c csvrow.c

.PHONY: test clean
test: test_csvrow
	./test_csvrow

clean:
	rm -f test_csvrow
