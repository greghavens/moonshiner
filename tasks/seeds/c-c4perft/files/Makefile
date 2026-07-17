CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

test_c4: test_c4.c c4.c c4.h mintest.h
	$(CC) $(CFLAGS) -o $@ test_c4.c c4.c

.PHONY: test clean
test: test_c4
	./test_c4

clean:
	rm -f test_c4
