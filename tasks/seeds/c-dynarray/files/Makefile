CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

test_dynarray: test_dynarray.c dynarray.c dynarray.h mintest.h
	$(CC) $(CFLAGS) -o $@ test_dynarray.c dynarray.c

.PHONY: test clean
test: test_dynarray
	./test_dynarray

clean:
	rm -f test_dynarray
