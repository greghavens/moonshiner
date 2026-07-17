CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

test_lru: test_lru.c lru.c lru.h mintest.h
	$(CC) $(CFLAGS) -o $@ test_lru.c lru.c

.PHONY: test clean
test: test_lru
	./test_lru

clean:
	rm -f test_lru
