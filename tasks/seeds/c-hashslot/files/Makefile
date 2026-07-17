CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

test_hs: test_hs.c hs.c hs.h mintest.h
	$(CC) $(CFLAGS) -o $@ test_hs.c hs.c

.PHONY: test clean
test: test_hs
	./test_hs

clean:
	rm -f test_hs
